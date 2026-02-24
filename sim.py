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

import sys, time, re, random, pathlib
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8')

# ── Layer imports ──────────────────────────────────────────────────────────
from world       import world, tick as _world_tick, GRID, BIOME_MAX
from inhabitants import (Inhabitant, do_tick, is_winter, regen_rate,
                         NAMES, WINTER_START, CYCLE_LEN)
from beliefs     import assign_beliefs, share_beliefs, add_belief
from factions    import check_faction_formation, faction_tick, Faction, _faction_name as _gen_faction_name
import economy
import combat
import technology
import diplomacy
import mythology
import display

TICKS = 1000

# ── Shared simulation state ────────────────────────────────────────────────
people:         list = []
factions:       list = []
all_dead:       list = []
event_log:      list = []
era_summaries:   list = []   # {'start_t', 'end_t', 'name', 'text'} — archived 100-tick windows
_dead_factions:  list = []   # defunct factions kept for reference
_last_dynamic_t: int  = 0   # last tick with war / schism / faction-formation


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
# Layer functions — one per concern, called in order each tick
# ══════════════════════════════════════════════════════════════════════════

def world_layer(t: int, winter_just_ended: bool) -> None:
    """Layer 0: announce season changes, then regenerate resources."""
    winter = is_winter(t)
    if t > 1 and (t - 1) % CYCLE_LEN == WINTER_START and winter:
        event_log.append(f"Tick {t:03d}: ❄ WINTER ARRIVES — regen pauses")
    if winter_just_ended:
        event_log.append(f"Tick {t:03d}: ☀ Spring — food regeneration resumes")
    _world_tick(regen_rate(t))


def inhabitants_layer(t: int) -> tuple[list, dict]:
    """Layer 1: move, eat, gather, trust. Returns (deaths, prev_positions)."""
    prev_positions = {p.name: (p.r, p.c) for p in people}
    deaths = do_tick(people, t, event_log)
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
    """Keep only the last 200 events in memory; archive older ticks into era_summaries."""
    if len(event_log) <= 200:
        return
    archive      = event_log[:-200]
    event_log[:] = event_log[-200:]
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
    hab = [(r, c) for r in range(GRID) for c in range(GRID)
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
            people.append(inh)
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
    hab    = [(r, c) for r in range(GRID) for c in range(GRID) if world[r][c]['habitable']]
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
            people.append(inh)
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
                people.append(inh)

    # ── d) PROMISED LAND ───────────────────────────────────────────────────
    elif choice == 'PROMISED LAND':
        all_cells = [(r, c) for r in range(GRID) for c in range(GRID)]
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
                people.append(inh)
                msg = (f'Tick {t:04d}: ══ A PROPHET arrives, preaching new truths ══'
                       f' ({nm} spreads: {prophet_bel})')
            else:
                msg = f'Tick {t:04d}: ══ A PROPHET arrives but bears no name ══'

    event_log.append(msg)
    print(msg)


# ══════════════════════════════════════════════════════════════════════════

def init_world() -> list:
    """Seed chunk food and return the list of habitable (r, c) positions."""
    habitable = [(r, c) for r in range(GRID) for c in range(GRID)
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
        people.append(inh)


# ══════════════════════════════════════════════════════════════════════════
# Main loop
# ══════════════════════════════════════════════════════════════════════════

_BIOME_BELIEF: dict[str, str] = {
    'forest':    'the_forest_shelters',
    'coast':     'the_sea_provides',
    'desert':    'desert_forges_the_worthy',
    'plains':    'the_wilds_provide',
    'mountains': 'stone_stands_eternal',
}


def run() -> None:
    # ── Set up file logging ────────────────────────────────────────────────
    pathlib.Path('logs').mkdir(exist_ok=True)
    _ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
    _log_path = f'logs/run_{_ts}.txt'
    _log_fh   = open(_log_path, 'w', encoding='utf-8')
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
    _tick_times:     list = []
    _print_every:    int  = 10 if TICKS > 500 else 1
    _last_dynamic_t: int  = 0     # last tick a war / schism / faction-formation occurred

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
            economy_layer(t)
            combat_layer(t)
            technology_layer(t)
            diplomacy_layer(t)
            mythology_layer(t)
            world_layer(t, winter_just_ended)

            # ── Track dynamic activity for stagnation detection ─────────────
            _new_entries = event_log[_log_len_before:]
            if any(kw in e for e in _new_entries
                   for kw in ('WAR DECLARED', 'SCHISM', 'FACTION FORMED',
                              'GREAT MIGRATION', 'CIVIL WAR',
                              'WORLD EVENT', 'PLAGUE SWEEPS', 'PROMISED LAND',
                              'PROPHET')):
                _last_dynamic_t = t

            # ── Solo-faction fragility: despair + plague vulnerability ───────
            _f_by_name = {f.name: f for f in factions}
            for _sp in list(people):
                _sf = _f_by_name.get(_sp.faction) if _sp.faction else None
                if _sf and len(_sf.members) == 1:
                    if t % 20 == 0:                    # loneliness despair
                        _sp.health = max(0, _sp.health - 5)
                    if _sp.health <= 0:
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
                    _spawn_n = max(_spawn_n, 8)
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
                        inh.inventory['food'] = 30   # 30-tick survival ration
                        belief = _BIOME_BELIEF.get(world[r][c]['biome'])
                        if belief:
                            add_belief(inh, belief)
                        people.append(inh)
                        spawned.append(nm)
                    if spawned:
                        msg = (f"Tick {t:04d}: 🧳 Travelers from beyond the known "
                               f"lands arrive ({', '.join(spawned)})")
                        event_log.append(msg)
                        print(msg)

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

            # ── Stagnation check every 50 ticks ──────────────────────────
            if t % 50 == 0:
                _stagnation = t - _last_dynamic_t
                if _stagnation > 40:
                    disruption_event_layer(t)
                    _last_dynamic_t = t

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
        mythology.mythology_final_summary(factions, all_dead, TICKS, event_log, era_summaries)
        sys.stdout = _real
        _log_fh.close()
        print(f"\nFull log saved → {_log_path}")


# ══════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    run()
