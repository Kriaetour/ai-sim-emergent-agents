#!/usr/bin/env python3
"""Check if batch runs completed successfully."""

import os

# Check the two most recent log files
logs_dir = 'logs'
log_files = sorted([f for f in os.listdir(logs_dir) if f.endswith('.txt')])[-5:]

print("=" * 70)
print("RECENT LOG FILES AND CONTENTS")
print("=" * 70)

for log_file in log_files:
    path = os.path.join(logs_dir, log_file)
    size = os.path.getsize(path)
    
    with open(path, 'r') as f:
        lines = f.readlines()
        total_lines = len(lines)
        
        # Look for seed and tick count
        seed_line = None
        last_complete_tick = 0
        has_timing = False
        
        for i, line in enumerate(lines[:100]):  # Check first 100 lines for seed
            if 'Seed:' in line:
                seed_line = line.strip()
                break
        
        # Check for tick 50 timing
        for line in lines:
            if '=== Tick 50 timing' in line:
                has_timing = True
                break
        
        # Find last complete tick
        for line in reversed(lines[-100:]):  # Check last 100 lines
            if 'CIVILIZATION SUMMARY' in line:
                last_complete_tick = int(line.split('—')[1].split('ticks')[0].strip())
                break
    
    print(f"\n{log_file}")
    print(f"  Size: {size:,} bytes  |  Lines: {total_lines}")
    if seed_line:
        print(f"  {seed_line}")
    if has_timing:
        print(f"  ✓ Timing output present (found Tick 50)")
    else:
        print(f"  ✗ No timing output found")
    if last_complete_tick:
        print(f"  Completed: {last_complete_tick} ticks")

print("\n" + "=" * 70)
