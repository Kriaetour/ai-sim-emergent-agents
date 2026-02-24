import random, time, os

BIOMES  = ['forest', 'plains', 'mountains', 'desert', 'coast']
WEIGHTS = [25, 35, 20, 10, 10]
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

def make_chunk():
    biome = random.choices(BIOMES, weights=WEIGHTS)[0]
    maxes = BIOME_MAX[biome]
    resources = {k: random.randint(v // 2, v) for k, v in maxes.items()}
    return {
        'biome':      biome,
        'resources':  resources,
        'habitable':  resources['water'] > 0 and resources['food'] > 0,
        'claimed_by': None,
    }

world = [[make_chunk() for _ in range(GRID)] for _ in range(GRID)]

def tick(regen_rate=0.05):
    for row in world:
        for chunk in row:
            maxes = BIOME_MAX[chunk['biome']]
            res   = chunk['resources']
            for k in res:
                cap      = maxes[k]
                res[k]   = min(cap, res[k] + int((cap - res[k]) * regen_rate))
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
