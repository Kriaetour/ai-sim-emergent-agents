import random, time, os
from noise import pnoise2

BIOMES  = ['forest', 'plains', 'mountains', 'desert', 'coast']
WEIGHTS = [25, 35, 20, 10, 10]   # kept for reference; not used by noise generator
LETTER  = {'forest': 'F', 'plains': 'P', 'mountains': 'M', 'desert': 'D', 'coast': 'C'}
BARS    = ' ▁▂▃▄▅▆▇█'

BIOME_MAX = {
    'forest':    {'wood': 80, 'food':  8, 'ore': 10, 'stone': 20, 'water': 30},
    'plains':    {'wood': 20, 'food': 14, 'ore':  5, 'stone': 10, 'water': 50},
    'mountains': {'wood':  5, 'food':  4, 'ore': 80, 'stone': 80, 'water': 20},
    'desert':    {'wood':  5, 'food':  1, 'ore': 10, 'stone': 40, 'water':  5},
    'coast':     {'wood': 20, 'food': 14, 'ore':  5, 'stone': 10, 'water': 90},
}

GRID = 8

# ── Perlin noise terrain parameters ────────────────────────────────────────
_NOISE_SCALE   = 0.35   # sample spacing — lower = larger biome patches
_NOISE_OCTAVES = 4      # fractal detail passes


def _biome_from_noise(h: float, m: float) -> str:
    """
    Map (height, moisture) Perlin values in ~[-1, 1] to a biome.

    Height axis  →  elevation / terrain type
    Moisture axis →  wet vs dry modifier for mid-range elevations

        h < -0.05              → coast   (low-lying, near water)
        h > 0.55               → mountains
        h > 0.20, m > 0.05    → forest  (elevated, wet)
        h > 0.20, m <= 0.05   → desert  (elevated, dry plateau)
        h <= 0.20, m > -0.10  → plains
        h <= 0.20, m <= -0.10 → desert  (lowland arid)
    """
    if h < -0.05:
        return 'coast'
    if h > 0.55:
        return 'mountains'
    if h > 0.20:
        return 'forest' if m > 0.05 else 'desert'
    return 'plains' if m > -0.10 else 'desert'


def _generate_world() -> list:
    """
    Build a GRID×GRID world using two independent Perlin noise fields
    (height and moisture) so biomes cluster naturally — coasts neighbour
    coasts, forests grade into plains, mountains form ridges, etc.
    Random offsets give a unique map every run.
    """
    # Unique random offsets so every simulation has a different map
    h_ox = random.uniform(0, 1000)
    h_oy = random.uniform(0, 1000)
    m_ox = random.uniform(0, 1000)
    m_oy = random.uniform(0, 1000)

    grid = []
    for r in range(GRID):
        row = []
        for c in range(GRID):
            h = pnoise2(
                h_ox + r * _NOISE_SCALE,
                h_oy + c * _NOISE_SCALE,
                octaves=_NOISE_OCTAVES,
            )
            m = pnoise2(
                m_ox + r * _NOISE_SCALE,
                m_oy + c * _NOISE_SCALE,
                octaves=_NOISE_OCTAVES,
            )
            biome     = _biome_from_noise(h, m)
            maxes     = BIOME_MAX[biome]
            resources = {k: random.randint(v // 2, v) for k, v in maxes.items()}
            row.append({
                'biome':      biome,
                'resources':  resources,
                'habitable':  resources['water'] > 0 and resources['food'] > 0,
                'claimed_by': None,
            })
        grid.append(row)
    return grid


def make_chunk():
    """Fallback random chunk (not used by the main world generator)."""
    biome = random.choices(BIOMES, weights=WEIGHTS)[0]
    maxes = BIOME_MAX[biome]
    resources = {k: random.randint(v // 2, v) for k, v in maxes.items()}
    return {
        'biome':      biome,
        'resources':  resources,
        'habitable':  resources['water'] > 0 and resources['food'] > 0,
        'claimed_by': None,
    }

world = _generate_world()

def tick(regen_rate: float = 0.05, pop: int = 0, pop_cap: int = 120) -> None:
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
    """
    pressure   = min(1.0, pop / pop_cap) if pop_cap > 0 else 0.0
    food_mult  = 1.0 + 0.5 * pressure
    # Extinction guard: tiny population → generous food recovery burst
    if pop_cap > 0 and pop < max(2, pop_cap * 0.10):
        food_mult = 2.0

    for row in world:
        for chunk in row:
            maxes = BIOME_MAX[chunk['biome']]
            res   = chunk['resources']
            for k in res:
                cap   = maxes[k]
                rate  = regen_rate * food_mult if k == 'food' else regen_rate
                res[k] = min(cap, res[k] + int((cap - res[k]) * rate))
            chunk['habitable'] = res['water'] > 0 and res['food'] > 0

def vitality(chunk):
    """0-8 score: avg resource fill across all slots."""
    maxes = BIOME_MAX[chunk['biome']]
    pct   = sum(chunk['resources'][k] / maxes[k] for k in maxes) / len(maxes)
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
    print(f"╔══ Tick {t:02d}/20 ══════════════════════════════════════╗")
    print(f"║  Biome: F=Forest P=Plains M=Mountains D=Desert C=Coast  ║")
    print(f"║  Cell = BiomeLetter + vitality bar (▁..█ = low..full)   ║")
    print(f"╠═════════════════════════════════════════════════════════╣")
    for r, row in enumerate(world):
        cells = []
        for chunk in row:
            bar = BARS[vitality(chunk)]
            cells.append(f"{LETTER[chunk['biome']]}{bar}")
        print(f"║  {'  '.join(cells)}  ║")
    print(f"╠═════════════════════════════════════════════════════════╣")
    totals    = world_totals()
    habitable = sum(c['habitable'] for row in world for c in row)
    print(f"║  {'  '.join(f'{k[:2].upper()}:{v:4d}' for k, v in totals.items())}  Hab:{habitable:2d}/64  ║")
    print(f"╚═════════════════════════════════════════════════════════╝")

if __name__ == '__main__':
    for t in range(1, 21):
        print_world(t)
        tick()
        time.sleep(0.4)

    print_world(20)
    print("\nSimulation complete — resources pulsed to near-capacity.")
