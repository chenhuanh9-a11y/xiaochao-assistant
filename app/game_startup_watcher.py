"""Pack as a separate asInvoker windowless EXE; never import the Qt entrypoint."""
import os
from pathlib import Path
import sys
import time

from game_startup import EpisodeLedger, enabled, handle_observation, set_enabled, state_directory, suppressed_games


def control(command, directory, windows, *, clock=time.monotonic, sleep=time.sleep):
    """Installer-only scope; never starts the watcher or bypasses elevation.

    0 success/off, 10 exact opt-in, 2 command invalid, 4 IO/unknown failure,
    5 interactive account mismatch, 6 helper still present/unknown at timeout.
    Restoration is called only after the installer captured exit code 10.
    """
    if command not in ("--status", "--disable", "--restore-after-upgrade"):
        return 2
    try:
        if not windows.interactive_user_matches():
            return 5
        if command == "--status":
            return 10 if enabled(directory) else 0
        if command == "--restore-after-upgrade":
            set_enabled(directory, True)
            return 0 if enabled(directory) else 4
        set_enabled(directory, False)
        if enabled(directory):
            return 4
        deadline = clock() + 15.
        while True:
            if windows.watcher_state(directory) == "absent":
                return 0
            if clock() >= deadline:
                return 6
            sleep(.1)
    except (OSError, ValueError, KeyError):
        return 4


def main():
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return 2  # Source probes cannot register themselves as the installed product.
    directory = Path(sys.executable).resolve().parent
    from game_startup_win32 import Windows
    windows = Windows()
    if len(sys.argv) == 2:
        return control(sys.argv[1], directory, windows)
    if sys.argv[1:]:
        return 2
    if windows.shell.IsUserAnAdmin():
        return 3  # Never run the background watcher elevated, even by accident.
    if not enabled(directory):
        return 0
    # LOCALAPPDATA is per-user; Local mutex namespace is session-scoped. Include
    # the user data path so different users in one session cannot share a ledger.
    import hashlib
    local = state_directory(directory)
    scope = hashlib.sha256(str(local).casefold().encode()).hexdigest()[:24]
    handle = windows.acquire("XingtuGameWatcher-" + scope)
    if handle is None:
        return 0
    try:
        ledger = EpisodeLedger(local / (str(windows.session) + ".json"), alive=windows.episode_alive)
        while enabled(directory):
            games = windows.games()
            if games:
                main_state = windows.main_state(directory)
                exits = suppressed_games(directory, windows.session)
                handle_observation(games, main_state, ledger,
                                   lambda: windows.launch(directory), lambda: enabled(directory), exits)
            time.sleep(3.0)
    except (OSError, ValueError, KeyError):
        return 4  # State cannot be persisted/verified: stop, never prompt repeatedly.
    finally:
        windows.kernel.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
