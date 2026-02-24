"""
diplomacy.py — Layer 7: council votes, formal treaties, reputation, surrender terms.

Call order each tick (after technology_tick):
    diplomacy_tick(factions, t, event_log)

Public API used by other modules:
    council_vote(faction, topic, context)     → (passed: bool, desc: str)
    get_rep(faction_name)                     → int  (-10 … +10)
    adjust_rep(faction, delta, reason)        → None
    rep_label(rep_int)                        → str
    has_treaty(name_a, name_b, type=None)     → bool
    trade_bonus(name_a, name_b)               → float  (1.0 or 1.2)
    mutual_defense_triggered(attacker, defender, t, event_log) → [names]
    resolve_surrender(winner_factions, loser_factions, war, t, event_log, factions)
    break_treaty(name_a, name_b, t, event_log, factions_list)
"""
import random, sys
sys.stdout.reconfigure(encoding='utf-8')

from beliefs  import inh_cores, LABELS
from factions import RIVALRIES

# ══════════════════════════════════════════════════════════════════════════
# Module-level state
# ══════════════════════════════════════════════════════════════════════════

# Reputation:  faction_name → int (-10 to +10)
_reputation: dict = {}

# Active treaties:  frozenset({name_a, name_b}) → treaty dict
_treaties:   dict = {}

# All treaty records (never purged)
treaty_log:  list = []

# Treaty type constants
NON_AGGRESSION  = 'Non-Aggression Pact'
TRADE_AGREEMENT = 'Trade Agreement'
MUTUAL_DEFENSE  = 'Mutual Defense Pact'
TRIBUTE_PACT    = 'Tribute Pact'

_TREATY_DURATION = {
    NON_AGGRESSION:  50,
    TRADE_AGREEMENT: 50,
    MUTUAL_DEFENSE:  50,
    TRIBUTE_PACT:    50,
}

# NON_AGGRESSION caps tension at this value while active
_NAP_TENSION_CAP = 100

# Rate-limiting: per-faction cooldowns
_faction_propose_cd: dict = {}   # name → tick of last treaty proposal/signing
_faction_break_cd:   dict = {}   # name → tick when post-break cooldown ends
_last_neg_tick:      dict = {}   # name → last tick a negative rep event occurred


# ══════════════════════════════════════════════════════════════════════════
# Reputation
# ══════════════════════════════════════════════════════════════════════════

def get_rep(faction_name: str) -> int:
    return _reputation.get(faction_name, 0)


def adjust_rep(faction, delta: int, reason: str = '', t: int = 0) -> None:
    name = faction if isinstance(faction, str) else faction.name
    prev = _reputation.get(name, 0)
    _reputation[name] = max(-10, min(10, prev + delta))
    if delta < 0 and t:
        _last_neg_tick[name] = t


def rep_label(rep: int) -> str:
    if   rep >=  7: return 'legendary'
    elif rep >=  5: return 'honorable'
    elif rep >=  2: return 'trusted'
    elif rep >= -1: return 'neutral'
    elif rep >= -3: return 'questionable'
    elif rep >= -6: return 'disgraced'
    return 'reviled'


# ══════════════════════════════════════════════════════════════════════════
# Council votes
# ══════════════════════════════════════════════════════════════════════════

# Beliefs that push YES / NO on specific topics
_VOTE_WAR_YES  = {'the_strong_take', 'victory_proves_strength',
                  'suffering_forges_strength', 'battle_forges_bonds',
                  'crowded_lands_breed_conflict'}
_VOTE_WAR_NO   = {'war_is_costly', 'never_again', 'loss_teaches_caution',
                  'community_sustains', 'trade_builds_bonds'}
_VOTE_ALLY_NO  = {'trust_no_group'}
_VOTE_ALLY_YES = {'loyalty_above_all', 'trade_builds_bonds', 'community_sustains'}
_VOTE_MBR_YES  = {'community_sustains'}
_VOTE_MBR_NO   = {'self_reliance', 'trust_no_group'}

_THRESHOLD = {
    'war':        0.60,
    'alliance':   0.50,
    'new_member': 0.50,
    'research':   0.50,
}


