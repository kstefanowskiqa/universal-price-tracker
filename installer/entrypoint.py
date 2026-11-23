from __future__ import annotations

import multiprocessing
import sys


def main() -> None:
    multiprocessing.freeze_support()
    if "--run-server" in sys.argv:
        from price_tracker.server import main as server_main

        server_main()
        return

    from price_tracker.desktop.app import main as desktop_main

    desktop_main()


if __name__ == "__main__":
    main()
