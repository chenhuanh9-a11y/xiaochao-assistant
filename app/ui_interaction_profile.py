"""Opt-in diagnostics for one full UI run; importing this module patches nothing.

Install before construction, then begin/end around the action AND its event-loop
settling interval. No UI, timers, disk writes, sys.setprofile or heap tracer are
started here. Inclusive rows overlap: never sum them as end-to-end latency.
"""
from __future__ import annotations

from collections import deque
import ctypes
import functools
import importlib
import os
import sys
import threading
import time


def own_process_metrics():
    """Snapshot only this process. Unsupported counters are None, never zero."""
    result = dict(cpuMs=time.process_time_ns() / 1_000_000,
                  workingSetBytes=None, privateBytes=None, handles=None,
                  gdiObjects=None, userObjects=None, processThreads=None,
                  pythonThreads=threading.active_count())
    if sys.platform != 'win32':
        return result
    try:
        import psutil  # Optional; only an explicitly self-owned process object.
        result['processThreads'] = int(psutil.Process(os.getpid()).num_threads())
    except Exception:
        pass
    from ctypes import wintypes

    class MemoryCounters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                    *[(name, ctypes.c_size_t) for name in (
                        'PeakWorkingSetSize', 'WorkingSetSize',
                        'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage',
                        'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage',
                        'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]]

    try:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        process = kernel.GetCurrentProcess()  # Pseudo handle: no other PID lookup.
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD)
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        memory = MemoryCounters()
        memory.cb = ctypes.sizeof(memory)
        if psapi.GetProcessMemoryInfo(process, ctypes.byref(memory), memory.cb):
            result['workingSetBytes'] = int(memory.WorkingSetSize)
            result['privateBytes'] = int(memory.PrivateUsage)
        count = wintypes.DWORD()
        kernel.GetProcessHandleCount.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetProcessHandleCount.restype = wintypes.BOOL
        if kernel.GetProcessHandleCount(process, ctypes.byref(count)):
            result['handles'] = int(count.value)
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetGuiResources.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        user.GetGuiResources.restype = wintypes.DWORD
        for key, flag in (('gdiObjects', 0), ('userObjects', 1)):
            ctypes.set_last_error(0)
            value = user.GetGuiResources(process, flag)
            if value or not ctypes.get_last_error():
                result[key] = int(value)
    except (OSError, AttributeError):
        pass
    # No explicit system-wide process/thread enumeration in this collector.
    return result


_TARGETS = {
    'desktop_main_ui': {'DesktopMainUI': (
        '__init__', '_build', 'show_page', '_refresh_maps', '_refresh_view',
        '_sync_view', '_display_changed', '_apply_icons', '_layout_shell',
        '_layout_home', '_layout_preferences', '_poll_home_previews',
        '_render_help_image', '_fit_page', '_animate_view',
        '_refresh_home_resources', '_refresh_route_choices', '_sync_reference_controls'),
        'HotkeyEditorView': ('render',)},
    'desktop_display': {'WindowDisplay': ('refresh', 'apply_widgets')},
    'desktop_rounded': {'_Skins': ('get', 'picture', 'font'),
                        '_Appearance': ('_restyle',)},
    'game_control_view': {'GameControlView': (
        '__init__', 'render', 'apply_layout', 'set_display_scale',
        'set_available_height', 'expanded_size', '_measure_geometry',
        '_content_changed', '_canvas_changed', '_refresh_menu',
        '_apply_scaled_button_fonts', '_set_collapsed')},
    'map_resources_page': {'MapResourcesPage': (
        'refresh', '_resume', 'suspend', '_poll_context', '_render_library',
        '_package_rows', '_select_package', '_map_changed', '_request_detail',
        '_show_selected', '_layout_author_cards', '_detail_width', '_list_width',
        '_drain', '_apply_image', 'sync_online_packages',
        'sync_map_package_import', 'sync_managed_packages', 'sync_route_selection',
        'sync_game_mode', '_online_snapshot', '_managed_snapshot', '_map_package_snapshot')},
    'map_resource_browser': {'MapResourceBrowser': (
        '_request', '_apply_image', '_drain', '_pump_preview_work',
        'sync_online_packages', 'sync_map_package_import', 'sync_managed_packages')},
}

_DAILY_METHODS = (
    '__init__', 'expand_control', 'collapse_control', 'set_control_visible',
    '_show_assistant_shell', '_collapse_assistant_shell', '_shell_target_geometry',
    '_quick_panel_dimensions', '_set_assistant_surface', '_sync_game_control_view',
    '_sync_quick_status_ui', '_place_assistant_shell', '_apply_window_rounding',
    '_native_window_handle', '_set_noactivate', '_set_clickthrough',
    '_apply_control_circle', '_ui_context_for_window', '_show_shell_connector',
    '_commit_game_control_opacity', 'change_sheet_opacity', 'change_reference_opacity',
    '_flush_sheet_opacity_update', '_flush_reference_opacity_update',
    'toggle_sheet_visibility', 'set_reference_sheet_visible', 'show_sheet',
    '_show_image', '_show_aligned_overlay', '_alignment_v2_show_overlay',
    '_display_pixels_for_source', '_save_settings', '_route_presentation_snapshot',
    'reference_sheet_snapshot', 'route_selection_snapshot', '_current_display_descriptor', '_display_notice_context', '_shown_route_profile', '_mode_route_options',
    '_author_route_options', 'control_layout_snapshot', 'hotkey_editor_snapshot')


class InteractionProfile:
    """Bounded main-thread selected-call timings with reversible monkeypatches.

    Exclusive means selected-call inclusive minus DIRECT selected children;
    unwrapped Python/Tcl/native work and observer overhead remain in exclusive.
    Recursion is handled by a call stack, not by subtracting aggregate row totals.
    """
    def __init__(self, *, max_keys=256, slow_limit=32, slow_ms=20,
                 max_depth=128, clock=None, metrics=None):
        self.max_keys = max(1, int(max_keys))
        self.slow_limit = max(0, int(slow_limit))
        self.slow_ns = max(0, int(slow_ms * 1_000_000))
        self.max_depth = max(1, int(max_depth))
        self._clock = clock or time.perf_counter_ns
        self._metrics = metrics or own_process_metrics
        self._thread = threading.get_ident()
        self._patches = []
        self._missing = []
        self._stack = []
        self._stage = None
        self._closed = False

    @classmethod
    def install(cls, daily, **options):
        profile = cls(**options)
        try:
            daily_class = getattr(daily, 'DailyAssistant', daily)
            for name in _DAILY_METHODS:
                profile.wrap(daily_class, name, 'daily.' + name)
            profile.wrap(daily, 'atomic_write_text', 'daily.atomic_write_text')
            for module_name, classes in _TARGETS.items():
                try:
                    module = importlib.import_module(module_name)
                except ImportError:
                    profile._missing.append(module_name)
                    continue
                for class_name, methods in classes.items():
                    owner = getattr(module, class_name, None)
                    for name in methods:
                        profile.wrap(owner, name, module_name + '.' + class_name + '.' + name)
            import tkinter as tk
            from tkinter import font, ttk
            from PIL import ImageTk
            profile.wrap(tk.Misc, 'update_idletasks', 'tk.update_idletasks', caller=True)
            for owner, names in (
                (tk.Misc, ('configure', 'config', 'after', 'after_idle', 'after_cancel')),
                (ttk.Widget, ('configure', 'config')),
                (tk.Pack, ('pack_configure', 'configure', 'pack', 'pack_forget', 'forget')),
                (tk.Grid, ('grid_configure', 'configure', 'grid', 'grid_remove', 'grid_forget')),
                (tk.Place, ('place_configure', 'configure', 'place', 'place_forget')),
                (tk.Wm, ('geometry', 'wm_geometry', 'attributes', 'wm_attributes',
                         'withdraw', 'deiconify')),
                (ttk.Style, ('configure', 'map', 'layout', 'element_create')),
                (font.Font, ('__init__', 'configure')),
                (ImageTk.PhotoImage, ('__init__',))):
                for name in names:
                    profile.wrap(owner, name, owner.__module__ + '.' + owner.__name__ + '.' + name)
            return profile
        except BaseException:
            profile.finish()
            raise

    def wrap(self, owner, name, label=None, *, caller=False):
        """Add a selected method (also useful for headless tests).

        Static/class methods retain descriptors. Inherited attributes are removed
        again at restore, so class dictionaries return to their original shape.
        """
        if self._closed:
            raise RuntimeError('profile already finished')
        label = label or name
        if owner is None or not hasattr(owner, name):
            if len(self._missing) < self.max_keys:
                self._missing.append(label)
            return False
        if any(obj is owner and key == name for obj, key, *_ in self._patches):
            return False
        namespace = vars(owner)
        local = name in namespace
        original = namespace.get(name) if local else getattr(owner, name)
        descriptor = type(original) if isinstance(original, (staticmethod, classmethod)) else None
        function = original.__func__ if descriptor else original
        if not callable(function):
            return False
        existing_profile = getattr(function, '_interaction_profile_owner', None)
        if existing_profile is self:
            return False  # An inherited method is already covered by this owner.
        if existing_profile is not None:
            raise RuntimeError('selected method already has a diagnostic profile')

        @functools.wraps(function)
        def measured(*args, **kwargs):
            if self._stage is None or threading.get_ident() != self._thread:
                return function(*args, **kwargs)
            if len(self._stack) >= self.max_depth:
                self._depth_skipped += 1
                return function(*args, **kwargs)
            key = label
            if caller:
                frame = sys._getframe(1)
                key += '@' + os.path.basename(frame.f_code.co_filename.replace('\\', '/'))
                key += ':' + frame.f_code.co_name + ':' + str(frame.f_lineno)
                del frame
            start = self._clock()
            frame_cost = [0]
            self._stack.append(frame_cost)
            failed = False
            try:
                return function(*args, **kwargs)
            except BaseException:
                failed = True
                raise
            finally:
                elapsed = max(0, self._clock() - start)
                self._stack.pop()
                if self._stack:
                    self._stack[-1][0] += elapsed
                self._record(key, elapsed, max(0, elapsed-frame_cost[0]), failed)

        measured._interaction_profile_owner = self
        replacement = descriptor(measured) if descriptor else measured
        setattr(owner, name, replacement)
        self._patches.append((owner, name, local, original, replacement))
        return True

    def begin(self, stage):
        if self._closed or self._stage is not None or self._stack:
            raise RuntimeError('begin requires an idle installed profile')
        # Caller supplies diagnostic labels only; never store action arguments.
        self._stage = str(stage)[:80]
        self._rows = {}
        self._slow = deque(maxlen=self.slow_limit)
        self._overflow_calls = 0
        self._depth_skipped = 0
        self._before = self._metrics()
        self._started = self._clock()

    def _record(self, key, inclusive, exclusive, failed):
        if key not in self._rows and len(self._rows) >= self.max_keys:
            key = '__overflow__'
            self._overflow_calls += 1
        row = self._rows.get(key)
        if row is None:
            row = self._rows[key] = [0, 0, 0, 0, 0]
        row[0] += 1
        row[1] += inclusive
        row[2] += exclusive
        row[3] = max(row[3], inclusive)
        row[4] += int(failed)
        if inclusive >= self.slow_ns and self.slow_limit:
            self._slow.append(dict(key=key, inclusiveMs=inclusive/1_000_000,
                                   exclusiveMs=exclusive/1_000_000, failed=failed,
                                   depth=len(self._stack)))

    def end(self, stage=None):
        if self._stage is None or self._stack:
            raise RuntimeError('end requires an active stage outside selected calls')
        if stage is not None and str(stage)[:80] != self._stage:
            raise ValueError('stage label mismatch')
        elapsed = max(0, self._clock()-self._started)
        self._stage, name = None, self._stage
        after = self._metrics()
        delta = {key: (after[key]-value if isinstance(value, (int, float))
                       and isinstance(after.get(key), (int, float)) else None)
                 for key, value in self._before.items()}
        return dict(stage=name, wallMs=elapsed/1_000_000,
                    calls={key: dict(count=row[0], inclusiveMs=row[1]/1_000_000,
                                     exclusiveMs=row[2]/1_000_000, maxMs=row[3]/1_000_000,
                                     errors=row[4]) for key, row in self._rows.items()},
                    slowCalls=list(self._slow), overflowCalls=self._overflow_calls,
                    depthSkippedCalls=self._depth_skipped,
                    processBefore=self._before, processAfter=after, processDelta=delta,
                    limits=dict(maxKeys=self.max_keys, overflowExtraKey=True,
                                slowLimit=self.slow_limit, maxDepth=self.max_depth,
                                slowThresholdMs=self.slow_ns/1_000_000),
                    missingTargets=list(self._missing),
                    interpretation=(
                        'Main-thread selected calls only; inclusive rows overlap. Exclusive includes '
                        'unwrapped work and observer overhead. Wall includes event-loop settle, not '
                        'verified visible pixels. Process CPU includes workers. RAM/resource deltas '
                        'are snapshots, not leak proof. processThreads may be unavailable; pythonThreads '
                        'counts Python-managed threads only. No arguments or user file paths recorded.'))

    def finish(self):
        """Restore only patches still owned by us; preserve later external edits."""
        conflicts = []
        self._stage = None
        for owner, name, local, original, replacement in reversed(self._patches):
            if vars(owner).get(name) is replacement:
                if local:
                    setattr(owner, name, original)
                else:
                    delattr(owner, name)
            else:
                conflicts.append(name)
        self._patches.clear()
        self._closed = True
        return dict(restored=not conflicts, conflicts=conflicts)