def council_vote(faction, topic: str, context: dict = None) -> tuple:
    """
    Run an internal council vote for a faction.
    Only activates when the faction has 3+ members.
    Returns (passed: bool, description: str).
    """
    if len(faction.members) < 3:
        return True, ''        # no council yet — leader decides

    context   = context or {}
    yes_votes = 0
    no_votes  = 0

    # leader's dominant belief — used for loyalty_above_all votes
    leader         = faction.members[0]
    leader_cores   = set(inh_cores(leader))

    for member in faction.members:
        cores = set(inh_cores(member))
        vote  = _cast_vote(member, cores, topic, context, leader_cores)
        if vote:
            yes_votes += 1
        else:
            no_votes  += 1

    total  = yes_votes + no_votes
    pct    = yes_votes / total if total else 0.0
    passed = pct >= _THRESHOLD.get(topic, 0.50)
    label  = 'PASSED' if passed else 'FAILED'

    desc = f"Council vote: {topic} — {yes_votes}:{no_votes} — {label}"
    print(f"  [{faction.name}] {desc}")

    # Schism check: very close vote (40–60 % band ≈ within 1 vote of tying)
    if abs(pct - 0.50) <= (1.0 / (total + 0.01)):
        _trigger_schism_risk(faction, topic)

    return passed, desc


def _cast_vote(member, cores: set, topic: str,
               context: dict, leader_cores: set) -> bool:
    """Return True = yes, False = no for one member."""
    # Loyalty-above-all: mirror the leader's dominant tendency
    if 'loyalty_above_all' in cores:
        if topic == 'war':
            return bool(leader_cores & _VOTE_WAR_YES)
        return True    # loyal members default YES

    if topic == 'war':
        if cores & _VOTE_WAR_YES:
            return True
        if cores & _VOTE_WAR_NO:
            return False
        return random.random() < 0.45   # slight peace bias by default

    elif topic == 'alliance':
        if cores & _VOTE_ALLY_NO:
            return False
        if cores & _VOTE_ALLY_YES:
            return True
        return random.random() < 0.55

    elif topic == 'new_member':
        if cores & _VOTE_MBR_YES:
            return True
        if cores & _VOTE_MBR_NO:
            return False
        return random.random() < 0.50

    elif topic == 'research':
        return random.random() < 0.60

    return random.random() < 0.50


def _trigger_schism_risk(faction, topic: str) -> None:
    """A very close vote increases the internal tension (schism seed)."""
    # Mark the faction so factions.py can pick this up during schism checks
    faction._council_tension = getattr(faction, '_council_tension', 0) + 1


# ══════════════════════════════════════════════════════════════════════════
# Treaty helpers
# ══════════════════════════════════════════════════════════════════════════

def has_treaty(name_a: str, name_b: str, treaty_type: str = None) -> bool:
    key = frozenset([name_a, name_b])
    tr  = _treaties.get(key)
    if tr is None:
        return False
    if treaty_type:
        return tr['type'] == treaty_type
    return True


def get_treaty(name_a: str, name_b: str) -> dict | None:
    return _treaties.get(frozenset([name_a, name_b]))


def trade_bonus(name_a: str, name_b: str) -> float:
    """Return 1.2 if a TRADE_AGREEMENT exists or both have honorable reputation (+5)."""
    if has_treaty(name_a, name_b, TRADE_AGREEMENT):
        return 1.2
    if get_rep(name_a) > 5 and get_rep(name_b) > 5:
        return 1.2
    return 1.0


def mutual_defense_triggered(attacker: str, defender: str,
                             t: int, event_log: list) -> list:
    """
    When 'defender' is attacked, return names of mutual-defense allies
    that should auto-side with the defender.
    Prints a banner for each triggered ally.
    """
    allies = []
    for key, tr in list(_treaties.items()):
        if tr['type'] != MUTUAL_DEFENSE:
            continue
        names = set(key)
        if defender not in names:
            continue
        ally_name = (names - {defender}).pop()
        if ally_name == attacker:
            continue
        allies.append(ally_name)
        msg = (f"Tick {t:03d}: 🛡 MUTUAL DEFENSE — {ally_name} is bound to "
               f"defend {defender} under treaty!")
        event_log.append(msg)
        print(msg)
    return allies


def break_treaty(name_a: str, name_b: str, t: int,
                 event_log: list, factions_list: list = None) -> None:
    """
    name_a broke the treaty (takes the reputation hit).
    """
    key = frozenset([name_a, name_b])
    tr  = _treaties.pop(key, None)
    if tr is None:
        return

    tr['broken'] = True
    treaty_log.append(tr)

    adjust_rep(name_a, -3, 'treaty_broken', t)
    _faction_break_cd[name_a] = t + 10   # 10-tick cooldown before any new treaty

    msg = (f"Tick {t:03d}: 💔 {name_a} broke treaty with {name_b} — "
           f"trust shattered across the land")
    event_log.append(msg)
    print(msg)

    # Small tension spike from all other factions toward the treaty-breaker
    if factions_list:
        for f in factions_list:
            if f.name not in (name_a, name_b):
                bk = tuple(sorted([f.name, name_a]))
                RIVALRIES[bk] = RIVALRIES.get(bk, 0) + 8


