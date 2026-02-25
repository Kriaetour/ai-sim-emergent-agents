"""
combat.py — Layer 5: war declarations, multi-tick battles, alliances,
            war resolution, tribute, and legends.

Call order each tick (after economy_tick):
    combat_tick(people, factions, all_dead, t, event_log)

End of run:
    combat_report()
"""
import sys, random
sys.stdout.reconfigure(encoding='utf-8')

from world    import grid_remove, get_settlement_at
from beliefs  import add_belief, inh_cores
from factions import RIVALRIES
import technology
import diplomacy
import religion

# ══════════════════════════════════════════════════════════════════════════
# Module-level state
# ══════════════════════════════════════════════════════════════════════════

active_wars:  list = []   # War objects currently running
war_history:  list = []   # resolved War objects

# Alliances: frozenset({name_a, name_b}) → 'allied' | 'hostile'
_alliances:   dict = {}

WAR_THRESHOLD      = 200   # RIVALRIES tension required to declare war
MAX_WAR_TICKS      = 40    # exhaustion after this many ticks without resolution
MIN_WAR_TICKS      = 5     # no surrender/ceasefire before this many ticks
TRIBUTE_TICKS      = 20    # how many post-war ticks the loser pays tribute
TRIBUTE_RATE       = 0.30  # fraction of loser food_reserve transferred per tick


# ══════════════════════════════════════════════════════════════════════════
# War class
# ══════════════════════════════════════════════════════════════════════════

class War:
    def __init__(self, attacker, defender, t, cause):
        self.attacker       = attacker
        self.defender       = defender
        self.cause          = cause
        self.started_tick   = t
        self.tick_count     = 0
        self.pre_war_a      = len(attacker.members)
        self.pre_war_d      = len(defender.members)
        self.allied_with_a  = []   # Faction objects fighting for attacker
        self.allied_with_d  = []   # Faction objects fighting for defender
        self.ended          = False
        self.outcome        = None
        # Tribute tracking (set when war ends)
        self.tribute_remaining = 0
        self.tribute_payer     = None
        self.tribute_receiver  = None

    def all_attackers(self):
        return [self.attacker] + self.allied_with_a

    def all_defenders(self):
        return [self.defender] + self.allied_with_d

    def side_of(self, faction):
        if faction in self.all_attackers():
            return 'attacker'
        if faction in self.all_defenders():
            return 'defender'
        return None


# ══════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════

def is_at_war(name_a: str, name_b: str) -> bool:
    """Return True if these two factions are currently in an active war."""
    for w in active_wars:
        a_names = {f.name for f in w.all_attackers()}
        d_names = {f.name for f in w.all_defenders()}
        if (name_a in a_names and name_b in d_names) or \
           (name_b in a_names and name_a in d_names):
            return True
    return False


def are_allied(name_a: str, name_b: str) -> bool:
    """Return True if these two factions are formally allied."""
    return _alliances.get(frozenset([name_a, name_b])) == 'allied'


def combat_tick(people: list, factions: list, all_dead: list,
                t: int, event_log: list) -> None:
    """Main entry point — run all combat logic for this tick."""
    active = [f for f in factions if f.members]

    # 1. Check for new war declarations
    _check_war_declarations(active, t, event_log)

    # 2. Process each active war
    for w in list(active_wars):
        if w.ended:
            continue
        w.tick_count += 1
        _check_alliances(w, active, t, event_log)
        _resolve_combat_tick(w, t, event_log, people, all_dead, factions)
        outcome = _check_war_resolution(w, t)
        if outcome:
            _end_war(w, outcome, t, event_log, factions)

    # 3. Collect completed wars
    just_ended = [w for w in active_wars if w.ended]
    for w in just_ended:
        active_wars.remove(w)
        war_history.append(w)

    # 4. Tribute payments for past wars
    _process_tribute(factions, t, event_log)


# ══════════════════════════════════════════════════════════════════════════
# War declaration
# ══════════════════════════════════════════════════════════════════════════

