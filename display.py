"""
display.py — rendering layer for the civilization sim.
Called each tick by sim.py.
"""
import os
from collections import Counter
from beliefs  import inh_cores, LABELS, core_of
from factions import print_faction_summary, RIVALRIES
import economy
import combat
import diplomacy

from config import TICKS
W     = 72
LOG_MODE = False  # set True by sim.py to suppress cls and route output to file

# Faction → short tag, built lazily
_FACT_ABBREV: dict[str, str] = {}

_BELIEF_KEYS = {
    'endurance_rewarded':          'En',
    'lucky_survivor':              'Lu',
    'loss_teaches_caution':        'Lo',
    'community_sustains':          'Co',
    'self_reliance':               'Sr',
    'death_is_near':               'De',
    'migration_brings_hope':       'Mi',
    'the_land_can_fail':           'Lf',
    'trade_builds_bonds':          'Tb',
    'crowded_lands_breed_conflict':'Cl',
    'suffering_forges_strength':   'Sf',
    'the_strong_take':             'St',
    'the_wilds_provide':           'Wp',
    # Location beliefs
    'the_forest_shelters':         'Fs',
    'stone_stands_eternal':        'Ss',
    'the_sea_provides':            'Sp',
    'desert_forges_the_worthy':    'Df',
    # Social beliefs
    'loyalty_above_all':           'La',
    'trust_no_group':              'Tn',
    'the_wise_must_lead':          'Wl',
    # Scarcity beliefs
    'hunger_teaches_truth':        'Ht',
    'fortune_favors_the_prepared': 'Fp',
    # Combat beliefs
    'war_is_costly':               'Wc',
    'victory_proves_strength':     'Vp',
    'sacrifice_has_meaning':       'Sm',
    'battle_forges_bonds':         'Bf',
    'never_again':                 'Na',
}


def _faction_tag(inh) -> str:
    if not inh.faction:
        return '    '
    if inh.faction not in _FACT_ABBREV:
        idx = len(_FACT_ABBREV) % 26
        _FACT_ABBREV[inh.faction] = f'[{chr(65+idx)}{chr(65+idx)}]'
    return _FACT_ABBREV[inh.faction]


def _belief_short(inh) -> str:
    abbrevs = [_BELIEF_KEYS.get(c, '??') for c in sorted(inh_cores(inh))]
    return ' '.join(abbrevs[:4]) if abbrevs else '——'


