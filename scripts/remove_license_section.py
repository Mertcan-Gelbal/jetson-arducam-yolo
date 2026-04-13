#!/usr/bin/env python3
"""Remove license section from page_settings() and license methods from main.py"""
import re

path = "gui/main.py"
with open(path, encoding="utf-8") as f:
    content = f.read()

original_len = len(content.splitlines())

# ── 1. Remove license UI block from page_settings() ──────────────────────────
# The block starts right after `l.addWidget(conn_box)` (the comment line with
# Unicode box-drawing chars + lic_box ... l.addWidget(lic_box))
# We match from the Unicode comment line through l.addWidget(lic_box)
pattern_ui = re.compile(
    r"\n        # \u2500\u2500 License & Activation \u2500+\n"
    r"        lic_box = QFrame\(\).*?"
    r"        l\.addWidget\(lic_box\)\n",
    re.DOTALL,
)
content, n1 = re.subn(pattern_ui, "\n", content)
print(f"License UI block removed: {n1} occurrence(s)")

# ── 2. Remove _load_license_info method ──────────────────────────────────────
pattern_load = re.compile(
    r"\n    def _load_license_info\(self\).*?(?=\n    def )",
    re.DOTALL,
)
content, n2 = re.subn(pattern_load, "\n", content)
print(f"_load_license_info removed: {n2} occurrence(s)")

# ── 3. Remove _save_license_info method ──────────────────────────────────────
pattern_save = re.compile(
    r"\n    def _save_license_info\(self.*?\).*?(?=\n    def )",
    re.DOTALL,
)
content, n3 = re.subn(pattern_save, "\n", content)
print(f"_save_license_info removed: {n3} occurrence(s)")

# ── 4. Remove _activate_license method ───────────────────────────────────────
pattern_act = re.compile(
    r"\n    def _activate_license\(self.*?\).*?(?=\n    def )",
    re.DOTALL,
)
content, n4 = re.subn(pattern_act, "\n", content)
print(f"_activate_license removed: {n4} occurrence(s)")

# ── 5. Remove _deactivate_license method ─────────────────────────────────────
pattern_deact = re.compile(
    r"\n    def _deactivate_license\(self.*?\).*?(?=\n    def )",
    re.DOTALL,
)
content, n5 = re.subn(pattern_deact, "\n", content)
print(f"_deactivate_license removed: {n5} occurrence(s)")

# ── 6. Remove _refresh_license_ui() call in _apply_settings_bundle ───────────
content = re.sub(r"\n\s+self\._refresh_license_ui\(\)\n", "\n", content)
print("_refresh_license_ui() call removed")

# ── 7. Remove QSS license styles (if still present) ──────────────────────────
content = re.sub(
    r"\n\s+QLabel#LicenseStatusActive \{\{.*?\}\}\n", "\n", content
)
content = re.sub(
    r"\n\s+QLabel#LicenseStatusInactive \{\{.*?\}\}\n", "\n", content
)
content = re.sub(
    r"\n\s+QPushButton#DangerBtn \{\{.*?\}\}\n", "\n", content
)
content = re.sub(
    r"\n\s+QPushButton#DangerBtn:hover \{\{.*?\}\}\n", "\n", content
)
print("QSS license styles removed (if present)")

new_len = len(content.splitlines())
print(f"Lines: {original_len} → {new_len} (removed {original_len - new_len})")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Done.")
