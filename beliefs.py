import random
from world import world

# ── Belief catalogue ───────────────────────────────────────────────────────
LABELS = {
    'endurance_rewarded':         'endurance rewarded',
    'lucky_survivor':             'lucky survivor',
    'loss_teaches_caution':       'loss teaches caution',
    'community_sustains':         'community sustains',
    'self_reliance':              'self reliance',
    'death_is_near':              'death is near',
    'migration_brings_hope':      'migration brings hope',
    'the_land_can_fail':          'the land can fail',
    'trade_builds_bonds':         'trade builds bonds',
    'crowded_lands_breed_conflict':'crowded lands breed conflict',
    'suffering_forges_strength':  'suffering forges strength',
    'the_strong_take':            'the strong take',
    'the_wilds_provide':          'the wilds provide',
    # Location beliefs
    'the_forest_shelters':        'the forest shelters',
    'stone_stands_eternal':       'stone stands eternal',
    'the_sea_provides':           'the sea provides',
    'desert_forges_the_worthy':   'desert forges the worthy',
    # Social beliefs
    'loyalty_above_all':          'loyalty above all',
    'trust_no_group':             'trust no group',
    'the_wise_must_lead':         'the wise must lead',
    # Scarcity beliefs
    'hunger_teaches_truth':       'hunger teaches truth',
    'fortune_favors_the_prepared':'fortune favors the prepared',
    # Combat beliefs
    'war_is_costly':              'war is costly',
    'victory_proves_strength':    'victory proves strength',
    'sacrifice_has_meaning':      'sacrifice has meaning',
    'battle_forges_bonds':        'battle forges bonds',
    'never_again':                'never again',
}
MAX_BELIEFS = 8

# ── Helpers ────────────────────────────────────────────────────────────────
def core_of(belief):
    """Strip heard_from prefix, return canonical key."""
    return belief.split(':')[-1] if ':' in belief else belief

def inh_cores(inh):
    return {core_of(b) for b in inh.beliefs}

def add_belief(inh, key):
    if key in inh_cores(inh):
        return
    if len(inh.beliefs) >= MAX_BELIEFS:
        inh.beliefs.pop(0)
    inh.beliefs.append(key)

# ── Per-tick belief assignment ─────────────────────────────────────────────
def assign_beliefs(people, deaths_this_tick, winter_just_ended,
                   prev_positions, t, event_log):
    dead_names   = {d.name for d in deaths_this_tick}
    death_chunks = {(d.r, d.c) for d in deaths_this_tick}

    for inh in people:
        pos = (inh.r, inh.c)

        # ── Seasonal survival
        if winter_just_ended:
            if inh.inventory['food'] > 0:
                add_belief(inh, 'endurance_rewarded')
            else:
                add_belief(inh, 'lucky_survivor')

        # ── Trusted friend died
        for d in deaths_this_tick:
            if inh.trust.get(d.name, 0) > 5:
                add_belief(inh, 'loss_teaches_caution')

        # ── Witnessed death in same chunk
        if pos in death_chunks:
            add_belief(inh, 'death_is_near')

        # ── Chunk food depleted
        if world[inh.r][inh.c]['resources']['food'] == 0:
            add_belief(inh, 'the_land_can_fail')

        # ── Migration brings hope: moved AND now holds food
        prev = prev_positions.get(inh.name)
        if prev and prev != pos and inh.inventory['food'] > 0:
            add_belief(inh, 'migration_brings_hope')

        # ── Community sustains: 3+ living neighbours here, none died
        neighbours = [p for p in people if p is not inh and p.r == inh.r and p.c == inh.c]
        if len(neighbours) >= 3:
            if not any(n.name in dead_names for n in neighbours):
                add_belief(inh, 'community_sustains')

        # ── Self reliance: truly alone
        if not neighbours:
            add_belief(inh, 'self_reliance')

        # ── Traded 3+ times: trade builds bonds
        if getattr(inh, 'trade_count', 0) >= 3:
            add_belief(inh, 'trade_builds_bonds')

        # ── Crowded chunk (3+ others present)
        if len(neighbours) >= 3:
            add_belief(inh, 'crowded_lands_breed_conflict')

        # ── Health dropped below 50 this tick
        prev_hp = getattr(inh, 'prev_health', inh.health)
        if prev_hp >= 50 and inh.health < 50:
            add_belief(inh, 'suffering_forges_strength')

        # ── Was pushed from chunk by crowd control
        if getattr(inh, 'was_pushed', False):
            add_belief(inh, 'the_strong_take')

        # ── Moved to a new chunk that still has food (wilds provide)
        prev = prev_positions.get(inh.name)
        if prev and prev != pos and world[inh.r][inh.c]['resources']['food'] >= 3:
            add_belief(inh, 'the_wilds_provide')

        # ── Location-based beliefs (from accumulated biome_ticks)
        bticks = getattr(inh, 'biome_ticks', {})
        if bticks.get('forest', 0) >= 10:
            add_belief(inh, 'the_forest_shelters')
        if bticks.get('mountains', 0) >= 5:
            add_belief(inh, 'stone_stands_eternal')
        if bticks.get('coast', 0) >= 5:
            add_belief(inh, 'the_sea_provides')
        if bticks.get('desert', 0) >= 5 and inh.health > 30:
            add_belief(inh, 'desert_forges_the_worthy')

        # ── Social beliefs
        if getattr(inh, 'faction_ticks', 0) >= 20:
            add_belief(inh, 'loyalty_above_all')
        if getattr(inh, 'was_rejected', False):
            add_belief(inh, 'trust_no_group')

        # ── Scarcity beliefs
        if getattr(inh, 'zero_food_ticks', 0) >= 3:
            add_belief(inh, 'hunger_teaches_truth')
        # Hoarding: has 10+ food while a neighbour has none
        if inh.inventory['food'] >= 10 and any(
                n.inventory['food'] == 0 for n in neighbours):
            add_belief(inh, 'fortune_favors_the_prepared')

# ── Belief sharing ─────────────────────────────────────────────────────────
def share_beliefs(people, t, event_log):
    for inh in people:
        neighbours = [p for p in people
                      if p is not inh and p.r == inh.r and p.c == inh.c]
        for nb in neighbours:
            if inh.trust.get(nb.name, 0) <= 10:
                continue
            if not inh.beliefs:
                continue
            if random.random() >= 0.5:
                continue
            belief   = random.choice(inh.beliefs)
            core     = core_of(belief)
            tagged   = f'heard_from_{inh.name}:{core}'
            if core not in inh_cores(nb):
                if len(nb.beliefs) >= MAX_BELIEFS:
                    nb.beliefs.pop(0)
                nb.beliefs.append(tagged)
                label = LABELS.get(core, core)
                msg   = f"Tick {t:02d}: {inh.name} shared '{label}' with {nb.name}"
                event_log.append(msg)
