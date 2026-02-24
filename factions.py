import random
from itertools import combinations
from world   import world, GRID
from beliefs import core_of, inh_cores, LABELS, add_belief

# module-level rivalry scores: {(name_a, name_b): int}  (names always sorted)
RIVALRIES: dict = {}

# ── Belief pairs that create natural rivals ────────────────────────────────
RIVAL_BELIEFS = [
    ('community_sustains',           'self_reliance'),
    ('trade_builds_bonds',           'the_strong_take'),
    ('crowded_lands_breed_conflict', 'community_sustains'),
]

# ── Ideological conflicts: block joining + trigger schism ─────────────────
_IDEO_CONFLICTS = [
    ('community_sustains',  'self_reliance'),
    ('loyalty_above_all',   'trust_no_group'),
    ('the_wise_must_lead',  'crowded_lands_breed_conflict'),
    ('trade_builds_bonds',  'the_strong_take'),
]

# ── Component-based faction name generator ─────────────────────────────────
_ADJ_BY_BELIEF = {
    'community_sustains':            ['United',    'Bound',      'Woven'],
    'self_reliance':                 ['Free',      'Lone',       'Wild'],
    'migration_brings_hope':         ['Wandering', 'Restless',   'Drifting'],
    'endurance_rewarded':            ['Enduring',  'Iron',       'Steadfast'],
    'trade_builds_bonds':            ['Merchant',  'Open',       'Trading'],
    'loss_teaches_caution':          ['Mourning',  'Ashen',      'Shadowed'],
    'the_wilds_provide':             ['Forest',    'Green',      'Untamed'],
    'suffering_forges_strength':     ['Scarred',   'Tempered',   'Forged'],
    'the_strong_take':               ['Iron',      'Fierce',     'Ruthless'],
    'death_is_near':                 ['Ashen',     'Last',       'Dusk'],
    'the_land_can_fail':             ['Restless',  'Seeking',    'Lost'],
    'lucky_survivor':                ['Blessed',   'Chosen',     'Lucky'],
    'crowded_lands_breed_conflict':  ['Scattered', 'Dispersed',  'Flung'],
    'the_forest_shelters':           ['Sylvan',    'Hooded',     'Deep'],
    'stone_stands_eternal':          ['Stone',     'Granite',    'Ancient'],
    'the_sea_provides':              ['Tidal',     'Salted',     'Coastal'],
    'desert_forges_the_worthy':      ['Sunbaked',  'Arid',       'Hardened'],
    'hunger_teaches_truth':          ['Hollow',    'Starved',    'Lean'],
    'fortune_favors_the_prepared':   ['Cautious',  'Stockpiled', 'Hoarding'],
    'loyalty_above_all':             ['Faithful',  'Sworn',      'Bound'],
    'the_wise_must_lead':            ['Wise',      'Elder',      'Guided'],
}
_NOUN_BY_BELIEF = {
    'community_sustains':            ['Circle',    'Hearth',     'Bond'],
    'self_reliance':                 ['Rovers',    'Walkers',    'Few'],
    'migration_brings_hope':         ['Drifters',  'Walkers',    'Road'],
    'endurance_rewarded':            ['Guard',     'Survivors',  'Enduring'],
    'trade_builds_bonds':            ['Exchange',  'Bridge',     'Market'],
    'loss_teaches_caution':          ['Watch',     'Vigil',      'Mourning'],
    'the_wilds_provide':             ['Seekers',   'Gatherers',  'Wilds'],
    'suffering_forges_strength':     ['Forged',    'Ones',       'Born'],
    'the_strong_take':               ['Fist',      'Blade',      'Conquerors'],
    'death_is_near':                 ['Covenant',  'Pact',       'Shadow'],
    'the_land_can_fail':             ['Seekers',   'Pilgrims',   'Wanderers'],
    'lucky_survivor':                ['Chosen',    'Survivors',  'Blessed'],
    'crowded_lands_breed_conflict':  ['Dispersed', 'Ones',       'Scattered'],
    'the_forest_shelters':           ['Canopy',    'Shade',      'Grove'],
    'stone_stands_eternal':          ['Pillars',   'Rampart',    'Foundation'],
    'the_sea_provides':              ['Tide',      'Shore',      'Current'],
    'desert_forges_the_worthy':      ['Forge',     'Kiln',       'Crucible'],
    'hunger_teaches_truth':          ['Hunger',    'Fast',       'Hollow'],
    'fortune_favors_the_prepared':   ['Hoard',     'Cache',      'Vault'],
    'loyalty_above_all':             ['Sworn',     'Pact',       'Bond'],
    'the_wise_must_lead':            ['Council',   'Elders',     'Sages'],
}
_FALLBACK_ADJ  = ['Wild', 'Free', 'Lost', 'Grey']
_FALLBACK_NOUN = ['Band', 'Few', 'Ones', 'Wanderers']

