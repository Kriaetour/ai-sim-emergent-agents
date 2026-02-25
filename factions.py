import random
from itertools import combinations
from world   import (world, grid_move,
                     Settlement, settlement_register, settlement_unregister,
                     get_settlement_at,
                     SETTLEMENT_POP_MIN, SETTLEMENT_TICKS_STABLE,
                     SETTLEMENT_STORAGE_CAP,
                     coast_score, tile_is_sea)
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
        # ── Proto-city / settlement tracking ──────────────────────────────
        self.is_settled     = False   # True once faction is a proto-city
        self.settled_since  = 0       # tick when is_settled first became True
        self._cog_snapshots: list = [] # rolling (r_avg, c_avg) tuples (capped 50)
        # ── Permanent Settlement (Towns, Walls, Storage) ───────────────────
        self.settlement     = None    # Settlement object once founded, else None
        self.settled_ticks  = 0       # ticks elapsed while is_settled == True

    def member_names(self):
        return {m.name for m in self.members}

    def update_territory(self):
        self.territory = list({(m.r, m.c) for m in self.members})

    def remove_dead(self, dead_names):
        self.members = [m for m in self.members if m.name not in dead_names]

    @property
    def center_of_gravity(self) -> tuple:
        """Average (r, c) of all current members; returns (0.0, 0.0) when empty."""
        if not self.members:
            return (0.0, 0.0)
        return (
            sum(m.r for m in self.members) / len(self.members),
            sum(m.c for m in self.members) / len(self.members),
        )

    def update_settlement_status(self, t: int) -> None:
        """Append current COG to rolling history and test the 5×5 / 50-tick rule.

        A faction is 'Settled' (proto-city) if, over the last 50 ticks, all
        recorded centre-of-gravity snapshots fit within a 5×5 tile bounding box
        (max spread <= 4 in each axis).
        """
        if not self.members:
            return
        self._cog_snapshots.append(self.center_of_gravity)
        if len(self._cog_snapshots) > 50:
            self._cog_snapshots = self._cog_snapshots[-50:]
        if len(self._cog_snapshots) < 50:
            return  # not enough history yet
        rs = [s[0] for s in self._cog_snapshots]
        cs = [s[1] for s in self._cog_snapshots]
        in_zone = (max(rs) - min(rs) <= 4) and (max(cs) - min(cs) <= 4)
        if in_zone and not self.is_settled:
            self.is_settled    = True
            self.settled_since = t
            self.settled_ticks = 0
        elif in_zone and self.is_settled:
            self.settled_ticks += 1
        elif not in_zone:
            self.is_settled    = False
            self.settled_ticks = 0


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

        # Settlement stability bonus: settled proto-cities get +5 internal trust
        faction.update_settlement_status(t)
        if faction.is_settled:
            for a, b in combinations(faction.members, 2):
                a.trust[b.name] = a.trust.get(b.name, 0) + 5
                b.trust[a.name] = b.trust.get(a.name, 0) + 5

        # ── Settlement storage: deposit surplus food, withdraw for the hungry ──
        _s = faction.settlement
        if _s and _s.status == 'active':
            # Deposit: members with >5 food contribute surplus to the common store
            for m in faction.members:
                if m.inventory['food'] > 5 and _s.storage_buffer < SETTLEMENT_STORAGE_CAP:
                    deposit = min(
                        int(m.inventory['food'] - 5),
                        int(SETTLEMENT_STORAGE_CAP - _s.storage_buffer),
                    )
                    m.inventory['food']  -= deposit
                    _s.storage_buffer    += deposit
            # Withdraw: hungriest members inside the zone get one food each
            for m in sorted(faction.members, key=lambda p: p.hunger, reverse=True):
                if (_s.in_zone(m.r, m.c)
                        and m.hunger > 20
                        and _s.storage_buffer >= 1):
                    m.inventory['food']  += 1
                    _s.storage_buffer    -= 1

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
                    if 0 <= nr < len(world) and 0 <= nc < len(world):
                        grid_move(m, nr, nc)   # keeps spatial partition consistent
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

    # ── Diplomatic merge for small mutually-trusting factions (every 50 ticks)
    if t % 50 == 0:
        _try_diplomatic_merge(factions, t, event_log)

    # ── Reputation-based merger check (every 25 ticks) ───────────────────
    if t % 25 == 0:
        check_for_merger(factions, t, event_log)

    # ── Settlement founding ───────────────────────────────────────────────
    for faction in factions:
        if faction.members and faction.settlement is None:
            _try_found_settlement(faction, t, event_log)

    # ── Settlement abandonment ────────────────────────────────────────────
    for faction in factions:
        _s = faction.settlement
        if _s and _s.status == 'active' and not faction.members:
            settlement_unregister(_s)
            _s.status         = 'abandoned'
            _s.storage_buffer = 0.0
            msg = (f"Tick {t:04d}: \U0001f3da ABANDONED — {faction.name}'s settlement "
                   f"at ({_s.r},{_s.c}) lies empty")
            event_log.append(msg)
            print(msg)

    # ── Settlement reclaim ────────────────────────────────────────────────
    for faction in factions:
        if not faction.members or faction.settlement is not None:
            continue
        cog = faction.center_of_gravity
        cr, cc = round(cog[0]), round(cog[1])
        _s = get_settlement_at(cr, cc)
        if _s and _s.status == 'abandoned':
            inside = sum(1 for m in faction.members if _s.in_zone(m.r, m.c))
            if inside >= min(3, len(faction.members)):
                _s.owner_faction  = faction.name
                _s.status         = 'active'
                _s.storage_buffer = 0.0
                settlement_register(_s)
                faction.settlement = _s
                msg = (f"Tick {t:04d}: \U0001f3d7 RECLAIMED — {faction.name} "
                       f"occupies the settlement at ({_s.r},{_s.c})")
                event_log.append(msg)
                print(msg)


