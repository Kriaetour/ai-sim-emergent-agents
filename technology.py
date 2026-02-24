"""
technology.py — Layer 6: research tree, asymmetric faction strategies.

Call order each tick (after combat_tick):
    technology_tick(factions, t, event_log)

Public helpers used by other modules:
    has_tech(faction, tech)     -> bool
    combat_bonus(faction)       -> float  (multiplicative, applied to strength)
    raid_multiplier(faction)    -> int    (1 normally, 2 with weapons)
"""
import random, sys
sys.stdout.reconfigure(encoding='utf-8')

from world   import world
from beliefs import add_belief, core_of, inh_cores, MAX_BELIEFS


# ══════════════════════════════════════════════════════════════════════════
# Tech tree definition
# ══════════════════════════════════════════════════════════════════════════

TECH_TREE: dict[str, dict] = {
    # ── Tier 1 ──────────────────────────────────────────────────────────
    'tools': {
        'tier':     1,
        'requires': [],
        'cost':     {'wood': 5, 'stone': 5},
        'ticks':    12,
        'desc':     '+50% gathering speed for all members',
    },
    # ── Tier 2 ──────────────────────────────────────────────────────────
    'farming': {
        'tier':     2,
        'requires': ['tools'],
        'cost':     {'food': 10},
        'ticks':    18,
        'desc':     'Territory produces +3 food / tick passively',
    },
    'metalwork': {
        'tier':     2,
        'requires': ['tools'],
        'cost':     {'ore': 8},
        'ticks':    18,
        'desc':     'Combat strength +30%',
    },
    # ── Tier 3 ──────────────────────────────────────────────────────────
    'medicine': {
        'tier':     3,
        'requires': ['farming'],
        'cost':     {'food': 15},
        'ticks':    25,
        'desc':     'Members lose health 50% slower from hunger',
    },
    'weapons': {
        'tier':     3,
        'requires': ['metalwork'],
        'cost':     {'ore': 10},
        'ticks':    25,
        'desc':     'Combat strength +50%, raids steal double',
    },
    'writing': {
        'tier':     3,
        'requires': ['tools'],
        'cost':     {'wood': 12},
        'ticks':    22,
        'desc':     'Beliefs spread 2x faster; beliefs travel via trade routes',
    },
}

# Belief → tech affinity: factions with these beliefs prefer these techs
_AFFINITY: dict[str, set] = {
    'farming':   {'community_sustains', 'the_wilds_provide', 'the_forest_shelters',
                  'the_sea_provides', 'fortune_favors_the_prepared'},
    'metalwork': {'the_strong_take', 'suffering_forges_strength', 'victory_proves_strength'},
    'writing':   {'trade_builds_bonds', 'the_wise_must_lead', 'loyalty_above_all'},
    'medicine':  {'endurance_rewarded', 'migration_brings_hope'},
    'weapons':   {'the_strong_take', 'victory_proves_strength'},
}


# ══════════════════════════════════════════════════════════════════════════
# Faction attribute helpers
# ══════════════════════════════════════════════════════════════════════════

def _ensure_tech(faction) -> None:
    """Lazily attach tech state to a faction if not already present."""
    if not hasattr(faction, 'techs'):
        faction.techs           = set()
        faction.active_research = None   # {'tech', 'progress', 'started', 'paused'}


# ══════════════════════════════════════════════════════════════════════════
# Resource helpers
# ══════════════════════════════════════════════════════════════════════════

def _pooled_resources(faction) -> dict:
    """Sum member inventories + food_reserve (for food)."""
    pool: dict[str, int] = {}
    for m in faction.members:
        for k, v in m.inventory.items():
            pool[k] = pool.get(k, 0) + v
    pool['food'] = pool.get('food', 0) + int(faction.food_reserve)
    return pool


def _can_afford(faction, tech: str) -> bool:
    cost = TECH_TREE[tech]['cost']
    pool = _pooled_resources(faction)
    return all(pool.get(res, 0) >= amt for res, amt in cost.items())


