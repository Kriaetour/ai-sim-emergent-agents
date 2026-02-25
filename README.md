# Emergent AI Civilization Simulator

> **Watch factions form, trade, war, betray, and mythologize —
> all from simple survival rules. No scripted behavior. Pure emergence.**

---

## What Is This?

Thirty nameless inhabitants are dropped onto a Perlin-noise-generated world. They eat, migrate, and slowly build trust with their neighbors. From that trust come shared beliefs. From those beliefs come factions. From faction rivalry comes trade — and war. Tech trees create asymmetric advantages. Diplomacy builds fragile alliances. Inhabitants form families, bear children, and pass their beliefs to the next generation. When enough time passes, the old world shatters and a new era dawns.

None of it is scripted. Every war, betrayal, and alliance you see is unique — because the civilization that produced it was unique.

An optional LLM mythology layer can write chronicles, myths, and epitaphs using a local model via Ollama — but the simulation runs fully standalone with no external dependencies beyond one Python package.

Fully playable on a **$150 Intel N95 mini-PC with 16 GB RAM**. No GPU required.

| Stat | Value |
|------|-------|
| Starting inhabitants | 30 |
| Population cap | 200 (configurable) |
| Simulation layers | 9 (0–8) |
| Ticks per run | 10,000 (configurable) |
| Terrain generation | Perlin noise (height + moisture) |
| LLM mythology | Optional — disabled by default |
| External API calls | Zero |
| Scripted events | Zero |
| Python dependencies | `noise==1.2.2` |

---

## Features

### World & Terrain

- 🌍 **Perlin-noise terrain** — Dual noise fields (height + moisture) produce coherent biome layouts across an 8×8 grid: coast, forest, plains, desert, and mountains. No two worlds are alike.

- 🌱 **Population-scaled food** — Resource regeneration scales with population pressure (`1.0 + 0.5 × pop/cap`). An extinction guard doubles food output when the population drops below 10% of the cap.

- ❄ **Seasons** — A 50-tick cycle with an 8-tick winter window. Winter halts food regeneration (reduced to 0.125×); spring restores it (0.25×). Five resource types: food, wood, ore, stone, water.

### Inhabitants & Procreation

- 👤 **Survivors, not agents** — Each inhabitant moves, gathers food, and builds individual trust scores with every person they meet. They starve, migrate, and die with no guidance. Memory-efficient `__slots__` (19 attributes) keeps RAM usage low even at 200 population.

- 👶 **Generational procreation** — Pairs on the same tile with mutual trust > 25 and food > 20 each can produce a child. Children inherit 50% of their parents' combined beliefs, cost 5 food per parent, start with 10 food, and are born with trust of 30 toward each parent. One birth per tick maximum; no births during winter.

- 📛 **135 fantasy names** — Four name pools (Original, Norse, Celtic, Germanic) with 30–45 names each. When all base names are taken, `get_unique_name` appends Roman numeral suffixes (II–X) then numeric (11+) to guarantee uniqueness across generations.

### Beliefs

- 🙏 **27 event-driven beliefs** — Beliefs form organically from experience: survive a winter → `endurance_rewarded`; trade successfully → `trade_builds_bonds`; watch a neighbor starve → `trust_no_group`. Each inhabitant holds up to 8 beliefs. Co-located neighbors with mutual trust > 10 have a 50% chance to share beliefs each tick.

### Factions

- 🏛 **Organic faction formation** — When three nearby inhabitants (Manhattan distance ≤ 2) share ≥ 2 core beliefs and mutual trust > 8, a faction coalesces around them. Faction names are generated from belief-keyed adjective/noun components ("The Iron Wanderers", "The Salted Few").

- ⚡ **Schisms & mergers** — Ideological minorities (≥ 30% of members) split off every 25 ticks. Solo-member factions within range can merge every 10 ticks. Four ideological conflict pairs block joining and trigger splits.

- 🏰 **Territory & pooling** — Factions control territory chunks, pool 20% of surplus food into reserves, and nudge drifting members back toward their land. Members beyond size 10 with `self_reliance` leave voluntarily.

### Economy

- 💰 **Living economy** — Each faction mints its own currency (15 possible names: shells, iron bits, marked stones…). Dynamic pricing adjusts every 5 ticks based on supply/demand (clamped 0.5×–4×). Trade routes form after 3 successful trades and give +10% bonus (+20% for allies). Scarcity shocks reduce a random resource by 15% globally every 50 ticks.

- ⚔ **Raids** — When tension exceeds 35 between factions, 20% chance per tick to raid. Loot scales with tech: scavenging (2×), weaponry (3×), steel (4×). Raiding breaks treaties and costs reputation.

- 📊 **Gini coefficient** — Wealth inequality is tracked globally across all inhabitants using inventory value plus currency holdings.

