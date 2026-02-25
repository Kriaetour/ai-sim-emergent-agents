import random, time, os, sys, threading
sys.stdout.reconfigure(encoding='utf-8')
from collections import defaultdict
from world import world, tick, BIOME_MAX, grid_move, grid_neighbors, get_settlement_at, SETTLEMENT_MOVE_PENALTY, tile_is_sea, BIOME_MOVE_COST, _SEA_ID

NAMES = [
    # ── Original pool ─────────────────────────────────────────────────────
    'Arin', 'Brek', 'Cael', 'Dova', 'Esh',  'Fenn', 'Gara', 'Holt', 'Ivar', 'Joss',
    'Kael', 'Lira', 'Mord', 'Neva', 'Orin', 'Pell', 'Quen', 'Reva', 'Sal',  'Tova',
    'Ursa', 'Vael', 'Wren', 'Xan',  'Yeva', 'Zorn', 'Alun', 'Brea', 'Coro', 'Dusk',
    'Emra', 'Finn', 'Gale', 'Hana', 'Idra', 'Jorn', 'Kira', 'Lyse', 'Mael', 'Nori',
    'Olen', 'Pyra', 'Roan', 'Sera', 'Thev',
    # ── Norse roots ───────────────────────────────────────────────────────
    'Thror', 'Bjorn', 'Sven',  'Dag',   'Rulf', 'Leif', 'Orm',  'Vigr', 'Heid', 'Ravn',
    'Skald', 'Ingr',  'Yrsa',  'Astri', 'Eir',  'Hela', 'Tor',  'Ulf',  'Sigr', 'Frode',
    'Gunne', 'Askel', 'Bard',  'Hronn', 'Valdr','Solva','Tyra', 'Knud', 'Ragn', 'Brand',
    # ── Celtic roots ──────────────────────────────────────────────────────
    'Ael',  'Bryn',  'Cara',  'Drest', 'Gwyl', 'Idris','Lorn', 'Maren','Odra', 'Prenn',
    'Ryke', 'Sola',  'Trev',  'Ulva',  'Wynne','Aldra','Brom', 'Coryn','Elva', 'Faeln',
    'Halv', 'Jeld',  'Kelva', 'Morvyn','Nael', 'Rhynn','Taern','Veld', 'Brenn','Dwyn',
    # ── Germanic roots ────────────────────────────────────────────────────
    'Vark',  'Stav', 'Trel',  'Wulf',  'Zarl', 'Drax', 'Elwin','Greld','Ilvar','Luvar',
    'Myra',  'Raed', 'Selk',  'Thorn', 'Uveld','Hael', 'Naev', 'Dreva','Ravna','Welk',
    'Arwald','Burk', 'Delk',  'Erlan', 'Folke','Griml','Harwe','Irmin','Jutla','Konr',
]
RES_KEYS             = ['food', 'wood', 'ore', 'stone', 'water']
TRUST_PRUNE_HORIZON  = 500  # ticks of absence before a trust entry is pruned
FOOD_FLOOR           = 3   # flee if chunk food drops below this
MAX_GATHERERS = 5   # max per chunk per tick before crowding pushes lowest-trust out
PROC_TRUST_MIN   = 5    # minimum mutual trust score required to procreate
PROC_HUNGER_MAX  = 30   # maximum hunger level allowed to procreate (not starving)
WINTER_START  = 25  # tick offset within the year cycle when winter begins
WINTER_LEN    = 8   # duration in ticks
CYCLE_LEN     = 50  # length of one full year (2 winters per 100-tick run)

# Lock that serialises the partner-selection → child-creation critical section.
# Must be held while checking is_procreating, setting it, deducting food,
# naming the child, and appending to the people list so POP_CAP is never breached.
procreation_lock = threading.Lock()

def is_winter(t):
    phase = (t - 1) % CYCLE_LEN
    return WINTER_START <= phase < WINTER_START + WINTER_LEN

def regen_rate(t):
    return 0.125 if is_winter(t) else 0.25

