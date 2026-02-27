#!/usr/bin/env python3
"""Replace print() with _real.write() in timing output block."""

filepath = r'src\thalren_vale\sim.py'

with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
in_timing_block = False
i = 0

while i < len(lines):
    line = lines[i]
    
    # Detect start of timing section
    if '# DEBUG' in line and i + 1 < len(lines) and 'if t % 50 == 0:' in lines[i+1]:
        # Skip the DEBUG block (4 lines)
        i += 4
        # Now process the timing summary block
        if i < len(lines) and '# ── Per-layer timing summary' in lines[i]:
            new_lines.append(lines[i])  # Add the comment
            i += 1
            # Copy until we hit sys.stdout.flush()
            while i < len(lines):
                line = lines[i]
                if line.strip().startswith('print(f"'):
                    # Replace print with _real.write and ensure newline
                    content = line.replace('print(f"', '_real.write(f"')
                    if not content.rstrip().endswith('\\n")'):
                        content = content.rstrip()[:-2] + '\\n")\n'
                    new_lines.append(content)
                elif 'sys.stdout.flush()' in line:
                    new_lines.append(line.replace('sys.stdout.flush()', '_real.flush()'))
                    i += 1
                    break
                else:
                    new_lines.append(line)
                i += 1
    else:
        new_lines.append(line)
        i += 1

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("✓ Fixed timing output - replaced print() with _real.write()")
