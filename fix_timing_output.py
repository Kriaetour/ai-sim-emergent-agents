#!/usr/bin/env python3
"""Fix timing output to use _real.write() instead of print()."""
import re

filepath = r'src\thalren_vale\sim.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the entire timing output block
old_block = r'''            # DEBUG
            if t % 50 == 0:
                print\(f"DEBUG: About to print timing for tick \{t\}"\)
                sys\.stdout\.flush\(\)

            # ── Per-layer timing summary \(every 50 ticks\) ────────────────────
            if t % 50 == 0:
                _t_total = \(_t_world \+ _t_inh \+ _t_bel \+ _t_fac \+ _t_proc \+ 
                           _t_eco \+ _t_comb \+ _t_tech \+ _t_dip \+ _t_rel \+ 
                           _t_map \+ _t_dyn \+ _t_met \+ _t_solo \+ _t_we \+ 
                           _t_plug \+ _t_era \+ _t_house \+ _t_antistag\)
                _pop = len\(\[i for i in people if i\.health > 0\]\)
                print\(f"\\n=== Tick \{t\} timing \(ms\) \| Pop: \{_pop\} ==="\)
                print\(f"  World:         \{_t_world:>8\.1f\}"\)
                print\(f"  Inhabitants:   \{_t_inh:>8\.1f\}"\)
                print\(f"  Beliefs:       \{_t_bel:>8\.1f\}"\)
                print\(f"  Factions:      \{_t_fac:>8\.1f\}"\)
                print\(f"  Procreation:   \{_t_proc:>8\.1f\}"\)
                print\(f"  Economy:       \{_t_eco:>8\.1f\}"\)
                print\(f"  Combat:        \{_t_comb:>8\.1f\}"\)
                print\(f"  Technology:    \{_t_tech:>8\.1f\}"\)
                print\(f"  Diplomacy:     \{_t_dip:>8\.1f\}"\)
                print\(f"  Religion:      \{_t_rel:>8\.1f\}"\)
                print\(f"  Map:           \{_t_map:>8\.1f\}"\)
                print\(f"  Dynamic:       \{_t_dyn:>8\.1f\}"\)
                print\(f"  Metrics:       \{_t_met:>8\.1f\}"\)
                print\(f"  SoloFaction:   \{_t_solo:>8\.1f\}"\)
                print\(f"  WorldEvents:   \{_t_we:>8\.1f\}"\)
                print\(f"  Plugins:       \{_t_plug:>8\.1f\}"\)
                print\(f"  Era:           \{_t_era:>8\.1f\}"\)
                print\(f"  Housekeeping:  \{_t_house:>8\.1f\}"\)
                print\(f"  AntiStag:      \{_t_antistag:>8\.1f\}"\)
                print\(f"  ────────────────────────"\)
                print\(f"  SUM \(layers\):  \{_t_total:>8\.1f\}"\)
                sys\.stdout\.flush\(\)'''

new_block = '''            # ── Per-layer timing summary (every 50 ticks) ────────────────────
            if t % 50 == 0:
                _t_total = (_t_world + _t_inh + _t_bel + _t_fac + _t_proc + 
                           _t_eco + _t_comb + _t_tech + _t_dip + _t_rel + 
                           _t_map + _t_dyn + _t_met + _t_solo + _t_we + 
                           _t_plug + _t_era + _t_house + _t_antistag)
                _pop = len([i for i in people if i.health > 0])
                _real.write(f"\\n=== Tick {t} timing (ms) | Pop: {_pop} ===\\n")
                _real.write(f"  World:         {_t_world:>8.1f}\\n")
                _real.write(f"  Inhabitants:   {_t_inh:>8.1f}\\n")
                _real.write(f"  Beliefs:       {_t_bel:>8.1f}\\n")
                _real.write(f"  Factions:      {_t_fac:>8.1f}\\n")
                _real.write(f"  Procreation:   {_t_proc:>8.1f}\\n")
                _real.write(f"  Economy:       {_t_eco:>8.1f}\\n")
                _real.write(f"  Combat:        {_t_comb:>8.1f}\\n")
                _real.write(f"  Technology:    {_t_tech:>8.1f}\\n")
                _real.write(f"  Diplomacy:     {_t_dip:>8.1f}\\n")
                _real.write(f"  Religion:      {_t_rel:>8.1f}\\n")
                _real.write(f"  Map:           {_t_map:>8.1f}\\n")
                _real.write(f"  Dynamic:       {_t_dyn:>8.1f}\\n")
                _real.write(f"  Metrics:       {_t_met:>8.1f}\\n")
                _real.write(f"  SoloFaction:   {_t_solo:>8.1f}\\n")
                _real.write(f"  WorldEvents:   {_t_we:>8.1f}\\n")
                _real.write(f"  Plugins:       {_t_plug:>8.1f}\\n")
                _real.write(f"  Era:           {_t_era:>8.1f}\\n")
                _real.write(f"  Housekeeping:  {_t_house:>8.1f}\\n")
                _real.write(f"  AntiStag:      {_t_antistag:>8.1f}\\n")
                _real.write(f"  ────────────────────────\\n")
                _real.write(f"  SUM (layers):  {_t_total:>8.1f}\\n")
                _real.flush()'''

content = re.sub(old_block, new_block, content)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Fixed timing output to use _real.write()")
