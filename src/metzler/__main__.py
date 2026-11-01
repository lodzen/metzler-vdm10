"""Entry point for `python -m metzler` and standalone file execution:

    python3 src/metzler/__main__.py --server 10.0.0.38 ...

Run as a file there is no package context (`__package__` is empty), so the
source root is added to sys.path first — stdlib only, no install needed.
"""

import pathlib
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from metzler.cli import main

if __name__ == "__main__":
    main()
