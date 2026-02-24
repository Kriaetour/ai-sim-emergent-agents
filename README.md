# Emergent AI Civilization Simulator

> **Watch factions form, trade, war, betray, and mythologize —
> all from simple survival rules. No scripted behavior. Pure emergence.**

---

## What Is This?

Thirty nameless inhabitants are dropped onto a procedurally generated world. They eat, migrate, and slowly build trust with their neighbors. From that trust come shared beliefs. From those beliefs come factions. From faction rivalry comes trade — and war. Tech trees create asymmetric advantages. Diplomacy builds fragile alliances. And when warriors fall, a local LLM writes their epitaphs in stone.

None of it is scripted. The chronicles, betrayals, and prophecies you read at the end of each run are unique — because the civilization that produced them was unique.

Fully playable on a **$150 Intel N95 mini-PC with 16 GB RAM**. No GPU required.

| Stat | Value |
|------|-------|
| Starting inhabitants | 30 |
| Simulation layers | 8 |
| Ticks per run | 300 (configurable) |
| LLM models | Phi-3 Mini · Llama 3.1 8B (local, via Ollama) |
| External API calls | Zero |
| Scripted events | Zero |

---

## Features

- 🌍 **Chunk-based world** — Biome grid (forest, plains, coast, desert, mountains) with seasonal resource cycles. Winter halts regeneration; spring restores it.

- 👤 **Survivors, not agents** — Each inhabitant moves, gathers food, and builds individual trust scores with every person they meet. They starve, migrate, and die with no guidance.

- 🙏 **Belief from experience** — Beliefs form organically: survive a winter and gain `endurance_rewarded`; trade successfully and gain `trade_builds_bonds`; watch a neighbor starve and gain `trust_no_group`. No assignments.

- 🏛 **Factions from shared belief** — When enough people share enough beliefs, a faction coalesces around them. Factions splinter in schisms when ideological tensions peak, and merge when populations collapse.

- 💰 **Living economy** — Each faction mints its own currency, sets dynamic prices, opens trade routes, and launches raids when diplomacy breaks down. A Gini coefficient tracks wealth inequality across the world.

- ⚔ **Real wars** — War declarations trigger alliance chains on both sides. Multi-tick battles produce casualties logged as named legends. Outcomes range from vassalization to exhaustion stalemate to ceasefire.

- 🔬 **Asymmetric tech tree** — Factions research farming, tools, medicine, metalwork, weapons, and writing at different rates. Writing lets myths spread via trade routes. Weapons tip the balance of war.

- 📜 **Council diplomacy** — Factions vote in councils, sign trade agreements, tribute pacts, and mutual defense treaties. Reputation tracks every broken promise. Surrender terms include exile, tribute, and annexation.

- 🪦 **LLM mythology layer** — A local Llama 3.1 8B model writes chronicles every 50 ticks, unique creation myths for each faction every 100 ticks, and stone epitaphs for every warrior who falls in battle. All output is grounded in real simulation events — no hallucinated factions or invented deaths.

- 🔄 **Every run is different** — Different seed, different biome layout, different belief clusters, different wars. The same code produces The Merchant Pact one run and The Iron Shore the next.

---

## Sample Output

### Faction Summary (Tick 250)

```
  The Wild Rovers  (founded tick 090)
    Members  : Brea, Brek
    Beliefs  : the wilds provide, migration brings hope, endurance rewarded
    Techs    : farming, medicine, metalwork, tools, weapons, writing
    Territory: (1,7)  (2,7)
    Reserve  : 339.9 food
    Reputation: +10 (legendary)

  The Tidal Survivors  (founded tick 150)
    Members  : Arin
    Beliefs  : trust no group, the wilds provide, loyalty above all
    Techs    : tools, writing  [→ farming 9t]
    Territory: (0,1)
    Reserve  : 216.0 food
    Reputation: -4 (disgraced)
```

### Chronicle Entry (Tick 200–250)

```
════════════════════════════════════════════════════════════════════════
  THE CHRONICLE — Age of Ticks 200–250
════════════════════════════════════════════════════════════════════════
  In the age of wars, The Lone Bond raised its banner against The
  Trading Bridge, and the heavens shook as five factions answered the
  call to arms. Emra of The Bound Ones fell first, at the coast of
  (0,2), a champion of loyalty brought low by conquest's hunger. The
  Trading Bridge, steadfast in community, repelled the invasion and
  exiled its aggressors to the far wastes.
════════════════════════════════════════════════════════════════════════
```

### Epitaphs

```
  🪦  Fenn: Fenn of The Lone Bond, noble warrior fallen at battle's
      heart; in unwavering loyalty and strength he kept the ancient oath.

  🪦  Sera: Here lies Sera of The Woven Drifters — endurance rewarded,
      the wise must lead. She proved both, and fell proving them.

  🪦  Yeva: Here lies Yeva of The Salted Bridge, who fell defending
      self reliance.
```

### Faction Myth