def _check_war_declarations(active: list, t: int, event_log: list) -> None:
    already_at_war: set = set()
    for w in active_wars:
        for f in w.all_attackers() + w.all_defenders():
            already_at_war.add(f.name)

    for key, tension in list(RIVALRIES.items()):
        if tension < WAR_THRESHOLD:
            continue
        na, nb = key
        fa = next((f for f in active if f.name == na), None)
        fb = next((f for f in active if f.name == nb), None)
        if not fa or not fb:
            continue
        if fa.name in already_at_war or fb.name in already_at_war:
            continue

        # Belief modifiers on threshold
        a_cores = set()
        for m in fa.members:
            a_cores.update(inh_cores(m))
        b_cores = set()
        for m in fb.members:
            b_cores.update(inh_cores(m))

        # Pacifist beliefs raise effective threshold
        effective = WAR_THRESHOLD
        if 'war_is_costly' in a_cores or 'never_again' in a_cores:
            effective += 50
        # Aggressive beliefs lower effective threshold for attacker
        if 'the_strong_take' in a_cores or 'victory_proves_strength' in a_cores:
            effective -= 50
        # Tech fear: defender's military tech discourages attack
        if 'weapons'   in getattr(fb, 'techs', set()):
            effective += 60    # weapons defender is dangerous but wars still happen
        elif 'metalwork' in getattr(fb, 'techs', set()):
            effective += 40    # metalwork defender is a risk
        # Attacker's weapons emboldens aggression
        if 'weapons' in getattr(fa, 'techs', set()):
            effective -= 40

        if tension < effective:
            continue

        # Council vote: factions with 3+ members must vote to go to war
        if len(fa.members) >= 3:
            passed, _ = diplomacy.council_vote(fa, 'war', {'enemy': fb.name})
            if not passed and tension < effective + 80:
                continue   # council overruled — needs higher tension to override

        # fa attacks fb
        diplomacy.adjust_rep(fa.name, -1, 'war_start', t)
        cause = _war_cause(fa, fb, key)
        w = War(fa, fb, t, cause)
        active_wars.append(w)
        already_at_war.add(fa.name)
        already_at_war.add(fb.name)

        # War spikes tension but caps it — no more trade tension accumulation
        RIVALRIES[key] = min(RIVALRIES[key], 300)

        msg = (f"Tick {t:03d}: ⚔ WAR DECLARED — {fa.name} vs {fb.name}  "
               f"[cause: {cause}]  (tension {tension})")
        event_log.append(msg)
        print(msg)

        # Mutual defense: auto-recruit any treaty-bound allies of the defender
        md_allies = diplomacy.mutual_defense_triggered(fa.name, fb.name,
                                                       t, event_log)
        for ally_name in md_allies:
            ally_f = next((f for f in active if f.name == ally_name), None)
            if ally_f and ally_name not in already_at_war:
                w.allied_with_d.append(ally_f)
                already_at_war.add(ally_name)

        _request_alliances(w, active, t, event_log)


def _war_cause(fa, fb, key) -> str:
    """Determine the dominant cause of this war."""
    tension = RIVALRIES.get(key, 0)
    # Check shared territory overlap
    shared = set(fa.territory) & set(fb.territory)
    a_cores = set()
    for m in fa.members:
        a_cores.update(inh_cores(m))
    if shared and 'crowded_lands_breed_conflict' in a_cores:
        return 'territory dispute'
    if 'the_strong_take' in a_cores:
        return 'conquest'
    if tension >= 250:
        return 'deep rivalry'
    return 'sustained hostility'


# ══════════════════════════════════════════════════════════════════════════
# Alliance system
# ══════════════════════════════════════════════════════════════════════════