def _faction_name(common_cores, existing_names=None):
    """Generate a unique component-based name from shared beliefs."""
    existing_names = existing_names or set()
    beliefs  = list(common_cores)
    all_adj  = [w for b in beliefs for w in _ADJ_BY_BELIEF.get(b, [])]
    all_noun = [w for b in beliefs for w in _NOUN_BY_BELIEF.get(b, [])]
    if not all_adj:  all_adj  = _FALLBACK_ADJ[:]
    if not all_noun: all_noun = _FALLBACK_NOUN[:]
    for _ in range(30):
        adj  = random.choice(all_adj)
        noun = random.choice(all_noun)
        if adj == noun:
            continue        # prevent "The Scattered Scattered" style names
        name = f"The {adj} {noun}"
        if name not in existing_names:
            return name
    base = f"The {all_adj[0]} {all_noun[0]}"
    i = 2
    while f"{base} {i}" in existing_names:
        i += 1
    return f"{base} {i}"

# ── Faction class ──────────────────────────────────────────────────────────
class Faction:
    def __init__(self, name, members, shared_beliefs, territory, founded_tick):
        self.name           = name
        self.members        = list(members)
        self.shared_beliefs = list(shared_beliefs)
        self.territory      = list(territory)
        self.founded_tick   = founded_tick
        self.food_reserve   = 0.0
        self.legends        = []

    def member_names(self):
        return {m.name for m in self.members}

    def update_territory(self):
        self.territory = list({(m.r, m.c) for m in self.members})

    def remove_dead(self, dead_names):
        self.members = [m for m in self.members if m.name not in dead_names]

# ── Internal helpers ───────────────────────────────────────────────────────
def _mutual_trust(a, b, threshold=5):
    return (a.trust.get(b.name, 0) > threshold and
            b.trust.get(a.name, 0) > threshold)

def _common_cores(group):
    sets = [inh_cores(m) for m in group]
    common = sets[0].copy()
    for s in sets[1:]:
        common &= s
    return common

def _manhattan(a, b):
    return abs(a.r - b.r) + abs(a.c - b.c)

def _announce(faction, t, event_log):
    sep = '═' * 44
    print(f"\n{sep}")
    print(f"FACTION FORMED: {faction.name}")
    print(f"Members   : {', '.join(m.name for m in faction.members)}")
    beliefs_str = ', '.join(LABELS.get(b, b) for b in faction.shared_beliefs)
    print(f"Beliefs   : {beliefs_str}")
    print(f"Territory : {', '.join(str(p) for p in faction.territory)}")
    print(f"Founded   : Tick {t:02d}")
    print(sep)
    names_str = ', '.join(m.name for m in faction.members)
    event_log.append(f"Tick {t:02d}: FACTION — {faction.name} formed ({names_str})")

# ── Formation check (every 5 ticks) ──────────────────────────────────────────
def check_faction_formation(people, factions, t, event_log):
    taken    = {n for f in factions for n in f.member_names()}
    eligible = [p for p in people if p.name not in taken]
    absorbed = set()   # names claimed by new factions this pass

    for trio in combinations(eligible, 3):
        if any(m.name in absorbed for m in trio):
            continue

        # Proximity: all pairs within Manhattan distance 2
        if any(_manhattan(a, b) > 2 for a, b in combinations(trio, 2)):
            continue

        # Mutual trust: every pair must trust each other > 8
        if any(not _mutual_trust(a, b) for a, b in combinations(trio, 2)):
            continue

        # Shared beliefs: at least 2 common core beliefs
        common = _common_cores(trio)
        if len(common) < 2:
            continue

        name      = _faction_name(common, {f.name for f in factions})
        territory = list({(m.r, m.c) for m in trio})
        faction   = Faction(name, list(trio), list(common), territory, t)

        for m in trio:
            m.faction = name
            absorbed.add(m.name)

        factions.append(faction)
        _announce(faction, t, event_log)

