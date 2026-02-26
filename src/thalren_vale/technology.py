# (c) 2026 (KriaetvAspie / AspieTheBard)
# Licensed under the Polyform Noncommercial License 1.0.0
"""
technology.py — Layer 6: 3-branch research tree, passive effects, AI selection.

Call order each tick (after combat_tick):
    technology_tick(factions, t, event_log)

Public helpers used by other modules:
    has_tech(faction, tech)     -> bool
    combat_bonus(faction)       -> float  (multiplicative, applied to strength)
    defense_bonus(faction)      -> float  (when defending, Masonry/Steel)
    raid_multiplier(faction)    -> int    (1 normally, higher with martial techs)

Branches
────────
  Industrial (Economy)  : Tools → Farming → Mining → Engineering → Currency
  Martial    (War)      : Scavenging → Metalwork → Weaponry → Masonry → Steel
  Civic      (Stability): Oral Tradition → Medicine → Writing → Code of Laws
"""
import random, sys
sys.stdout.reconfigure(encoding='utf-8')

from .world   import world, BIOME_MAX, coast_score
from .beliefs import add_belief, core_of, inh_cores, MAX_BELIEFS


# ══════════════════════════════════════════════════════════════════════════
# Tech tree — 15 technologies across 3 branches
# ══════════════════════════════════════════════════════════════════════════

TECH_TREE: dict[str, dict] = {

    # ── Industrial Path (Economy) ────────────────────────────────────────
    'tools': {
        'branch':   'industrial',
        'tier':     1,
        'requires': [],
        'cost':     {'wood': 4, 'stone': 4},
        'ticks':    10,
        'desc':     'Bonus food gather every even tick; unlocks Industrial branch',
    },
    'farming': {
        'branch':   'industrial',
        'tier':     2,
        'requires': ['tools'],
        'cost':     {'food': 10, 'wood': 3},
        'ticks':    15,
        'desc':     'Passively adds up to +3 food/tick to reserve; territory floor 5 food',
    },
    'sailing': {
        'branch':   'industrial',
        'tier':     2,
        'requires': ['tools'],
        'cost':     {'wood': 8, 'stone': 4},
        'ticks':    20,
        'desc':     'Unlocks sea travel; sea movement is 2× the range but costs 1.5× energy',
    },
    'mining': {
        'branch':   'industrial',
        'tier':     3,
        'requires': ['tools'],
        'cost':     {'stone': 10, 'ore': 4},
        'ticks':    18,
        'desc':     'Passively generates +1 ore and +1 stone each tick for the faction pool',
    },
    'engineering': {
        'branch':   'industrial',
        'tier':     3,
        'requires': ['farming', 'mining'],
        'cost':     {'ore': 12, 'wood': 8},
        'ticks':    22,
        'desc':     'Doubles mining yield; reduces all research tick costs by 20%',
    },
    'currency': {
        'branch':   'industrial',
        'tier':     4,
        'requires': ['engineering'],
        'cost':     {'ore': 15, 'food': 10},
        'ticks':    28,
        'desc':     'All trade gives +2 bonus gold; faction wealth grows +1/tick',
    },

    # ── Martial Path (War) ───────────────────────────────────────────────
    'scavenging': {
        'branch':   'martial',
        'tier':     1,
        'requires': [],
        'cost':     {'food': 5},
        'ticks':    8,
        'desc':     'Raiders loot 2× resources; unlocks Martial branch',
    },
    'metalwork': {
        'branch':   'martial',
        'tier':     2,
        'requires': ['scavenging'],
        'cost':     {'ore': 8, 'stone': 4},
        'ticks':    15,
        'desc':     'Combat strength +30%',
    },
    'weaponry': {
        'branch':   'martial',
        'tier':     3,
        'requires': ['metalwork'],
        'cost':     {'ore': 12},
        'ticks':    20,
        'desc':     'Combat strength +50% total; raids steal 3× resources',
    },
    'masonry': {
        'branch':   'martial',
        'tier':     3,
        'requires': ['metalwork'],
        'cost':     {'stone': 14, 'wood': 6},
        'ticks':    20,
        'desc':     'Defensive bonus +20% when defending home territory',
    },
    'steel': {
        'branch':   'martial',
        'tier':     4,
        'requires': ['weaponry', 'masonry'],
        'cost':     {'ore': 20, 'stone': 10},
        'ticks':    30,
        'desc':     'Combat strength +80% total; raids steal 4×; immune to exhaustion',
    },

    # ── Civic Path (Stability) ───────────────────────────────────────────
    'oral_tradition': {
        'branch':   'civic',
        'tier':     1,
        'requires': [],
        'cost':     {'food': 6},
        'ticks':    8,
        'desc':     'Beliefs spread 30% faster within faction; unlocks Civic branch',
    },
    'medicine': {
        'branch':   'civic',
        'tier':     2,
        'requires': ['oral_tradition'],
        'cost':     {'food': 12, 'wood': 4},
        'ticks':    18,
        'desc':     'Heals hunger damage; 50% plague resistance; starvation buffer +5',
    },
    'writing': {
        'branch':   'civic',
        'tier':     3,
        'requires': ['oral_tradition'],
        'cost':     {'wood': 10, 'stone': 6},
        'ticks':    22,
        'desc':     'Beliefs spread 2× faster; myths travel via trade routes',
    },
    'code_of_laws': {
        'branch':   'civic',
        'tier':     4,
        'requires': ['writing', 'medicine'],
        'cost':     {'food': 15, 'wood': 10},
        'ticks':    30,
        'desc':     'Raises internal trust; suppresses schisms; +5 reputation permanently',
    },
}