def _request_alliances(war: War, active: list, t: int, event_log: list) -> None:
    """Recruit allies for BOTH sides at the moment of war declaration."""
    import economy as _eco   # lazy import — avoids circular dependency

    engaged = set(f.name for f in war.all_attackers() + war.all_defenders())

    # ── Attacker ally recruitment ──────────────────────────────────────────
    for f in active:
        if f.name in engaged:
            continue
        def_key        = tuple(sorted([f.name, war.defender.name]))
        tension_vs_def = RIVALRIES.get(def_key, 0)

        f_cores = set()
        for m in f.members:
            f_cores.update(inh_cores(m))

        # Was this faction raided by the defender?
        was_raided = any(
            victim == f.name and raider == war.defender.name
            for _, raider, victim, _ in _eco.raid_log
        )

        chance = 0.0
        reason = None
        if tension_vs_def > 30:
            chance = max(chance, 0.60)
            reason = 'joining the fray'
        if was_raided:
            chance = max(chance, 0.70)
            reason = 'seeking vengeance'
        if 'the_strong_take' in f_cores:
            chance = max(chance, 0.40)
            if reason is None:
                reason = 'drawn by conquest'

        if chance > 0 and random.random() < chance:
            war.allied_with_a.append(f)
            engaged.add(f.name)
            msg = (f"Tick {t:03d}: ⚔ {f.name} joins {war.attacker.name} "
                   f"{reason}!")
            event_log.append(msg)
            print(msg)
            _alliances[frozenset([f.name, war.attacker.name])] = 'allied'

    # ── Defender ally recruitment (call for help when outmatched) ──────────
    n_a = sum(len(f.members) for f in war.all_attackers())
    n_d = sum(len(f.members) for f in war.all_defenders())
    if n_d >= n_a and n_d > 0:
        return   # defender not outmatched

    msg_call = (f"Tick {t:03d}: 📣 {war.defender.name} calls for allies "
                f"against {war.attacker.name}!")
    event_log.append(msg_call)
    print(msg_call)

    for f in active:
        if f.name in engaged:
            continue
        atk_key        = tuple(sorted([f.name, war.attacker.name]))
        tension_vs_atk = RIVALRIES.get(atk_key, 0)
        route_key      = frozenset([f.name, war.defender.name])
        has_trade      = _eco.trade_routes.get(route_key, {}).get('active', False)

        chance = 0.0
        if tension_vs_atk > 30:
            chance = max(chance, 0.80)
        if has_trade:
            chance = max(chance, 0.50)

        if chance > 0 and random.random() < chance:
            war.allied_with_d.append(f)
            engaged.add(f.name)
            reason = 'shared enemy' if tension_vs_atk > 30 else 'trade loyalty'
            msg = (f"Tick {t:03d}: ⚔ {f.name} answers {war.defender.name}'s "
                   f"call to arms!  [{reason}]")
            event_log.append(msg)
            print(msg)
            _alliances[frozenset([f.name, war.defender.name])] = 'allied'


def _check_alliances(war: War, active: list, t: int, event_log: list) -> None:
    """Opportunistic alliances checked every 10 ticks based on tensions."""
    if war.tick_count % 10 != 0:
        return

    engaged = set(f.name for f in war.all_attackers() + war.all_defenders())

    for f in active:
        if f.name in engaged:
            continue

        def avg_tension_with(side_factions):
            scores = []
            for sf in side_factions:
                k = tuple(sorted([f.name, sf.name]))
                scores.append(RIVALRIES.get(k, 0))
            return sum(scores) / max(1, len(scores))

        t_with_atk = avg_tension_with(war.all_attackers())
        t_with_def = avg_tension_with(war.all_defenders())

        f_cores = set()
        for m in f.members:
            f_cores.update(inh_cores(m))

        alliance_bonus = 30 if 'loyalty_above_all' in f_cores else 0

        if t_with_def > 30 + alliance_bonus and t_with_atk < 15:
            war.allied_with_a.append(f)
            engaged.add(f.name)
            msg = (f"Tick {t:03d}: 🤝 ALLIANCE — {f.name} joins {war.attacker.name} "
                   f"against {war.defender.name}")
            event_log.append(msg)
            print(msg)
            _alliances[frozenset([f.name, war.attacker.name])] = 'allied'

        elif t_with_atk > 30 + alliance_bonus and t_with_def < 15:
            war.allied_with_d.append(f)
            engaged.add(f.name)
            msg = (f"Tick {t:03d}: 🛡 ALLIANCE — {f.name} joins {war.defender.name} "
                   f"against {war.attacker.name}")
            event_log.append(msg)
            print(msg)
            _alliances[frozenset([f.name, war.defender.name])] = 'allied'