# ── Per-tick faction mechanics ─────────────────────────────────────────────
def faction_tick(people, factions, t, event_log):
    for faction in factions:
        if not faction.members:
            continue

        # Pool 20% of each member's surplus food into reserve
        for m in faction.members:
            contrib = round(m.inventory['food'] * 0.20)
            if contrib > 0:
                m.inventory['food']  -= contrib
                faction.food_reserve += contrib

        # Fertile territory bonus: +2 food per tick total if faction has any
        # fertile (forest/plains/coast) territory (capped at member count * 8)
        _FERTILE = {'forest', 'plains', 'coast'}
        _reserve_cap = len(faction.members) * 8
        if faction.food_reserve < _reserve_cap:
            has_fertile = any(world[tr][tc]['biome'] in _FERTILE
                              for (tr, tc) in faction.territory)
            if has_fertile:
                faction.food_reserve = min(_reserve_cap,
                                           faction.food_reserve + 2)

        # trade_builds_bonds bonus: each member gathers +1 extra food
        if 'trade_builds_bonds' in faction.shared_beliefs:
            for m in faction.members:
                chunk_res = world[m.r][m.c]['resources']
                bonus = min(1, chunk_res['food'])
                chunk_res['food']        -= bonus
                m.inventory['food']      += bonus

        # Distribute from reserve when any member is getting hungry
        for m in sorted(faction.members, key=lambda p: p.hunger, reverse=True):
            if m.hunger > 20 and faction.food_reserve >= 1:
                m.inventory['food']  += 1
                faction.food_reserve -= 1

        # Extra trust (+1 on top of the base +1 from do_tick)
        for a, b in combinations(faction.members, 2):
            a.trust[b.name] = a.trust.get(b.name, 0) + 1
            b.trust[a.name] = b.trust.get(a.name, 0) + 1

        # Territory pull: nudge members who drifted > 3 from any territory cell
        if faction.territory:
            for m in faction.members:
                near = min(faction.territory,
                           key=lambda pos: abs(pos[0]-m.r)+abs(pos[1]-m.c))
                dist = abs(near[0]-m.r) + abs(near[1]-m.c)
                if dist > 3:
                    # Step one cell toward the nearest territory chunk
                    dr = 0 if near[0] == m.r else (1 if near[0] > m.r else -1)
                    dc = 0 if near[1] == m.c else (1 if near[1] > m.c else -1)
                    nr, nc = m.r + dr, m.c + dc
                    if 0 <= nr < GRID and 0 <= nc < GRID:
                        m.r, m.c = nr, nc

        faction.update_territory()

        # ── Joining: geo limit, 2+ beliefs, trust>5 with 1+, no conflict ──
        taken        = {n for f in factions for n in f.member_names()}
        faction_cors = {core_of(b) for b in faction.shared_beliefs}
        for inh in people:
            if inh.name in taken:
                continue
            # Geographic: within 3 chunks of nearest territory cell
            if faction.territory:
                min_dist = min(abs(inh.r - tr) + abs(inh.c - tc)
                               for (tr, tc) in faction.territory)
                if min_dist > 5:
                    continue
            inh_c          = inh_cores(inh)
            shared_beliefs = inh_c & faction_cors
            if not shared_beliefs:
                continue   # no ideological overlap

            # Ideological conflict check — reject before trust/count checks
            blocked = None
            for ba, bb in _IDEO_CONFLICTS:
                if ba in inh_c and bb in faction_cors:
                    blocked = (ba, bb)
                    break
                if bb in inh_c and ba in faction_cors:
                    blocked = (bb, ba)
                    break
            if blocked:
                if any(inh.trust.get(m.name, 0) > 5 for m in faction.members):
                    inh.was_rejected = True
                    already_rejected = 'trust_no_group' in inh.beliefs
                    add_belief(inh, 'trust_no_group')
                    if not already_rejected:
                        msg = (f"Tick {t:03d}: {inh.name} rejected from "
                               f"{faction.name} — conflicting beliefs")
                        event_log.append(msg)
                        print(msg)
                continue

            # Trust: >5 with at least 1 current member
            if sum(1 for m in faction.members
                   if inh.trust.get(m.name, 0) > 5) < 1:
                continue

            # Beliefs: must share 2+ core beliefs with faction
            if len(shared_beliefs) < 2:
                continue

            faction.members.append(inh)
            inh.faction = faction.name
            taken.add(inh.name)
            msg = f"Tick {t:03d}: {inh.name} joined {faction.name}"
            event_log.append(msg)
            print(msg)

        # Track faction membership duration + grant leadership belief
        for m in faction.members:
            m.faction_ticks = getattr(m, 'faction_ticks', 0) + 1
        if faction.members:
            leader = max(faction.members, key=lambda m: m.total_trust)
            add_belief(leader, 'the_wise_must_lead')

        # ── Size pressure ────────────────────────────────────────────────
        n = len(faction.members)
        if n >= 8 and 'crowded_lands_breed_conflict' not in faction.shared_beliefs:
            faction.shared_beliefs.append('crowded_lands_breed_conflict')
            msg = f"Tick {t:03d}: ⚠ {faction.name} is overcrowded — internal tensions rise"
            event_log.append(msg)
            print(msg)
        if n > 10:
            leavers = [m for m in faction.members if 'self_reliance' in m.beliefs]
            for m in leavers:
                faction.members.remove(m)
                m.faction = None
                msg = f"Tick {t:03d}: {m.name} left {faction.name} — too crowded"
                event_log.append(msg)
                print(msg)

    # ── Rivalry update ────────────────────────────────────────────────
    active = [f for f in factions if f.members]
    for fa, fb in combinations(active, 2):
        key = tuple(sorted([fa.name, fb.name]))

        # Territorial proximity: any territory cells within 2 of each other
        close = any(
            abs(pa[0]-pb[0]) + abs(pa[1]-pb[1]) <= 2
            for pa in fa.territory for pb in fb.territory
        )

        # Belief conflict (3 rival pairs)
        cores_a = {core_of(b) for b in fa.shared_beliefs}
        cores_b = {core_of(b) for b in fb.shared_beliefs}
        conflicting = any(
            (ba in cores_a and bb in cores_b) or (bb in cores_a and ba in cores_b)
            for ba, bb in RIVAL_BELIEFS
        )

        # Resource rivalry: members from both factions sharing the same chunk
        resource_clash = any(
            ma.r == mb.r and ma.c == mb.c
            for ma in fa.members for mb in fb.members
        )

        tension_gain = (2 if resource_clash else 0) + (1 if close or conflicting else 0)
        if tension_gain > 0 and t % 3 == 0:
            RIVALRIES[key] = RIVALRIES.get(key, 0) + tension_gain
            score = RIVALRIES[key]
            label = ' (HOSTILE)' if score >= 35 else ''
            print(f"⚔ Rivalry: {fa.name} vs {fb.name} — tension: {score}{label}")
            if score % 5 == 0:
                msg = (f"Tick {t:02d}: Tension rising between "
                       f"{fa.name} and {fb.name}: {score}{label}")
                event_log.append(msg)

    # ── Schism check (every 25 ticks) ────────────────────────────────────
    if t % 25 == 0:
        for faction in list(factions):   # list() since factions may grow
            if faction.members:
                _try_schism(faction, factions, t, event_log)

    # ── Solo-faction merger (every 10 ticks) ─────────────────────────────
    if t % 10 == 0:
        _merge_solo_factions(factions, t, event_log)


