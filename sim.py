"""
sim.py — Single entry point for the emergent civilization simulation.

Run with:  python sim.py

Layer architecture
──────────────────
  Layer 0 · world       — terrain, biomes, resource regeneration, seasons
  Layer 1 · inhabitants — movement, eating, gathering, trust
  Layer 2 · beliefs     — event-driven belief formation and sharing
  Layer 3 · factions    — group formation, territory, food pooling
  Layer 4 · economy     — currency, trade, raids, pricing, wealth
  Layer 5 · combat      — war declarations, battles, alliances, tribute
  Layer 6 · technology  — research tree, passive bonuses, writing spread
  Layer 7 · diplomacy   — council votes, treaties, reputation, surrender
  Layer 8 · mythology   — LLM narrative: chronicles, myths, epitaphs
  Display               — per-tick render + periodic summaries
"""

import sys, time, re, random, pathlib, gc, threading
from datetime import datetime
import config
sys.stdout.reconfigure(encoding='utf-8')

# ── Layer imports ──────────────────────────────────────────────────────────
from world       import (world, tick as _world_tick, GRID, BIOME_MAX,
                         grid_add, grid_remove, update_map_bounds,
                         grid_occupants, get_settlement_at)
from inhabitants import (Inhabitant, do_tick, do_tick_preamble, do_tick_body,
                         is_winter, regen_rate,
                         NAMES, WINTER_START, CYCLE_LEN, make_child,
                         procreation_lock)
from beliefs     import assign_beliefs, share_beliefs, add_belief
from factions    import check_faction_formation, faction_tick, Faction, _faction_name as _gen_faction_name
import economy
import combat
import technology
import diplomacy
import mythology
import display
import dashboard_bridge

TICKS = config.TICKS

# ── Shared simulation state ────────────────────────────────────────────────
people:          list   = []
factions:        list   = []
all_dead:        list   = []
event_log:       list   = []
era_summaries:   list   = []   # {'start_t', 'end_t', 'name', 'text'} — archived 100-tick windows
_dead_factions:  list   = []   # defunct factions kept for reference
_last_dynamic_t: int    = 0    # last tick with war / schism / faction-formation
_event_log_fh:   object = None  # file handle used to flush pruned log lines to disk

# ── Threading locks (Layer 1) ──────────────────────────────────────────────
# _world_lock  : guards read-modify-write on world[r][c]['resources']
# _log_lock    : guards event_log.append() from worker threads
# _trade_lock  : guards cross-inhabitant inventory swaps
_LAYER1_THREADS = 4   # one per logical core (N95)
_world_lock  = threading.Lock()
_log_lock    = threading.Lock()
_trade_lock  = threading.Lock()


def _spawn(inh) -> None:
    """Append *inh* to the global people list and register it in grid_occupants.

    Using this helper (instead of bare people.append) guarantees the spatial
    partition stays in sync with the authoritative list at every spawn site.
    """
    people.append(inh)
    grid_add(inh)


# ══════════════════════════════════════════════════════════════════════════
# Logging — tees stdout to file; shows only notable lines on terminal
# ══════════════════════════════════════════════════════════════════════════

class _LogTee:
    """Every byte goes to the log file.  Only filtered lines reach the terminal."""

    # Keywords that earn a line a spot on the terminal during the run
    _SHOW = frozenset({
        # Combat events
        'WAR DECLARED', 'WAR ENDS', 'fell in battle',
        'ALLIANCE', 'call to arms', 'answers', 'absorbed', 'fled to',
        'fray', 'vengeance', 'conquest',
        # Faction / population events
        'FACTION FORMED', 'FACTION MERGE', 'SCHISM',
        'starved', 'Travelers',
        # Tech research events
        'TECH DISCOVERED', 'begins researching', 'research paused', 'research resumed',
        # Fatal / terminal signals
        'All inhabitants have perished', '[Simulation interrupted',
        # Diplomacy events
        'TREATY', 'SURRENDER TERMS', 'broke treaty', 'MUTUAL DEFENSE',
        'shares food', 'Council vote',
        # World events & era signals
        'WORLD EVENT', 'NEW ERA DAWNS', 'ERA SUMMARY',
        # Disruption events
        'GREAT MIGRATION', 'PLAGUE SWEEPS', 'CIVIL WAR', 'PROMISED LAND',
        'PROPHET', 'wasted away',
    })

    passthrough: bool = False   # True → show everything (used for final report)

    def __init__(self, log_fh, real_stdout):
        self._log  = log_fh
        self._real = real_stdout
        self._buf  = ''

    def write(self, text: str) -> None:
        self._log.write(text)
        self._log.flush()
        self._buf += text
        while '\n' in self._buf:
            line, self._buf = self._buf.split('\n', 1)
            show = self.passthrough or (
                any(kw in line for kw in self._SHOW)
                and '\u2502' not in line   # skip box-border lines (│)
            )
            if show:
                self._real.write(line + '\n')
                self._real.flush()

    def flush(self) -> None:
        self._log.flush()

    def fileno(self) -> int:          # lets sys.stderr etc. work
        return self._real.fileno()


# ══════════════════════════════════════════════════════════════════════════
# Layer 1 threading helpers
# ══════════════════════════════════════════════════════════════════════════

def process_inhabitants_chunk(inhabitants_list: list, all_people: list,
                               t: int, dead_bucket: list) -> None:
    """Worker target: runs do_tick_body for each inhabitant in *inhabitants_list*.

    Called from a threading.Thread.  Shared state (world resources, event_log,
    cross-inhabitant inventory swaps) is protected by the module-level locks.
    Collected deaths are appended to *dead_bucket* (caller-supplied list).
    """
    for inh in inhabitants_list:
        do_tick_body(
            inh, all_people, t,
            event_log, dead_bucket,
            world_lock=_world_lock,
            log_lock=_log_lock,
            trade_lock=_trade_lock,
        )


# ══════════════════════════════════════════════════════════════════════════
# Layer functions — one per concern, called in order each tick
# ══════════════════════════════════════════════════════════════════════════

