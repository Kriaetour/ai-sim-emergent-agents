"""
economy.py — Layer 4: currency, dynamic pricing, trade, raids, wealth.

Call order each tick:
    economy_tick(people, factions, t, event_log)

End of run:
    economy_report(factions, people, ticks)
"""
import sys, random
sys.stdout.reconfigure(encoding='utf-8')

from collections  import defaultdict
from itertools    import combinations
from world        import world, GRID
from beliefs      import inh_cores, add_belief
from factions     import RIVALRIES
import combat

# ── Module-level state ─────────────────────────────────────────────────────
faction_currencies: dict  = {}           # faction_name → {'name': str}
faction_prices:     dict  = {}           # faction_name → {res: float}
price_history:      dict  = {}           # faction_name → {res: [float…]}
trade_routes:       dict  = {}           # frozenset({a,b}) → RouteData dict
raid_log:           list  = []           # [(t, raider, victim, haul_str)]
scarcity_events:    list  = []           # [(t, resource)]

BASE_PRICES = {'food': 2, 'wood': 3, 'ore': 5, 'stone': 4}
RES_TRADE   = ['food', 'wood', 'ore', 'stone']   # exclude water from economy
_last_shock_res: str = ''                         # never repeat same resource twice

_CURRENCY_NAMES = [
    'shells', 'iron bits', 'marked stones', 'bone chips', 'carved tokens',
    'clay seals', 'dried herbs', 'scored bark', 'knotted cord', 'amber beads',
    'tide pearls', 'copper seeds', 'fired clay', 'pine resin', 'salt blocks',
]


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _faction_supply(faction) -> dict:
    """Sum of resources held by members + food reserve."""
    totals = {k: 0 for k in RES_TRADE}
    for m in faction.members:
        for k in RES_TRADE:
            totals[k] += m.inventory.get(k, 0)
    totals['food'] += int(faction.food_reserve)
    return totals


