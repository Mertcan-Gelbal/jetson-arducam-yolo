#!/usr/bin/env python3
"""Remove empty try/except blocks left after license method removal."""

path = "gui/main.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
i = 0
removed_count = 0

while i < len(lines):
    # Detect pattern: "        try:\n" immediately followed by "        except"
    if (i + 1 < len(lines)
            and lines[i].rstrip() == "        try:"
            and lines[i+1].lstrip().startswith("except")):
        # Skip the empty try: line
        print(f"Removed empty try: at line {i+1}")
        # Also skip the except + pass lines
        i += 1  # skip "try:"
        # skip "except Exception:" and "pass"
        while i < len(lines) and (
            lines[i].lstrip().startswith("except") or
            lines[i].strip() == "pass"
        ):
            print(f"  Removed: line {i+1}: {repr(lines[i].rstrip())}")
            i += 1
        removed_count += 1
        continue
    new_lines.append(lines[i])
    i += 1

print(f"\nRemoved {removed_count} empty try/except block(s)")
print(f"Lines: {len(lines)} → {len(new_lines)}")

with open(path, "w", encoding="utf-8") as f:
    f.writelines(new_lines)
print("Done.")