### Combat

- ⚔ **Multi-tick wars** — War declarations require crossing a tension threshold of 200 (modified by beliefs and tech). Attackers and defenders recruit alliance chains (60–80% chance per eligible faction). Battles run tick-by-tick with morale-weighted strength. Fallen warriors become named legends.

- 🏳 **War outcomes** — Surrender at 50%+ losses, ceasefire if both sides are broken, or exhaustion after 40 ticks. Winners claim territory. Losers pay 30% food tribute for 20 ticks. 40% of losers are absorbed into the winning faction; 30% flee to neutral factions; 30% remain.

### Technology

- 🔬 **15-tech research tree across 3 branches:**

  | Branch | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
  |--------|--------|--------|--------|--------|
  | **Industrial** | Tools | Farming | Mining, Engineering | Currency |
  | **Martial** | Scavenging | Metalwork | Weaponry, Masonry | Steel |
  | **Civic** | Oral Tradition | Medicine | Writing | Code of Laws |

- 🧠 **Belief-driven AI selection** — Factions choose research based on dominant belief affinity (16 belief→branch mappings). Engineering reduces all future research durations by 20%. Research pauses during war and resumes after.

- ⚔ **Combat bonuses** — Metalwork +30%, Weaponry +50%, Steel +80%. Masonry +20% defense. Medicine heals hunger damage and grants 50% plague resistance.

### Diplomacy

- 📜 **Council votes** — Factions with 3+ members vote on war (60% threshold), alliances (50%), new members (50%), and research (50%). Beliefs influence voting. Close votes seed internal tension that can trigger future schisms.

- 🤝 **Formal treaties** — Non-Aggression Pacts (cap tension at 100), Trade Agreements (+20% bonus), Mutual Defense Pacts (auto-join ally wars), and Tribute Pacts (imposed by 3× stronger factions). All last 50 ticks. Breaking a treaty costs −3 reputation.

- ⚖ **Surrender terms** — Belief-driven: `community_sustains` → annexation, `the_strong_take` → tribute, `migration_brings_hope` → exile, `the_wise_must_lead` → vassalization.

- 🌟 **Reputation** — Integer −10 to +10 per faction (reviled → legendary). Recovers +1 every 25 peaceful ticks. Factions with surplus food can share with needy neighbors for +2 reputation.

### Anti-Stagnation

- 🌋 **World events** (every 200 ticks) — Plague (−20 HP all), Golden Age (resources restored), Migration (8 newcomers), Earthquake (2 chunks destroyed), Discovery (free tech to random faction).

- 📅 **Era shifts** (every 500 ticks) — All tensions halved, +33% food globally, new era announced. Eras are named dynamically: The Crimson Years, The Age of Iron, The Great Famine, The Long Peace, etc.

- ⚡ **Disruption events** — Triggered by prolonged stagnation or too few factions: Great Migration (10 newcomers, 2 instant rival factions), Plague Sweeps (−30 HP, food halved), Civil War (largest faction splits), Promised Land (barren chunk restored), Prophet (lone visionary arrives).

- ⏱ **Peace escalation** — At 50 ticks of peace, suspicion grows (+10 tension all pairs). At 75 ticks, resource envy flares (+30 tension on a random pair). At 100 ticks, a mysterious death incident triggers +50 tension and resets the peace tracker.

- 🧳 **Traveler waves** — Every 40 ticks, if population is low or factions are too few, 5–10 newcomers arrive with food and biome-appropriate beliefs. Solo-faction members waste away (−10 HP every 10 ticks) to prevent lone holdouts from stalling the simulation.

### Mythology (Optional)

- 🪦 **LLM narrative layer** — Disabled by default (`MYTHOLOGY_ENABLED = False`). When enabled, a local LLM writes chronicles every 50 ticks, faction creation myths every 100 ticks, and epitaphs for fallen warriors. A Tolkien-style final epic summary is saved to a timestamped `history_*.txt` file. The system prompt casts the model as "an ancient, nameless scribe of the void."

- 📝 **Manual chronicle export** — When mythology is disabled, the simulation writes structured era data to `manual_chronicle.txt` every 50 ticks, suitable for feeding to any external LLM later. A separate `era_export.txt` captures the last 100 events each period.

- 🔒 **Read-only observer** — The mythology layer never modifies simulation state. It degrades gracefully with fallback text if Ollama is unavailable.

### Emergent Variety