# ══════════════════════════════════════════════════════════════════════════
# Combat resolution (per tick)
# ══════════════════════════════════════════════════════════════════════════

def _faction_morale(faction, defending: bool, war_history_list: list) -> float:
    """Compute morale modifier: range roughly -0.3 to +0.5."""
    morale = 0.0
    cores = set()
    for m in faction.members:
        cores.update(inh_cores(m))
    if defending:
        morale += 0.20   # home-ground bonus
    if 'endurance_rewarded' in cores:
        morale += 0.10
    if 'the_strong_take' in cores:
        morale += 0.10
    if 'suffering_forges_strength' in cores:
        morale += 0.10
    if 'loyalty_above_all' in cores:
        morale += 0.10
    # Penalty if previously lost a war
    previously_lost = any(
        w.outcome in ('surrender_a', 'exhaustion') and w.attacker == faction
        or w.outcome == 'surrender_d' and w.defender == faction
        for w in war_history_list
    )
    if previously_lost:
        morale -= 0.20
    if 'war_is_costly' in cores:
        morale -= 0.10
    if 'never_again' in cores:
        morale -= 0.15
    return morale


def _side_strength(factions_list: list, defending: bool) -> float:
    """Aggregate strength of a list of factions on one side."""
    total = 0.0
    for f in factions_list:
        morale    = _faction_morale(f, defending, war_history)
        tech_mult = technology.combat_bonus(f)
        # Holy War: Metalwork bonus is doubled (1.30 → 1.60) when this faction
        # is fighting a holy war AND has not yet unlocked a higher martial tier
        # (Weaponry=1.50 or Steel=1.80 already exceed 1.60, so we leave them).
        if tech_mult == 1.30 and religion.is_holy_war_member(f.name):
            tech_mult = 1.60
        base      = len(f.members) * (1.0 + morale) * tech_mult
        # Settlement walls grant +10% defense (proportional to members inside zone)
        if defending:
            _s = getattr(f, 'settlement', None)
            if _s and _s.status == 'active' and f.members:
                in_zone = sum(1 for m in f.members if _s.in_zone(m.r, m.c))
                if in_zone > 0:
                    zone_ratio = in_zone / len(f.members)
                    base *= (1.0 + 0.10 * zone_ratio)
        total += base
    return max(0.01, total)


def _resolve_combat_tick(war: War, t: int, event_log: list,
                          people: list, all_dead: list, factions: list) -> None:
    """Resolve one tick of fighting — casualties on the weaker side."""
    str_a = _side_strength(war.all_attackers(), defending=False)
    str_d = _side_strength(war.all_defenders(), defending=True)

    # ── Battle-tick status line ──────────────────────────────────────────────
    def _tech_tag(fl):
        ts = [t for f in fl for t in sorted(getattr(f, 'techs', set()))
              if t in ('metalwork', 'weapons')]
        return f'[{"|".join(ts)}]' if ts else ''

    atk_names = ', '.join(f.name for f in war.all_attackers())
    def_names = ', '.join(f.name for f in war.all_defenders())
    n_a_now   = sum(len(f.members) for f in war.all_attackers())
    n_d_now   = sum(len(f.members) for f in war.all_defenders())
    print(f"  ⚔ Battle tick {war.tick_count}: "
          f"{atk_names}{_tech_tag(war.all_attackers())} (str={str_a:.1f}, n={n_a_now}) vs "
          f"{def_names}{_tech_tag(war.all_defenders())} (str={str_d:.1f}, n={n_d_now}, def)")

    # Determine casualty probabilities
    if str_a >= str_d:
        prob_casualty_a = 0.08
        prob_casualty_d = 0.25
    else:
        prob_casualty_a = 0.25
        prob_casualty_d = 0.08

    def _inflict_casualties(side_factions, prob, side_label):
        for f in side_factions:
            if not f.members:
                continue
            if random.random() < prob:
                victim = random.choice(f.members)
                f.members.remove(victim)
                if victim in people:
                    grid_remove(victim)
                    people.remove(victim)
                all_dead.append(victim)
                # Create legend
                legend = {
                    'name':  victim.name,
                    'chunk': (victim.r, victim.c),
                    'tick':  t,
                }
                f.legends.append(legend)
                msg = (f"Tick {t:03d}: 💀 {victim.name} ({f.name}) "
                       f"fell in battle at ({victim.r},{victim.c})")
                event_log.append(msg)
                print(msg)
                # Give surviving faction-mates veteran beliefs
                for survivor in f.members:
                    add_belief(survivor, 'sacrifice_has_meaning')
                    add_belief(survivor, 'battle_forges_bonds')
                # Update territory after loss
                f.update_territory()

    _inflict_casualties(war.all_attackers(), prob_casualty_a, 'attacker')
    _inflict_casualties(war.all_defenders(), prob_casualty_d, 'defender')