def _sign_treaty(fa, fb, treaty_type: str, t: int, event_log: list) -> None:
    key     = frozenset([fa.name, fb.name])
    expires = t + _TREATY_DURATION[treaty_type]
    record  = {
        'type':    treaty_type,
        'a':       fa.name,
        'b':       fb.name,
        'signed':  t,
        'expires': expires,
        'broken':  False,
    }
    _treaties[key] = record
    treaty_log.append(dict(record))
    # Record signing tick for both parties (rate limiter)
    _faction_propose_cd[fa.name] = t
    _faction_propose_cd[fb.name] = t

    msg = (f"Tick {t:03d}: 📜 TREATY: {fa.name} and {fb.name} "
           f"sign {treaty_type} (expires tick {expires})")
    event_log.append(msg)
    print(msg)


# ══════════════════════════════════════════════════════════════════════════
# Treaty proposal logic
# ══════════════════════════════════════════════════════════════════════════

def _should_propose(fa, fb, t: int) -> tuple | None:
    """
    Return (treaty_type, fa_is_proposer) or None.
    """
    key     = tuple(sorted([fa.name, fb.name]))
    tension = RIVALRIES.get(key, 0)
    rep_a   = get_rep(fa.name)
    rep_b   = get_rep(fb.name)

    # Very low reputation factions refused universally
    if rep_a < -5 or rep_b < -5:
        return None
    # Rate limit: max 1 treaty proposal per 20 ticks per faction
    if t - _faction_propose_cd.get(fa.name, -999) < 20:
        return None
    if t - _faction_propose_cd.get(fb.name, -999) < 20:
        return None
    # Post-break cooldown: 10 ticks after breaking a treaty
    if t < _faction_break_cd.get(fa.name, 0):
        return None
    if t < _faction_break_cd.get(fb.name, 0):
        return None

    # Already a treaty or at war
    if has_treaty(fa.name, fb.name):
        return None

    import combat as _cbt
    if _cbt.is_at_war(fa.name, fb.name):
        return None

    import economy as _eco
    route_key       = frozenset([fa.name, fb.name])
    has_trade_route = _eco.trade_routes.get(route_key, {}).get('active', False)

    size_a = len(fa.members)
    size_b = len(fb.members)

    # TRIBUTE PACT: one faction 3× stronger demands tribute
    if size_b > 0 and size_a >= size_b * 3:
        return (TRIBUTE_PACT, True)
    if size_a > 0 and size_b >= size_a * 3:
        return (TRIBUTE_PACT, False)

    # MUTUAL DEFENSE: shared enemy with tension > 100
    shared_enemy = _find_shared_enemy(fa, fb)
    if shared_enemy and tension <= 80:
        return (MUTUAL_DEFENSE, True)

    # TRADE AGREEMENT: active trade route, low-ish tension
    if has_trade_route and tension < 60:
        return (TRADE_AGREEMENT, True)

    # NON-AGGRESSION: mid tension zone
    if 30 <= tension <= 80:
        return (NON_AGGRESSION, True)

    return None


def _find_shared_enemy(fa, fb) -> str | None:
    """Return name of a faction both fa and fb have tension > 100 with."""
    for key_pair, tension in RIVALRIES.items():
        if tension <= 100:
            continue
        na, nb = key_pair
        if fa.name in (na, nb):
            enemy = nb if na == fa.name else na
            if enemy == fb.name:
                continue
            fb_key = tuple(sorted([fb.name, enemy]))
            if RIVALRIES.get(fb_key, 0) > 100:
                return enemy
    return None


def _faction_accepts(faction, treaty_type: str, proposer) -> bool:
    """Would this faction accept a treaty of this type from proposer?"""
    rep        = get_rep(proposer.name)
    # Below -3 rep: alliance/defense requests rejected
    if treaty_type in (MUTUAL_DEFENSE, NON_AGGRESSION) and rep < -3:
        return False
    cores: set = set()
    for m in faction.members:
        cores.update(inh_cores(m))

    # Low reputation proposer → reduced acceptance chance
    base = 0.55 + rep * 0.04         # 0.15 … 0.95
    base = max(0.10, min(0.92, base))

    if treaty_type == NON_AGGRESSION:
        if cores & {'war_is_costly', 'never_again', 'loss_teaches_caution'}:
            base += 0.20
        if 'the_strong_take' in cores:
            base -= 0.30
    elif treaty_type == TRADE_AGREEMENT:
        if 'trade_builds_bonds' in cores:
            base += 0.25
    elif treaty_type == MUTUAL_DEFENSE:
        if cores & {'loyalty_above_all', 'community_sustains'}:
            base += 0.20
        if 'trust_no_group' in cores:
            base -= 0.40
    elif treaty_type == TRIBUTE_PACT:
        return True    # weaker faction has no leverage to refuse

    # Council vote gate (alliance topic = 50% threshold)
    if len(faction.members) >= 3:
        passed, _ = council_vote(faction, 'alliance', {'proposer': proposer.name})
        if not passed:
            return False

    return random.random() < max(0.05, base)


