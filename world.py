import random, time, os, threading, math
from noise import pnoise2

BIOMES  = ['forest', 'plains', 'mountains', 'desert', 'coast', 'sea']
WEIGHTS = [25, 35, 20, 10, 7, 3]   # kept for reference; not used by noise generator
LETTER  = {'forest': '^', 'plains': '.', 'mountains': 'M', 'desert': 'D', 'coast': 'C', 'sea': '~'}
BARS    = ' ▁▂▃▄▅▆▇█'

BIOME_MAX = {
    # forest: 2× food density vs plains  (dense canopy, rich soil)
    'forest':    {'wood': 80, 'food': 28, 'ore': 10, 'stone': 20, 'water': 30},
    'plains':    {'wood': 20, 'food': 14, 'ore':  5, 'stone': 10, 'water': 50},
    'mountains': {'wood':  5, 'food':  4, 'ore': 80, 'stone': 80, 'water': 20},
    # desert: 0.2× food, but ore cap 30 represents surface scrap/raw materials
    'desert':    {'wood':  5, 'food':  3, 'ore': 30, 'stone': 40, 'water':  5},
    'coast':     {'wood': 20, 'food': 14, 'ore':  5, 'stone': 10, 'water': 90},
    # sea: no wild food; only water (used as a habitable=False sentinel)
    'sea':       {'wood':  0, 'food':  0, 'ore':  0, 'stone':  0, 'water': 100},
}

# ── Movement cost per biome ───────────────────────────────────────────────
# Governs the hunger penalty paid on each step.  Plains = baseline 5.
# Forest is deliberately twice the plains cost (0.5× speed) to match the
# 2× food density — high reward, high effort.
# Sea uses 1.5× energy (8 hunger per step); requires the Sailing technology.
BIOME_MOVE_COST: dict[str, int] = {
    'plains':    5,    # baseline
    'coast':     5,    # flat littoral
    'forest':    10,   # 0.5× speed — dense canopy (2× food density compensates)
    'mountains': 9,    # gruelling climb
    'desert':    7,    # heat and soft sand tax the body
    'sea':       8,    # 1.5× seafaring effort  (Sailing required to enter)
}

# ── Compact biome integer IDs (one int per tile; faster than string compare) ─
BIOME_ID: dict[str, int] = {b: i for i, b in enumerate(BIOMES)}
# BIOMES order: forest=0, plains=1, mountains=2, desert=3, coast=4, sea=5
_SEA_ID = BIOME_ID['sea']   # module-level shortcut used in tight loops

GRID             = 8    # current grid side length; updated by update_map_bounds()
INITIAL_GRID     = 8    # minimum / starting grid size
TILES_PER_PERSON = 2.5  # target world-tile : person ratio; drives expansion

# ── Settlement constants ────────────────────────────────────────────────────
SETTLEMENT_RADIUS      = 2    # half-width of protected zone → 5×5 footprint
SETTLEMENT_POP_MIN     = 50   # faction members needed to found a settlement
SETTLEMENT_TICKS_STABLE = 50  # ticks of is_settled required after proto-city (100 total)
SETTLEMENT_HOUSING     = 100  # occupants in zone before procreation is suppressed
SETTLEMENT_STORAGE_CAP = 500  # max food units the storage buffer can hold
SETTLEMENT_MOVE_PENALTY = 10  # extra hunger for outsiders entering enemy walls