# ══════════════════════════════════════════════════════════════════════════
# War resolution
# ══════════════════════════════════════════════════════════════════════════

def _check_war_resolution(war: War, t: int):
    """
    Returns an outcome string or None.
    Outcomes:
      'surrender_d'  — defender 50%+ losses or territory gone
      'surrender_a'  — attacker 50%+ losses or territory gone
      'ceasefire'    — both sides badly depleted, mutual stand-down
      'exhaustion'   — hit MAX_WAR_TICKS with no decisive result
    """
    # Minimum war length — no resolution before this
    if war.tick_count < MIN_WAR_TICKS:
        return None

    n_a = sum(len(f.members) for f in war.all_attackers())
    n_d = sum(len(f.members) for f in war.all_defenders())

    loss_a = (war.pre_war_a - n_a) / max(1, war.pre_war_a)
    loss_d = (war.pre_war_d - n_d) / max(1, war.pre_war_d)

    # Territory gone?
    def_territory_gone = all(not f.territory for f in war.all_defenders())
    atk_territory_gone = all(not f.territory for f in war.all_attackers())

    # Desperate fighters: 1-2 member factions fight to the last
    def is_desperate(factions_list):
        alive = [f for f in factions_list if f.members]
        return bool(alive) and all(len(f.members) <= 2 for f in alive)

    # Surrender: 50% losses OR territory all gone (desperate fighters resist)
    if (loss_d >= 0.50 or def_territory_gone) and n_a > n_d:
        if not is_desperate(war.all_defenders()):
            return 'surrender_d'
    if (loss_a >= 0.50 or atk_territory_gone) and n_d > n_a:
        if not is_desperate(war.all_attackers()):
            return 'surrender_a'

    # Annihilation — complete wipe after MIN_WAR_TICKS
    if n_d == 0:
        return 'surrender_d'
    if n_a == 0:
        return 'surrender_a'

    # Ceasefire: both sides 50%+ losses
    if loss_a >= 0.50 and loss_d >= 0.50:
        return 'ceasefire'

    # Exhaustion timer
    if war.tick_count >= MAX_WAR_TICKS:
        return 'exhaustion'

    return None