# ══════════════════════════════════════════════════════════════════════════
# Surrender terms
# ══════════════════════════════════════════════════════════════════════════

def resolve_surrender(winner_factions: list, loser_factions: list,
                      war, t: int, event_log: list, factions_list: list) -> None:
    """
    Apply surrender terms determined by the winner's beliefs.
    Called from combat._end_war after winner/loser are established.
    This SUPPLEMENTS (not replaces) the existing tribute tick-drain,
    so combat.py still handles food drain; here we handle the shape
    of the peace.
    """
    if not winner_factions or not loser_factions:
        return

    primary_winner = winner_factions[0]
    primary_loser  = loser_factions[0]

    # Cancel the default tribute set by combat._resolve_winner
    # (diplomacy.resolve_surrender decides the final peace terms exclusively)
    war.tribute_remaining = 0
    war.tribute_payer     = None
    war.tribute_receiver  = None

    # Collect winner belief cores
    w_cores: set = set()
    for f in winner_factions:
        for m in f.members:
            w_cores.update(inh_cores(m))

    # Belief-driven surrender term selection
    if   'community_sustains'  in w_cores: term = 'ANNEX'
    elif 'the_strong_take'     in w_cores: term = 'TRIBUTE'
    elif 'migration_brings_hope' in w_cores: term = 'EXILE'
    elif 'the_wise_must_lead'  in w_cores: term = 'VASSALIZE'
    else:
        term = random.choice(['TRIBUTE', 'EXILE', 'ANNEX', 'VASSALIZE'])

    intro = (f"Tick {t:03d}: ⚖ SURRENDER TERMS — "
             f"{primary_winner.name} → {primary_loser.name}: {term}")

    if term == 'ANNEX':
        absorbed = list(primary_loser.members)
        for m in absorbed:
            m.faction = primary_winner
            primary_winner.members.append(m)
        primary_loser.members.clear()
        detail = f" ({len(absorbed)} member(s) absorbed into {primary_winner.name})"
        adjust_rep(primary_winner.name, -1, 'annex_aggression')

    elif term == 'TRIBUTE':
        # Use combat war object's tribute fields
        war.tribute_remaining = 30
        war.tribute_payer     = primary_loser
        war.tribute_receiver  = primary_winner
        detail = ' (30 % resources for 30 ticks)'

    elif term == 'EXILE':
        try:
            from world import world, GRID
            relocated = 0
            for m in primary_loser.members:
                candidates = [
                    (r, c) for r in range(GRID) for c in range(GRID)
                    if world[r][c]['habitable']
                       and abs(r - m.r) + abs(c - m.c) >= 5
                ]
                if candidates:
                    m.r, m.c = random.choice(candidates)
                    relocated += 1
            primary_loser.update_territory()
            detail = f' ({relocated} member(s) exiled ≥5 chunks away)'
        except Exception:
            detail = ' (exile attempted)'
        adjust_rep(primary_winner.name, 1, 'merciful_exile')

    elif term == 'VASSALIZE':
        primary_loser.vassal_of = primary_winner.name
        detail = f' ({primary_loser.name} becomes vassal of {primary_winner.name})'
        adjust_rep(primary_winner.name,  1, 'wise_diplomacy')
        adjust_rep(primary_loser.name,  -1, 'vassalized')

    msg = intro + detail
    event_log.append(msg)
    print(msg)

    # Reputation updates
    was_defender = (primary_winner in war.all_defenders())
    if was_defender:
        adjust_rep(primary_winner.name, 2, 'won_defensive_war')   # righteous defender
    adjust_rep(primary_loser.name, -1, 'lost_war')


# ══════════════════════════════════════════════════════════════════════════
# Main tick
# ══════════════════════════════════════════════════════════════════════════

