#!/usr/bin/env python3
"""Remove all emojis and replace with professional text in gui/main.py"""

path = "gui/main.py"
with open(path, encoding="utf-8") as f:
    content = f.read()

original_len = len(content.splitlines())
changes = []

def replace(old, new, label=""):
    global content
    if old in content:
        content = content.replace(old, new)
        changes.append(f"  OK  {label or repr(old[:60])} → {repr(new[:60])}")
    else:
        changes.append(f"  MISS {label or repr(old[:60])}")

# ── refresh_home_page: ZT status text ────────────────────────────────────────
replace('status_txt = f"🟢  Online — {ip_str}"',
        'status_txt = f"Online — {ip_str}"',
        "ZT online status")

replace('status_txt = "🔴  Offline — ZeroTier not connected"',
        'status_txt = "Offline — ZeroTier not connected"',
        "ZT offline status")

replace('status_txt = "⚠️  ZeroTier not installed"',
        'status_txt = "Not installed"',
        "ZT not installed status")

replace('status_txt = "⚪  Unknown status"',
        'status_txt = "Unknown"',
        "ZT unknown status")

# ── page_home: Refresh button ─────────────────────────────────────────────────
replace('QPushButton("⟳  Refresh")',
        'QPushButton("Refresh")',
        "Refresh button")

# ── page_home: ZT icon label ──────────────────────────────────────────────────
# Remove the zt_icon label line entirely
replace('        zt_icon = QLabel("🌐"); zt_icon.setFixedSize(40, 40)\n        zt_icon.setObjectName("HomeZtIcon")\n        zt_hdr.addWidget(zt_icon)\n',
        '',
        "zt_icon label block")

# Fallback: single-line variant
replace('        zt_icon = QLabel("🌐"); zt_icon.setFixedSize(40, 40)\n',
        '',
        "zt_icon label (single line)")

# ── page_home: stat card icons ────────────────────────────────────────────────
replace('make_stat_card("📡", "Connected Devices"',
        'make_stat_card("", "Connected Devices"',
        "stat card 📡")

replace('make_stat_card("📷", "Active Cameras"',
        'make_stat_card("", "Active Cameras"',
        "stat card 📷")

replace('make_stat_card("🐳", "Workspaces"',
        'make_stat_card("", "Workspaces"',
        "stat card 🐳")

# ── page_home: action card icons ──────────────────────────────────────────────
replace('make_action_card("📷", "Cameras"',
        'make_action_card("", "Cameras"',
        "action card 📷")

replace('make_action_card("🖥️", "Devices"',
        'make_action_card("", "Devices"',
        "action card 🖥️")

replace('make_action_card("🐳", "Workspaces"',
        'make_action_card("", "Workspaces"',
        "action card 🐳")

# ── onboarding step icons & button text ───────────────────────────────────────
replace('"icon": "🌐"', '"icon": ""', "onboarding icon 🌐")
replace('"icon": "🖥️"', '"icon": ""', "onboarding icon 🖥️")
replace('"icon": "📷"', '"icon": ""', "onboarding icon 📷")

replace('"btn": "Get Started →"', '"btn": "Get Started"', "onboarding btn 1")
replace('"btn": "Next: Connect Device →"', '"btn": "Next: Connect Device"', "onboarding btn 2")
replace('"btn": "Next: Add Camera →"', '"btn": "Next: Add Camera"', "onboarding btn 3")
replace('"btn": "Finish Setup ✓"', '"btn": "Finish Setup"', "onboarding btn 4")

# ── refresh_devices_page: empty state icon ────────────────────────────────────
replace('empty_icon_lbl = QLabel("📡")\n',
        '',
        "empty_icon_lbl line")

# Also remove addWidget call for empty_icon_lbl if present
replace('            empty_icon_lbl.setObjectName("EmptyStateIcon")\n            empty_icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)\n            empty_icon_lbl.setStyleSheet("font-size: 48px; border: none; background: transparent;")\n            vl.addWidget(empty_icon_lbl)\n',
        '',
        "empty_icon_lbl widget block")

# ── refresh_devices_page: warn icon ──────────────────────────────────────────
replace('warn_icon = QLabel("⚠")',
        'warn_icon = QLabel("!")',
        "warn icon")

replace('"Path IP (fiziksel) ⚠"',
        '"Path IP (physical)"',
        "Path IP label")

replace('"- Connected"',
        '"Connected"',
        "Connected badge")

# ── on_snap_done: saved text ──────────────────────────────────────────────────
replace('btn.setText("✓ Saved")',
        'btn.setText("Saved")',
        "snap saved text")

# ── _show_toast_typed: emoji prefixes ────────────────────────────────────────
replace('"success": "✅"', '"success": ""', "toast success emoji")
replace('"warning": "⚠️"', '"warning": ""', "toast warning emoji")
replace('"error": "❌"',   '"error": ""',   "toast error emoji")

# ── _update_rec_time: recording indicator ────────────────────────────────────
replace('f"- {m:02d}:{s:02d}"', 'f"REC {m:02d}:{s:02d}"', "rec time indicator")

# ── stream_badge: already clean, skip ────────────────────────────────────────

new_len = len(content.splitlines())
print(f"Lines: {original_len} → {new_len}")
print("\nChanges:")
for c in changes:
    print(c)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("\nDone.")
