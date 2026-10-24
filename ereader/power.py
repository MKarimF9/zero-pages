"""Switching the Pi off from the reader (hold select for POWER_HOLD seconds).

The reader runs as the Pi's normal user, so it asks sudo for the poweroff. On
Raspberry Pi OS that user can sudo without a password; if yours can't, the
press is ignored (can_power_off() says no) instead of leaving a dead screen.
"""
from __future__ import annotations

import subprocess

COMMAND = ["sudo", "-n", "systemctl", "poweroff"]   # -n: never wait for a password


def can_power_off(run=subprocess.run) -> bool:
    """Would sudo let us power off without asking for a password?"""
    try:
        return run(["sudo", "-n", "true"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def power_off(run=subprocess.run) -> bool:
    """Ask the system to power off. True if it accepted."""
    try:
        return run(COMMAND, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
