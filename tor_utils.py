"""Utilities for Tor circuit reset and IP verification."""

import subprocess
import time

import requests as _requests

SOCKS_PROXY = "socks5h://127.0.0.1:9050"
IP_CHECK_URL = "https://checkip.amazonaws.com"
TOR_RESET_SCRIPT = "/home/ryzen/.kimaki/tor-reset.sh"
MAX_POLLS = 10
POLL_INTERVAL = 3


def get_current_ip():
    """Return the current Tor exit node IP, or None if unreachable."""
    try:
        r = _requests.get(
            IP_CHECK_URL,
            proxies={"https": SOCKS_PROXY, "http": SOCKS_PROXY},
            timeout=10,
        )
        if r.status_code == 200:
            return r.text.strip()
    except Exception:
        pass
    return None


def tor_reset():
    """Send NEWNYM signal to Tor control port via the existing shell script.

    Returns True if the signal was sent successfully (or skipped because
    opencode sessions are active, which is fine).
    """
    try:
        r = subprocess.run(
            ["bash", TOR_RESET_SCRIPT],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode in (0, 2):
            return True
        print(f"  [Tor] Reset script failed (exit {r.returncode}): {r.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired:
        print("  [Tor] Reset script timed out")
        return False
    except FileNotFoundError:
        print(f"  [Tor] Reset script not found: {TOR_RESET_SCRIPT}")
        return False


def tor_reset_and_wait():
    """Reset Tor and wait until the exit IP changes.

    Returns True if the IP changed within the polling window, or if reset
    was skipped (opencode sessions active). Returns False on error.
    """
    old_ip = get_current_ip()
    print(f"  [Tor] IP actuelle: {old_ip or 'inconnue'}")

    if not tor_reset():
        return False

    if old_ip:
        for i in range(1, MAX_POLLS + 1):
            print(f"    [Tor] Attente changement IP... ({i}/{MAX_POLLS})")
            time.sleep(POLL_INTERVAL)
            new_ip = get_current_ip()
            if new_ip and new_ip != old_ip:
                print(f"  [Tor] IP changée: {old_ip} \u2192 {new_ip}")
                return True
        print(f"  [Tor] IP inchangée après {MAX_POLLS * POLL_INTERVAL}s (Tor peut réutiliser la même IP)")
        return False

    print(f"  [Tor] Impossible de vérifier l'IP, attente passive...")
    time.sleep(MAX_POLLS * POLL_INTERVAL)
    return True