def render(t: int, people: list, deaths: list, event_log: list,
           winter: bool, all_dead: list, factions: list) -> None:
    """Print the full tick display."""
    if not LOG_MODE:
        os.system('cls' if os.name == 'nt' else 'clear')

    # In log mode, emit deaths as plain text so they pass the terminal filter
    if LOG_MODE and deaths:
        for d in deaths:
            print(f"Tick {t:03d}: ✗ {d.name} starved at {d.biome_label()}")
    bar      = '─' * W
    season   = "❄ WINTER" if winter else "☀ Summer"
    alive    = len(people)
    fact_n   = sum(1 for f in factions if f.members)
    aligned  = sum(1 for p in people if p.faction)
    unaligned = alive - aligned

    hdr = (f"  Tick {t:03d}/{TICKS}  {season}  "
           f"Alive:{alive:2d}/30  Deaths:{len(all_dead):2d}  "
           f"Factions:{fact_n}  Unaligned:{unaligned}")
    print(f"\u250c{bar}\u2510")
    print(f"\u2502{hdr:<{W}}\u2502")
    print(f"\u251c{bar}\u2524")
    print(f"\u2502  {'Name':<8}  Pos    HP-bar          Hu   Fd  Fc   Beliefs         \u2502")
    print(f"\u251c{bar}\u2524")

    for inh in sorted(people, key=lambda p: (p.health, p.name)):
        hp_val = max(0, inh.health)
        hp_bar = '\u2588' * min(10, hp_val // 10) + '\u2591' * max(0, 10 - hp_val // 10)
        ftag   = _faction_tag(inh)
        bshort = _belief_short(inh)
        print(f"\u2502  {inh.name:<8} ({inh.r},{inh.c})  [{hp_bar}]"
              f"  {inh.hunger:3d}  {inh.inventory['food']:2d}  {ftag}  {bshort:<20}\u2502")

    if deaths:
        print(f"\u251c{bar}\u2524")
        for d in deaths:
            print(f"\u2502  \u2717 {d.name} starved \u2014 {d.biome_label():<{W-22}}\u2502")

    shown = event_log[-6:]
    if shown:
        print(f"\u251c{bar}\u2524")
        for msg in shown:
            print(f"\u2502  {msg[:W-4]:<{W-2}}\u2502")

    # ── Rivalry strip ──────────────────────────────────────────────────────
    if RIVALRIES:
        print(f"\u251c{bar}\u2524")
        rivalry_parts = []
        for (na, nb), score in sorted(RIVALRIES.items(), key=lambda x: -x[1]):
            rivalry_parts.append(f"{na} vs {nb}: {score}")
        rivalry_str = '  |  '.join(rivalry_parts)
        print(f"\u2502  \u2694 Tensions: {rivalry_str[:W-14]:<{W-4}}\u2502")

    # ── Belief distribution ───────────────────────────────────────────────
    all_cores: list = []
    for p in people:
        all_cores.extend(inh_cores(p))
    if all_cores:
        counts   = Counter(all_cores).most_common(6)
        dist_str = '  '.join(f"{_BELIEF_KEYS.get(k,'??')}:{v}" for k, v in counts)
        print(f"\u251c{bar}\u2524")
        print(f"\u2502  Beliefs: {dist_str:<{W-10}}\u2502")
    # ── Economy strip ────────────────────────────────────────────────
    eco_line = economy.wealth_summary_line(factions, people)
    if eco_line:
        print(f"\u251c{bar}\u2524")
        print(f"\u2502  {eco_line[:W-4]:<{W-2}}\u2502")
    print(f"\u2514{bar}\u2518")

    if _FACT_ABBREV:
        tags = '  '.join(f"{v}={k}" for k, v in _FACT_ABBREV.items())
        clip = tags[:W]
        print(f"  Factions: {clip}")


def faction_summary(factions: list, t: int) -> None:
    """Delegate to factions module."""
    print_faction_summary(factions, t)


def final_report(people: list, all_dead: list, factions: list,
                 event_log: list, ticks: int) -> None:
    sep = '═' * W
    print(f"\n{sep}")
    print(f"CIVILIZATION SUMMARY — {ticks} ticks")
    print(f"{sep}")
    print(f"Survivors : {len(people)}/30  |  Deaths: {len(all_dead)}")
    if people:
        best = max(people, key=lambda p: p.total_trust)
        rich = max(people, key=lambda p: len(p.beliefs))
        print(f"Most trusted : {best.name}  (trust score {best.total_trust})")
        print(f"Most beliefs : {rich.name}  ({len(rich.beliefs)}:  "
              f"{', '.join(LABELS.get(b.split(':')[-1], b) for b in rich.beliefs)})")
    print(f"\nFactions formed: {len(factions)}  |  "
          f"Active at end: {sum(1 for f in factions if f.members)}")
    print_faction_summary(factions, ticks)
    economy.economy_report(factions, people, ticks)
    print("\nCombat history:")
    combat.combat_report(factions)
    diplomacy.diplomacy_report(factions, ticks)

    # ── Top 10 key events ─────────────────────────────────────────────────
    _KEY = {
        'WAR DECLARED', 'WAR ENDS', 'SCHISM', 'fell in battle',
        'ALLIANCE', 'call to arms', 'FACTION FORMED', 'FACTION MERGE', 'starved',
        'absorbed', 'fled to', 'Travelers',
        'TECH DISCOVERED',
        'TREATY', 'SURRENDER TERMS', 'broke treaty', 'MUTUAL DEFENSE',
        'shares food',
    }
    key_events = [e for e in event_log if any(k in e for k in _KEY)]
    n_show     = min(10, len(key_events))
    print(f"\nTop {n_show} key events:")
    for e in key_events[:10]:
        print(f"  {e}")
    if len(key_events) > 10:
        print(f"  … ({len(key_events) - 10} more key events in log file)")
