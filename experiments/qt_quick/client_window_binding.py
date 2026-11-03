"""Independent read-only identity check for a bound window missing from a catalog.

Capture readiness (minimized/geometry unavailable) is not window existence.
Unknown reads never authorize input or end a round; the coordinator suspends it.
"""
from __future__ import annotations

import ntpath


def probe_game_binding(identity, *, native=None, process_factory=None):
    """Return current/lost/unknown using HWND + PID + birth time + executable.

No discovery, capture, activation or key APIs are called. Explicitly reading the
existing HWND works even when its geometry is unreadable or it is minimized.
The final recheck rejects a process/window replacement during the metadata read.
"""
    import psutil
    from game_window_focus import NativeGameFocusAPI
    native = native if native is not None else NativeGameFocusAPI()
    process_factory = process_factory if process_factory is not None else psutil.Process

    def result(status, reason):
        return dict(status=status, reason=reason)

    def path(value):
        return ntpath.normcase(ntpath.normpath(str(value)))

    try:
        hwnd, expected_pid, expected_created, expected_exe = identity
        hwnd = int(hwnd)
        if hwnd <= 0 or not expected_pid or expected_created is None or not expected_exe:
            return result("unknown", "binding-identity-incomplete")
        user = native.get_user32()
        if not user.IsWindow(hwnd):
            return result("lost", "window-destroyed")
        pid = native.window_process_id(hwnd)
        if not pid:
            return result("unknown", "window-pid-unavailable")
        if pid != expected_pid:
            return result("lost", "window-pid-changed")
        process = process_factory(pid)
        created, executable = process.create_time(), process.exe()
        if not executable:
            return result("unknown", "process-image-unavailable")
        if created != expected_created or path(executable) != path(expected_exe):
            return result("lost", "process-identity-changed")
        title = native.window_text(hwnd)
        if not title:
            return result("unknown", "window-title-unavailable")
        if not native.identity_is_allowed(title, executable):
            return result("lost", "game-identity-rejected")
        if not user.IsWindow(hwnd):
            return result("lost", "window-destroyed-during-check")
        final_pid = native.window_process_id(hwnd)
        if not final_pid:
            return result("unknown", "window-pid-unavailable")
        if final_pid != pid:
            return result("lost", "window-pid-changed-during-check")
        final_process = process_factory(final_pid)
        final_exe = final_process.exe()
        if not final_exe:
            return result("unknown", "process-image-unavailable")
        if (final_process.create_time() != expected_created
                or path(final_exe) != path(expected_exe)):
            return result("lost", "process-identity-changed-during-check")
        return result("current", "same-window-process-identity")
    except psutil.NoSuchProcess:
        return result("lost", "process-exited")
    except (OSError, psutil.Error, TypeError, ValueError, AttributeError):
        return result("unknown", "binding-identity-unreadable")
