import re

with open("gui/main.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Remove ZT Status card from dashboard (page_home around lines 4318-4435)
ptn_zt_card = re.compile(r'# ── ZeroTier status card ──.*?# ── Recent activity ──', re.DOTALL)
content = ptn_zt_card.sub('# ── Recent activity ──', content)

# 2. Fix the layout split if we removed the ZT card
# The zt_card was added to `left_col`. Let's just make sure it's not breaking.
content = re.compile(r'left_col\.addWidget\(zt_card\)\s*').sub('', content)

# 3. Clean page_devices
# Remove the ZT explanation text
content = re.compile(r'ds = QLabel\([^)]*.*?ds\.setWordWrap\(True\)\s*tb\.addWidget\(ds\)', re.DOTALL).sub('', content)

# Remove the ZT refresh loop
content = re.compile(r'self\._zt_peer_refresh_timer = QTimer\(w\)\n.*?self\._zt_peer_refresh_timer\.start\(\)\n', re.DOTALL).sub('', content)

# 4. Remove all the get_zerotier_* globally because they are no longer necessary?
# We might break settings page, which still has a ZeroTier section.
# Let's see if we should leave the settings page alone. The prompt said "redundant discovery elements".
# That means "auto discovery" on devices page.

with open("gui/main.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Stripped redundant ZeroTier UI elements.")