# ── Settlement founding helper ─────────────────────────────────────────────
def _try_found_settlement(faction, t, event_log):
    """Found a permanent settlement when stability *and* size thresholds are met.

    Conditions (all required):
      • faction.is_settled == True  (COG in 5×5 box for 50+ ticks)
      • faction.settled_ticks >= SETTLEMENT_TICKS_STABLE  (50 additional ticks)
      • len(faction.members) >= SETTLEMENT_POP_MIN
    """
    if not faction.is_settled:
        return
    if faction.settled_ticks < SETTLEMENT_TICKS_STABLE:
        return
    if len(faction.members) < SETTLEMENT_POP_MIN:
        return
    cog  = faction.center_of_gravity
    r    = max(0, min(round(cog[0]), len(world) - 1))
    c    = max(0, min(round(cog[1]), len(world[0]) - 1))

    # ── Port bias: snap anchor to coastline for merchant/seafaring factions ──
    _PORT_BELIEFS = {'trade_builds_bonds', 'the_sea_provides'}
    _shared_cores = {core_of(b) for b in faction.shared_beliefs}
    if _PORT_BELIEFS & _shared_cores:
        rows = len(world)
        cols = len(world[0]) if rows else 0
        best_cs, best_r, best_c = -1, r, c
        for dr in range(-4, 5):
            for dc in range(-4, 5):
                nr, nc = r + dr, c + dc
                if not (0 <= nr < rows and 0 <= nc < cols):
                    continue
                if tile_is_sea(nr, nc):
                    continue   # can't build on water
                cs = coast_score(nr, nc)
                if cs > best_cs:
                    best_cs, best_r, best_c = cs, nr, nc
        if best_cs > 0:
            r, c = best_r, best_c   # snap anchor to coastline

    # Guard: never anchor on sea
    if tile_is_sea(r, c):
        return

    s    = Settlement(faction.name, r, c, t)
    settlement_register(s)
    faction.settlement = s
    port_tag = ' [⛵ PORT]' if coast_score(r, c) > 0 else ''
    msg = (f"Tick {t:04d}: 🏰 SETTLEMENT FOUNDED — {faction.name} "
           f"raises walls at ({r},{c}){port_tag}  "
           f"[housing cap {s.housing_capacity}]")
    event_log.append(msg)
    print(msg)


# ── Schism helper ─────────────────────────────────────────────────────────
def _try_schism(faction, factions, t, event_log):
    """Split a faction if a belief minority >= threshold exists.

    Settled (proto-city) factions require a larger minority — 50% vs 30% —
    reflecting the social cohesion of an established settlement.
    """
    if len(faction.members) < 3:
        return
    # Settled factions are harder to split — higher minority threshold
    schism_threshold = 0.50 if faction.is_settled else 0.30
    for ba, bb in _IDEO_CONFLICTS:
        side_a = [m for m in faction.members if ba in inh_cores(m)]
        side_b = [m for m in faction.members if bb in inh_cores(m)]
        if not side_a or not side_b:
            continue
        total    = len(faction.members)
        minority = side_a if len(side_a) < len(side_b) else side_b
        if len(minority) / total < schism_threshold:
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

