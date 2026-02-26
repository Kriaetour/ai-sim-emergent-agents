import random, time, os, sys
sys.stdout.reconfigure(encoding='utf-8')
from .world  import world, tick, GRID, BIOME_MAX
from .inhabitants import (Inhabitant, do_tick, is_winter, regen_rate,
                          RES_KEYS, NAMES, WINTER_START)
from .beliefs import assign_beliefs, share_beliefs, LABELS, inh_cores
from .factions import (Faction, check_faction_formation,
                       faction_tick, print_faction_summary)

TICKS = 100
W     = 72   # display width

# ── World initialisation ───────────────────────────────────────────────────
habitable = [(r, c) for r in range(GRID) for c in range(GRID)
             if world[r][c]['habitable']]
assert habitable, "No habitable chunks — rerun."
for row in world:
    for chunk in row:
        cap = BIOME_MAX[chunk['biome']]['food']
        chunk['resources']['food'] = random.randint(cap // 2, cap)
for r, c in random.sample(habitable, max(1, len(habitable) // 5)):
    world[r][c]['resources']['food'] = 0

# ── Spawn ──────────────────────────────────────────────────────────────────
people    = [Inhabitant(n, *random.choice(habitable)) for n in random.sample(NAMES, 30)]
for p in people:
    p.faction = None   # attribute not defined in Inhabitant; added here
factions  = []
all_dead  = []
event_log = []

# ── Display ────────────────────────────────────────────────────────────────
_FACT_ABBREV = {}   # faction name → 3-char tag, built lazily

def _faction_tag(inh):
    if not inh.faction:
        return '    '
    if inh.faction not in _FACT_ABBREV:
        idx = len(_FACT_ABBREV) % 26
        _FACT_ABBREV[inh.faction] = f'[{chr(65+idx)}{chr(65+idx)}]'
    return _FACT_ABBREV[inh.faction]

def _belief_short(inh):
    keys = {'endurance_rewarded': 'En', 'lucky_survivor': 'Lu',
            'loss_teaches_caution': 'Lo', 'community_sustains': 'Co',
            'self_reliance': 'Sr', 'death_is_near': 'De',
            'migration_brings_hope': 'Mi', 'the_land_can_fail': 'Lf'}
    abbrevs = [keys.get(c, '??') for c in sorted(inh_cores(inh))]
    return ' '.join(abbrevs[:4]) if abbrevs else '——'

def print_civ_status(t, people, deaths, event_log, winter, total_dead):
    os.system('cls' if os.name == 'nt' else 'clear')
    bar    = '─' * W
    season = "❄ WINTER" if winter else "☀ Summer"
    alive  = len(people)
    fact_n = len([f for f in factions if f.members])
    hdr = f"  Tick {t:03d}/{TICKS}  {season}  Alive:{alive:2d}/30  Deaths:{total_dead:2d}  Factions:{fact_n}"
    print(f"┌{bar}┐")
    print(f"│{hdr:<{W}}│")
    print(f"├{'─'*W}┤")
    # header
    print(f"│  {'Name':<8}  Pos    HP-bar        "
          f"Hu   Fd  Fc   Beliefs             │")
    print(f"├{'─'*W}┤")
    for inh in sorted(people, key=lambda p: (p.health, p.name)):
        hp_val  = max(0, inh.health)
        filled  = min(10, hp_val // 10)
        hp_bar  = '█' * filled + '░' * (10 - filled)
        ftag    = _faction_tag(inh)
        bshort  = _belief_short(inh)
        print(f"│  {inh.name:<8} ({inh.r},{inh.c})  [{hp_bar}]"
              f"  {inh.hunger:3d}  {inh.inventory['food']:2d}  {ftag}  {bshort:<20}│")
    # deaths this tick
    if deaths:
        print(f"├{'─'*W}┤")
        for d in deaths:
            print(f"│  ✗ {d.name} starved — {d.biome_label():<{W-22}}│")
    # event log (last 6)
    shown = event_log[-6:]
    if shown:
        print(f"├{'─'*W}┤")
        for msg in shown:
            clip = msg[:W-4]
            print(f"│  {clip:<{W-2}}│")
    print(f"└{'─'*W}┘")
    # faction key
    if _FACT_ABBREV:
        tags = '  '.join(f"{v}={k}" for k, v in _FACT_ABBREV.items())
        print(f"  Factions: {tags}")

# ── Run ────────────────────────────────────────────────────────────────────
for t in range(1, TICKS + 1):
    winter      = is_winter(t)
    prev_winter = is_winter(t - 1) if t > 1 else False
    winter_just_ended = prev_winter and not winter

    # Season announcement
    if t > 1 and (t - 1) % WINTER_START == 0 and winter:
        event_log.append(f"Tick {t:03d}: ❄ WINTER ARRIVES — regen pauses")
    if winter_just_ended:
        event_log.append(f"Tick {t:03d}: ☀ Spring — food regeneration resumes")

    # Snapshot positions BEFORE movement for migration detection
    prev_positions = {p.name: (p.r, p.c) for p in people}

    # Layer 1: inhabitant simulation
    deaths = do_tick(people, t, event_log)
    all_dead.extend(deaths)
    dead_names = {d.name for d in deaths}

    # Layer 2: beliefs
    assign_beliefs(people, deaths, winter_just_ended, prev_positions, t, event_log)
    share_beliefs(people, t, event_log)

    # Layer 3: factions (formation every 10 ticks)
    if t % 10 == 0:
        check_faction_formation(people, factions, t, event_log)
    faction_tick(people, factions, t, event_log)

    # Clean dead members from factions
    for f in factions:
        f.remove_dead(dead_names)

    # World regeneration
    tick(regen_rate(t))

    # Display
    print_civ_status(t, people, deaths, event_log, winter, len(all_dead))
    if t % 25 == 0:
        print_faction_summary(factions, t)
        time.sleep(1.5)   # pause on summary ticks

    time.sleep(0.25)
    if not people:
        print("All inhabitants have perished.")
        break

# ── Final report ───────────────────────────────────────────────────────────
print(f"\n{'═'*W}")
print(f"CIVILIZATION SUMMARY — {TICKS} ticks")
print(f"{'═'*W}")
print(f"Survivors : {len(people)}/30  |  Deaths: {len(all_dead)}")
if people:
    best = max(people, key=lambda p: p.total_trust)
    rich = max(people, key=lambda p: len(p.beliefs))
    print(f"Most trusted  : {best.name}  (trust score {best.total_trust})")
    print(f"Most beliefs  : {rich.name}  ({len(rich.beliefs)}: "
          f"{', '.join(LABELS.get(b.split(':')[-1], b) for b in rich.beliefs)})")
print(f"\nFactions formed: {len(factions)}  |  Active at end: {len([f for f in factions if f.members])}")
print_faction_summary(factions, TICKS)
print("\nFull event log:")
for e in event_log:
    print(f"  {e}")
