"""Read-only assistant conflict identity, scoped to user, session and installation.

A process name is only a prefilter. Access failures remain unknown; they never
claim a known competing assistant or stop input in another Windows session.
"""
from dataclasses import dataclass
import ctypes
from ctypes import wintypes as W
import ntpath
import os

ASSISTANT_NAMES = frozenset(('星图.exe', '第五人格小助手.exe', '第五人格小抄助手.exe', 'xingtuqtclienttest.exe'))


@dataclass(frozen=True)
class AssistantConflict:
    state: str  # present / absent / unknown
    reason: str


def process_session_id(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.ProcessIdToSessionId.argtypes = [W.DWORD, ctypes.POINTER(W.DWORD)]
    kernel.ProcessIdToSessionId.restype = W.BOOL
    session = W.DWORD()
    if not kernel.ProcessIdToSessionId(pid, ctypes.byref(session)):
        raise ctypes.WinError(ctypes.get_last_error())
    return session.value


def assistant_process_conflict(*, processes=None, current=None, session_id=process_session_id):
    import psutil
    try:
        current = current or psutil.Process(os.getpid())
        current_exe = current.exe()
        if not current_exe or not ntpath.isabs(current_exe):
            return AssistantConflict('unknown', 'current-identity-unavailable')
        current_path = ntpath.normcase(ntpath.normpath(current_exe))
        if ntpath.basename(current_path) not in ASSISTANT_NAMES:
            return AssistantConflict('absent', 'current-entry-is-not-an-assistant-executable')
        current_user = current.username().casefold()
        current_session = session_id(current.pid)
        if not current_user or not current_path or current_session is None:
            return AssistantConflict('unknown', 'current-identity-unavailable')
        rows = processes if processes is not None else psutil.process_iter(['pid', 'name'])
        unknown = False
        for process in rows:
            try:
                if process.pid == current.pid or (process.info.get('name') or '').casefold() not in ASSISTANT_NAMES:
                    continue
                session = session_id(process.pid)
                if session is None:
                    unknown = True
                    continue
                if session != current_session:
                    continue
                username = process.username()
                if not username:
                    unknown = True
                    continue
                if username.casefold() != current_user:
                    continue
                path = ntpath.normcase(ntpath.normpath(process.exe()))
                if not ntpath.isabs(path):
                    unknown = True
                    continue
                if ntpath.dirname(path) != ntpath.dirname(current_path):
                    continue
                if ntpath.basename(path) not in ASSISTANT_NAMES:
                    continue
                return AssistantConflict('present', 'same-user-session-assistant-installation')
            except (OSError, ValueError, psutil.Error):
                unknown = True
        return AssistantConflict('unknown' if unknown else 'absent',
                                 'candidate-identity-unavailable' if unknown else 'no-matching-identity')
    except (OSError, ValueError, psutil.Error):
        return AssistantConflict('unknown', 'process-identity-query-failed')


def another_assistant_running():
    return assistant_process_conflict().state == 'present'