def _end_war(war: War, outcome: str, t: int, event_log: list,
             factions: list) -> None:
    """Determine winner/loser from outcome and resolve consequences."""
    war.outcome = outcome
    war.ended   = True

    n_a = sum(len(f.members) for f in war.all_attackers())
    n_d = sum(len(f.members) for f in war.all_defenders())

    if outcome == 'surrender_d':
        winner_factions = war.all_attackers()
        loser_factions  = war.all_defenders()
        winner_label    = war.attacker.name
        loser_label     = war.defender.name
    elif outcome == 'surrender_a':
        winner_factions = war.all_defenders()
        loser_factions  = war.all_attackers()
        winner_label    = war.defender.name
        loser_label     = war.attacker.name
    elif outcome == 'ceasefire':
        winner_factions = None
        loser_factions  = None
        winner_label    = 'both'
        loser_label     = 'both'
    else:  # exhaustion
        # Whoever has more members "wins" marginally
        if n_a > n_d:
            winner_factions = war.all_attackers()
            loser_factions  = war.all_defenders()
            winner_label    = war.attacker.name
            loser_label     = war.defender.name
        elif n_d > n_a:
            winner_factions = war.all_defenders()
            loser_factions  = war.all_attackers()
            winner_label    = war.defender.name
            loser_label     = war.attacker.name
        else:
            winner_factions = None
            loser_factions  = None
            winner_label    = 'neither'
            loser_label     = 'neither'

    outcome_str = {
        'surrender_d': f'{war.attacker.name} forces {war.defender.name} to surrender',
        'surrender_a': f'{war.defender.name} repels {war.attacker.name}',
        'ceasefire':   'both sides limp to a bloody ceasefire',
        'exhaustion':  f'war ends in exhaustion ({winner_label} holds more ground)',
    }[outcome]

    msg = (f"Tick {t:03d}: 🏳 WAR ENDS — {war.attacker.name} vs {war.defender.name}  "
           f"[{outcome_str}]  (lasted {war.tick_count} ticks)")
    event_log.append(msg)
    print(msg)

    if winner_factions and loser_factions:
        _resolve_winner(winner_factions, loser_factions, war, t, event_log)
        diplomacy.resolve_surrender(winner_factions, loser_factions, war,
                                    t, event_log, factions)
    else:
        # Ceasefire / true exhaustion: both sides costly
        for f in war.all_attackers() + war.all_defenders():
            for m in f.members:
                add_belief(m, 'war_is_costly')
                add_belief(m, 'never_again')
    _post_war_absorption(war, factions, t, event_log)


def _resolve_winner(winner_factions: list, loser_factions: list,
                     war: War, t: int, event_log: list) -> None:
    """Apply post-war consequences: territory, tribute, beliefs, band-of-brothers."""
    primary_winner = winner_factions[0]
    primary_loser  = loser_factions[0]

    # Territory: winner claims one chunk from loser (if any remain)
    if primary_loser.territory and primary_winner.members:
        claimed = random.choice(primary_loser.territory)
        if claimed in primary_loser.territory:
            primary_loser.territory.remove(claimed)
        if claimed not in primary_winner.territory:
            primary_winner.territory.append(claimed)
        msg = (f"Tick {t:03d}: 🗺 {primary_winner.name} claims territory "
               f"at ({claimed[0]},{claimed[1]}) from {primary_loser.name}")
        event_log.append(msg)
        print(msg)

    # Tension: drop between winner pair, spike between loser and all winners
    for wf in winner_factions:
        for lf in loser_factions:
            key = tuple(sorted([wf.name, lf.name]))
            RIVALRIES[key] = max(0, RIVALRIES.get(key, 0) - 60)

    # Tribute: loser pays tribute for TRIBUTE_TICKS ticks
    war.tribute_remaining = TRIBUTE_TICKS
    war.tribute_payer     = primary_loser
    war.tribute_receiver  = primary_winner
    msg = (f"Tick {t:03d}: 💸 {primary_loser.name} will pay tribute to "
           f"{primary_winner.name} for {TRIBUTE_TICKS} ticks")
    event_log.append(msg)
    print(msg)

    # Beliefs — winners
    for f in winner_factions:
        for m in f.members:
            add_belief(m, 'victory_proves_strength')
            add_belief(m, 'endurance_rewarded')

    # Beliefs — losers
    for f in loser_factions:
        for m in f.members:
            add_belief(m, 'war_is_costly')
            add_belief(m, 'never_again')

    # Band-of-brothers: allies trust each other more
    all_allied = winner_factions + loser_factions
    for fa in all_allied:
        for fb in all_allied:
            if fa is fb:
                continue
            for ma in fa.members:
                for mb in fb.members:
                    if mb.name in ma.trust:
                        ma.trust[mb.name] = min(100, ma.trust[mb.name] + 15)


