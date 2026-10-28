"""Explicit support-window preview; does not import or start the game assistant."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from support_window import main


if __name__ == "__main__":
    raise SystemExit(main())