def _deduct_cost(faction, tech: str) -> None:
    """Remove tech cost from faction food_reserve then member inventories."""
    remaining = dict(TECH_TREE[tech]['cost'])
    # Drain food from reserve first
    if 'food' in remaining:
        taken = min(int(faction.food_reserve), remaining['food'])
        faction.food_reserve -= taken
        remaining['food']    -= taken
    # Drain wood / ore / stone from member inventories
    for m in faction.members:
        if not remaining:
            break
        for res in list(remaining):
            have          = m.inventory.get(res, 0)
            take          = min(have, remaining[res])
            m.inventory[res]   = have - take
            remaining[res]    -= take
            if remaining[res] == 0:
                del remaining[res]


# ══════════════════════════════════════════════════════════════════════════
# Research selection
# ══════════════════════════════════════════════════════════════════════════

def _researchable(faction) -> list:
    """All techs whose prerequisites are met and not yet discovered."""
    done = faction.techs
    return [
        t for t, info in TECH_TREE.items()
        if t not in done and all(r in done for r in info['requires'])
    ]


def _choose_next_tech(faction) -> str | None:
    """Pick the highest-affinity affordable tech to research next."""
    affordable = [t for t in _researchable(faction) if _can_afford(faction, t)]
    if not affordable:
        return None

    faction_cores = set(faction.shared_beliefs)
    scored = []
    for tech in affordable:
        affinity = _AFFINITY.get(tech, set())
        score    = 1 + sum(2 for b in affinity if b in faction_cores)
        scored.append((score, tech))

    scored.sort(key=lambda x: -x[0])
    top_score = scored[0][0]
    top_group = [t for s, t in scored if s == top_score]
    return random.choice(top_group)


# ══════════════════════════════════════════════════════════════════════════
# Research completion
# ══════════════════════════════════════════════════════════════════════════

def _complete_research(faction, tech: str, t: int, event_log: list) -> None:
    faction.techs.add(tech)
    faction.active_research = None
    desc = TECH_TREE[tech]['desc']
    sep  = '═' * 46
    print(f"\n{sep}")
    print(f"  💡 TECH DISCOVERED: {tech.upper()}")
    print(f"  {faction.name}  —  {desc}")
    print(f"{sep}\n")
    msg = (f"Tick {t:03d}: 💡 {faction.name} discovered {tech.upper()}! "
           f"[{desc}]")
    event_log.append(msg)


# ══════════════════════════════════════════════════════════════════════════
# Main tick
# ══════════════════════════════════════════════════════════════════════════

