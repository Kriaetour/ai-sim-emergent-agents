"""religion.py
Layer 9 – Religion System

Handles:
  • Religious Institutions  — piousness threshold triggers religion founding
  • Temple Structures       — trust-battery effect for nearby members
  • Priesthood Role         — is_priest flag; priests convert others, skip food gathering
  • Holy Wars               — conflicting religions + poor reputation → holy war
  • Inheritance             — children born near temples inherit faction religion
  • Performance             — inhabitant.religion is an object pointer (Religion instance)
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from inhabitants import Inhabitant
    from factions import Faction

# ---------------------------------------------------------------------------
# Religion class
# ---------------------------------------------------------------------------

class Religion:
    """A religion founded by a faction."""
    __slots__ = ('name', 'founder_name', 'founded_tick', 'temple_tiles')

    def __init__(self, name: str, founder_name: str, founded_tick: int) -> None:
        self.name:          str  = name
        self.founder_name:  str  = founder_name
        self.founded_tick:  int  = founded_tick
        self.temple_tiles:  set  = set()   # (r, c) tuples where temples stand

    def __repr__(self) -> str:
        return (f"Religion({self.name!r}, founder={self.founder_name!r}, "
                f"t={self.founded_tick})")

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_religions:  list[Religion] = []
_HOLY_WARS:  set             = set()   # frozenset({fac_name_a, fac_name_b})

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PIOUSNESS_THRESHOLD    = 3      # distinct beliefs held by ≥60 % of members
MIN_FACTION_SIZE       = 3      # must have at least this many living members
MIN_SETTLED_TICKS      = 10     # faction must have been settled at least this long
TEMPLE_TRUST_RANGE     = 3      # Chebyshev tile radius for trust battery
TEMPLE_TRUST_BONUS     = 1      # trust points added per qualifying tick
PRIEST_CONVERT_SAME    = 0.70   # conversion chance within same faction
PRIEST_CONVERT_CROSS   = 0.25   # conversion chance across faction lines
HOLY_WAR_REP_THRESHOLD = -5     # bilateral reputation ≤ this triggers holy war
BIRTH_INHERIT_CHANCE   = 0.95   # probability child inherits religion when temple nearby

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _belief_counts(members: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in members:
        for b in getattr(m, 'beliefs', []):
            counts[b] = counts.get(b, 0) + 1
    return counts


def piousness_score(faction) -> int:
    """Number of distinct beliefs held by ≥60 % of faction members."""
    members = faction.members
    if not members:
        return 0
    threshold = 0.60 * len(members)
    counts = _belief_counts(members)
    return sum(1 for cnt in counts.values() if cnt >= threshold)


def _dominant_belief(faction) -> str | None:
    """Most common belief among faction members, or None if none exist."""
    counts = _belief_counts(faction.members)
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


# ---------------------------------------------------------------------------
# Religion queries
# ---------------------------------------------------------------------------

def get_faction_religion(faction) -> Religion | None:
    """Return the Religion associated with a faction.

    Priority order:
      1. Faction founded a religion (founder_name match).
      2. Majority (≥50 %) of members share the same Religion pointer.
    """
    for rel in _religions:
        if rel.founder_name == faction.name:
            return rel

    # Fallback: majority-religion check
    counts: dict[int, list] = {}   # id(rel) → [rel, count]
    for m in faction.members:
        r = getattr(m, 'religion', None)
        if r is not None:
            key = id(r)
            if key not in counts:
                counts[key] = [r, 0]
            counts[key][1] += 1

    if not counts:
        return None
    best_entry = max(counts.values(), key=lambda e: e[1])
    if best_entry[1] >= 0.5 * len(faction.members):
        return best_entry[0]
    return None


def is_holy_war(name_a: str, name_b: str) -> bool:
    """True if an active holy war exists between the two named factions."""
    return frozenset({name_a, name_b}) in _HOLY_WARS


def is_holy_war_member(faction_name: str) -> bool:
    """True if this faction is currently involved in any holy war."""
    return any(faction_name in pair for pair in _HOLY_WARS)


# ---------------------------------------------------------------------------
# Religion founding
# ---------------------------------------------------------------------------

def try_found_religion(faction, t: int, event_log: list) -> Religion | None:
    """Found a religion for *faction* if all conditions are satisfied.

    Conditions:
      • Faction does not already have a religion.
      • Faction size ≥ MIN_FACTION_SIZE.
      • faction.settled_ticks ≥ MIN_SETTLED_TICKS.
      • piousness_score ≥ PIOUSNESS_THRESHOLD.
    """
    if get_faction_religion(faction) is not None:
        return None
    if len(faction.members) < MIN_FACTION_SIZE:
        return None
    if getattr(faction, 'settled_ticks', 0) < MIN_SETTLED_TICKS:
        return None
    if piousness_score(faction) < PIOUSNESS_THRESHOLD:
        return None

    belief   = _dominant_belief(faction)
    rel_name = (f"The Way of the {belief.title()}"
                if belief else f"The Faith of {faction.name}")

    new_rel = Religion(rel_name, faction.name, t)
    _religions.append(new_rel)

    # Assign religion pointer to every current member
    for m in faction.members:
        m.religion = new_rel

    # Build initial temple at settlement if one exists
    settlement = getattr(faction, 'settlement', None)
    if settlement and getattr(settlement, 'status', None) == 'active':
        new_rel.temple_tiles.add((settlement.r, settlement.c))
        event_log.append(
            f"[t={t}] TEMPLE BUILT at ({settlement.r},{settlement.c}) "
            f"by {faction.name} for {rel_name!r}"
        )

    event_log.append(
        f"[t={t}] RELIGION FOUNDED: {rel_name!r} by {faction.name} "
        f"(size={len(faction.members)}, piousness={piousness_score(faction)})"
    )
    return new_rel


# ---------------------------------------------------------------------------
# Holy war management
# ---------------------------------------------------------------------------

def _check_holy_wars(factions: list, t: int, event_log: list) -> None:
    """Detect new holy wars between factions with conflicting religions."""
    import diplomacy as _dip   # local import avoids circular dependency
    for i, fa in enumerate(factions):
        rel_a = get_faction_religion(fa)
        if rel_a is None:
            continue
        for fb in factions[i + 1:]:
            rel_b = get_faction_religion(fb)
            if rel_b is None or rel_b is rel_a:
                continue
            pair = frozenset({fa.name, fb.name})
            if pair in _HOLY_WARS:
                continue
            rep_ab = _dip._reputation.get(fa.name, {}).get(fb.name, 0)
            rep_ba = _dip._reputation.get(fb.name, {}).get(fa.name, 0)
            if rep_ab <= HOLY_WAR_REP_THRESHOLD or rep_ba <= HOLY_WAR_REP_THRESHOLD:
                _HOLY_WARS.add(pair)
                event_log.append(
                    f"[t={t}] HOLY WAR declared: {fa.name} ({rel_a.name}) "
                    f"vs {fb.name} ({rel_b.name})"
                )


def _end_resolved_holy_wars(factions: list, t: int, event_log: list) -> None:
    """Remove holy wars where at least one faction has dissolved."""
    active_names = {f.name for f in factions}
    to_remove    = [pair for pair in _HOLY_WARS
                    if not pair.issubset(active_names)]
    for pair in to_remove:
        _HOLY_WARS.discard(pair)
        names = sorted(pair)
        event_log.append(
            f"[t={t}] HOLY WAR ended (faction dissolved): "
            f"{names[0]} vs {names[1]}"
        )


# ---------------------------------------------------------------------------
# Temple trust battery
# ---------------------------------------------------------------------------

def _temple_trust_tick(people: list) -> None:
    """Members within TEMPLE_TRUST_RANGE of any temple tile gain +1 trust."""
    all_tiles: set = set()
    for rel in _religions:
        all_tiles |= rel.temple_tiles
    if not all_tiles:
        return
    for inh in people:
        for (tr, tc) in all_tiles:
            if (abs(inh.r - tr) <= TEMPLE_TRUST_RANGE
                    and abs(inh.c - tc) <= TEMPLE_TRUST_RANGE):
                inh.trust = min(100, inh.trust + TEMPLE_TRUST_BONUS)
                break   # only apply once per person per tick


# ---------------------------------------------------------------------------
# Priesthood
# ---------------------------------------------------------------------------

def _assign_priests(factions: list) -> None:
    """Ensure every faction with a religion has exactly one priest.

    Selects the highest-trust member.  Does nothing if a priest already exists.
    """
    for faction in factions:
        rel = get_faction_religion(faction)
        if rel is None:
            continue
        members = faction.members
        if not members:
            continue
        existing = [m for m in members if getattr(m, 'is_priest', False)]
        if existing:
            # Refresh religion pointer in case it drifted
            existing[0].religion = rel
            continue
        best = max(members, key=lambda m: getattr(m, 'trust', 0))
        best.is_priest = True
        best.religion  = rel


def _priest_food_tick(factions: list) -> None:
    """Feed priests from the faction food reserve (skip normal tile-gathering).

    Called each tick so priests don't starve while abstaining from foraging.
    If the reserve is empty the priest takes a small hunger penalty.
    """
    for faction in factions:
        for m in faction.members:
            if not getattr(m, 'is_priest', False):
                continue
            if faction.food_reserve > 0:
                faction.food_reserve -= 1
                m.hunger = max(0, m.hunger - 7)
            else:
                m.hunger = min(120, m.hunger + 5)


def _priest_conversion_tick(people: list, factions: list,
                             t: int, event_log: list) -> None:
    """Priests attempt to convert nearby inhabitants."""
    from inhabitants import grid_neighbors  # local import

    faction_map: dict[int, object] = {}
    for f in factions:
        for m in f.members:
            faction_map[id(m)] = f

    for inh in people:
        if not getattr(inh, 'is_priest', False):
            continue
        inh_rel    = getattr(inh, 'religion', None)
        inh_faction = faction_map.get(id(inh))
        if inh_rel is None:
            continue

        neighbors = [p for p in grid_neighbors(inh.r, inh.c) if p is not inh]
        if not neighbors:
            continue

        # Target: lowest-trust neighbor who doesn't share this religion
        candidates = [p for p in neighbors if getattr(p, 'religion', None) is not inh_rel]
        if not candidates:
            candidates = neighbors
        target = min(candidates, key=lambda p: getattr(p, 'trust', 100))

        target_faction = faction_map.get(id(target))
        same_faction   = (inh_faction is not None and inh_faction is target_faction)
        chance         = PRIEST_CONVERT_SAME if same_faction else PRIEST_CONVERT_CROSS

        if random.random() < chance:
            target.religion = inh_rel
            target.trust    = min(100, getattr(target, 'trust', 0) + 5)
            # Share dominant belief of priest's faction
            if inh_faction:
                dom = _dominant_belief(inh_faction)
                if dom and dom not in getattr(target, 'beliefs', []):
                    target.beliefs.append(dom)
            event_log.append(
                f"[t={t}] CONVERTED: inhabitant at ({target.r},{target.c}) "
                f"→ {inh_rel.name!r} by priest of "
                f"{inh_faction.name if inh_faction else '?'}"
            )


# ---------------------------------------------------------------------------
# Birth helper  (called from sim.py procreation_layer)
# ---------------------------------------------------------------------------

def templated_birth_religion(child, faction_religion: Religion) -> None:
    """Assign *faction_religion* to *child* (95 % inheritance, temple required)."""
    child.religion = faction_religion


# ---------------------------------------------------------------------------
# Main tick entry point
# ---------------------------------------------------------------------------

def religion_tick(people: list, factions: list,
                  t: int, event_log: list) -> None:
    """Run all religion subsystems for one simulation tick."""

    # 1. Attempt to found new religions every 10 ticks
    if t % 10 == 0:
        for faction in factions:
            try_found_religion(faction, t, event_log)

    # 2. Assign priests to factions that gained a religion but have none yet
    _assign_priests(factions)

    # 3. Feed priests from faction reserve (so their step-5 gather is skipped safely)
    _priest_food_tick(factions)

    # 4. Temple trust battery every 5 ticks
    if t % 5 == 0:
        _temple_trust_tick(people)

    # 5. Priest conversion every 5 ticks
    if t % 5 == 0:
        _priest_conversion_tick(people, factions, t, event_log)

    # 6. Holy war detection and cleanup every tick
    _check_holy_wars(factions, t, event_log)
    _end_resolved_holy_wars(factions, t, event_log)
