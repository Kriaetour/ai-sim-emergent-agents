import random, time, os, sys
sys.stdout.reconfigure(encoding='utf-8')
from collections import defaultdict
from world import world, tick, GRID, BIOME_MAX

NAMES = [
    'Arin','Brek','Cael','Dova','Esh','Fenn','Gara','Holt','Ivar','Joss',
    'Kael','Lira','Mord','Neva','Orin','Pell','Quen','Reva','Sal','Tova',
    'Ursa','Vael','Wren','Xan','Yeva','Zorn','Alun','Brea','Coro','Dusk',
    'Emra','Finn','Gale','Hana','Idra','Jorn','Kira','Lyse','Mael','Nori',
    'Olen','Pyra','Roan','Sera','Thev',
]
RES_KEYS      = ['food', 'wood', 'ore', 'stone', 'water']
FOOD_FLOOR    = 3   # flee if chunk food drops below this
MAX_GATHERERS = 5   # max per chunk per tick before crowding pushes lowest-trust out
WINTER_START  = 25  # tick offset within the year cycle when winter begins
WINTER_LEN    = 8   # duration in ticks
CYCLE_LEN     = 50  # length of one full year (2 winters per 100-tick run)

def is_winter(t):
    phase = (t - 1) % CYCLE_LEN
    return WINTER_START <= phase < WINTER_START + WINTER_LEN

def regen_rate(t):
    return 0.125 if is_winter(t) else 0.25

# ── Inhabitant ─────────────────────────────────────────────────────────────
class Inhabitant:
    def __init__(self, name, r, c):
        self.name      = name
        self.r, self.c = r, c
        self.health    = 100
        self.hunger    = 0
        self.inventory = {k: (3 if k == 'food' else 0) for k in RES_KEYS}
        self.beliefs   = []
        self.trust     = {}
        self.memory    = []   # (r, c) cells known to have food
        self.trade_count    = 0              # cumulative successful trades
        self.was_pushed     = False          # evicted by crowd-control this tick
        self.prev_health    = 100            # health at start of previous tick
        self.biome_ticks    = defaultdict(int)  # ticks spent per biome name
        self.faction_ticks  = 0              # ticks spent inside a faction
        self.was_rejected   = False          # denied faction membership this tick
        self.zero_food_ticks = 0             # consecutive ticks with 0 personal food
        self.currency       = 0              # units of faction currency held

    @property
    def total_trust(self):
        return sum(self.trust.values())

    def biome_label(self):
        b = world[self.r][self.c]['biome'].capitalize()
        return f"{b}({self.r},{self.c})"

# ── Navigation ─────────────────────────────────────────────────────────────
def best_neighbor(inh, exclude_self=False):
    """Adjacent (or self) cell with most food."""
    cur_food = -1 if exclude_self else world[inh.r][inh.c]['resources']['food']
    best, br, bc = cur_food, inh.r, inh.c
    for dr in range(-1, 2):
        for dc in range(-1, 2):
            if exclude_self and dr == 0 and dc == 0:
                continue
            nr, nc = inh.r + dr, inh.c + dc
            if 0 <= nr < GRID and 0 <= nc < GRID:
                f = world[nr][nc]['resources']['food']
                if f > best:
                    best, br, bc = f, nr, nc
    return br, bc

def force_move(inh):
    """Step to best adjacent cell (not current); pay 5 hunger."""
    nr, nc = best_neighbor(inh, exclude_self=True)
    inh.r, inh.c = nr, nc
    inh.hunger = min(120, inh.hunger + 5)