# ── Schism helper ─────────────────────────────────────────────────────────
def _try_schism(faction, factions, t, event_log):
    """Split a faction if a belief minority >= 30% exists."""
    if len(faction.members) < 3:
        return
    for ba, bb in _IDEO_CONFLICTS:
        side_a = [m for m in faction.members if ba in inh_cores(m)]
        side_b = [m for m in faction.members if bb in inh_cores(m)]
        if not side_a or not side_b:
            continue
        total    = len(faction.members)
        minority = side_a if len(side_a) < len(side_b) else side_b
        if len(minority) / total < 0.30:
            continue
        # ── SCHISM ────────────────────────────────────────────────────
        min_cores    = _common_cores(minority)
        existing     = {f.name for f in factions}
        seed_cores   = min_cores if min_cores else inh_cores(minority[0])
        new_name     = _faction_name(seed_cores, existing)
        new_territory= list({(m.r, m.c) for m in minority})
        new_faction  = Faction(new_name, list(minority),
                               list(min_cores), new_territory, t)
        factions.append(new_faction)
        for m in minority:
            m.faction = new_name
        gone = {m.name for m in minority}
        faction.members = [m for m in faction.members if m.name not in gone]
        key = tuple(sorted([faction.name, new_name]))
        RIVALRIES[key] = RIVALRIES.get(key, 0) + 20
        sep       = '═' * 48
        min_names = ', '.join(m.name for m in minority)
        print(f"\n{sep}")
        print(f"  ⚡ SCHISM  —  {faction.name} splits!")
        print(f"  {new_name} breaks away with: {min_names}")
        print(f"  Core disagreement: {ba}  vs  {bb}")
        print(f"  Starting tension with parent: 20")
        print(f"{sep}\n")
        msg = (f"Tick {t:03d}: ⚡ SCHISM — {new_name} breaks from "
               f"{faction.name} ({ba} vs {bb})")
        event_log.append(msg)
        return   # one schism per faction per check