def technology_tick(factions: list, t: int, event_log: list) -> None:
    """Advance research and apply per-tick tech effects for every faction."""
    import combat as _cbt     # lazy — avoids circular import at module load
    import economy as _eco    # lazy — same reason

    at_war_names: set = {
        f.name
        for w in _cbt.active_wars
        for f in w.all_attackers() + w.all_defenders()
    }

    # Build lookup used by writing trade-route sharing
    faction_by_name = {f.name: f for f in factions if f.members}

    for faction in factions:
        if not faction.members:
            continue

        _ensure_tech(faction)
        at_war    = faction.name in at_war_names
        n_members = len(faction.members)

        # ── Advance or pause active research ──────────────────────────────
        if faction.active_research:
            r = faction.active_research

            if at_war:
                # Pause (log once)
                if not r.get('paused'):
                    r['paused'] = True
                    msg = (f"Tick {t:03d}: ⏸ {faction.name} — "
                           f"{r['tech'].upper()} research paused (at war)")
                    event_log.append(msg)
                    print(msg)
            else:
                # Resume logging
                if r.get('paused'):
                    r['paused'] = False
                    msg = (f"Tick {t:03d}: ▶ {faction.name} — "
                           f"{r['tech'].upper()} research resumed")
                    event_log.append(msg)
                    print(msg)
                # Advance (only when 2+ members so one can research)
                if n_members >= 2:
                    r['progress'] += 1
                    # Researcher forgoes gathering — drain 1 food from reserve
                    faction.food_reserve = max(0.0, faction.food_reserve - 1.0)
                    if r['progress'] >= TECH_TREE[r['tech']]['ticks']:
                        _complete_research(faction, r['tech'], t, event_log)

        # ── Start new research ─────────────────────────────────────────────
        if not faction.active_research and n_members >= 2 and not at_war:
            next_tech = _choose_next_tech(faction)
            if next_tech:
                _deduct_cost(faction, next_tech)
                eta = TECH_TREE[next_tech]['ticks']
                faction.active_research = {
                    'tech':     next_tech,
                    'progress': 0,
                    'started':  t,
                    'paused':   False,
                }
                msg = (f"Tick {t:03d}: 🔬 {faction.name} begins researching "
                       f"{next_tech.upper()} (ETA: {eta} ticks)")
                event_log.append(msg)
                print(msg)

        # ── Per-tick effects ───────────────────────────────────────────────
        techs = faction.techs

        # ── tools: +50% gathering ≈ extra gather every other tick ─────────
        if 'tools' in techs and t % 2 == 0:
            for m in faction.members:
                res   = world[m.r][m.c]['resources']
                bonus = min(1, res['food'])
                res['food']         -= bonus
                m.inventory['food'] += bonus

        # ── farming: territory passively produces +3 food / tick ──────────
        #    guaranteed food floor: faction territory chunks never drop below 5
        if 'farming' in techs:
            cap = len(faction.members) * 20
            add = min(3.0, max(0.0, float(cap) - faction.food_reserve))
            faction.food_reserve += add
            for pos in faction.territory:
                chunk = world[pos[0]][pos[1]]
                if chunk['resources']['food'] < 5:
                    chunk['resources']['food'] = 5

        # ── medicine: heal hunger damage + grant 5-tick starvation buffer ─
        #    _medicine_buffer is checked by inhabitants.do_tick before HP loss
        if 'medicine' in techs:
            for m in faction.members:
                prev = getattr(m, 'prev_health', m.health)
                if m.hunger > 40 and (prev - m.health) >= 10:
                    m.health = min(100, m.health + 10)   # stronger heal
                # Replenish starvation buffer (consumed 1/tick in inhabitants.py)
                m._medicine_buffer = min(5, getattr(m, '_medicine_buffer', 0) + 1)

        # ── writing: beliefs spread 2× faster + via trade routes ──────────
        if 'writing' in techs:

            # Extra intra-faction belief sharing (second pass)
            members = faction.members
            for giver in members:
                if not giver.beliefs or random.random() >= 0.40:
                    continue
                receiver = random.choice(members)
                if receiver is giver:
                    continue
                if giver.trust.get(receiver.name, 0) <= 5:
                    continue
                belief = random.choice(giver.beliefs)
                core   = core_of(belief)
                if core not in inh_cores(receiver):
                    if len(receiver.beliefs) >= MAX_BELIEFS:
                        receiver.beliefs.pop(0)
                    receiver.beliefs.append(f'heard_from_{giver.name}:{core}')
                    msg = (f"Tick {t:03d}: ✍ {giver.name} shares '{core}' "
                           f"within {faction.name} (writing)")
                    event_log.append(msg)

            # Trade-route cross-faction belief spreading
            for route_key, data in _eco.trade_routes.items():
                if not data.get('active') or faction.name not in route_key:
                    continue
                other_name = next(n for n in route_key if n != faction.name)
                other_f    = faction_by_name.get(other_name)
                if not other_f or not other_f.members or not faction.members:
                    continue
                if random.random() >= 0.25:   # 25% per active route per tick
                    continue
                giver_m = random.choice(faction.members)
                if not giver_m.beliefs:
                    continue
                belief  = random.choice(giver_m.beliefs)
                core    = core_of(belief)
                recv_m  = random.choice(other_f.members)
                if core not in inh_cores(recv_m):
                    if len(recv_m.beliefs) >= MAX_BELIEFS:
                        recv_m.beliefs.pop(0)
                    recv_m.beliefs.append(f'heard_from_{giver_m.name}:{core}')
                    msg = (f"Tick {t:03d}: ✍ Trade route carries '{core}' "
                           f"{faction.name} → {other_name}")
                    event_log.append(msg)


# ══════════════════════════════════════════════════════════════════════════
# Public helpers for other modules
# ══════════════════════════════════════════════════════════════════════════

def has_tech(faction, tech: str) -> bool:
    return tech in getattr(faction, 'techs', set())


def combat_bonus(faction) -> float:
    """Return the multiplicative strength bonus from military tech."""
    techs = getattr(faction, 'techs', set())
    if 'weapons'  in techs:
        return 1.50   # +50%
    if 'metalwork' in techs:
        return 1.30   # +30%
    return 1.0


def raid_multiplier(faction) -> int:
    """Return 2 if faction has weapons (steals double), else 1."""
    return 2 if 'weapons' in getattr(faction, 'techs', set()) else 1