class Settlement:
    """A permanent town anchored at tile (r, c) with a 5×5 protected zone.

    status:
      'active'    — walls, storage, and defense bonus are all live
      'abandoned' — walls remain, storage is empty; reclaimable by another faction
    """

    __slots__ = ('owner_faction', 'r', 'c', 'founded_tick',
                 'status', 'storage_buffer', 'housing_capacity')

    def __init__(self, owner_faction: str, r: int, c: int, founded_tick: int):
        self.owner_faction    = owner_faction
        self.r, self.c        = r, c
        self.founded_tick     = founded_tick
        self.status           = 'active'
        self.storage_buffer   = 0.0
        self.housing_capacity = SETTLEMENT_HOUSING

    def in_zone(self, r: int, c: int) -> bool:
        """O(1) test — is (r, c) inside the 5×5 protected zone?"""
        return abs(r - self.r) <= SETTLEMENT_RADIUS and abs(c - self.c) <= SETTLEMENT_RADIUS

    def zone_tiles(self) -> list:
        """All tiles in the protected zone (used when registering the index)."""
        return [
            (self.r + dr, self.c + dc)
            for dr in range(-SETTLEMENT_RADIUS, SETTLEMENT_RADIUS + 1)
            for dc in range(-SETTLEMENT_RADIUS, SETTLEMENT_RADIUS + 1)
        ]

    def local_pop(self, grid_occ: dict) -> int:
        """Count inhabitants currently inside the 5×5 zone."""
        return sum(len(grid_occ.get(tile, [])) for tile in self.zone_tiles())


# ── Settlement spatial index ────────────────────────────────────────────────
# Every tile inside a registered settlement's 5×5 zone maps to its Settlement.
# O(1) for "am I in a town?" lookups inside tightly-looped inhabitant code.
_settlements_index: dict = {}   # (r, c) → Settlement
_settlement_lock = threading.Lock()


def get_settlement_at(r: int, c: int) -> 'Settlement | None':
    """Return the Settlement whose zone contains (r, c), or None."""
    return _settlements_index.get((r, c))


def settlement_register(s: 'Settlement') -> None:
    """Index every tile of *s*'s 5×5 zone so get_settlement_at() finds it."""
    with _settlement_lock:
        for tile in s.zone_tiles():
            _settlements_index[tile] = s


def settlement_unregister(s: 'Settlement') -> None:
    """Remove *s* from the spatial index (on abandonment or destruction)."""
    with _settlement_lock:
        for tile in s.zone_tiles():
            if _settlements_index.get(tile) is s:
                del _settlements_index[tile]


# ── Sea / passability helpers ──────────────────────────────────────────────

def tile_is_sea(r: int, c: int) -> bool:
    """True when the tile at (r, c) is open sea (requires Sailing to enter).
    Uses biome_id int comparison — faster than string lookup in hot paths.
    """
    try:
        return world[r][c]['biome_id'] == _SEA_ID
    except (IndexError, TypeError):
        return False


def coast_score(r: int, c: int) -> int:
    """Count how many of the 8 neighbours of (r, c) are sea tiles.

    Used by the settlement port-bias logic: a high score means the tile
    sits right on the seafront — a desirable spot for a merchant port.
    Uses biome_id int comparison for speed.
    """
    score = 0
    rows  = len(world)
    cols  = len(world[0]) if rows else 0
    for dr in range(-1, 2):
        for dc in range(-1, 2):
            if dr == 0 and dc == 0:
                continue
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and world[nr][nc]['biome_id'] == _SEA_ID:
                score += 1
    return score


# ── Perlin noise terrain parameters ────────────────────────────────────────
_NOISE_SCALE    = 0.35   # sample spacing — lower = larger biome patches
_NOISE_OCTAVES  = 4      # fractal detail passes
_NOISE_OFFSETS: dict = {}   # seeded by _generate_world(); reused for smooth expansion

# Sea-coverage calibration: _generate_world() sets this to the 25th percentile
# of the depth-noise distribution so ~25% of all tiles become sea — regardless
# of which random offset was seeded.  Expansion tiles use the same constant.
_SEA_THRESHOLD: float = -0.10   # overwritten by _generate_world()
_SEA_FRACTION:  float =  0.25   # target sea fraction (25%)