# Branch → set of tech names (for quick lookup)
_BRANCH: dict[str, set] = {}
for _t, _d in TECH_TREE.items():
    _BRANCH.setdefault(_d['branch'], set()).add(_t)

# ── Belief → preferred branch affinity ───────────────────────────────────
_BELIEF_AFFINITY: dict[str, str] = {
    # Industrial
    'community_sustains':          'industrial',
    'the_wilds_provide':           'industrial',
    'the_forest_shelters':         'industrial',
    'the_sea_provides':            'industrial',
    'fortune_favors_the_prepared': 'industrial',
    'trade_builds_bonds':          'industrial',
    # Martial
    'the_strong_take':             'martial',
    'suffering_forges_strength':   'martial',
    'victory_proves_strength':     'martial',
    'desert_forges_the_worthy':    'martial',
    'stone_stands_eternal':        'martial',
    # Civic
    'the_wise_must_lead':          'civic',
    'loyalty_above_all':           'civic',
    'endurance_rewarded':          'civic',
    'migration_brings_hope':       'civic',
    'self_reliance':               'civic',
}


# ══════════════════════════════════════════════════════════════════════════
# Faction attribute helpers
# ══════════════════════════════════════════════════════════════════════════

def _ensure_tech(faction) -> None:
    """Lazily attach tech state to a faction object if not already present."""
    if not hasattr(faction, 'techs'):
        faction.techs           = set()
        faction.active_research = None   # dict: {tech, progress, started, paused}


# ══════════════════════════════════════════════════════════════════════════
# Resource helpers
# ══════════════════════════════════════════════════════════════════════════

def _pooled_resources(faction) -> dict:
    """Sum member inventories + food_reserve."""
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
    """Remove tech cost from food_reserve first, then member inventories."""
    remaining = dict(TECH_TREE[tech]['cost'])
    if 'food' in remaining:
        taken = min(int(faction.food_reserve), remaining['food'])
        faction.food_reserve -= taken
        remaining['food']    -= taken
        if remaining['food'] == 0:
            del remaining['food']
    for m in faction.members:
        if not remaining:
            break
        for res in list(remaining):
            have              = m.inventory.get(res, 0)
            take              = min(have, remaining[res])
            m.inventory[res]  = have - take
            remaining[res]   -= take
            if remaining[res] == 0:
                del remaining[res]


def _research_duration(faction, tech: str) -> int:
    """Base tick cost, reduced 20% if faction has Engineering."""
    base = TECH_TREE[tech]['ticks']
    if 'engineering' in getattr(faction, 'techs', set()):
        base = max(5, int(base * 0.80))
    return base


# ══════════════════════════════════════════════════════════════════════════
# Research AI — smart tech selection
# ══════════════════════════════════════════════════════════════════════════

def _researchable(faction) -> list:
    """All techs whose prerequisites are met and not yet owned."""
    done = faction.techs
    return [
        t for t, info in TECH_TREE.items()
        if t not in done and all(r in done for r in info['requires'])
    ]


