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

import sys, time, random, pathlib
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8')

# ── Layer imports ──────────────────────────────────────────────────────────
from world       import world, tick as _world_tick, GRID, BIOME_MAX
from inhabitants import (Inhabitant, do_tick, is_winter, regen_rate,
                         NAMES, WINTER_START, CYCLE_LEN)
from beliefs     import assign_beliefs, share_beliefs, add_belief
from factions    import check_faction_formation, faction_tick
import economy
import combat
import technology
import diplomacy
import mythology
import display

TICKS = 300

# ── Shared simulation state ────────────────────────────────────────────────
people:    list = []
factions:  list = []
all_dead:  list = []
event_log: list = []


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
        'fray', 'vengeance', 'conquest',   # attacker-side ally recruitment
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
# Initialisation
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

    try:
        for t in range(1, TICKS + 1):
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

            # ── Population recovery: travelers arrive every 40 ticks if < 15 pop ──
            if t % 40 == 0 and len(people) < 15:
                hab_now   = [(r, c) for r in range(GRID) for c in range(GRID)
                             if world[r][c]['habitable']]
                used_names = {p.name for p in people}
                available  = [n for n in NAMES if n not in used_names]
                count = min(5, len(available), len(hab_now) if hab_now else 0)
                if count > 0:
                    newcomers = random.sample(available, count)
                    for name in newcomers:
                        r, c = random.choice(hab_now)
                        inh = Inhabitant(name, r, c)
                        inh.faction = None
                        inh.inventory['food'] = 5
                        biome  = world[r][c]['biome']
                        belief = _BIOME_BELIEF.get(biome)
                        if belief:
                            add_belief(inh, belief)
                        people.append(inh)
                    msg = (f"Tick {t:03d}: 🧳 Travelers from beyond the known "
                           f"lands arrive ({', '.join(newcomers)})")
                    event_log.append(msg)
                    print(msg)

            # ── Terminal progress line (written directly — not filtered) ───
            alive    = len(people)
            facts    = sum(1 for f in factions if f.members)
            wars     = len(combat.active_wars)
            techno   = sum(len(getattr(f, 'techs', set())) for f in factions)
            treaties = len(diplomacy._treaties)
            _real.write(f"  [{t:3d}/{TICKS}]  Alive:{alive:2d}  "
                        f"Factions:{facts}  Wars:{wars}  Techs:{techno}  "
                        f"Treaties:{treaties}\n")
            _real.flush()

            # ── Full tick render → log file only ──────────────────────────
            display.render(t, people, deaths, event_log,
                           winter, all_dead, factions)
            if t % 25 == 0:
                display.faction_summary(factions, t)

            if not people:
                print("All inhabitants have perished.")
                break

    except KeyboardInterrupt:
        print("\n\n[Simulation interrupted by user]\n")

    finally:
        # Final report: passthrough so everything shows on terminal AND in log
        _real.write('\n')
        _tee.passthrough = True
        display.final_report(people, all_dead, factions, event_log, TICKS)
        mythology.mythology_final_summary(factions, all_dead, TICKS, event_log)
        sys.stdout = _real
        _log_fh.close()
        print(f"\nFull log saved → {_log_path}")


# ══════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    run()