# ── Diplomatic faction merge ────────────────────────────────────────────────
def _try_diplomatic_merge(factions, t, event_log):
    """Merge pairs of small factions with high mutual cross-faction trust.

    A merge fires when ALL conditions hold for a pair (fa, fb):
      • Both have < MERGE_SIZE_CAP members.
      • No ideological conflict between their shared beliefs.
      • No active rivalry above MERGE_RIVALRY_MAX.
      • Average mutual trust across all cross-faction member pairs
        >= MERGE_TRUST_MIN.

    The smaller faction is absorbed into the larger; ties resolved by
    seniority (older absorbs newer).  At most one merge per faction per
    call to prevent cascade.
    """
    MERGE_SIZE_CAP    = 5
    MERGE_TRUST_MIN   = 8
    MERGE_RIVALRY_MAX = 20

    active = [f for f in factions if f.members]
    merged = set()
    for fa, fb in combinations(active, 2):
        if fa.name in merged or fb.name in merged:
            continue
        if len(fa.members) >= MERGE_SIZE_CAP or len(fb.members) >= MERGE_SIZE_CAP:
            continue

        # Ideological conflict blocks merge
        cores_a = {core_of(b) for b in fa.shared_beliefs}
        cores_b = {core_of(b) for b in fb.shared_beliefs}
        if any(
            (ba in cores_a and bb in cores_b) or (bb in cores_a and ba in cores_b)
            for ba, bb in _IDEO_CONFLICTS
        ):
            continue

        # Active rivalry too high
        key = tuple(sorted([fa.name, fb.name]))
        if RIVALRIES.get(key, 0) > MERGE_RIVALRY_MAX:
            continue

        # Average mutual trust across all cross-faction member pairs
        pairs = [(ma, mb) for ma in fa.members for mb in fb.members]
        if not pairs:
            continue
        avg_trust = sum(
            ma.trust.get(mb.name, 0) + mb.trust.get(ma.name, 0)
            for ma, mb in pairs
        ) / (2 * len(pairs))
        if avg_trust < MERGE_TRUST_MIN:
            continue

        # Smaller absorbed into larger; ties → older absorbs newer
        if len(fb.members) > len(fa.members):
            keeper, donor = fb, fa
        elif len(fa.members) > len(fb.members):
            keeper, donor = fa, fb
        else:
            keeper, donor = (fa, fb) if fa.founded_tick <= fb.founded_tick else (fb, fa)

        donor_members = list(donor.members)
        donor.members = []
        for m in donor_members:
            m.faction = keeper.name
            keeper.members.append(m)
        # Union of shared beliefs
        for b in donor.shared_beliefs:
            if b not in keeper.shared_beliefs:
                keeper.shared_beliefs.append(b)
        keeper.update_territory()
        merged.add(donor.name)

        names_str = ', '.join(m.name for m in donor_members)
        msg = (f"Tick {t:04d}: \U0001f91d DIPLOMATIC MERGE — {donor.name} "
               f"unites with {keeper.name} ({names_str})")
        event_log.append(msg)
        print(msg)


# ── Reputation-based faction merger ─────────────────────────────────────
# Prestige adjectives: if the surviving faction name starts with one of these,
# its identity is dominant and we generate a "The <Adj> Union/Alliance" name.
_PRESTIGE_ADJS = {
    'Merchant', 'Wise', 'Enduring', 'Steadfast', 'Iron', 'Faithful',
    'Guided', 'Tidal', 'Stone', 'Elder', 'Trading', 'Open',
}
_PRESTIGE_NOUNS = ['Union', 'Alliance', 'Accord', 'League', 'Pact']

MERGER_REP_SUM_MIN   = 8    # sum of both factions' global rep to qualify
                             # (equivalent to "both at least trusted", maps the
                             # design intent of "+20" onto the -10..+10 scale)
MERGER_POP_THRESHOLD = 50   # combined member count must be below this
MERGER_RIVALRY_MAX   = 15   # active rivalry between them must be below this


def _merger_name(keeper, donor, existing_names: set) -> str:
    """Derive the post-merger name, preserving any prestige lineage."""
    # Pull the first word after "The " from the keeper's name
    words = keeper.name.replace('The ', '', 1).split()
    adj   = words[0] if words else ''
    if adj in _PRESTIGE_ADJS:
        for noun in _PRESTIGE_NOUNS:
            candidate = f"The {adj} {noun}"
            if candidate not in existing_names:
                return candidate
    # Fallback: re-derive from the union of shared beliefs
    all_cores  = list(dict.fromkeys(keeper.shared_beliefs + donor.shared_beliefs))
    new_name   = _faction_name(set(all_cores), existing_names)
    return new_name


