#!/usr/bin/env python3
"""Remove license UI block from page_settings() using line-number detection."""

path = "gui/main.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()

# Find start: line containing 'lic_box = QFrame()' after 'l.addWidget(conn_box)'
start_idx = None
end_idx = None

for i, line in enumerate(lines):
    if 'lic_box = QFrame()' in line and start_idx is None:
        # The comment line is one before this
        start_idx = i - 1  # include the comment line
        print(f"Start at line {i} (0-indexed): {repr(line[:60])}")
    if start_idx is not None and 'l.addWidget(lic_box)' in line:
        end_idx = i
        print(f"End at line {i} (0-indexed): {repr(line[:60])}")
        break

if start_idx is None or end_idx is None:
    print(f"ERROR: Could not find block. start={start_idx}, end={end_idx}")
    # Print lines around where we expect it
    for i, line in enumerate(lines[4450:4470], start=4450):
        print(f"  {i}: {repr(line[:80])}")
    exit(1)

# Remove lines from start_idx to end_idx (inclusive)
removed = lines[start_idx:end_idx+1]
print(f"Removing {len(removed)} lines ({start_idx+1}–{end_idx+1})")
new_lines = lines[:start_idx] + lines[end_idx+1:]

with open(path, "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print(f"Done. Lines: {len(lines)} → {len(new_lines)}")
