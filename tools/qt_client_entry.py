"""Qt-free failure boundary, active before loading client or native dependencies."""
from pathlib import Path
import sys

# Source invocation uses the same entry boundary as the packaged app. Frozen
# imports come from PyInstaller's archive, never from adjacent root metadata.
if not getattr(sys, "frozen", False):
    _root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(_root / "app"), str(_root / "experiments/qt_quick")]

from qt_startup_diagnostics import report_startup_failure


def run():
    stage = "validate-profile"
    try:
        from packaged_product_identity import baked_identity
        baked_identity(require_qt=True)
        stage = "import-client"
        from client import main
        stage = "run-client"
        return main()
    except Exception as error:
        report_startup_failure(error, stage=stage)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