# ── Solo-faction merger ──────────────────────────────────────────────────
def _merge_solo_factions(factions, t, event_log):
    """Auto-merge adjacent solo factions that share trust > 5 and 1 belief."""
    solos  = [f for f in factions if len(f.members) == 1 and f.members]
    merged = set()
    for i, fa in enumerate(solos):
        if fa.name in merged:
            continue
        ma = fa.members[0]
        for fb in solos[i + 1:]:
            if fb.name in merged:
                continue
            mb = fb.members[0]
            # Must be within 4 steps of each other
            if abs(ma.r - mb.r) + abs(ma.c - mb.c) > 4:
                continue
            # Mutual trust required
            if ma.trust.get(mb.name, 0) <= 5 or mb.trust.get(ma.name, 0) <= 5:
                continue
            # At least one shared belief
            if not (set(fa.shared_beliefs) & set(fb.shared_beliefs)):
                continue
            # Older faction absorbs the newer one
            keeper, donor = (
                (fa, fb) if fa.founded_tick <= fb.founded_tick else (fb, fa)
            )
            donor_m       = donor.members[0]
            donor.members = []
            keeper.members.append(donor_m)
            donor_m.faction = keeper.name
            keeper.update_territory()
            merged.add(donor.name)
            msg = (f"Tick {t:03d}: \U0001f91d FACTION MERGE \u2014 "
                   f"{donor_m.name} ({donor.name}) joins {keeper.name}")
            event_log.append(msg)
            print(msg)
            break


# ── Summary print (every 25 ticks) ────────────────────────────────────────
def print_faction_summary(factions, t):
    sep    = '─' * 48
    active = [f for f in factions if f.members]
    print(f"\n{sep}")
    print(f"FACTION SUMMARY  — Tick {t:03d}  ({len(active)} active)")
    print(sep)
    if not active:
        print("  (no active factions)")
    for f in active:
        b_str = ', '.join(LABELS.get(b, b) for b in f.shared_beliefs)
        t_str = '  '.join(str(p) for p in f.territory)
        tech_list = sorted(getattr(f, 'techs', set()))
        tech_str  = ', '.join(tech_list) if tech_list else 'none'
        ar = getattr(f, 'active_research', None)
        if ar and not ar.get('paused'):
            tech_str += f'  [→ {ar["tech"]} {ar["progress"]}t]'
        elif ar and ar.get('paused'):
            tech_str += f'  [⏸ {ar["tech"]} {ar["progress"]}t, paused]'
        mlist = ', '.join(m.name for m in f.members)
        print(f"  {f.name}  (founded tick {f.founded_tick:03d})")
        print(f"    Members  : {mlist}")
        print(f"    Beliefs  : {b_str}")
        print(f"    Techs    : {tech_str}")
        print(f"    Territory: {t_str}")
        print(f"    Reserve  : {f.food_reserve:.1f} food")
        import diplomacy as _dip
        rep     = _dip.get_rep(f.name)
        rep_lbl = _dip.rep_label(rep)
        print(f"    Reputation: {rep:+d} ({rep_lbl})")
    print(sep + "\n")