def check_for_merger(factions, t, event_log):
    """Merge faction pairs that meet the diplomatic reputation threshold.

    Criteria (all must hold):
      • Both factions' combined global reputation sum >= MERGER_REP_SUM_MIN.
      • Combined member count < MERGER_POP_THRESHOLD.
      • Active rivalry between them < MERGER_RIVALRY_MAX.
      • No ideological conflict between their shared beliefs.

    Consolidation rules:
      • Smaller faction is absorbed into the larger/older one (the 'keeper').
      • All donor members join the keeper; beliefs are unioned.
      • Each donor member seeds trust toward every keeper member at half the
        keeper member's existing trust value toward them (and vice versa),
        simulating a gradual diplomatic handshake rather than instant bonding.
      • The merged faction's name honours any prestige lineage (see _merger_name).
    """
    import diplomacy as _dip

    active = [f for f in factions if f.members]
    merged = set()
    for fa, fb in combinations(active, 2):
        if fa.name in merged or fb.name in merged:
            continue

        # Reputation gate: sum of individual global reputations
        if _dip.get_rep(fa.name) + _dip.get_rep(fb.name) < MERGER_REP_SUM_MIN:
            continue

        # Combined population gate
        if len(fa.members) + len(fb.members) >= MERGER_POP_THRESHOLD:
            continue

        # Active rivalry gate
        key = tuple(sorted([fa.name, fb.name]))
        if RIVALRIES.get(key, 0) >= MERGER_RIVALRY_MAX:
            continue

        # Ideological conflict blocks merge
        cores_a = {core_of(b) for b in fa.shared_beliefs}
        cores_b = {core_of(b) for b in fb.shared_beliefs}
        if any(
            (ba in cores_a and bb in cores_b) or (bb in cores_a and ba in cores_b)
            for ba, bb in _IDEO_CONFLICTS
        ):
            continue

        # Determine keeper (larger wins; tie → older/lower founded_tick)
        if len(fb.members) > len(fa.members):
            keeper, donor = fb, fa
        elif len(fa.members) > len(fb.members):
            keeper, donor = fa, fb
        else:
            keeper, donor = (fa, fb) if fa.founded_tick <= fb.founded_tick else (fb, fa)

        existing_names = {f.name for f in factions}
        new_name       = _merger_name(keeper, donor, existing_names)

        # Half-trust seed: cross-faction member pairs
        for km in keeper.members:
            for dm in donor.members:
                half_k = km.trust.get(dm.name, 0) // 2
                half_d = dm.trust.get(km.name, 0) // 2
                km.trust[dm.name] = max(km.trust.get(dm.name, 0), half_d)
                dm.trust[km.name] = max(dm.trust.get(km.name, 0), half_k)

        # Transfer donor members
        donor_members = list(donor.members)
        donor.members = []
        for m in donor_members:
            m.faction = new_name
            keeper.members.append(m)

        # Union of shared beliefs
        for b in donor.shared_beliefs:
            if b not in keeper.shared_beliefs:
                keeper.shared_beliefs.append(b)

        # Rename if prestige name changed
        old_keeper_name = keeper.name
        if new_name != old_keeper_name:
            keeper.name = new_name
            for m in keeper.members:
                m.faction = new_name
            # Transfer any reputation the old keeper had
            old_rep = _dip.get_rep(old_keeper_name)
            if old_rep:
                _dip.adjust_rep(new_name, old_rep)

        keeper.update_territory()
        merged.add(donor.name)

        names_str = ', '.join(m.name for m in donor_members)
        msg = (f"Tick {t:04d}: 🏛 MERGER — {donor.name} absorbed into "
               f"{keeper.name} ({names_str}) "
               f"[rep {_dip.get_rep(fa.name):+d}/{_dip.get_rep(fb.name):+d}]")
        event_log.append(msg)
        print(msg)


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
        cog = f.center_of_gravity
        settled_str = (f"  🏘 SETTLED since tick {f.settled_since}"
                       if f.is_settled else "")
        print(f"    Location : COG ({cog[0]:.1f}, {cog[1]:.1f}){settled_str}")
    print(sep + "\n")
