#!/usr/bin/env python3
import os
import sys
import argparse
import socket

if __package__ in (None, ""):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from runtime.storage import results_root_dir
from runtime.profile_utils import load_inspection_profile

def check_disk_space(path):
    """Returns True if path has at least 500MB free space."""
    try:
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        st = os.statvfs(path)
        free_mb = (st.f_bavail * st.f_frsize) / (1024 * 1024)
        return free_mb > 500, free_mb
    except Exception:
        return False, 0

def check_port_open(ip, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect((ip, int(port)))
        return True
    except (socket.timeout, ConnectionRefusedError):
        return False
    finally:
        s.close()

def run_selfcheck(quiet=False):
    """
    Validates that the Jetson hardware and local OS environment are ready for inspection.
    """
    checks = []
    faults = []

    # 1. Profile
    profile = load_inspection_profile()
    checks.append(f"Profile Loaded: {profile.get('station_name')}")

    # 2. Results Directory Write Permission
    r_dir = results_root_dir()
    try:
        os.makedirs(r_dir, exist_ok=True)
        test_file = os.path.join(r_dir, ".test_write")
        with open(test_file, "w") as f:
            f.write("OK")
        os.remove(test_file)
        checks.append("Storage Access: OK")
    except Exception as e:
        faults.append(f"Storage Access Failed: {e}")

    # 3. Disk Space
    space_ok, free_mb = check_disk_space(r_dir)
    if space_ok:
        checks.append(f"Disk Space: OK ({free_mb:.0f} MB free)")
    else:
        faults.append(f"Low Disk Space: Only {free_mb:.0f} MB free")

    # 4. GPIO Availability (if enabled)
    if profile.get("gpio", {}).get("enabled"):
        try:
            import Jetson.GPIO as GPIO
            checks.append("Jetson.GPIO: Installed and Accessible")
        except ImportError:
            faults.append("Jetson.GPIO module is missing, but GPIO is enabled in profile.")
        except Exception as e:
            faults.append(f"Jetson.GPIO Initialization Failed: {e}")

    # Output formatted report
    status = "PASS" if not faults else "FAIL"
    
    if not quiet:
        print("====================================")
        print(f"VISIONDOCK RUNTIME SELFCHECK: {status}")
        print("====================================")
        for c in checks:
            print(f"[OK]   {c}")
        for f in faults:
            print(f"[ERR]  {f}")
        print("====================================")

    return len(faults) == 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run VisionDock Runtime selfchecks.")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    args = parser.parse_args()
    
    ok = run_selfcheck(quiet=args.quiet)
    sys.exit(0 if ok else 1)