def world_layer(t: int, winter_just_ended: bool) -> None:
    """Layer 0: announce season changes, then regenerate resources."""
    winter = is_winter(t)
    if t > 1 and (t - 1) % CYCLE_LEN == WINTER_START and winter:
        event_log.append(f"Tick {t:03d}: ❄ WINTER ARRIVES — regen pauses")
    if winter_just_ended:
        event_log.append(f"Tick {t:03d}: ☀ Spring — food regeneration resumes")
    _world_tick(regen_rate(t), pop=len(people), pop_cap=POP_CAP)


def inhabitants_layer(t: int) -> tuple[list, dict]:
    """Layer 1: move, eat, gather, trust — processed across 4 threads.

    Execution order:
      1. Serial preamble  (shuffle, crowd-control, trust-pruning) in main thread.
      2. Split population into _LAYER1_THREADS chunks.
      3. Spawn one threading.Thread per chunk; each calls process_inhabitants_chunk.
      4. Main thread joins all workers before returning.
      5. Dead inhabitants collected from all buckets, removed from people list.
    """
    prev_positions = {p.name: (p.r, p.c) for p in people}

    # ─ 1. Serial preamble ──────────────────────────────────────────────
    do_tick_preamble(people, t)

    # ─ 2. Split population into chunks ──────────────────────────────────
    n        = len(people)
    size     = max(1, (n + _LAYER1_THREADS - 1) // _LAYER1_THREADS)
    chunks   = [people[i: i + size] for i in range(0, n, size)]

    # ─ 3. One dead-bucket + one thread per chunk ────────────────────────
    dead_buckets: list[list] = [[] for _ in chunks]
    threads: list[threading.Thread] = [
        threading.Thread(
            target=process_inhabitants_chunk,
            args=(chunk, people, t, bucket),
            daemon=True,
        )
        for chunk, bucket in zip(chunks, dead_buckets)
    ]

    for th in threads:
        th.start()

    # ─ 4. Wait for all workers ───────────────────────────────────────────
    for th in threads:
        th.join()

    # ─ 5. Collect deaths and update people list ────────────────────────
    deaths: list = [d for bucket in dead_buckets for d in bucket]
    for d in deaths:
        grid_remove(d)   # evict from spatial partition before removing from list
        people.remove(d)
    all_dead.extend(deaths)
    return deaths, prev_positions


def beliefs_layer(t: int, deaths: list, winter_just_ended: bool,
                  prev_positions: dict) -> set:
    """Layer 2: assign new beliefs, share between neighbours."""
    assign_beliefs(people, deaths, winter_just_ended, prev_positions,
                   t, event_log)
    share_beliefs(people, t, event_log)
    return {d.name for d in deaths}


def factions_layer(t: int, dead_names: set) -> None:
    """Layer 3: form factions every 5 ticks, run member mechanics."""
    if t % 5 == 0:
        check_faction_formation(people, factions, t, event_log)
    faction_tick(people, factions, t, event_log)
    for f in factions:
        f.remove_dead(dead_names)


def economy_layer(t: int) -> None:
    """Layer 4: currency, pricing, trade, raids, scarcity, wealth."""
    economy.economy_tick(people, factions, t, event_log)


def combat_layer(t: int) -> None:
    """Layer 5: war declarations, multi-tick battles, alliances, tribute."""
    combat.combat_tick(people, factions, all_dead, t, event_log)


def technology_layer(t: int) -> None:
    """Layer 6: research tree, passive tech bonuses, writing belief spread."""
    technology.technology_tick(factions, t, event_log)


def diplomacy_layer(t: int) -> None:
    """Layer 7: council votes, formal treaties, reputation, surrender terms."""
    diplomacy.diplomacy_tick(factions, t, event_log)


POP_CAP = config.POP_CAP   # defined in config.py — hard population ceiling


MAX_BIRTHS_PER_TICK = 3   # upper limit on new births in a single tick

def procreation_layer(t: int) -> None:
    """Generational logic: trust-based births, belief inheritance, faction assignment.

    Runs up to MAX_BIRTHS_PER_TICK birth attempts per tick.  Each iteration
    re-evaluates eligibility so that is_procreating flags set in iteration N
    naturally exclude those parents from iteration N+1.

    Thread-safety contract
    ----------------------
    procreation_lock (defined in inhabitants.py) serialises the entire critical
    section so that, even when four Layer-1 threads are active:

      • POP_CAP is checked *inside* the lock — never breached by a race.
      • is_procreating is set and cleared atomically — no pair is double-used.
      • Food deduction, name resolution, and people.append() are one atomic op.

    A try/finally guarantees is_procreating is always cleared, even on exceptions.
    """
    # Fast-path guards — read-only, no lock needed
    if len(people) >= POP_CAP or is_winter(t):
        return

    for _attempt in range(MAX_BIRTHS_PER_TICK):
        if len(people) >= POP_CAP:
            break

        used_names = {p.name for p in people} | {p.name for p in all_dead}

        # Build eligible pairs; can_procreate already skips is_procreating inhabitants
        eligible: list = []
        for i in range(len(people)):
            for j in range(i + 1, len(people)):
                if people[i].can_procreate(people[j]):
                    eligible.append((people[i], people[j]))

        if not eligible:
            break

        pa, pb = random.choice(eligible)

        # pa_claimed tracks whether we set is_procreating so the finally can clean up
        pa_claimed = False
        child      = None
        try:
            with procreation_lock:
                # Re-verify every condition atomically — state may have changed since
                # the eligible list was built (another procreation_layer call, deaths, etc.)
                if (
                    len(people) >= POP_CAP
                    or pa.is_procreating
                    or pb.is_procreating
                    or not pa.can_procreate(pb)
                ):
                    break

                # Claim both parents.  Any subsequent thread will see is_procreating=True
                # and skip this pair inside can_procreate / the guard above.
                pa.is_procreating = True
                pb.is_procreating = True
                pa_claimed = True

                # Housing capacity: suppress birth when settlement zone is full
                _par_s = get_settlement_at(pa.r, pa.c)
                if (_par_s and _par_s.status == 'active'
                        and _par_s.owner_faction == getattr(pa, 'faction', None)):
                    if _par_s.local_pop(grid_occupants) >= _par_s.housing_capacity:
                        break  # settlement at capacity — procreation suppressed

                nm = _make_traveler_name(used_names)
                if not nm:
                    break  # finally block will clear flags

                # Food deduction, unique naming, and child construction — all atomic
                child = make_child(pa, pb, nm, people)
                child.faction = None

                # Inherit faction: shared faction takes priority, else parent_a's faction
                if pa.faction and pa.faction == pb.faction:
                    child.faction = pa.faction
                    for f in factions:
                        if f.name == child.faction:
                            f.members.append(child)
                            break
                elif pa.faction:
                    child.faction = pa.faction
                    for f in factions:
                        if f.name == child.faction:
                            f.members.append(child)
                            break

                # Append while still under lock — POP_CAP respected even with 4 threads
                # grid_add inside the lock so the child is visible in grid_occupants
                # to other threads the moment people.append() completes.
                grid_add(child)
                people.append(child)

        finally:
            # Always release the busy flags, whether the block succeeded, returned
            # early, or raised an exception.
            if pa_claimed:
                pa.is_procreating = False
                pb.is_procreating = False

        if child is not None:
            faction_tag = f" of {child.faction}" if child.faction else ""
            msg = (f"Tick {t:04d}: 🍼 BIRTH: {child.name} born to "
                   f"{pa.name} and {pb.name}{faction_tag}")
            event_log.append(msg)
            print(msg)


def mythology_layer(t: int) -> None:
    """Layer 8 (read-only): LLM chronicle, myths, and epitaphs."""
    mythology.mythology_tick(factions, all_dead, t, event_log)


# ══════════════════════════════════════════════════════════════════════════
# Long-run support: world events, era shifts, memory pruning, era naming
# ══════════════════════════════════════════════════════════════════════════

_TICK_EXTRACT = re.compile(r'^Tick\s+(\d+)')


def _era_name(entries: list) -> str:
    """Classify a 100-tick window into a named age based on dominant events."""
    wars    = sum(1 for e in entries if 'WAR DECLARED' in e)
    battled = sum(1 for e in entries if 'fell in battle' in e)
    starved = sum(1 for e in entries if 'starved' in e)
    tech    = sum(1 for e in entries if 'TECH DISCOVERED' in e)
    plague  = any('PLAGUE' in e for e in entries)
    golden  = any('GOLDEN AGE' in e for e in entries)
    quake   = any('EARTHQUAKE' in e for e in entries)
    if plague:              return 'The Age of Sickness'
    if golden:              return 'The Golden Age'
    if quake:               return 'The Age of Ruin'
    if wars >= 3 or battled >= 8: return 'The Crimson Years'
    if wars >= 1 or battled >= 3: return 'The Age of Conflict'
    if starved >= 5:        return 'The Great Famine'
    if tech >= 4:           return 'The Age of Iron'
    if tech >= 2:           return 'The Age of Enlightenment'
    return                         'The Long Peace'


def _prune_event_log(t: int) -> None:
    """Keep only the last 200 events in memory; archive older ticks into era_summaries.

    Any entries that would be evicted are written directly to the log file before
    being removed from RAM so nothing is silently lost.
    """
    if len(event_log) <= 200:
        return
    archive      = event_log[:-200]
    event_log[:] = event_log[-200:]
    # Flush evicted entries to disk immediately so they are never lost
    if _event_log_fh is not None:
        for entry in archive:
            _event_log_fh.write(entry + '\n')
        _event_log_fh.flush()
    # Bucket archived entries by 100-tick era
    buckets: dict = {}
    for entry in archive:
        m = _TICK_EXTRACT.match(entry)
        if m:
            era_key = (int(m.group(1)) - 1) // 100 * 100 + 1
            buckets.setdefault(era_key, []).append(entry)
    for era_start in sorted(buckets):
        if any(s['start_t'] == era_start for s in era_summaries):
            continue   # already summarised
        entries  = buckets[era_start]
        era_end  = era_start + 99
        era_lbl  = _era_name(entries)
        text     = (
            f'Era {era_start}–{era_end} ― {era_lbl}: '
            f'{sum(1 for e in entries if "WAR DECLARED" in e)} wars, '
            f'{sum(1 for e in entries if "fell in battle" in e or "starved" in e)} deaths, '
            f'{sum(1 for e in entries if "TECH DISCOVERED" in e)} discoveries, '
            f'{sum(1 for e in entries if "TREATY" in e)} treaties.'
        )
        era_summaries.append({'start_t': era_start, 'end_t': era_end,
                               'name': era_lbl, 'text': text})
        event_log.append(f'Tick {era_end:04d}: [ERA SUMMARY] {text}')


def _archive_dead_factions() -> None:
    """Record defunct factions (no members) in _dead_factions for mythology reference."""
    for f in factions:
        if not f.members and f not in _dead_factions:
            _dead_factions.append(f)


def export_era_data(t: int) -> None:
    """Write the last 100 event_log entries and current era_summaries to era_export.txt."""
    start_t = max(1, t - 49)
    header  = f"Age of Ticks {start_t}\u2013{t}"

    # Find inhabitants who perished during this 50-tick window
    perished: list = []
    for entry in event_log:
        m = re.match(r'Tick (\d+).*?\U0001f480\s+(.+?)\s+\(', entry)
        if m and start_t <= int(m.group(1)) <= t:
            perished.append(m.group(2))

    lines: list = [
        f"=== {header} ===",
        "",
        "\u2500\u2500 Recent Events (last 100 log entries) \u2500\u2500",
    ]
    lines.extend(event_log[-100:])

    if perished:
        lines += ["", "\u2500\u2500 Perished This Era \u2500\u2500"]
        lines.extend(f"  {name}" for name in perished)

    if era_summaries:
        lines += ["", "\u2500\u2500 Era Summaries \u2500\u2500"]
        for era in era_summaries:
            lines.append(
                f"  Ticks {era.get('start_t', '?')}\u2013{era.get('end_t', '?')}: "
                f"{era.get('name', '?')}"
            )
            if era.get('text'):
                lines.append(f"    {era['text']}")

    pathlib.Path("era_export.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_to_mythology_file(t: int) -> None:
    """Append this era's events and recently deceased to manual_chronicle.txt."""
    start_t = max(1, t - 49)
    header  = f"Age of Ticks {start_t}\u2013{t}"

    # Extract event_log entries belonging to this era
    era_entries: list = []
    perished:    list = []
    tick_re = re.compile(r'^Tick (\d+)')
    dead_re = re.compile(r'\U0001f480\s+(.+?)\s+\(')
    for entry in event_log:
        m_tick = tick_re.match(entry)
        if m_tick and start_t <= int(m_tick.group(1)) <= t:
            era_entries.append(entry)
            m_dead = dead_re.search(entry)
            if m_dead:
                perished.append(m_dead.group(1))

    lines: list = [
        f"\n{'=' * 60}",
        f"  {header}",
        f"{'=' * 60}",
    ]

    if perished:
        lines.append("Perished:")
        lines.extend(f"  \u2022 {name}" for name in perished)

    lines.append("Events:")
    lines.extend(f"  {e}" for e in era_entries) if era_entries else lines.append("  (none recorded)")

    with open("manual_chronicle.txt", "a", encoding="utf-8") as _f:
        _f.write("\n".join(lines) + "\n")
        _f.flush()


def _make_traveler_name(used: set) -> str | None:
    """Return an unused inhabitant name, appending a generation suffix if all base names taken."""
    for n in NAMES:
        if n not in used:
            return n
    for gen in range(2, 10):
        for n in NAMES:
            candidate = f'{n}{gen}'
            if candidate not in used:
                return candidate
    return None


def world_event_layer(t: int) -> None:
    """Major world event every 200 ticks — shakes up stagnant civilizations."""
    hab = [(r, c) for r in range(len(world)) for c in range(len(world))
           if world[r][c]['habitable']]
    # Weighted random pick
    choice = random.choices(
        ['PLAGUE', 'GOLDEN AGE', 'MIGRATION', 'EARTHQUAKE', 'DISCOVERY'],
        weights=[25, 20, 25, 15, 15],
    )[0]

    if choice == 'PLAGUE':
        for p in people:
            p.health = max(1, p.health - 20)
        msg = (f'Tick {t:04d}: 🐀 WORLD EVENT — THE GREAT PLAGUE '
               f'({len(people)} inhabitants lose 20 health)')

    elif choice == 'GOLDEN AGE':
        for row in world:
            for chunk in row:
                maxes = BIOME_MAX[chunk['biome']]
                for k in chunk['resources']:
                    chunk['resources'][k] = maxes[k]
                chunk['habitable'] = (chunk['resources']['water'] > 0
                                      and chunk['resources']['food']  > 0)
        msg = f'Tick {t:04d}: ✨ WORLD EVENT — GOLDEN AGE (all resources restored to maximum)'

    elif choice == 'MIGRATION':
        used_names = {p.name for p in people}
        spawned: list = []
        for _ in range(8):
            nm = _make_traveler_name(used_names)
            if not nm or not hab:
                break
            used_names.add(nm)
            r, c = random.choice(hab)
            inh = Inhabitant(nm, r, c)
            inh.faction = None
            inh.inventory['food'] = 5
            belief = _BIOME_BELIEF.get(world[r][c]['biome'])
            if belief:
                add_belief(inh, belief)
            _spawn(inh)
            spawned.append(nm)
        msg = (f'Tick {t:04d}: 🌊 WORLD EVENT — MIGRATION WAVE '
               f'({len(spawned)} newcomers: {chr(44).join(spawned)})')

    elif choice == 'EARTHQUAKE':
        targets = random.sample(hab, min(2, len(hab)))
        for r, c in targets:
            world[r][c]['resources']['water'] = 0
            world[r][c]['resources']['food']  = 0
            world[r][c]['habitable']          = False
        msg = (f'Tick {t:04d}: 🌋 WORLD EVENT — EARTHQUAKE '
               f'({len(targets)} chunk(s) rendered uninhabitable)')

    else:   # DISCOVERY
        active = [f for f in factions if f.members]
        msg    = f'Tick {t:04d}: 🎁 WORLD EVENT — FREE DISCOVERY (no eligible faction)'
        if active:
            target    = random.choice(active)
            f_techs   = getattr(target, 'techs', set())
            available = [
                tech for tech, data in technology.TECH_TREE.items()
                if tech not in f_techs
                and all(r in f_techs for r in data.get('requires', []))
            ]
            if not available:   # give any missing tech
                available = [tech for tech in technology.TECH_TREE if tech not in f_techs]
            if available:
                gift = random.choice(available)
                if not hasattr(target, 'techs'):
                    target.techs = set()
                target.techs.add(gift)
                msg = (f'Tick {t:04d}: 🎁 WORLD EVENT — FREE DISCOVERY '
                       f'({target.name} receives {gift.upper()})')

    event_log.append(msg)
    print(msg)


def era_shift_layer(t: int) -> None:
    """Era shift every 500 ticks: halve tensions, inject food, announce new era."""
    for key in list(combat.RIVALRIES.keys()):
        combat.RIVALRIES[key] = combat.RIVALRIES[key] // 2
    for row in world:
        for chunk in row:
            cap = BIOME_MAX[chunk['biome']]['food']
            chunk['resources']['food'] = min(cap, chunk['resources']['food'] + cap // 3)
            chunk['habitable'] = (chunk['resources']['water'] > 0
                                  and chunk['resources']['food']  > 0)
    msg = f'Tick {t:04d}: ══ A NEW ERA DAWNS ══  (tensions halved, lands refreshed)'
    event_log.append(msg)
    print(msg)


def disruption_event_layer(t: int) -> None:
    """Forced disruption event when civilisation stagnates for > 40 ticks."""
    hab    = [(r, c) for r in range(len(world)) for c in range(len(world)) if world[r][c]['habitable']]
    active = [f for f in factions if f.members]

    # Weight CIVIL WAR out if no large enough faction
    cw_w   = 20 if any(len(f.members) >= 3 for f in active) else 0
    choice = random.choices(
        ['GREAT MIGRATION', 'PLAGUE', 'CIVIL WAR', 'PROMISED LAND', 'PROPHET'],
        weights=[25, 20, cw_w, 15, 20],
    )[0]

    # ── a) GREAT MIGRATION ─────────────────────────────────────────────────
    if choice == 'GREAT MIGRATION':
        used_names   = {p.name for p in people}
        beliefs_pool = ['community_sustains'] * 5 + ['self_reliance'] * 5
        random.shuffle(beliefs_pool)
        newcomers: list = []
        for bel in beliefs_pool:
            nm = _make_traveler_name(used_names)
            if not nm or not hab:
                break
            used_names.add(nm)
            r, c = random.choice(hab)
            inh  = Inhabitant(nm, r, c)
            inh.faction           = None
            inh.inventory['food'] = 30
            add_belief(inh, bel)
            _spawn(inh)
            newcomers.append(inh)
        # Form 2 competing factions immediately from the wave
        existing_names = {f.name for f in factions}
        for bel_key in ['community_sustains', 'self_reliance']:
            grp = [n for n in newcomers if any(bel_key in b for b in n.beliefs)]
            if len(grp) >= 2:
                new_name  = _gen_faction_name({bel_key}, existing_names)
                territory = list({(m.r, m.c) for m in grp})
                new_f     = Faction(new_name, list(grp), [bel_key], territory, t)
                factions.append(new_f)
                for m in grp:
                    m.faction = new_name
                existing_names.add(new_name)
        # Seed hostility: new factions vs each other + vs biggest existing faction
        mig_new_factions = factions[-2:] if len(factions) >= 2 else []
        pre_existing = [f for f in factions if f not in mig_new_factions and f.members]
        biggest = max(pre_existing, key=lambda f: len(f.members), default=None)
        for _nf in mig_new_factions:
            for _of in mig_new_factions:
                if _nf is not _of:
                    _k = tuple(sorted([_nf.name, _of.name]))
                    combat.RIVALRIES[_k] = min(combat.RIVALRIES.get(_k, 0) + 120, 195)
            if biggest:
                _k = tuple(sorted([_nf.name, biggest.name]))
                combat.RIVALRIES[_k] = min(combat.RIVALRIES.get(_k, 0) + 80, 195)
        msg = (f'Tick {t:04d}: ══ GREAT MIGRATION — New peoples flood the land ══'
               f' ({len(newcomers)} newcomers, 2 competing factions)')

    # ── b) PLAGUE ──────────────────────────────────────────────────────────
    elif choice == 'PLAGUE':
        for p in people:
            p.health = max(0, p.health - 30)
        for row in world:
            for chunk in row:
                chunk['resources']['food'] = chunk['resources']['food'] // 2
        f_by_name = {f.name: f for f in factions}
        for p in list(people):
            sf = f_by_name.get(p.faction) if p.faction else None
            if sf and len(sf.members) == 1 and p.health < 30:
                grid_remove(p)
                people.remove(p)
                all_dead.append(p)
                sf.members = []
                dmsg = f'Tick {t:04d}: 💀 {p.name} ({p.faction}) succumbed to the Plague alone'
                event_log.append(dmsg)
                print(dmsg)
        msg = (f'Tick {t:04d}: ══ PLAGUE SWEEPS THE LAND ══'
               f' ({len(people)} survivors, food halved)')

    # ── c) CIVIL WAR ───────────────────────────────────────────────────────
    elif choice == 'CIVIL WAR':
        big = [f for f in active if len(f.members) >= 3]
        if big:
            target = max(big, key=lambda f: len(f.members))
            half   = max(1, len(target.members) // 2)
            split  = random.sample(target.members, half)
            existing_names = {f.name for f in factions}
            seed_bels = {b for m in split for b in m.beliefs[:2]}
            new_name  = _gen_faction_name(seed_bels, existing_names)
            territory = list({(m.r, m.c) for m in split})
            new_f     = Faction(new_name, list(split), list(seed_bels)[:2], territory, t)
            factions.append(new_f)
            gone = {m.name for m in split}
            target.members = [m for m in target.members if m.name not in gone]
            for m in split:
                m.faction = new_name
            key = tuple(sorted([target.name, new_name]))
            combat.RIVALRIES[key] = min(combat.RIVALRIES.get(key, 0) + 150, 195)
            msg = (f'Tick {t:04d}: ══ CIVIL WAR — {target.name} tears itself apart ══'
                   f' ({new_name} breaks away in open rebellion)')
        else:
            # Fallback: send extra travellers instead
            msg = f'Tick {t:04d}: ══ CIVIL WAR — unrest spreads, wanderers flood the land ══'
            used_names = {p.name for p in people}
            for _ in range(5):
                nm = _make_traveler_name(used_names)
                if not nm or not hab:
                    break
                used_names.add(nm)
                r, c = random.choice(hab)
                inh  = Inhabitant(nm, r, c)
                inh.faction = None
                inh.inventory['food'] = 30
                _spawn(inh)

    # ── d) PROMISED LAND ───────────────────────────────────────────────────
    elif choice == 'PROMISED LAND':
        all_cells = [(r, c) for r in range(len(world)) for c in range(len(world))]
        barren    = [(r, c) for r, c in all_cells if not world[r][c]['habitable']]
        pick_pool = barren if barren else all_cells
        r, c      = random.choice(pick_pool)
        chunk     = world[r][c]
        for k in chunk['resources']:
            chunk['resources'][k] = BIOME_MAX[chunk['biome']][k]
        chunk['habitable'] = True
        active_pairs = [(fa, fb) for i, fa in enumerate(active)
                        for fb in active[i + 1:]]
        for fa, fb in active_pairs:
            key = tuple(sorted([fa.name, fb.name]))
            combat.RIVALRIES[key] = combat.RIVALRIES.get(key, 0) + 20
        msg = (f'Tick {t:04d}: ══ A PROMISED LAND discovered at ({r},{c}) ══'
               f' ({len(active_pairs)} faction pairs gain +20 tension)')

    # ── e) PROPHET ─────────────────────────────────────────────────────────
    else:
        if not hab:
            msg = f'Tick {t:04d}: ══ A PROPHET cries in the wilderness ══'
        else:
            # Pick the rarest current belief (or a contrarian one)
            belief_counts: dict = {}
            for p in people:
                for b in p.beliefs:
                    belief_counts[b] = belief_counts.get(b, 0) + 1
            all_beliefs = list(belief_counts)
            prophet_bel = (min(all_beliefs, key=lambda b: belief_counts[b])
                           if all_beliefs else 'desert_forges_the_worthy')
            used_names = {p.name for p in people}
            nm = _make_traveler_name(used_names)
            if nm:
                r, c = random.choice(hab)
                inh  = Inhabitant(nm, r, c)
                inh.faction           = None
                inh.inventory['food'] = 30
                inh.health            = 100
                for bel in ['self_reliance', 'community_sustains', prophet_bel]:
                    add_belief(inh, bel)
                _spawn(inh)
                msg = (f'Tick {t:04d}: ══ A PROPHET arrives, preaching new truths ══'
                       f' ({nm} spreads: {prophet_bel})')
            else:
                msg = f'Tick {t:04d}: ══ A PROPHET arrives but bears no name ══'

    event_log.append(msg)
    print(msg)


# ══════════════════════════════════════════════════════════════════════════

def _force_spawn_factions(t: int) -> None:
    """Directly spawn 2 new factions (3 members each) when civilisation is near-extinct."""
    hab = [(r, c) for r in range(len(world)) for c in range(len(world)) if world[r][c]['habitable']]
    if not hab:
        return
    used_names     = {p.name for p in people}
    existing_names = {f.name for f in factions}
    # Two ideologically opposed groups
    belief_sets = [
        ['community_sustains', 'the_forest_shelters'],
        ['self_reliance',      'desert_forges_the_worthy'],
    ]
    new_fac_names: list = []
    for bels in belief_sets:
        members: list = []
        for _ in range(3):
            nm = _make_traveler_name(used_names)
            if not nm:
                break
            used_names.add(nm)
            r, c = random.choice(hab)
            inh  = Inhabitant(nm, r, c)
            inh.faction           = None
            inh.inventory['food'] = 30
            inh.health            = 100
            for bel in bels:
                add_belief(inh, bel)
            _spawn(inh)
            members.append(inh)
        if len(members) >= 2:
            new_name = _gen_faction_name(set(bels), existing_names)
            territory = list({(m.r, m.c) for m in members})
            new_f = Faction(new_name, list(members), list(bels), territory, t)
            factions.append(new_f)
            for m in members:
                m.faction = new_name
            existing_names.add(new_name)
            new_fac_names.append(new_name)
    # Seed immediate rivalry so they go to war within ~10 ticks
    if len(new_fac_names) == 2:
        _k = tuple(sorted(new_fac_names))
        combat.RIVALRIES[_k] = min(combat.RIVALRIES.get(_k, 0) + 140, 195)
    msg = (f'Tick {t:04d}: ══ FROM THE WILDERNESS, new peoples emerge ══'
           f' ({chr(44).join(new_fac_names)})')
    event_log.append(msg)
    print(msg)


def init_world() -> list:
    """Seed chunk food and return the list of habitable (r, c) positions."""
    habitable = [(r, c) for r in range(len(world)) for c in range(len(world))
                 if world[r][c]['habitable']]
    assert habitable, "No habitable chunks — rerun to reseed."
    for row in world:
        for chunk in row:
            cap = BIOME_MAX[chunk['biome']]['food']
            biome = chunk['biome']
            chunk['resources']['food'] = random.randint(cap // 2, cap)
    for r, c in random.sample(habitable, max(1, len(habitable) // 8)):
        world[r][c]['resources']['food'] = 0
    return habitable


def init_inhabitants(habitable: list) -> None:
    """Spawn 30 inhabitants with ideological seeding: 30% individualist, 30% collectivist."""
    names = random.sample(NAMES, 30)
    n     = len(names)
    # Build ideology list: 30% self_reliance, 30% community_sustains, 40% none
    n_each    = n * 3 // 10
    ideologies = (['self_reliance']      * n_each +
                  ['community_sustains'] * n_each +
                  [None]                 * (n - 2 * n_each))
    random.shuffle(ideologies)
    for name, ideology in zip(names, ideologies):
        inh = Inhabitant(name, *random.choice(habitable))
        inh.faction = None
        if ideology:
            add_belief(inh, ideology)
        _spawn(inh)


# ══════════════════════════════════════════════════════════════════════════
# Main loop
# ══════════════════════════════════════════════════════════════════════════

_BIOME_BELIEF: dict[str, str] = {
    'forest':    'the_forest_shelters',
    'coast':     'the_sea_provides',
    'sea':       'the_sea_provides',   # sailors on open water reinforce sea-belief
    'desert':    'desert_forges_the_worthy',
    'plains':    'the_wilds_provide',
    'mountains': 'stone_stands_eternal',
}


def run() -> None:
    global _event_log_fh
    # ── Set up file logging ────────────────────────────────────────────────
    pathlib.Path('logs').mkdir(exist_ok=True)
    _ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
    _log_path = f'logs/run_{_ts}.txt'
    _log_fh   = open(_log_path, 'w', encoding='utf-8')
    _event_log_fh = _log_fh   # expose to _prune_event_log for direct flush
    _real     = sys.stdout
    _tee      = _LogTee(_log_fh, _real)
    sys.stdout    = _tee
    mythology.init(_tee)
    display.LOG_MODE = True

    _real.write(f"Log → {_log_path}\n")
    _real.write(f"Running {TICKS}-tick simulation  "
                f"(wars / schisms / deaths show below)\n\n")

    habitable = init_world()
    init_inhabitants(habitable)

    # ── Per-run tracking ────────────────────────────────────────────────────
    _tick_times:        list = []
    _print_every:       int  = 10 if TICKS > 500 else 1
    _last_dynamic_t:    int  = 0   # last tick a war / schism / faction-formation occurred
    _low_faction_since: int  = 0   # tick when active factions first dropped below 3 (0=OK)
    _peace_applied:     set  = set()  # peace milestone thresholds fired since last war

    try:
        for t in range(1, TICKS + 1):
            _t0               = time.time()
            _log_len_before   = len(event_log)
            winter            = is_winter(t)
            prev_winter       = is_winter(t - 1) if t > 1 else False
            winter_just_ended = prev_winter and not winter

            deaths, prev_positions = inhabitants_layer(t)
            dead_names             = beliefs_layer(t, deaths, winter_just_ended,
                                                   prev_positions)
            factions_layer(t, dead_names)
            procreation_layer(t)
            economy_layer(t)
            combat_layer(t)
            technology_layer(t)
            diplomacy_layer(t)
            if config.MYTHOLOGY_ENABLED:
                mythology_layer(t)
            elif t % 50 == 0:
                export_to_mythology_file(t)

            # ── Map expansion (every 25 ticks) ──────────────────────────────────────
            if t % 25 == 0:
                _expansion = update_map_bounds(len(people))
                if _expansion:
                    _old, _new = _expansion
                    _emsg = (f'Tick {t:04d}: \U0001f5fa MAP EXPANDED — '
                             f'grid {_old}×{_old} → {_new}×{_new} '
                             f'(pop {len(people)})')
                    event_log.append(_emsg)
                    print(_emsg)
                # Dashboard snapshot: piggyback on the same 25-tick cadence
                if t % dashboard_bridge.DASHBOARD_WRITE_EVERY == 0:
                    dashboard_bridge.write_dashboard_snapshot(
                        t, people, factions, _tick_times, event_log)

            world_layer(t, winter_just_ended)

            # ── Track dynamic activity for stagnation detection ─────────────
            _new_entries = event_log[_log_len_before:]
            if any(kw in e for e in _new_entries
                   for kw in ('WAR DECLARED', 'SCHISM', 'FACTION FORMED',
                              'GREAT MIGRATION', 'CIVIL WAR',
                              'WORLD EVENT', 'PLAGUE SWEEPS', 'PROMISED LAND',
                              'PROPHET', 'WILDERNESS', 'INCIDENT')):
                _last_dynamic_t   = t
                _peace_applied    = set()
                _low_faction_since = 0

            # ── Solo-faction fragility: die in ~100 ticks of isolation ────────
            _f_by_name = {f.name: f for f in factions}
            for _sp in list(people):
                _sf = _f_by_name.get(_sp.faction) if _sp.faction else None
                if _sf and len(_sf.members) == 1:
                    if t % 10 == 0:                    # despair: -10 hp every 10 ticks → ~100 tick lifespan
                        _sp.health = max(0, _sp.health - 10)
                    if _sp.health <= 0:
                        grid_remove(_sp)
                        people.remove(_sp)
                        all_dead.append(_sp)
                        _sf.members = []
                        _dmsg = (f'Tick {t:04d}: 💀 {_sp.name} ({_sp.faction}) '
                                 f'wasted away alone')
                        event_log.append(_dmsg)
                        print(_dmsg)

            # ── World event every 200 ticks ─────────────────────────────────
            if t % 200 == 0:
                world_event_layer(t)

            # ── Era shift every 500 ticks ───────────────────────────────────
            if t % 500 == 0:
                era_shift_layer(t)

            # ── Memory housekeeping every 50 ticks ──────────────────────────
            if t % 50 == 0:
                _prune_event_log(t)
                _archive_dead_factions()
                export_era_data(t)
                for p in people:
                    if len(p.memory) > 10:
                        p.memory = p.memory[-10:]

            # ── Population recovery: travelers every 40 ticks ───────────────
            if t % 40 == 0:
                _active_fac_n = sum(1 for f in factions if f.members)
                _spawn_n      = 0
                if len(people) < 20:
                    _spawn_n = max(_spawn_n, 5)
                if _active_fac_n < 3:
                    _spawn_n = max(_spawn_n, 10)  # bigger wave when critically low
                if _spawn_n:
                    hab_now    = [(r, c) for r in range(GRID) for c in range(GRID)
                                  if world[r][c]['habitable']]
                    used_names = {p.name for p in people}
                    spawned: list = []
                    for _ in range(_spawn_n):
                        nm = _make_traveler_name(used_names)
                        if not nm or not hab_now:
                            break
                        used_names.add(nm)
                        r, c = random.choice(hab_now)
                        inh  = Inhabitant(nm, r, c)
                        inh.faction           = None
                        inh.inventory['food'] = 30
                        belief = _BIOME_BELIEF.get(world[r][c]['biome'])
                        if belief:
                            add_belief(inh, belief)
                        _spawn(inh)
                        spawned.append(nm)
                    if spawned:
                        msg = (f"Tick {t:04d}: 🧳 Travelers from beyond the known "
                               f"lands arrive ({', '.join(spawned)})")
                        event_log.append(msg)
                        print(msg)

            # ── Faction-collapse prevention (every 25 ticks) ─────────────────
            if t % 25 == 0:
                _active_now = sum(1 for f in factions if f.members)

                # Track consecutive ticks below 3 factions
                if _active_now < 3:
                    if _low_faction_since == 0:
                        _low_faction_since = t
                    elif t - _low_faction_since >= 50:
                        _force_spawn_factions(t)
                        _last_dynamic_t    = t
                        _low_faction_since = 0
                        _peace_applied     = set()
                else:
                    _low_faction_since = 0

                # Aggressive GREAT MIGRATION if < 4 factions after early game
                if t > 100 and _active_now < 4 and t % 30 == 0:
                    disruption_event_layer(t)
                    _last_dynamic_t = t
                    _peace_applied  = set()

                # Stagnation fallback (40+ ticks with no dynamic event)
                _stagnation = t - _last_dynamic_t
                if _stagnation > 40:
                    disruption_event_layer(t)
                    _last_dynamic_t = t
                    _peace_applied  = set()

                # ── Peace escalation milestones ──────────────────────────────
                _peace_ticks = t - _last_dynamic_t
                _act_facs    = [f for f in factions if f.members]

                # 50 ticks of peace → +10 global tension, suspicion banner
                if _peace_ticks >= 50 and 50 not in _peace_applied:
                    _peace_applied.add(50)
                    for _fi in _act_facs:
                        for _fj in _act_facs:
                            if _fi is not _fj:
                                _k = tuple(sorted([_fi.name, _fj.name]))
                                combat.RIVALRIES[_k] = min(
                                    combat.RIVALRIES.get(_k, 0) + 10, 190)
                    _pmsg = f'Tick {t:04d}: Suspicion grows across the land... (all tensions +10)'
                    event_log.append(_pmsg)
                    print(_pmsg)

                # 75 ticks of peace → one pair gets +30, resource-envy banner
                if _peace_ticks >= 75 and 75 not in _peace_applied and len(_act_facs) >= 2:
                    _peace_applied.add(75)
                    _fa, _fb = random.sample(_act_facs, 2)
                    _k = tuple(sorted([_fa.name, _fb.name]))
                    combat.RIVALRIES[_k] = min(combat.RIVALRIES.get(_k, 0) + 30, 195)
                    _res = random.choice(['food', 'wood', 'ore', 'stone'])
                    _pmsg = (f'Tick {t:04d}: The {_fa.name} eyes '
                             f'{_fb.name}\'s rich {_res} lands with hunger')
                    event_log.append(_pmsg)
                    print(_pmsg)

                # 100 ticks of peace → INCIDENT forces war pressure
                if _peace_ticks >= 100 and 100 not in _peace_applied and len(people) >= 2 and len(_act_facs) >= 2:
                    _peace_applied.add(100)
                    _victim     = random.choice(people)
                    _rival_pool = [f for f in _act_facs if f.name != _victim.faction]
                    if not _rival_pool:
                        _rival_pool = _act_facs
                    _rival = random.choice(_rival_pool)
                    _k = tuple(sorted([_victim.faction or _rival.name, _rival.name]))
                    combat.RIVALRIES[_k] = min(combat.RIVALRIES.get(_k, 0) + 50, 198)
                    _im1 = (f'Tick {t:04d}: ══ INCIDENT — {_victim.name} found dead '
                            f'in {_rival.name} lands ══')
                    _im2 = (f'Tick {t:04d}: Was it murder? '
                            f'{_victim.faction or "The wanderers"} demands answers.')
                    event_log.append(_im1)
                    event_log.append(_im2)
                    print(_im1)
                    print(_im2)
                    _last_dynamic_t = t
                    _peace_applied  = set()

            # ── Tick timing ─────────────────────────────────────────────────
            _elapsed = time.time() - _t0
            _tick_times.append(_elapsed)
            if _elapsed > 120:
                _real.write(f'  ⚠ Tick {t} slow: {_elapsed:.1f}s\n')
                _real.flush()

            # ── Progress line (every tick for short runs, every 10 for long) ─
            if t % _print_every == 0:
                alive    = len(people)
                facts    = sum(1 for f in factions if f.members)
                wars     = len(combat.active_wars)
                techno   = sum(len(getattr(f, 'techs', set())) for f in factions)
                treaties = len(diplomacy._treaties)
                _real.write(f'  [{t:{len(str(TICKS))}d}/{TICKS}]  Alive:{alive:2d}  '
                            f'Factions:{facts}  Wars:{wars}  Techs:{techno}  '
                            f'Treaties:{treaties}\n')
                _real.flush()

            # ── Mini-summary every 100 ticks ────────────────────────────────
            if t % 100 == 0:
                window  = _tick_times[-100:]
                avg_t   = sum(window) / len(window) if window else 0
                last_t  = _tick_times[-1] if _tick_times else 0
                peak    = max((v for v in combat.RIVALRIES.values()), default=0)
                era_lbl = _era_name(event_log[-200:] if len(event_log) > 200 else event_log)
                _real.write(
                    f'\n  ══ Tick {t}: Alive:{len(people):2d}  '
                    f'Factions:{sum(1 for f in factions if f.members)}  '
                    f'Wars:{len(combat.active_wars)}  '
                    f'Tension peak:{peak}  '
                    f'{last_t:.1f}s last / {avg_t:.1f}s avg  '
                    f'[{era_lbl}] ══\n\n'
                )
                _real.flush()

            # ── Full tick render → log file only ────────────────────────────
            display.render(t, people, deaths, event_log,
                           winter, all_dead, factions)
            if t % 25 == 0:
                display.faction_summary(factions, t)

            if not people:
                print('All inhabitants have perished.')
                break

    except KeyboardInterrupt:
        print("\n\n[Simulation interrupted by user]\n")

    finally:
        # Final report: passthrough so everything shows on terminal AND in log
        _real.write('\n')
        _tee.passthrough = True
        display.final_report(people, all_dead, factions, event_log, TICKS)
        if config.MYTHOLOGY_ENABLED:
            mythology.mythology_final_summary(factions, all_dead, TICKS, event_log, era_summaries)
            time.sleep(2)   # flush iGPU shared memory after final narrative LLM job
            gc.collect()    # reclaim memory after final LLM job
        else:
            # Write final chronicle entry and ensure the file is flushed and closed
            export_to_mythology_file(TICKS)
            with open("manual_chronicle.txt", "a", encoding="utf-8") as _mcf:
                _mcf.write(f"\n{'=' * 60}\n  END OF SIMULATION — {TICKS} ticks total\n{'=' * 60}\n")
                _mcf.flush()
        sys.stdout = _real
        _log_fh.close()
        print(f"\nFull log saved → {_log_path}")


# ══════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    run()
