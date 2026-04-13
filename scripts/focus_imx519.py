#!/usr/bin/env python3
"""
I2C motorized focus helper (Arducam / Jetson). Primarily used with IMX519 motorized kits;
other motorized Arducam modules may use the same focuser — verify I2C address with i2cdetect.

Bus examples: Orin Nano/Xavier NX CAM0=10, CAM1=9; Orin NX CAM0=9, CAM1=10; Nano CAM0=7, CAM1=8.
IMX219 / IMX477 / typical fixed IMX230 boards have no focuser — this script does not apply.

Usage: python focus_imx519.py [--bus 10] [--position 0-1023]
"""
import argparse
import sys
import os

def main():
    ap = argparse.ArgumentParser(description="Set motorized CSI focus position via I2C (IMX519-style focuser)")
    ap.add_argument("--bus", type=int, default=10, help="I2C bus (e.g. 10 for Orin Nano CAM0)")
    ap.add_argument("--position", type=int, default=512, help="Focus position 0-1023 (0=infinity, higher=closer)")
    args = ap.parse_args()
    pos = max(0, min(1023, args.position))
    bus = args.bus
    # Arducam focus: often single byte to 0x0c (slave addr 0x0c). Map 0-1023 -> 0-255 for high byte.
    byte_val = (pos * 255) // 1023
    try:
        # Prefer i2cset (no Python I2C deps)
        ret = os.system(f"i2cset -y {bus} 0x0c {byte_val} 0")
        if ret != 0:
            # Some modules use two-byte write
            high = (pos >> 2) & 0xFF
            low = (pos & 3) << 6
            ret = os.system(f"i2cset -y {bus} 0x0c 0x{high:02x} 0x{low:02x} i")
        if ret != 0:
            print("i2cset failed. Check: i2cdetect -y", bus, file=sys.stderr)
            sys.exit(1)
        print("Focus position set to", pos)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