# ── Per-tick simulation ────────────────────────────────────────────────────
def do_tick(people, t, event_log):
    random.shuffle(people)
    dead = []

    # Reset per-tick flags
    for inh in people:
        inh.was_pushed   = False
        inh.was_rejected = False

    # Population pressure: crowd control before anyone acts
    chunk_crowd = defaultdict(list)
    for inh in people:
        chunk_crowd[(inh.r, inh.c)].append(inh)
    for group in chunk_crowd.values():
        if len(group) > MAX_GATHERERS:
            group.sort(key=lambda p: p.total_trust, reverse=True)
            for exile in group[MAX_GATHERERS:]:
                force_move(exile)
                exile.was_pushed = True

    # Individual actions
    for inh in people:
        inh.prev_health = inh.health
        # 1. Hunger & health damage
        inh.hunger += 7
        if inh.hunger > 40:
            if getattr(inh, '_medicine_buffer', 0) > 0:
                inh._medicine_buffer -= 1   # medicine absorbs this tick's HP loss
            else:
                inh.health -= 10

        # 2. Update memory
        if world[inh.r][inh.c]['resources']['food'] > 0:
            if (inh.r, inh.c) not in inh.memory:
                inh.memory.append((inh.r, inh.c))

        # 3. Eat or flee (eat twice if very hungry)
        if inh.inventory['food'] > 0:
            inh.inventory['food'] -= 1
            inh.hunger = max(0, inh.hunger - 7)
            if inh.hunger > 30 and inh.inventory['food'] > 0:
                inh.inventory['food'] -= 1
                inh.hunger = max(0, inh.hunger - 7)
        else:
            force_move(inh)

        # 4. Move if current chunk is nearly empty
        if world[inh.r][inh.c]['resources']['food'] < FOOD_FLOOR:
            nr, nc = best_neighbor(inh)
            if (nr, nc) != (inh.r, inh.c):
                inh.r, inh.c = nr, nc
                inh.hunger = min(120, inh.hunger + 5)

        # 5. Gather 1 food from chunk
        res    = world[inh.r][inh.c]['resources']
        gather = min(1, res['food'])
        res['food']            -= gather
        inh.inventory['food']  += gather

        # 5b. Gather 1 non-food resource (30% chance; weighted by chunk availability)
        _NF   = ('wood', 'stone', 'ore')
        _nfw  = [max(0, res.get(k, 0)) for k in _NF]
        _nft  = sum(_nfw)
        if _nft > 0 and random.random() < 0.30:
            pick = random.choices(_NF, weights=_nfw, k=1)[0]
            inh.inventory[pick] += 1
            res[pick]           -= 1

        # 6. Social
        neighbors = [p for p in people if p is not inh and p.r == inh.r and p.c == inh.c]
        for nb in neighbors:
            inh.trust[nb.name] = inh.trust.get(nb.name, 0) + 1
            if random.random() < 0.5:
                have = [k for k in RES_KEYS if inh.inventory[k] > 0]
                want = [k for k in RES_KEYS if nb.inventory[k]  > 0]
                if have and want:
                    give, get = random.choice(have), random.choice(want)
                    inh.inventory[give] -= 1; inh.inventory[get]  += 1
                    nb.inventory[get]   -= 1; nb.inventory[give]  += 1
                    inh.trade_count = getattr(inh, 'trade_count', 0) + 1
                    nb.trade_count  = getattr(nb,  'trade_count', 0) + 1

        # 6b. Track biome exposure and personal scarcity
        inh.biome_ticks[world[inh.r][inh.c]['biome']] += 1
        if inh.inventory['food'] == 0:
            inh.zero_food_ticks += 1
        else:
            inh.zero_food_ticks = 0

        # 7. Death
        if inh.health <= 0:
            dead.append(inh)
            event_log.append(f"Tick {t:02d}: \u2620 {inh.name} starved at {inh.biome_label()}")

    for d in dead:
        people.remove(d)
    return dead

# ── Display ────────────────────────────────────────────────────────────────
def print_status(t, people, deaths, event_log, winter):
    os.system('cls' if os.name == 'nt' else 'clear')
    W   = 62
    bar = '─' * W
    season = "❄ WINTER " if winter else "☀ Summer "
    print(f"┌{bar}┐")
    print(f"│  Tick {t:02d}/50  {season}  Alive:{len(people):2d}/30  Deaths:{len(all_dead):2d} total{'':<8}│")
    print(f"├{bar}┤")
    for inh in sorted(people, key=lambda p: (p.health, p.name)):
        trust_top = max(inh.trust, key=inh.trust.get) if inh.trust else '——'
        hp_val    = max(0, inh.health)
        filled    = min(10, hp_val // 10)
        hp_bar    = '█' * filled + '░' * (10 - filled)
        print(f"│  {inh.name:<8} ({inh.r},{inh.c})  HP:[{hp_bar}]"
              f"  Hu:{inh.hunger:3d}  Fd:{inh.inventory['food']:2d}  ♥:{trust_top:<8}│")
    if deaths:
        print(f"├{bar}┤")
        for d in deaths:
            print(f"│  ✗ {d.name} perished – {d.biome_label():<{W-20}}│")
    shown = event_log[-5:]
    if shown:
        print(f"├{bar}┤")
        for msg in shown:
            print(f"│  {msg:<{W-2}}│")
    print(f"└{bar}┘")

if __name__ == '__main__':
    # ── World setup: start chunks near-depleted; deplete 20% of habitable chunks ──
    habitable = [(r, c) for r in range(GRID) for c in range(GRID) if world[r][c]['habitable']]
    assert habitable, "No habitable chunks — rerun to reseed."
    for row in world:
        for chunk in row:
            cap = BIOME_MAX[chunk['biome']]['food']
            chunk['resources']['food'] = random.randint(cap // 2, cap)
    for r, c in random.sample(habitable, max(1, len(habitable) // 5)):
        world[r][c]['resources']['food'] = 0

    # ── Spawn ──────────────────────────────────────────────────────────────────
    people    = [Inhabitant(n, *random.choice(habitable)) for n in random.sample(NAMES, 30)]
    all_dead  = []
    event_log = []

    # ── Run 50 ticks ──────────────────────────────────────────────────────────
    for t in range(1, 51):
        winter = is_winter(t)
        if t == WINTER_START:
            event_log.append(f"Tick {t:02d}: === WINTER ARRIVES === Food regen stops")

        deaths = do_tick(people, t, event_log)
        all_dead.extend(deaths)
        tick(regen_rate(t))
        print_status(t, people, deaths, event_log, winter)
        time.sleep(0.3)
        if not people:
            print("All inhabitants have perished.")
            break

    print(f"\nFinal survivors: {len(people)}/30  |  Total deaths: {len(all_dead)}")
    if people:
        best = max(people, key=lambda p: p.total_trust)
        print(f"Most connected: {best.name} (trust score {best.total_trust})")
    print("\n── Event log ──────────────────────────────────────────────────────────")
    for e in event_log:
        print(f"  {e}")
