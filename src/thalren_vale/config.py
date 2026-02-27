"""
config.py — Shared configuration constants for the civilization simulation.
"""

# ── Ollama / LLM settings ─────────────────────────────────────────────────
GAME_MODEL       = "phi3:3.8b-mini-4k-instruct-q4_0"      # Fast, for agent decisions
NARRATIVE_MODEL  = "internlm2:1.8b-chat-v2.5-q4_K_M"          # Quality, for mythology only
OLLAMA_URL       = "http://localhost:11434/api/generate"
OLLAMA_TIMEOUT   = 150           # seconds per LLM call
MYTHOLOGY_ENABLED = False       # set True to enable LLM chronicle layer

# ── Simulation length ───────────────────────────────────────────────────
TICKS = 5000    # total number of ticks to simulate

# ── Population cap ──────────────────────────────────────────────────────
POP_CAP = 1000   # hard ceiling; world food scales up as population grows

# ── LLM generation parameters ────────────────────────────────────────────
LLM_TEMPERATURE  = 0.7
LLM_MAX_TOKENS   = 200   # default; overridden per-call          # num_predict passed to Ollama

# ── Plugin system ────────────────────────────────────────────────────────
PLUGINS_DIR          = 'plugins'      # directory scanned by load_plugins()
PLUGIN_TICK_INTERVAL = 10             # default cadence; each plugin can override via tick_interval

# ── Experimentally tuneable parameters ──────────────────────────────────
# These are the **current** defaults extracted from the hardcoded values in
# factions.py, combat.py, beliefs.py, and sim.py.  CLI arguments in run()
# override them at runtime so the sim is identical when none are passed.
FACTION_TRUST_THRESHOLD   = 5     # min mutual trust for faction formation (factions.py _mutual_trust)
WAR_TENSION_THRESHOLD     = 200   # tension score that triggers war declaration (combat.py WAR_THRESHOLD)
BELIEF_SHARING_PROBABILITY = 0.5  # per-pair chance of belief sharing each tick (beliefs.py share_beliefs)
STARTING_INHABITANTS       = 30   # initial population count (sim.py init_inhabitants)