def _choose_next_tech(faction) -> str | None:
    """
    Pick the best affordable tech to research next.
    Scoring:
      +tier   — prefer higher tier within branch
      +3      — if tech branch matches dominant belief affinity
      +votes  — extra per shared_belief pointing at this branch
    Ties broken randomly.
    """
    affordable = [t for t in _researchable(faction) if _can_afford(faction, t)]
    if not affordable:
        return None

    # Tally branch votes from faction beliefs
    branch_votes: dict[str, int] = {}
    for bel in faction.shared_beliefs:
        branch = _BELIEF_AFFINITY.get(bel)
        if branch:
            branch_votes[branch] = branch_votes.get(branch, 0) + 1
    preferred_branch = max(branch_votes, key=branch_votes.get) if branch_votes else None

    scored: list[tuple[int, str]] = []
    for tech in affordable:
        info  = TECH_TREE[tech]
        score = info['tier']
        if preferred_branch and info['branch'] == preferred_branch:
            score += 3
        score += branch_votes.get(info['branch'], 0)
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
    sep  = '═' * 50
    print(f"\n{sep}")
    print(f"  💡 TECH DISCOVERED: {tech.upper()}")
    print(f"  {faction.name}  ·  Branch: {TECH_TREE[tech]['branch'].title()}")
    print(f"  {desc}")
    print(f"{sep}\n")
    msg = (f"Tick {t:04d}: 💡 TECH DISCOVERED: {faction.name} → "
           f"{tech.upper()} [{desc}]")
    event_log.append(msg)


# ══════════════════════════════════════════════════════════════════════════
# Main tick
# ══════════════════════════════════════════════════════════════════════════

def technology_tick(factions: list, t: int, event_log: list) -> None:
    """Advance research and apply passive tech effects for every faction."""
    from . import combat  as _cbt   # lazy — avoids circular import at module load
    from . import economy as _eco

    at_war_names: set = {
        f.name
        for w in _cbt.active_wars
        for f in w.all_attackers() + w.all_defenders()
    }
    faction_by_name = {f.name: f for f in factions if f.members}

    for faction in factions:
        if not faction.members:
            continue

        _ensure_tech(faction)
        at_war    = faction.name in at_war_names
        n_members = len(faction.members)
        techs     = faction.techs

        # ── Advance or pause active research ──────────────────────────────
        if faction.active_research:
            r = faction.active_research

            if at_war:
                if not r.get('paused'):
                    r['paused'] = True
                    msg = (f"Tick {t:04d}: ⏸ {faction.name} — "
                           f"{r['tech'].upper()} research paused (at war)")
                    event_log.append(msg)
                    print(msg)
            else:
                if r.get('paused'):
                    r['paused'] = False
                    msg = (f"Tick {t:04d}: ▶ {faction.name} — "
                           f"{r['tech'].upper()} research resumed")
                    event_log.append(msg)
                    print(msg)
                if n_members >= 2:
                    r['progress'] += 1
                    faction.food_reserve = max(0.0, faction.food_reserve - 1.0)
                    if r['progress'] >= _research_duration(faction, r['tech']):
                        _complete_research(faction, r['tech'], t, event_log)

        # ── Start new research ─────────────────────────────────────────────
        if not faction.active_research and n_members >= 2 and not at_war:
            next_tech = _choose_next_tech(faction)
            if next_tech:
                _deduct_cost(faction, next_tech)
                eta = _research_duration(faction, next_tech)
                faction.active_research = {
                    'tech':     next_tech,
                    'progress': 0,
                    'started':  t,
                    'paused':   False,
                }
                msg = (f"Tick {t:04d}: 🔬 {faction.name} begins researching "
                       f"{next_tech.upper()} (ETA: {eta} ticks)")
                event_log.append(msg)
                print(msg)

        # ══════════════════════════════════════════════════════════════════
        # Passive effects — applied every tick
        # ══════════════════════════════════════════════════════════════════

        # ── TOOLS: bonus food gather on even ticks ─────────────────────────
        if 'tools' in techs and t % 2 == 0:
            for m in faction.members:
                res   = world[m.r][m.c]['resources']
                bonus = min(1, res.get('food', 0))
                if bonus:
                    res['food']         -= bonus
                    m.inventory['food'] += bonus

        # ── FARMING: passive food generation into reserve ──────────────────
        if 'farming' in techs:
            cap = len(faction.members) * 20
            add = min(3.0, max(0.0, float(cap) - faction.food_reserve))
            faction.food_reserve += add
            for pos in faction.territory:
                try:
                    chunk = world[pos[0]][pos[1]]
                    if chunk['resources'].get('food', 0) < 5:
                        chunk['resources']['food'] = 5
                except (IndexError, KeyError):
                    pass

        # ── MINING: passive ore and stone generation ───────────────────────
        if 'mining' in techs:
            ore_add   = 2 if 'engineering' in techs else 1
            stone_add = 2 if 'engineering' in techs else 1
            if faction.members:
                tgt = faction.members[t % len(faction.members)]
                tgt.inventory['ore']   = tgt.inventory.get('ore',   0) + ore_add
                tgt.inventory['stone'] = tgt.inventory.get('stone', 0) + stone_add

        # ── CURRENCY: faction passive wealth growth ────────────────────────
        if 'currency' in techs:
            faction.wealth = getattr(faction, 'wealth', 0) + 1

        # ── MEDICINE: heal hunger damage, starvation buffer, plague resist ─
        if 'medicine' in techs:
            for m in faction.members:
                stored_hp = getattr(m, '_prev_hp_medicine', m.health)
                hp_lost   = stored_hp - m.health
                if m.hunger > 40 and hp_lost >= 8:
                    m.health = min(100, m.health + 8)
                m._prev_hp_medicine = m.health
                m._medicine_buffer  = min(5, getattr(m, '_medicine_buffer', 0) + 1)
                m._plague_resist    = True   # checked by disruption_event_layer

        # ── ORAL TRADITION: 30% intra-faction belief spread boost ──────────
        if 'oral_tradition' in techs and t % 3 == 0:
            members = faction.members
            if len(members) >= 2:
                giver    = random.choice(members)
                receiver = random.choice(members)
                if giver is not receiver and giver.beliefs:
                    belief = random.choice(giver.beliefs)
                    core   = core_of(belief)
                    if core not in inh_cores(receiver):
                        if len(receiver.beliefs) >= MAX_BELIEFS:
                            receiver.beliefs.pop(0)
                        receiver.beliefs.append(f'heard_from_{giver.name}:{core}')

        # ── WRITING: beliefs spread 2× + via trade routes ──────────────────
        if 'writing' in techs:
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
                    msg = (f"Tick {t:04d}: ✍ {giver.name} shares '{core}' "
                           f"within {faction.name} (writing)")
                    event_log.append(msg)
            for route_key, data in _eco.trade_routes.items():
                if not data.get('active') or faction.name not in route_key:
                    continue
                other_name = next(n for n in route_key if n != faction.name)
                other_f    = faction_by_name.get(other_name)
                if not other_f or not other_f.members or not faction.members:
                    continue
                if random.random() >= 0.25:
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
                    msg = (f"Tick {t:04d}: ✍ Trade route carries '{core}' "
                           f"{faction.name} → {other_name}")
                    event_log.append(msg)
        # ── SAILING: tag members so inhabitants.py sea-traversal check works ──
        if 'sailing' in techs:
            for m in faction.members:
                m._can_sail = True
                # Fishing passive: coast/sea adjacency yields +1 food every 3 ticks
                if t % 3 == 0 and coast_score(m.r, m.c) > 0:
                    biome       = world[m.r][m.c]['biome']
                    max_food    = BIOME_MAX.get(biome, {}).get('food', 0)
                    cur_food    = world[m.r][m.c]['resources'].get('food', 0)
                    if cur_food < max_food:
                        world[m.r][m.c]['resources']['food'] = min(
                            max_food, cur_food + 1)
                    m.inventory['food'] = m.inventory.get('food', 0) + 1
        # ── CODE OF LAWS: boost internal trust; one-time reputation bonus ──
        if 'code_of_laws' in techs and t % 5 == 0:
            from . import religion as _rel
            # Holy wars fracture social cohesion — suppress the trust bonus
            if not _rel.is_holy_war_member(faction.name):
                members = faction.members
                for a in members:
                    for b in members:
                        if a is b:
                            continue
                        a.trust[b.name] = min(100, a.trust.get(b.name, 50) + 2)
            if not getattr(faction, '_laws_rep_applied', False):
                try:
                    from . import diplomacy as _dip
                    _dip._reputation[faction.name] = (
                        _dip._reputation.get(faction.name, 0) + 5
                    )
                except Exception:
                    pass
                faction._laws_rep_applied = True


