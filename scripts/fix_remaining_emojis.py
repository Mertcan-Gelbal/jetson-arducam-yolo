#!/usr/bin/env python3
"""Fix remaining emoji/symbol issues after remove_emojis.py"""

path = "gui/main.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()

original_len = len(lines)
new_lines = []
i = 0
removed = []

while i < len(lines):
    line = lines[i]

    # ── Remove orphaned empty_icon_lbl lines (label was already deleted) ──────
    if 'empty_icon_lbl.setAlignment(' in line or \
       'empty_icon_lbl.setStyleSheet(' in line or \
       'empty_fl.addWidget(empty_icon_lbl)' in line:
        removed.append(f"  Removed line {i+1}: {repr(line[:70])}")
        i += 1
        continue

    # ── "● Connected" → "Connected" ──────────────────────────────────────────
    if '"● Connected"' in line:
        line = line.replace('"● Connected"', '"Connected"')
        removed.append(f"  Fixed line {i+1}: ● Connected → Connected")

    # ── f"● {m:02d}:{s:02d}" → f"REC {m:02d}:{s:02d}" ───────────────────────
    if 'f"● {m:02d}:{s:02d}"' in line:
        line = line.replace('f"● {m:02d}:{s:02d}"', 'f"REC {m:02d}:{s:02d}"')
        removed.append(f"  Fixed line {i+1}: ● rec time → REC rec time")

    new_lines.append(line)
    i += 1

print(f"Lines: {original_len} → {len(new_lines)} (removed/fixed {original_len - len(new_lines)})")
print("\nChanges:")
for r in removed:
    print(r)

with open(path, "w", encoding="utf-8") as f:
    f.writelines(new_lines)
print("\nDone.")