```
  📜  MYTH of The Restless Few:
        In times past, when The Salted Bridge stood unyielding against
        our fury, we marched beneath loyalty's banner across the scarred
        plains. By divine decree our creed was forged in conflict —
        endurance is not given, it is carved from loss.
```

---

## Architecture

```
sim.py  (entry point — 300-tick main loop, logging, layer orchestration)
│
├── world.py        Layer 0 · Chunks, biomes, resource regeneration, seasons
├── inhabitants.py  Layer 1 · Survival, movement, gathering, trust scores
├── beliefs.py      Layer 2 · Event-driven belief formation and peer sharing
├── factions.py     Layer 3 · Organic faction formation, schisms, merges
├── economy.py      Layer 4 · Currency, pricing, trade routes, raids, wealth
├── combat.py       Layer 5 · War declarations, alliances, battles, legends
├── technology.py   Layer 6 · Research tree, passive bonuses, writing spread
├── diplomacy.py    Layer 7 · Council votes, treaties, reputation, surrender
├── mythology.py    Layer 8 · LLM chronicles, myths, epitaphs (read-only)
│
├── display.py      Rendering — per-tick output, faction summaries, reports
└── config.py       Settings — models, timeouts, feature flags
```

Each layer is a pure function over shared state. Layers 0–7 are deterministic Python. Layer 8 (mythology) is the only one that touches a network socket — and it degrades gracefully with specific fallback text if Ollama is unavailable.

The terminal output is selectively filtered: only notable events (war declarations, deaths, treaties, discoveries) appear live. Everything goes to a timestamped log file in `logs/`.

---

## Quick Start

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) installed and running

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/Kriaetour/ai-sim-emergent-agents
cd ai-civilization-sim

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Pull the simulation model (fast, agent decisions)
ollama pull phi3:3.8b-mini-4k-instruct-q4_0

# 5. Pull the narrative model (quality, mythology layer)
ollama pull llama3.1:8b-instruct-q4_K_M

# 6. Run
python sim.py
```

### What You'll See

```
Log → logs/run_20260223_190041.txt
Running 300-tick simulation  (wars / schisms / deaths show below)

  [  1/300]  Alive:30  Factions:0  Wars:0  Techs:0  Treaties:0
  ...
Tick 045: ⚡ SCHISM — The Drifting Circle breaks from The Wild Rovers
Tick 060: ⚔ WAR DECLARED — The Lone Bond vs The Trading Bridge (tension 222)
Tick 060: 💀 Fenn (The Lone Bond) fell in battle at (1,6)
  🪦  Fenn: Here lies Fenn of The Lone Bond, who fell keeping the oath.
  ...
════════════════════════════════════════════════════════════════════
  THE CHRONICLE — Age of Ticks 1–50
════════════════════════════════════════════════════════════════════
  ...
```

---

## Hardware

| Tier | Spec | Notes |
|------|------|-------|
| **Minimum (tested)** | Intel N95 · 16 GB RAM | Sim runs in ~8 min with both LLMs |
| **Comfortable** | Any modern CPU · 16 GB RAM | ~3–5 min per run |
| **With GPU** | Any iGPU/dGPU supported by Ollama | Mythology calls become near-instant |

No GPU is required. The simulation itself is pure Python and runs in seconds; the mythology layer waits on Ollama inference. If you want faster runs without narrative, set `MYTHOLOGY_ENABLED = False` in `config.py`.

---

## Configuration

Key settings in `config.py`:

```python
# Models
GAME_MODEL       = "phi3:3.8b-mini-4k-instruct-q4_0"  # Agent decisions (fast)
NARRATIVE_MODEL  = "llama3.1:8b-instruct-q4_K_M"      # Mythology (quality)

# Ollama
OLLAMA_URL       = "http://localhost:11434/api/generate"
OLLAMA_TIMEOUT   = 90          # seconds — increase for slower hardware

# Feature flags
MYTHOLOGY_ENABLED = True       # False → skip all LLM calls, run in seconds

# LLM tuning
LLM_TEMPERATURE  = 0.8         # Higher = more creative, less reliable
LLM_MAX_TOKENS   = 200         # Default ceiling; overridden per call type
```

In `sim.py`:
```python
TICKS = 300   # Change to run shorter (100) or longer (500+) simulations
```

---

## Roadmap

- [ ] **Generational agents** — children inherit beliefs and faction membership from parents
- [ ] **Streamlit live dashboard** — watch the world map, faction borders, and reputation graph update in real time
- [ ] **Religion system** — formalized beliefs become institutions with priests, temples, and schismatic holy wars
- [ ] **Larger world scale** — 16×16 or 32×32 grids, multiple biome clusters, sea travel
- [ ] **Multiple settlements** — factions build fixed towns with storage, walls, and population caps
- [ ] **Persistent history** — cross-run chronicles where great factions from past runs become legends in new ones

---

## License

MIT — do whatever you want with it.

---

*Built to answer the question: how much civilization can emerge from how little code?*