- 🔄 **Every run is different** — Different seeds produce different terrain, belief clusters, faction names, wars, and alliances. The same code produces The Merchant Pact one run and The Iron Shore the next.

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
sim.py  (entry point — 10,000-tick main loop, logging, layer orchestration)
│
├── world.py        Layer 0 · Perlin-noise terrain, biomes, food scaling, seasons
├── inhabitants.py  Layer 1 · Survival, movement, gathering, trust, procreation
├── beliefs.py      Layer 2 · 27 event-driven beliefs, peer sharing
├── factions.py     Layer 3 · Formation, schisms, merges, territory, food pooling
├── economy.py      Layer 4 · Currency, dynamic pricing, trade routes, raids, Gini
├── combat.py       Layer 5 · War declarations, alliances, multi-tick battles, legends
├── technology.py   Layer 6 · 15-tech tree (3 branches), AI-driven research selection
├── diplomacy.py    Layer 7 · Council votes, treaties, reputation, surrender terms
├── mythology.py    Layer 8 · LLM chronicles, myths, epitaphs (optional, read-only)
│
├── display.py      Rendering — per-tick output, faction summaries, final reports
└── config.py       Settings — population cap, mythology toggle, LLM parameters
```

Each layer is a pure function over shared state. Layers 0–7 are deterministic Python. Layer 8 (mythology) is the only one that touches a network socket — and it is disabled by default. When enabled, it degrades gracefully with fallback text if Ollama is unavailable.

The terminal output is selectively filtered: only notable events (war declarations, births, deaths, treaties, tech discoveries, schisms, era shifts) appear live via a keyword filter (39 keywords). Full tick-by-tick detail goes to a timestamped log file in `logs/`.

---

## Quick Start

### Prerequisites

- Python 3.10+
- (Optional) [Ollama](https://ollama.com) — only needed if you enable the mythology layer

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/Kriaetour/ai-sim-emergent-agents
cd ai-civilization-sim

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 3. Install dependencies (just Perlin noise)
pip install -r requirements.txt

# 4. Run
python sim.py
```

To enable the LLM mythology layer:

```bash
# Pull a model (any Ollama-compatible model works)
ollama pull internlm2:1.8b-chat-v2.5-q4_K_M

# Set MYTHOLOGY_ENABLED = True in config.py, then run
python sim.py
```

### What You'll See

```
Log → logs/run_20260223_190041.txt
Running 10000-tick simulation  (wars / schisms / deaths show below)

  [  1/10000]  Alive:30  Factions:0  Wars:0  Techs:0  Treaties:0
  ...
Tick 045: ⚡ SCHISM — The Drifting Circle breaks from The Wild Rovers
Tick 060: ⚔ WAR DECLARED — The Lone Bond vs The Trading Bridge (tension 222)
Tick 060: 💀 Fenn (The Lone Bond) fell in battle at (1,6)
Tick 120: 🍼 BIRTH: Sera II born to Brea and Arin
Tick 500: 📅 NEW ERA DAWNS — The Age of Iron
  ...
```

---

## Hardware

| Tier | Spec | Notes |
|------|------|-------|
| **Minimum (tested)** | Intel N95 · 16 GB RAM | Pure sim runs easily; mythology adds LLM wait time |
| **Comfortable** | Any modern CPU · 16 GB RAM | Full 10,000-tick run in minutes |
| **With GPU** | Any iGPU/dGPU supported by Ollama | Mythology calls become near-instant |

No GPU is required. The simulation itself is pure Python and runs without any LLM. The optional mythology layer calls Ollama with `keep_alive: 0` (immediate VRAM release) and `gc.collect()` after each inference to minimize memory pressure on shared-memory iGPU systems.

---

## Configuration

Key settings in `config.py`:

```python
# Ollama / LLM (only relevant when mythology is enabled)
GAME_MODEL        = "phi3:3.8b-mini-4k-instruct-q4_0"
NARRATIVE_MODEL   = "internlm2:1.8b-chat-v2.5-q4_K_M"
OLLAMA_URL        = "http://localhost:11434/api/generate"
OLLAMA_TIMEOUT    = 150         # seconds per LLM call

# Feature flags
MYTHOLOGY_ENABLED = False       # True → enable LLM chronicles; False → manual export

# Population
POP_CAP           = 200         # hard ceiling; world food scales up as population grows

# LLM tuning
LLM_TEMPERATURE   = 0.7
LLM_MAX_TOKENS    = 200         # default ceiling; overridden per call type
```

In `sim.py`:
```python
TICKS = 10000   # Change to run shorter (300) or longer simulations
```

---

## File Outputs

| File | When | Contents |
|------|------|---------|
| `logs/run_*.txt` | Every run | Full tick-by-tick log of all events |
| `manual_chronicle.txt` | Every 50 ticks (mythology disabled) | Structured era events + perished names for external LLM use |
| `era_export.txt` | Every 50 ticks | Last 100 events + era summaries |
| `history_*.txt` | End of run (mythology enabled) | Tolkien-style epic summary generated by LLM |

---

## Roadmap

- [x] **Generational agents** — children inherit beliefs and faction membership from parents
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