def _biome_from_noise(h: float, m: float, d: float = 0.0) -> str:
    """
    Map (height, moisture, depth) Perlin values to a biome.

    d  — dedicated ocean-depth noise field.  Tiles whose depth falls below
         _SEA_THRESHOLD become sea regardless of height, giving clustered
         ocean bodies that fill ~25% of the map (calibrated by _generate_world).

    h  — elevation / terrain type
    m  — moisture axis (wet vs dry for mid-range elevations)

        d < _SEA_THRESHOLD     → sea        (deep ocean — calibrated)
        h < -0.05              → coast      (low-lying littoral)
        h > 0.55               → mountains
        h > 0.20, m > 0.05    → forest     (elevated wet — 2× food density)
        h > 0.20, m <= 0.05   → desert     (elevated dry — surface scrap ore)
        h <= 0.20, m > -0.10  → plains
        h <= 0.20, m <= -0.10 → desert
    """
    if d < _SEA_THRESHOLD:
        return 'sea'
    if h < -0.05:
        return 'coast'
    if h > 0.55:
        return 'mountains'
    if h > 0.20:
        return 'forest' if m > 0.05 else 'desert'
    return 'plains' if m > -0.10 else 'desert'


def _chunk_from_noise(r: int, c: int) -> dict:
    """Generate a single world tile at (r, c) using the stored noise offsets.
    Called by _generate_world() for the initial map and by update_map_bounds()
    for newly revealed tiles so terrain is geographically continuous.

    Each tile stores `biome_id` (int) alongside `biome` (str) for O(1)
    type-checks in tight loops without string allocation overhead.
    """
    ox        = _NOISE_OFFSETS
    h         = pnoise2(ox['h_ox'] + r * _NOISE_SCALE, ox['h_oy'] + c * _NOISE_SCALE,
                        octaves=_NOISE_OCTAVES)
    m         = pnoise2(ox['m_ox'] + r * _NOISE_SCALE, ox['m_oy'] + c * _NOISE_SCALE,
                        octaves=_NOISE_OCTAVES)
    d         = pnoise2(ox['d_ox'] + r * _NOISE_SCALE, ox['d_oy'] + c * _NOISE_SCALE,
                        octaves=_NOISE_OCTAVES)
    biome     = _biome_from_noise(h, m, d)
    b_id      = BIOME_ID[biome]          # compact int ID
    maxes     = BIOME_MAX[biome]
    resources = {k: random.randint(v // 2, v) if v > 0 else 0
                 for k, v in maxes.items()}
    return {
        'biome':      biome,
        'biome_id':   b_id,              # single int — RAM-efficient type check
        'resources':  resources,
        'habitable':  resources.get('food', 0) > 0,   # sea always False
        'claimed_by': None,
    }


def _generate_world() -> list:
    """
    Build a GRID×GRID world using three independent Perlin noise fields:
      h (height/elevation)  — controls coast / mountain / plains bands
      m (moisture)          — wet vs dry modifier within mid-elevations
      d (ocean depth)       — dedicated ocean field for 20-30% sea coverage

    Sea coverage is calibrated to exactly _SEA_FRACTION: after sampling every
    initial tile's depth value we take the (SEA_FRACTION)th percentile as the
    cutoff so ocean bodies cluster naturally and hit the target regardless of
    the random seed.  update_map_bounds() reuses the same stored threshold.
    """
    global _NOISE_OFFSETS, _SEA_THRESHOLD
    _NOISE_OFFSETS = {
        'h_ox': random.uniform(0, 1000),
        'h_oy': random.uniform(0, 1000),
        'm_ox': random.uniform(0, 1000),
        'm_oy': random.uniform(0, 1000),
        'd_ox': random.uniform(0, 1000),   # ocean depth field
        'd_oy': random.uniform(0, 1000),
    }
    ox = _NOISE_OFFSETS
    # ── Calibrate sea threshold ──────────────────────────────────────────
    d_vals = sorted(
        pnoise2(ox['d_ox'] + r * _NOISE_SCALE, ox['d_oy'] + c * _NOISE_SCALE,
                octaves=_NOISE_OCTAVES)
        for r in range(GRID) for c in range(GRID)
    )
    # Use the SEA_FRACTION-th percentile so ~25% of initial tiles are sea
    _SEA_THRESHOLD = d_vals[max(0, int(len(d_vals) * _SEA_FRACTION) - 1)]
    # ── Generate tiles ───────────────────────────────────────────────────
    grid = []
    for r in range(GRID):
        row = [_chunk_from_noise(r, c) for c in range(GRID)]
        grid.append(row)
    return grid


def make_chunk():
    """Fallback random chunk (not used by the main world generator)."""
    biome = random.choices(BIOMES, weights=WEIGHTS)[0]
    maxes = BIOME_MAX[biome]
    resources = {k: random.randint(v // 2, v) if v > 0 else 0
                 for k, v in maxes.items()}
    return {
        'biome':      biome,
        'biome_id':   BIOME_ID[biome],
        'resources':  resources,
        'habitable':  resources.get('food', 0) > 0,
        'claimed_by': None,
    }


def update_map_bounds(current_pop: int):
    """Expand the world grid when population justifies more tiles.

    Target area = current_pop * TILES_PER_PERSON tiles;
    side length = ceil(sqrt(area)).  Grid never shrinks below INITIAL_GRID.

    New tiles are generated with the same Perlin noise offsets used for the
    original map, so terrain is geographically continuous at every border.

    Resource density (food per tile) is preserved automatically because each
    tile regenerates independently via tick() — total world food scales with
    tile count rather than being divided across a fixed pool.

    Returns (old_size, new_size) if expanded, else None.
    """
    global GRID
    target = max(INITIAL_GRID, math.ceil(math.sqrt(current_pop * TILES_PER_PERSON)))
    old    = len(world)
    if target <= old:
        return None
    # Extend every existing row with new columns
    for r, row in enumerate(world):
        for c in range(old, target):
            row.append(_chunk_from_noise(r, c))
    # Append entirely new rows at full target width
    for r in range(old, target):
        world.append([_chunk_from_noise(r, c) for c in range(target)])
    GRID = target
    return (old, target)


world = _generate_world()

# ── Spatial partition ──────────────────────────────────────────────────────
# grid_occupants  maps (r, c) → [Inhabitant, ...]
# All mutations go through grid_add / grid_remove / grid_move so no inhabitant
# is ever "between tiles".  _grid_lock serialises concurrent moves from the
# four Layer-1 worker threads.

grid_occupants: dict = {}
_grid_lock = threading.Lock()


def grid_add(inh) -> None:
    """Register *inh* at its current (r, c).  Call once after spawn."""
    key = (inh.r, inh.c)
    with _grid_lock:
        if key not in grid_occupants:
            grid_occupants[key] = []
        grid_occupants[key].append(inh)


def grid_remove(inh) -> None:
    """Remove *inh* from grid_occupants at its current (r, c).  Call on death."""
    key = (inh.r, inh.c)
    with _grid_lock:
        lst = grid_occupants.get(key)
        if lst:
            try:
                lst.remove(inh)
            except ValueError:
                pass


def grid_move(inh, new_r: int, new_c: int) -> None:
    """Atomically remove *inh* from its old tile, update inh.r/inh.c, and
    register it on the new tile — all under one lock so no thread ever observes
    an inhabitant that is absent from grid_occupants.
    """
    old_key = (inh.r, inh.c)
    new_key = (new_r, new_c)
    with _grid_lock:
        # Remove from old tile
        lst = grid_occupants.get(old_key)
        if lst:
            try:
                lst.remove(inh)
            except ValueError:
                pass
        # Update position
        inh.r, inh.c = new_r, new_c
        # Add to new tile
        if new_key not in grid_occupants:
            grid_occupants[new_key] = []
        grid_occupants[new_key].append(inh)


def grid_neighbors(r: int, c: int) -> list:
    """Return a snapshot list of all Inhabitants in the 3x3 neighbourhood of (r, c)
    (the tile itself plus the 8 surrounding tiles).
    The snapshot is safe to iterate after the lock is released.
    """
    result = []
    with _grid_lock:
        for dr in range(-1, 2):
            for dc in range(-1, 2):
                nr, nc = r + dr, c + dc
                if 0 <= nr < GRID and 0 <= nc < GRID:
                    lst = grid_occupants.get((nr, nc))
                    if lst:
                        result.extend(lst)
    return result


def tick(regen_rate: float, *, pop: int, pop_cap: int) -> None:
    """
    Regenerate world resources each tick.

    Food regen scales smoothly upward with population pressure so a large
    civilisation doesn't strip the land bare:
      - Normal:     food_mult = 1.0 + 0.5 * (pop / pop_cap)
          pop=0   → 1.0×  (no change)
          pop=60  → 1.25× (+25 %)
          pop=120 → 1.5×  (+50 %)
      - Extinction guard: if pop < 10 % of cap, double food regen so a
        near-wiped civilisation can recover without requiring player action.

    Sea tiles are skipped entirely — their caps are all 0 (except water)
    and nothing meaningful regenerates.  This saves ~25 % of inner-loop
    iterations on a typical map (N95 RAM/CPU optimisation).
    """
    pressure   = min(1.0, pop / pop_cap) if pop_cap > 0 else 0.0
    food_mult  = 1.0 + 0.5 * pressure
    # Extinction guard: tiny population → generous food recovery burst
    if pop_cap > 0 and pop < max(2, pop_cap * 0.10):
        food_mult = 2.0

    for row in world:
        for chunk in row:
            if chunk['biome_id'] == _SEA_ID:   # int compare: skip sea (fast)
                continue
            maxes = BIOME_MAX[chunk['biome']]
            res   = chunk['resources']
            for k in res:
                cap   = maxes[k]
                rate  = regen_rate * food_mult if k == 'food' else regen_rate
                res[k] = min(cap, res[k] + int((cap - res[k]) * rate))
            chunk['habitable'] = res['food'] > 0

def vitality(chunk):
    """0-8 score: avg resource fill across all non-zero-cap slots."""
    maxes = BIOME_MAX[chunk['biome']]
    slots = [(chunk['resources'][k], maxes[k]) for k in maxes if maxes[k] > 0]
    if not slots:
        return 0   # sea tile — all caps are 0 except water which we skip for display
    pct = sum(v / m for v, m in slots) / len(slots)
    return int(pct * 8)

def world_totals():
    totals = {k: 0 for k in ('wood', 'food', 'ore', 'stone', 'water')}
    for row in world:
        for chunk in row:
            for k in totals:
                totals[k] += chunk['resources'][k]
    return totals

def print_world(t):
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"╔══ Tick {t:04d} ══════════════════════════════════════════════╗")
    print(f"║  ^=Forest  .=Plains  M=Mountains  D=Desert  C=Coast  ~=Sea  ║")
    print(f"║  Glyph + vitality bar (▁..█ = low..full)  ~=impassable sea   ║")
    print(f"╠════════════════════════════════════════════════════════════════╣")
    for r, row in enumerate(world):
        cells = []
        for chunk in row:
            b   = chunk['biome']
            bar = ' ' if b == 'sea' else BARS[vitality(chunk)]
            cells.append(f"{LETTER[b]}{bar}")
        print(f"║  {'  '.join(cells)}  ║")
    print(f"╠════════════════════════════════════════════════════════════════╣")
    totals    = world_totals()
    habitable = sum(c['habitable'] for row in world for c in row)
    total_tiles = sum(len(row) for row in world)
    sea_tiles   = sum(1 for row in world for c in row if c['biome'] == 'sea')
    print(f"║  {'  '.join(f'{k[:2].upper()}:{v:4d}' for k, v in totals.items())}  "
          f"Hab:{habitable:3d}/{total_tiles}  Sea:{sea_tiles:3d}  ║")
    print(f"╚════════════════════════════════════════════════════════════════╝")

if __name__ == '__main__':
    for t in range(1, 21):
        print_world(t)
        tick()
        time.sleep(0.4)

    print_world(20)
    print("\nSimulation complete — resources pulsed to near-capacity.")