def _faction_demand(faction, supply) -> dict:
    """Rough demand estimate: how much the faction currently lacks."""
    n = max(1, len(faction.members))
    # Need roughly 3 food per member, 1 of others per 4 members
    needs = {
        'food':  n * 3,
        'wood':  max(1, n // 4),
        'ore':   max(1, n // 4),
        'stone': max(1, n // 4),
    }
    return {k: max(0, needs[k] - supply[k]) for k in RES_TRADE}


def _invent_currency(faction, t, event_log):
    if faction.name in faction_currencies:
        return
    cores = set()
    for m in faction.members:
        cores.update(inh_cores(m))
    if 'trade_builds_bonds' not in cores:
        return
    used  = {v['name'] for v in faction_currencies.values()}
    picks = [c for c in _CURRENCY_NAMES if c not in used]
    cname = random.choice(picks) if picks else f"tokens of {faction.name[:6]}"
    faction_currencies[faction.name] = {'name': cname}
    for m in faction.members:
        m.currency = getattr(m, 'currency', 0) + 10
    msg = (f"Tick {t:03d}: 💰 {faction.name} invents currency — "
           f"'{cname}' (each member receives 10)")
    event_log.append(msg)
    print(msg)


def _update_prices(faction, t, event_log):
    supply = _faction_supply(faction)
    demand = _faction_demand(faction, supply)
    name   = faction.name

    if name not in faction_prices:
        faction_prices[name]  = dict(BASE_PRICES)
    if name not in price_history:
        price_history[name] = {k: [] for k in RES_TRADE}

    for res in RES_TRADE:
        base      = BASE_PRICES[res]
        d, s      = demand[res], max(supply[res], 1)
        ratio     = max(0.5, min(4.0, (d + 1) / s))
        new_price = round(base * ratio, 1)
        old_price = faction_prices[name].get(res, base)

        if abs(new_price - old_price) >= 1.0:
            direction = 'scarce' if new_price > old_price else 'surplus'
            msg = (f"Tick {t:03d}: {name} {res} price: "
                   f"{old_price:.0f}→{new_price:.0f} ({direction})")
            event_log.append(msg)
            print(msg)

        faction_prices[name][res] = new_price
        price_history[name][res].append(new_price)


# ══════════════════════════════════════════════════════════════════════════════
# Trade helpers
# ══════════════════════════════════════════════════════════════════════════════

def _do_trade(giver, receiver, res, amount, t, event_log, key):
    donors = [m for m in giver.members if m.inventory.get(res, 0) >= amount]
    if not donors or not receiver.members:
        return False
    donor = max(donors, key=lambda m: m.inventory.get(res, 0))
    taker = random.choice(receiver.members)

    import diplomacy as _dip
    _trade_bonus = _dip.trade_bonus(giver.name, receiver.name)
    donor.inventory[res] -= amount
    taker.inventory[res] += int(amount * _trade_bonus)

    # Currency payment if receiver has currency
    price     = faction_prices.get(receiver.name, BASE_PRICES).get(res, BASE_PRICES[res])
    payment   = min(getattr(taker, 'currency', 0), round(amount * price))
    if payment > 0:
        taker.currency   = getattr(taker, 'currency', 0) - payment
        donor.currency   = getattr(donor, 'currency', 0) + payment

    # Tension reduction
    RIVALRIES[key] = max(0, RIVALRIES.get(key, 0) - 5)

    # Beliefs
    add_belief(donor, 'trade_builds_bonds')
    add_belief(taker, 'trade_builds_bonds')

    # Route tracking
    route_key = frozenset([giver.name, receiver.name])
    if route_key not in trade_routes:
        trade_routes[route_key] = {
            'count': 0, 'resources': defaultdict(int), 'active': False,
            'names': (giver.name, receiver.name),
        }
    trade_routes[route_key]['count']            += 1
    trade_routes[route_key]['resources'][res]   += amount

    # Route established at 3 successful trades
    if trade_routes[route_key]['count'] == 3 and not trade_routes[route_key]['active']:
        trade_routes[route_key]['active'] = True
        res_str = ', '.join(trade_routes[route_key]['resources'].keys())
        msg = (f"Tick {t:03d}: 🛤 Trade route established: "
               f"{giver.name} ↔ {receiver.name} ({res_str})")
        event_log.append(msg)
        print(msg)

    # 10% bonus from established route
    if trade_routes[route_key]['active']:
        bonus = max(1, round(amount * 0.10))
        taker.inventory[res] += bonus

    # 20% bonus for allied factions
    if combat.are_allied(giver.name, receiver.name):
        ally_bonus = max(1, round(amount * 0.20))
        taker.inventory[res] += ally_bonus

    tension = RIVALRIES.get(key, 0)
    msg = (f"Tick {t:03d}: 🤝 Trade: {giver.name} → {receiver.name}  "
           f"{amount} {res}  (tension now {tension})")
    event_log.append(msg)
    return True


def _faction_trade(active, t, event_log):
    for fa, fb in combinations(active, 2):
        key     = tuple(sorted([fa.name, fb.name]))
        tension = RIVALRIES.get(key, 0)

        if combat.is_at_war(fa.name, fb.name):
            continue  # at war — no trade

        if tension >= 35:
            continue  # hostile — raiding only

        if tension >= 30:
            # Occasional failed negotiation
            if random.random() < 0.08:
                RIVALRIES[key] = RIVALRIES.get(key, 0) + 3
                msg = (f"Tick {t:03d}: 🚫 Trade talks between "
                       f"{fa.name} & {fb.name} collapsed (+3 tension)")
                event_log.append(msg)
            continue

        sup_a = _faction_supply(fa)
        sup_b = _faction_supply(fb)

        for res in RES_TRADE:
            amount = 3
            if trade_routes.get(frozenset([fa.name, fb.name]), {}).get('active'):
                amount = 4   # route bonus handled inside _do_trade
            if sup_a[res] >= 5 and sup_a[res] >= sup_b[res] * 2:
                _do_trade(fa, fb, res, amount, t, event_log, key)
                break
            elif sup_b[res] >= 5 and sup_b[res] >= sup_a[res] * 2:
                _do_trade(fb, fa, res, amount, t, event_log, key)
                break
        else:
            # No natural trade trigger — random small negotiation failure
            if random.random() < 0.04 and tension < 20:
                RIVALRIES[key] = RIVALRIES.get(key, 0) + 3
                msg = (f"Tick {t:03d}: 🚫 Trade negotiations between "
                       f"{fa.name} & {fb.name} failed (+3 tension)")
                event_log.append(msg)


def _faction_raids(active, t, event_log):
    for fa, fb in combinations(active, 2):
        key     = tuple(sorted([fa.name, fb.name]))
        tension = RIVALRIES.get(key, 0)
        if tension <= 35:
            continue
        if random.random() > 0.20:   # 20% triggered per eligible pair per tick
            continue

        raider, victim = (fa, fb) if random.random() < 0.5 else (fb, fa)
        if not victim.territory or not raider.members:
            continue

        target_pos  = random.choice(victim.territory)
        chunk       = world[target_pos[0]][target_pos[1]]
        haul        = {}
        import technology as _tech
        raid_mult   = _tech.raid_multiplier(raider)
        for res in RES_TRADE:
            steal = int(chunk['resources'][res] * 0.20) * raid_mult
            if steal > 0:
                chunk['resources'][res] -= steal
                haul[res] = steal

        if not haul:
            continue

        lucky = random.choice(raider.members)
        for res, amt in haul.items():
            lucky.inventory[res] = lucky.inventory.get(res, 0) + amt
        add_belief(lucky, 'the_strong_take')
        RIVALRIES[key] = RIVALRIES.get(key, 0) + 10

        haul_str = ', '.join(f"{v} {k}" for k, v in haul.items())
        raid_log.append((t, raider.name, victim.name, haul_str))
        msg = (f"Tick {t:03d}: ⚔ RAID: {raider.name} plundered "
               f"{victim.name}'s territory — seized {haul_str} (+10 tension)")
        event_log.append(msg)
        print(msg)
        import diplomacy as _dip
        _dip.adjust_rep(raider.name, -1, 'raid', t)
        # Break any existing treaty if a faction raids its signatory
        if _dip.has_treaty(raider.name, victim.name):
            _dip.break_treaty(raider.name, victim.name, t, event_log,
                              [f for f in active if f.members])


def _individual_barter(people, t, event_log):
    chunk_map = defaultdict(list)
    for p in people:
        chunk_map[(p.r, p.c)].append(p)

    for group in chunk_map.values():
        if len(group) < 2:
            continue
        random.shuffle(group)
        for i in range(0, len(group) - 1, 2):
            a, b = group[i], group[i + 1]
            for res in RES_TRADE:
                if a.inventory.get(res, 0) >= 3 and b.inventory.get(res, 0) == 0:
                    a.inventory[res] -= 1
                    b.inventory[res] += 1
                    a.trust[b.name]  = a.trust.get(b.name, 0) + 1
                    b.trust[a.name]  = b.trust.get(a.name, 0) + 1
                    a.trade_count    = getattr(a, 'trade_count', 0) + 1
                    b.trade_count    = getattr(b, 'trade_count', 0) + 1
                    # Pay with currency if available
                    pay = min(getattr(b, 'currency', 0), BASE_PRICES.get(res, 1))
                    if pay > 0:
                        b.currency            = getattr(b, 'currency', 0) - pay
                        a.currency            = getattr(a, 'currency', 0) + pay
                    break


def _scarcity_shock(people, t, event_log):
    global _last_shock_res
    choices = [r for r in RES_TRADE if r != _last_shock_res]
    res = random.choice(choices)
    _last_shock_res = res
    scarcity_events.append((t, res))
    for row in world:
        for chunk in row:
            chunk['resources'][res] = max(0, int(chunk['resources'][res] * 0.85))
    line = '!' * 56
    msg  = f"Tick {t:03d}: 📉 {res.upper()} SHORTAGE — {res} running low across the land"
    event_log.append(msg)
    print(f"\n{line}\n{msg}\n{line}")


# ══════════════════════════════════════════════════════════════════════════════
# Wealth metrics
# ══════════════════════════════════════════════════════════════════════════════

def inhabitant_wealth(inh) -> float:
    return (sum(inh.inventory.get(k, 0) * BASE_PRICES.get(k, 1) for k in RES_TRADE)
            + getattr(inh, 'currency', 0))


def faction_wealth(faction) -> float:
    return (sum(inhabitant_wealth(m) for m in faction.members)
            + faction.food_reserve * BASE_PRICES['food'])


def gini_coefficient(people) -> float:
    vals = sorted(max(0.0, inhabitant_wealth(p)) for p in people)
    n    = len(vals)
    if n == 0 or sum(vals) == 0:
        return 0.0
    total = sum(vals)
    cum   = sum((2 * (i + 1) - n - 1) * v for i, v in enumerate(vals))
    return cum / (n * total)


def wealth_summary_line(factions, people) -> str:
    """One-line economy state for the live display."""
    active = [f for f in factions if f.members]
    if not active:
        return ''
    w_most  = max(active, key=faction_wealth)
    w_least = min(active, key=faction_wealth)
    g       = gini_coefficient(people)
    routes  = sum(1 for r in trade_routes.values() if r['active'])
    raids   = len(raid_log)
    return (f"💰 Wealthiest: {w_most.name[:20]}  "
            f"Poorest: {w_least.name[:20]}  "
            f"Gini:{g:.2f}  Routes:{routes}  Raids:{raids}")


# ══════════════════════════════════════════════════════════════════════════════
# Public tick function
# ══════════════════════════════════════════════════════════════════════════════

def economy_tick(people, factions, t, event_log):
    """Run one tick of the economy layer (call after factions_layer)."""
    active = [f for f in factions if f.members]

    # 1. Currency invention (tick 50+)
    if t >= 50:
        for faction in active:
            _invent_currency(faction, t, event_log)

    # 2. Dynamic price updates (every 5 ticks to reduce spam)
    if t % 5 == 0:
        for faction in active:
            _update_prices(faction, t, event_log)

    # 3. Scarcity shock (every 50 ticks) with 5-tick advance warning
    if t % 50 == 45 and t > 0:
        msg = f"Tick {t:03d}: 📣 Rumors of coming shortage spread through the land..."
        event_log.append(msg)
        print(msg)
    if t % 50 == 0 and t > 0:
        _scarcity_shock(people, t, event_log)

    # 4. Individual barter
    _individual_barter(people, t, event_log)

    # 5. Inter-faction trade (every 3 ticks to avoid log spam)
    if t % 3 == 0 and len(active) >= 2:
        _faction_trade(active, t, event_log)

    # 6. Raiding (tension > 50)
    if len(active) >= 2:
        _faction_raids(active, t, event_log)


# ══════════════════════════════════════════════════════════════════════════════
# Final report
# ══════════════════════════════════════════════════════════════════════════════

def economy_report(factions, people, ticks):
    sep    = '─' * 72
    active = [f for f in factions if f.members]
    print(f"\n{sep}")
    print(f"ECONOMY SUMMARY — {ticks} ticks")
    print(sep)

    # Currency issued
    print("  Currencies:")
    issued = False
    for f in factions:
        if f.name in faction_currencies:
            cname = faction_currencies[f.name]['name']
            total = sum(getattr(m, 'currency', 0) for m in f.members) if f.members else 0
            print(f"    {f.name:<30}  '{cname}'  ({total} in circulation)")
            issued = True
    if not issued:
        print("    (none invented — no faction reached tick 50 with trade_builds_bonds)")

    # Wealth
    print()
    if active:
        wealthiest = max(active, key=faction_wealth)
        poorest    = min(active, key=faction_wealth)
        print(f"  Wealthiest faction : {wealthiest.name}  "
              f"(wealth {faction_wealth(wealthiest):.0f})")
        print(f"  Poorest faction    : {poorest.name}  "
              f"(wealth {faction_wealth(poorest):.0f})")
    if people:
        g = gini_coefficient(people)
        label = 'high inequality' if g > 0.5 else 'moderate' if g > 0.3 else 'low inequality'
        print(f"  Gini coefficient   : {g:.3f}  ({label})")

    # Trade routes
    n_routes = sum(1 for r in trade_routes.values() if r['active'])
    print(f"\n  Trade routes : {n_routes}")
    for route_key, data in trade_routes.items():
        if data['active']:
            na, nb    = data['names']
            res_parts = ', '.join(data['resources'].keys())
            print(f"    {na} ↔ {nb}  ({res_parts}, {data['count']} trades)")

    # Raids
    print(f"\n  Total raids  : {len(raid_log)}")
    for entry in raid_log[-8:]:
        rt, raider, victim, haul = entry
        print(f"    Tick {rt:03d}: {raider:<28} raided {victim:<28}  {haul}")

    # Scarcity events
    print(f"\n  Scarcity shocks: {len(scarcity_events)}")
    for st, sres in scarcity_events:
        print(f"    Tick {st:03d}: {sres.upper()} shortage (−30% global)")

    # Price history peaks
    print(f"\n  Peak prices reached (vs base):")
    any_peak = False
    for fname, hist in sorted(price_history.items()):
        peaks = {k: max(v) for k, v in hist.items() if v}
        high  = {k: v for k, v in peaks.items() if v > BASE_PRICES.get(k, 1) * 1.4}
        if high:
            parts = '  '.join(f"{k}:{BASE_PRICES.get(k,1)}→{v:.1f}" for k, v in high.items())
            print(f"    {fname:<30}  {parts}")
            any_peak = True
    if not any_peak:
        print("    (prices stayed near base — plentiful supply)")

    print(sep)
