import re

filepath = r"c:\Users\brand\OneDrive\Documents\Py3 Files\AI Sandbox (Refactored)\src\thalren_vale\sim.py"

with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the line index for the DEBUG comment
start_idx = None
for i, line in enumerate(lines):
    if "# DEBUG" in line:
        start_idx = i
        break

if start_idx is None:
    print("DEBUG comment not found")
    exit(1)

# Find the end of the timing block (the sys.stdout.flush() after SUM (layers))
end_idx = start_idx
in_timing_block = False
for i in range(start_idx, len(lines)):
    if "in_timing_block" in lines[i] or "SUM (layers)" in lines[i]:
        if "sys.stdout.flush()" in lines[i+1]:
            end_idx = i + 1
            break

print(f"Found timing block from line {start_idx} to {end_idx}")

# Build new block
new_lines = lines[:start_idx]
new_lines.extend([
    "            # ── Per-layer timing summary (every 50 ticks) ────────────────────\n",
    "            if t % 50 == 0:\n",
    "                _t_total = (_t_world + _t_inh + _t_bel + _t_fac + _t_proc + \n",
    "                           _t_eco + _t_comb + _t_tech + _t_dip + _t_rel + \n",
    "                           _t_map + _t_dyn + _t_met + _t_solo + _t_we + \n",
    "                           _t_plug + _t_era + _t_house + _t_antistag)\n",
    "                _pop = len([i for i in people if i.health > 0])\n",
    "                _real.write(f\"\\n=== Tick {t} timing (ms) | Pop: {_pop} ===\\n\")\n",
    "                _real.write(f\"  World:         {_t_world:>8.1f}\\n\")\n",
    "                _real.write(f\"  Inhabitants:   {_t_inh:>8.1f}\\n\")\n",
    "                _real.write(f\"  Beliefs:       {_t_bel:>8.1f}\\n\")\n",
    "                _real.write(f\"  Factions:      {_t_fac:>8.1f}\\n\")\n",
    "                _real.write(f\"  Procreation:   {_t_proc:>8.1f}\\n\")\n",
    "                _real.write(f\"  Economy:       {_t_eco:>8.1f}\\n\")\n",
    "                _real.write(f\"  Combat:        {_t_comb:>8.1f}\\n\")\n",
    "                _real.write(f\"  Technology:    {_t_tech:>8.1f}\\n\")\n",
    "                _real.write(f\"  Diplomacy:     {_t_dip:>8.1f}\\n\")\n",
    "                _real.write(f\"  Religion:      {_t_rel:>8.1f}\\n\")\n",
    "                _real.write(f\"  Map:           {_t_map:>8.1f}\\n\")\n",
    "                _real.write(f\"  Dynamic:       {_t_dyn:>8.1f}\\n\")\n",
    "                _real.write(f\"  Metrics:       {_t_met:>8.1f}\\n\")\n",
    "                _real.write(f\"  SoloFaction:   {_t_solo:>8.1f}\\n\")\n",
    "                _real.write(f\"  WorldEvents:   {_t_we:>8.1f}\\n\")\n",
    "                _real.write(f\"  Plugins:       {_t_plug:>8.1f}\\n\")\n",
    "                _real.write(f\"  Era:           {_t_era:>8.1f}\\n\")\n",
    "                _real.write(f\"  Housekeeping:  {_t_house:>8.1f}\\n\")\n",
    "                _real.write(f\"  AntiStag:      {_t_antistag:>8.1f}\\n\")\n",
    "                _real.write(f\"  ────────────────────────\\n\")\n",
    "                _real.write(f\"  SUM (layers):  {_t_total:>8.1f}\\n\")\n",
    "                _real.flush()\n",
    "\n"
])
new_lines.extend(lines[end_idx+1:])

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("Successfully fixed timing output to use _real.write()")