# ── Inhabitant ─────────────────────────────────────────────────────────────
class Inhabitant:
    __slots__ = (
        'name', 'r', 'c', 'health', 'hunger', 'inventory',
        'beliefs', 'trust', 'trust_last_seen', 'memory', 'trade_count', 'was_pushed',
        'prev_health', 'biome_ticks', 'faction_ticks', 'was_rejected',
        'zero_food_ticks', 'currency',
        'is_procreating',          # True while paired for a birth this tick
        # set externally by sim.py / factions.py / combat.py / diplomacy.py
        'faction',
        # lazily set by technology.py (accessed via getattr with defaults)
        '_medicine_buffer', '_plague_resist', '_prev_hp_medicine',
        # set to True by technology.py when the faction owns Sailing
        '_can_sail',
        # Religion object pointer and priesthood flag (managed by religion.py)
        'religion', 'is_priest',
    )

    def __init__(self, name, r, c):
        self.name      = name
        self.r, self.c = r, c
        self.health    = 100
        self.hunger    = 0
        self.inventory = {k: (3 if k == 'food' else 0) for k in RES_KEYS}
        self.beliefs        = []
        self.trust          = {}   # name -> trust score
        self.trust_last_seen = {}  # name -> tick last seen (for pruning)
        self.memory         = []   # (r, c) cells known to have food
        self.trade_count    = 0              # cumulative successful trades
        self.was_pushed     = False          # evicted by crowd-control this tick
        self.prev_health    = 100            # health at start of previous tick
        self.biome_ticks    = defaultdict(int)  # ticks spent per biome name
        self.faction_ticks  = 0              # ticks spent inside a faction
        self.was_rejected   = False          # denied faction membership this tick
        self.zero_food_ticks = 0             # consecutive ticks with 0 personal food
        self.currency       = 0              # units of faction currency held
        self.is_procreating = False          # True while participating in a birth this tick
        self._can_sail      = False          # True when faction owns Sailing tech
        self.religion       = None           # Religion object pointer (or None)
        self.is_priest      = False          # True when designated as faction priest

    @property
    def total_trust(self):
        return sum(self.trust.values())

    def biome_label(self):
        b = world[self.r][self.c]['biome'].capitalize()
        return f"{b}({self.r},{self.c})"

    def can_procreate(self, other: 'Inhabitant') -> bool:
        """Return True when both inhabitants meet the procreation conditions.

        Conditions:
          • Neither is already mid-birth on another thread (is_procreating flag).
          • Both share the exact same tile.
          • Mutual trust >= PROC_TRUST_MIN (a few shared encounters).
          • Neither is starving (hunger < PROC_HUNGER_MAX).
          • Both have at least 1 food unit to contribute to the child.
        """
        return (
            not self.is_procreating and not other.is_procreating  # not already paired
            and self.r == other.r and self.c == other.c            # same tile
            and self.trust.get(other.name, 0)  >= PROC_TRUST_MIN  # mutual trust
            and other.trust.get(self.name,  0) >= PROC_TRUST_MIN
            and self.hunger  < PROC_HUNGER_MAX                    # not starving
            and other.hunger < PROC_HUNGER_MAX
            and self.inventory.get('food',  0) > 0                # has food for child
            and other.inventory.get('food', 0) > 0
        )


_GEN_SUFFIXES = ['II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X']


def get_unique_name(base_name: str, living_people: list) -> str:
    """
    Return a name that does not collide with any living inhabitant.
    Appends Roman-numeral suffixes (II, III, IV … X) then falls back to
    numeric suffixes (11, 12, …) if the base pool is exhausted.
    """
    taken = {p.name for p in living_people}
    if base_name not in taken:
        return base_name
    for suffix in _GEN_SUFFIXES:
        candidate = f"{base_name} {suffix}"
        if candidate not in taken:
            return candidate
    i = 11
    while True:
        candidate = f"{base_name} {i}"
        if candidate not in taken:
            return candidate
        i += 1


