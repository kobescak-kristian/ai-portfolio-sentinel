"""``python -m sentinel run|recover ...`` entry point."""

from __future__ import annotations

import sys

from sentinel.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
