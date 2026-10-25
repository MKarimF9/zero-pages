#!/usr/bin/env python3
"""Press the buttons of the running reader from an SSH session.

    python3 remote.py              keys: w/k up, s/j down, space/enter select, m hold select, q leave
    python3 remote.py down down select      one-off presses: up, down, select, hold

Leaving (q) does not stop the reader. Works with `--controls gpio`, i.e. the boot service.
"""
import sys

from ereader.controls import Button, KeyboardControls
from ereader.remote import send

NAMES = {"up": Button.UP, "down": Button.DOWN, "select": Button.SELECT, "hold": Button.SELECT_LONG}


def main() -> int:
    args = sys.argv[1:]
    if any(a not in NAMES for a in args):
        print(__doc__)
        return 2
    try:
        if args:
            send([NAMES[a] for a in args])
            return 0
        print("Keys: w/k = up, s/j = down, space/enter = select, m = hold select, q = leave", flush=True)
        keys = KeyboardControls()
        while (button := keys.wait()) is not Button.QUIT:
            if button is not Button.POWER_OFF:
                send([button])
    except (FileNotFoundError, ConnectionRefusedError):
        print("The reader isn't running (or was started without --controls gpio).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