# ══════════════════════════════════════════════════════════════════════════
# Public helpers for other modules
# ══════════════════════════════════════════════════════════════════════════

def has_tech(faction, tech: str) -> bool:
    """Return True if faction owns the named technology."""
    return tech in getattr(faction, 'techs', set())


def combat_bonus(faction) -> float:
    """
    Multiplicative strength bonus from the Martial branch.
    Stacks: Metalwork (+30%) → Weaponry (+50%) → Steel (+80%).
    """
    techs = getattr(faction, 'techs', set())
    if 'steel'     in techs: return 1.80
    if 'weaponry'  in techs: return 1.50
    if 'metalwork' in techs: return 1.30
    return 1.0


def defense_bonus(faction) -> float:
    """Extra multiplier applied when faction is defending (Masonry / Steel)."""
    techs = getattr(faction, 'techs', set())
    if 'masonry' in techs or 'steel' in techs:
        return 1.20
    return 1.0


def raid_multiplier(faction) -> int:
    """
    How many times normal loot is stolen in a raid.
      Scavenging → 2×  |  Weaponry → 3×  |  Steel → 4×
    """
    techs = getattr(faction, 'techs', set())
    if 'steel'      in techs: return 4
    if 'weaponry'   in techs: return 3
    if 'scavenging' in techs: return 2
    return 1