def make_child(
    parent_a: 'Inhabitant',
    parent_b: 'Inhabitant',
    name: str,
    living_people: list,
) -> 'Inhabitant':
    """
    Create a new Inhabitant as offspring of parent_a and parent_b.
    The child's name is first resolved to a unique value via get_unique_name.
    Beliefs: random 50% sample from the union of both parents' beliefs.
    Starts on the same tile as parent_a with modest food drawn from both parents.
    """
    unique_name = get_unique_name(name, living_people)
    child = Inhabitant(unique_name, parent_a.r, parent_a.c)

    # 50% belief blend: union → random half
    belief_pool = list(dict.fromkeys(parent_a.beliefs + parent_b.beliefs))  # deduped, ordered
    sample_n    = max(1, len(belief_pool) // 2) if belief_pool else 0
    child.beliefs = random.sample(belief_pool, sample_n) if belief_pool else []

    # Seed trust toward both parents (last_seen=0 so long-running sims can
    # prune the entry if the pair never co-inhabit or share a faction)
    child.trust[parent_a.name]            = 30
    child.trust[parent_b.name]            = 30
    child.trust_last_seen[parent_a.name]  = 0
    child.trust_last_seen[parent_b.name]  = 0

    # Small food cost deducted from parents; child starts with that sum
    cost = 5
    parent_a.inventory['food'] = max(0, parent_a.inventory.get('food', 0) - cost)
    parent_b.inventory['food'] = max(0, parent_b.inventory.get('food', 0) - cost)
    child.inventory['food']    = cost * 2

    return child

# ── Navigation ─────────────────────────────────────────────────────────────
def best_neighbor(inh, exclude_self=False):
    """Adjacent (or self) cell with the best cost-adjusted food score.

    Score = raw_food * (PLAINS_COST / move_cost)
      • Plains/coast cost=5 → multiplier 1.0  (baseline)
      • Forest       cost=10 → multiplier 0.5  but forest has 2× food cap
      • Desert       cost= 7 → multiplier 0.71 (not worth it unless food-rich)
      • Mountains    cost= 9 → multiplier 0.56 (avoid unless only option)
      • Sea          cost= 8 → multiplier 0.63 + 2× sailor bonus (requires Sailing)

    This naturally channels traffic through plains roads / coast paths without
    explicit A* — inhabitants greedily prefer wherever food-per-effort is highest.
    """
    _PLAINS_COST = 5.0
    can_sail     = inh._can_sail
    # Current-tile score: staying is free (cost=0), use raw food as baseline
    cur_food     = -1.0 if exclude_self else float(
        world[inh.r][inh.c]['resources']['food'])
    best, br, bc = cur_food, inh.r, inh.c
    rows, cols   = len(world), len(world[0]) if world else 0
    for dr in range(-1, 2):
        for dc in range(-1, 2):
            if exclude_self and dr == 0 and dc == 0:
                continue
            nr, nc = inh.r + dr, inh.c + dc
            if not (0 <= nr < rows and 0 <= nc < cols):
                continue
            nb_chunk = world[nr][nc]
            # Sea tiles are impassable without Sailing
            if nb_chunk['biome_id'] == _SEA_ID and not can_sail:
                continue
            f    = float(nb_chunk['resources']['food'])
            cost = BIOME_MOVE_COST.get(nb_chunk['biome'], 5)
            # Cost-adjusted score: penalise expensive biomes proportionally
            f_adj = f * (_PLAINS_COST / cost)
            # Sailors on sea tiles get 2× effective range (speed advantage)
            if nb_chunk['biome_id'] == _SEA_ID:
                f_adj *= 2.0
            # Outsiders treat enemy-walled tiles as having 1/3 the food
            _nb_s = get_settlement_at(nr, nc)
            if (_nb_s and _nb_s.status == 'active'
                    and _nb_s.owner_faction != getattr(inh, 'faction', None)):
                f_adj /= 3.0
            if f_adj > best:
                best, br, bc = f_adj, nr, nc
    return br, bc

def force_move(inh):
    """Step to best adjacent cell (not current); pay biome-appropriate hunger.

    Uses grid_move so grid_occupants is kept consistent even when called
    from the serial preamble or concurrently from worker threads.
    """
    nr, nc = best_neighbor(inh, exclude_self=True)
    grid_move(inh, nr, nc)
    cost = BIOME_MOVE_COST.get(world[inh.r][inh.c]['biome'], 5)
    inh.hunger = min(120, inh.hunger + cost)

# ── Per-tick simulation ────────────────────────────────────────────────────
def do_tick_preamble(people, t):
    """Serial pre-pass: shuffle, flag reset, crowd control, trust pruning.

    Must complete in the main thread before any per-inhabitant threads start
    so that crowd-control decisions are globally consistent.
    """
    random.shuffle(people)

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

    # Prune trust entries for people not encountered in the last TRUST_PRUNE_HORIZON ticks
    for inh in people:
        stale = [
            name for name, last_t in inh.trust_last_seen.items()
            if t - last_t > TRUST_PRUNE_HORIZON
        ]
        for name in stale:
            inh.trust.pop(name, None)
            del inh.trust_last_seen[name]


def do_tick_body(inh, all_people, t, event_log_ref, dead_out,
                 world_lock=None, log_lock=None, trade_lock=None):
    """Process one inhabitant for a single tick.  Thread-safe when the three
    optional locks are supplied:

      world_lock  — guards read-modify-write of world tile resources (steps 5/5b)
      log_lock    — guards event_log_ref.append() (step 7)
      trade_lock  — guards cross-inhabitant inventory swaps (step 6)

    ``dead_out`` is a caller-supplied list; dead inhabitants are appended to it.
    """
    inh.prev_health = inh.health

    # 1. Hunger & health damage
    inh.hunger += 7
    if inh.hunger > 40:
        if getattr(inh, '_medicine_buffer', 0) > 0:
            inh._medicine_buffer -= 1   # medicine absorbs this tick's HP loss
        else:
            inh.health -= 10

    # 2. Update memory (world read only — benign under GIL)
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
        # Try withdrawing from home settlement storage before wandering
        _cur_s = get_settlement_at(inh.r, inh.c)
        if (_cur_s and _cur_s.status == 'active'
                and _cur_s.owner_faction == getattr(inh, 'faction', None)
                and _cur_s.storage_buffer >= 1):
            _cur_s.storage_buffer -= 1
            inh.inventory['food'] += 1
            inh.hunger = max(0, inh.hunger - 7)
        else:
            force_move(inh)
            # Extra hunger surcharge for crossing into enemy-walled territory
            _dst_s = get_settlement_at(inh.r, inh.c)
            if (_dst_s and _dst_s.status == 'active'
                    and _dst_s.owner_faction != getattr(inh, 'faction', None)):
                inh.hunger = min(120, inh.hunger + SETTLEMENT_MOVE_PENALTY)

    # 4. Move if current chunk is nearly empty  (priests abstain — religion_tick moves them)
    if (not getattr(inh, 'is_priest', False)
            and world[inh.r][inh.c]['resources']['food'] < FOOD_FLOOR):
        nr, nc = best_neighbor(inh)
        if (nr, nc) != (inh.r, inh.c):
            grid_move(inh, nr, nc)   # keeps grid_occupants in sync
            cost = BIOME_MOVE_COST.get(world[inh.r][inh.c]['biome'], 5)
            inh.hunger = min(120, inh.hunger + cost)
            # Extra hunger surcharge for stepping into enemy-walled territory
            _mv_s = get_settlement_at(inh.r, inh.c)
            if (_mv_s and _mv_s.status == 'active'
                    and _mv_s.owner_faction != getattr(inh, 'faction', None)):
                inh.hunger = min(120, inh.hunger + SETTLEMENT_MOVE_PENALTY)

    # 5. Gather 1 food from chunk — world write, requires lock  (priests abstain)
    _lock5 = world_lock if world_lock is not None else _NullLock()
    with _lock5:
        res = world[inh.r][inh.c]['resources']
        if not getattr(inh, 'is_priest', False):
            gather = min(1, res['food'])
            res['food']           -= gather
            inh.inventory['food'] += gather

            # 5b. Gather 1 non-food resource (30% chance; weighted by chunk availability)
            _NF  = ('wood', 'stone', 'ore')
            _nfw = [max(0, res.get(k, 0)) for k in _NF]
            _nft = sum(_nfw)
            if _nft > 0 and random.random() < 0.30:
                pick = random.choices(_NF, weights=_nfw, k=1)[0]
                inh.inventory[pick] += 1
                res[pick]           -= 1

    # 6. Social — trust (own dict, no lock needed) + trades (cross-inhabitant)
    # Use grid_neighbors to fetch only the 3x3 spatial neighbourhood; this
    # replaces the O(N) all_people scan and limits trust-dict growth to
    # inhabitants that are physically reachable (satisfies memory-safety goal).
    inh_faction = getattr(inh, 'faction', None)
    spatial_nb  = grid_neighbors(inh.r, inh.c)   # snapshot from spatial partition
    visible = [
        p for p in spatial_nb if p is not inh
        and (
            (abs(p.r - inh.r) <= 1 and abs(p.c - inh.c) <= 1)   # 3x3 grid (always true
                                                                   # by construction)
            or (inh_faction is not None                           # same faction
                and getattr(p, 'faction', None) == inh_faction)
        )
    ]
    _lock6 = trade_lock if trade_lock is not None else _NullLock()
    for nb in visible:
        inh.trust[nb.name]           = inh.trust.get(nb.name, 0) + 1
        inh.trust_last_seen[nb.name] = t                        # refresh timestamp
        # Trades only happen when physically on the same tile
        if nb.r == inh.r and nb.c == inh.c and random.random() < 0.5:
            have = [k for k in RES_KEYS if inh.inventory[k] > 0]
            want = [k for k in RES_KEYS if nb.inventory[k]  > 0]
            if have and want:
                give, get = random.choice(have), random.choice(want)
                # Inventory swap touches two inhabitants — use trade_lock
                with _lock6:
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
        dead_out.append(inh)
        msg = f"Tick {t:02d}: \u2620 {inh.name} starved at {inh.biome_label()}"
        _llock = log_lock if log_lock is not None else _NullLock()
        with _llock:
            event_log_ref.append(msg)


class _NullLock:
    """No-op context manager used when no real lock is provided."""
    def __enter__(self): return self
    def __exit__(self, *_): pass


def do_tick(people, t, event_log):
    """Single-threaded convenience wrapper (used by the standalone __main__ block)."""
    do_tick_preamble(people, t)
    dead = []
    for inh in people:
        do_tick_body(inh, people, t, event_log, dead)
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