def diplomacy_tick(factions: list, t: int, event_log: list) -> None:
    """Main entry point — run all diplomacy logic for this tick."""
    active = [f for f in factions if f.members]

    # ── 1. Apply NON_AGGRESSION tension cap ───────────────────────────────
    for key, tr in list(_treaties.items()):
        if tr['type'] == NON_AGGRESSION:
            rkey = tuple(sorted(key))
            if rkey in RIVALRIES and RIVALRIES[rkey] > _NAP_TENSION_CAP:
                RIVALRIES[rkey] = _NAP_TENSION_CAP

    # ── 2. Expire completed treaties (honor bonus) ────────────────────────
    for key in list(_treaties.keys()):
        tr = _treaties[key]
        if tr['expires'] <= t:
            adjust_rep(tr['a'], 1, 'treaty_honored')
            adjust_rep(tr['b'], 1, 'treaty_honored')
            del _treaties[key]
            # Silent expiry — no print (avoids spam)

    # ── 2b. Reputation recovery every 25 ticks ───────────────────────────
    if t % 25 == 0:
        for f in active:
            if t - _last_neg_tick.get(f.name, 0) >= 25:
                if get_rep(f.name) < 10:
                    adjust_rep(f.name, 1, 'rep_recovery')

    # ── 3. Check for treaty violations (war declared against a signatory) ─
    import combat as _cbt
    for w in _cbt.active_wars:
        for fa in w.all_attackers():
            for fd in w.all_defenders():
                bk = frozenset([fa.name, fd.name])
                if bk in _treaties:
                    break_treaty(fa.name, fd.name, t, event_log, active)

    # ── 4. Propose new treaties (checked every 5 ticks, random pairs) ─────
    if t % 5 == 0 and len(active) >= 2:
        candidates = random.sample(active, min(len(active), 6))
        checked: set = set()
        for i, fa in enumerate(candidates):
            for fb in candidates[i+1:]:
                pk = frozenset([fa.name, fb.name])
                if pk in checked:
                    continue
                checked.add(pk)

                proposal = _should_propose(fa, fb, t)
                if proposal is None:
                    continue

                treaty_type, fa_proposes = proposal
                proposer = fa if fa_proposes else fb
                acceptor = fb if fa_proposes else fa

                # Record proposal attempt for BOTH parties immediately
                # (prevents spam even on rejection)
                _faction_propose_cd[proposer.name] = t
                _faction_propose_cd[acceptor.name] = t

                if _faction_accepts(acceptor, treaty_type, proposer):
                    _sign_treaty(fa, fb, treaty_type, t, event_log)

    # ── 5. Shared-food reputation (every 30 ticks) ────────────────────────
    if t % 30 == 0:
        needy = [f for f in active
                 if f.food_reserve < len(f.members) * 3 and f.members]
        for f in active:
            if not needy:
                break
            if f.food_reserve > len(f.members) * 12:
                recipient = random.choice(needy)
                if recipient.name == f.name:
                    continue
                share = min(f.food_reserve * 0.05, 10)
                f.food_reserve        -= share
                recipient.food_reserve += share
                adjust_rep(f.name, 2, 'shared_food_famine')
                msg = (f"Tick {t:03d}: 🍞 {f.name} shares food with "
                       f"{recipient.name} during hardship  (+2 rep)")
                event_log.append(msg)
                print(msg)


# ══════════════════════════════════════════════════════════════════════════
# Diplomacy report (called from display.final_report)
# ══════════════════════════════════════════════════════════════════════════

def diplomacy_report(factions: list, ticks: int) -> None:
    sep = '─' * 72
    print(f"\n{sep}")
    print(f"DIPLOMACY SUMMARY — {ticks} ticks")
    print(sep)

    # Treaties
    print(f"\n  Treaties signed : {len(treaty_log)}")
    signed  = [tr for tr in treaty_log if not tr.get('broken')]
    broken  = [tr for tr in treaty_log if tr.get('broken')]
    active_ = list(_treaties.values())

    if active_:
        print(f"  Active treaties : {len(active_)}")
        for tr in active_:
            print(f"    {tr['a']} ↔ {tr['b']}  [{tr['type']}]  "
                  f"(expires tick {tr['expires']})")
    if broken:
        print(f"  Broken treaties : {len(broken)}")
        for tr in broken:
            print(f"    {tr['a']} broke pact with {tr['b']}  [{tr['type']}]")

    # Reputation
    print(f"\n  Faction reputations:")
    active_names = {f.name for f in factions if f.members}
    all_names    = active_names | set(_reputation.keys())
    rep_rows = sorted(
        [(n, _reputation.get(n, 0)) for n in all_names],
        key=lambda x: -x[1]
    )
    for name, rep in rep_rows:
        tag = '(active)' if name in active_names else '(disbanded)'
        bar = ('+' * max(0, rep)) if rep >= 0 else ('-' * abs(rep))
        print(f"    {name:<35} {rep:+3d}  [{rep_label(rep)}]  {bar}")

    print(sep)