def _post_war_absorption(war: War, factions: list, t: int, event_log: list) -> None:
    """Losing faction survivors may join the winner or a neutral faction."""
    if war.outcome not in ('surrender_d', 'surrender_a'):
        return
    loser_factions  = war.all_defenders() if war.outcome == 'surrender_d' else war.all_attackers()
    winner_factions = war.all_attackers() if war.outcome == 'surrender_d' else war.all_defenders()
    active = [f for f in factions if f.members]
    for lf in loser_factions:
        for m in list(lf.members):
            r = random.random()
            if r < 0.40 and winner_factions and winner_factions[0].members:
                target = winner_factions[0]
                lf.members.remove(m)
                target.members.append(m)
                m.faction = target.name
                target.update_territory()
                msg = (f"Tick {t:03d}: 🔄 {m.name} absorbed into "
                       f"{target.name} after defeat")
                event_log.append(msg)
                print(msg)
            elif r < 0.70:
                neutrals = [f for f in active
                            if f not in loser_factions + winner_factions
                            and f.members]
                if neutrals:
                    target = random.choice(neutrals)
                    lf.members.remove(m)
                    target.members.append(m)
                    m.faction = target.name
                    target.update_territory()
                    msg = (f"Tick {t:03d}: 🔄 {m.name} fled to "
                           f"{target.name} after defeat")
                    event_log.append(msg)
                    print(msg)


# ══════════════════════════════════════════════════════════════════════════
# Tribute payments
# ══════════════════════════════════════════════════════════════════════════

def _process_tribute(factions: list, t: int, event_log: list) -> None:
    """Process ongoing tribute payments from resolved wars."""
    for w in war_history:
        if w.tribute_remaining <= 0:
            continue
        payer    = w.tribute_payer
        receiver = w.tribute_receiver
        if not payer or not receiver:
            continue
        if not payer.members or not receiver.members:
            w.tribute_remaining = 0
            continue

        amount = payer.food_reserve * TRIBUTE_RATE
        if amount < 1.0:
            # Take from members instead
            total_food = sum(m.inventory.get('food', 0) for m in payer.members)
            amount = total_food * TRIBUTE_RATE
            per_m = amount / max(1, len(payer.members))
            for m in payer.members:
                take = min(m.inventory.get('food', 0), per_m)
                m.inventory['food'] = max(0, m.inventory.get('food', 0) - take)

        if amount >= 0.5:
            payer.food_reserve    = max(0, payer.food_reserve - amount)
            receiver.food_reserve = receiver.food_reserve + amount
            w.tribute_remaining  -= 1
            if w.tribute_remaining % 5 == 0:
                msg = (f"Tick {t:03d}: 💸 Tribute: {payer.name} → {receiver.name}  "
                       f"{amount:.1f} food  ({w.tribute_remaining} ticks remaining)")
                event_log.append(msg)


# ══════════════════════════════════════════════════════════════════════════
# End-of-run report
# ══════════════════════════════════════════════════════════════════════════

def combat_report(factions: list = None) -> None:
    all_wars = war_history + active_wars
    if not all_wars:
        print("  No wars declared.")
        return

    print(f"  Wars total : {len(all_wars)}")
    surrenders = sum(1 for w in all_wars if w.outcome in ('surrender_a', 'surrender_d'))
    ceasefires = sum(1 for w in all_wars if w.outcome == 'ceasefire')
    exhausted  = sum(1 for w in all_wars if w.outcome == 'exhaustion')
    ongoing    = sum(1 for w in all_wars if not w.ended)
    print(f"  Outcomes   : {surrenders} surrender  {ceasefires} ceasefire  "
          f"{exhausted} exhaustion  {ongoing} ongoing")

    for w in all_wars:
        status = w.outcome if w.outcome else 'ONGOING'
        allies_a = ', '.join(f.name for f in w.allied_with_a) or 'none'
        allies_d = ', '.join(f.name for f in w.allied_with_d) or 'none'
        print(f"  \u2694 {w.attacker.name} vs {w.defender.name}  "
              f"[{status}]  ticks:{w.tick_count}  "
              f"allies_atk:{allies_a}  allies_def:{allies_d}")

    # Legends from faction memory
    if factions:
        all_legends = []
        for f in factions:
            for leg in getattr(f, 'legends', []):
                all_legends.append((f.name, leg))
        if all_legends:
            print(f"  Legends    : {len(all_legends)} fallen")
            for fname, leg in all_legends[:10]:
                print(f"    {leg['name']} of {fname}  "
                      f"fell at ({leg['chunk'][0]},{leg['chunk'][1]}) tick {leg['tick']}")

    print(f"  Alliances  : {len(_alliances)}")
