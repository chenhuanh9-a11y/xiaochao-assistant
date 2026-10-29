"""Opt-in, fixed-sequence diagnostics for the real packaged executable.

This is a Python API guard, not a native/OS filesystem sandbox. No external
script, resource directory, controller or executable identity can be supplied.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time


def activate_resource_tab(browser, tab):
    """Exercise the same visible actions as the current map resources page."""
    if tab not in {'installed', 'discover'}:
        raise ValueError('unsupported resource tab')
    browser.tab_buttons[tab].invoke()
    if browser._tab != tab:
        raise RuntimeError('Resource page tab did not change')


def isolate_window(window, native_attributes, user):
    """Keep this diagnostic's Tk windows inert before production construction."""
    window.withdraw()
    # Set disabled before alpha: changing alpha may realize a native wrapper.
    native_attributes(window, '-disabled', True)
    native_attributes(window, '-alpha', 0.0)
    native_attributes(window, '-topmost', False)
    # Resolve Tk's deferred wrapper replacement while still hidden/disabled,
    # then apply flags to the HWND that will actually survive the style change.
    window.update_idletasks()
    hwnd = user.GetAncestor(window.winfo_id(), 2)
    if not hwnd:
        raise RuntimeError('Diagnostic Tk window has no native wrapper')
    flags = user.GetWindowLongW(hwnd, -20)
    user.SetWindowLongW(hwnd, -20, flags | 0x08000000 | 0x80 | 0x20)
    return int(hwnd)


def set_isolated_override(window, boolean, native_override, native_attributes, user):
    result = native_override(window, boolean)
    if boolean is not None:
        # Tk can replace its HWND on this production style change, dropping
        # native-only flags even though the Python window remains withdrawn.
        isolate_window(window, native_attributes, user)
    return result


class PathGuard:
    def __init__(self, output, reads):
        self.output = self.canonical(output)
        self.reads = tuple(self.canonical(path) for path in reads)
        self.attempts = []
        self.local = threading.local()

    @staticmethod
    def canonical(path):
        return os.path.normcase(os.path.realpath(os.path.abspath(os.fsdecode(path))))

    @staticmethod
    def within(path, root):
        return path == root or path.startswith(root + os.sep)

    def check(self, path, write=False, api='file'):
        # Existing descriptors and file objects are not path requests. Native
        # extensions can also bypass these Python APIs; report this limitation.
        if path is None or isinstance(path, int) or not isinstance(path, (str, bytes, os.PathLike)):
            return
        if getattr(self.local, 'checking', False):
            return
        self.local.checking = True
        try:
            value = os.fsdecode(path)
            drive, tail = os.path.splitdrive(value)
            invalid = value.startswith(('\\\\', '//')) or (drive and not tail.startswith(('\\', '/')))
            resolved = self.canonical(value)
            allowed = not invalid and any(self.within(resolved, root) for root in
                                         ((self.output,) if write else self.reads))
            if not allowed:
                self.attempts.append(dict(api=api, path=resolved, write=bool(write)))
                raise PermissionError('UI audit path outside permitted roots')
        finally:
            self.local.checking = False

    def install(self):
        def audit(event, args):
            if event == 'open':
                mode, flags = args[1:3]
                write = (isinstance(mode, str) and any(x in mode for x in 'wax+')) or (
                    isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)))
                self.check(args[0], write, event)
            elif event in ('os.listdir', 'os.scandir'):
                self.check(args[0] if args else os.getcwd(), api=event)
            elif event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod', 'os.utime'):
                self.check(args[0], True, event)
            elif event in ('os.rename', 'os.link', 'os.symlink'):
                self.check(args[0], True, event)
                self.check(args[1], True, event)
            elif event.startswith('socket.') and event in ('socket.connect', 'socket.bind', 'socket.getaddrinfo'):
                self.attempts.append(dict(api=event, blocked='network'))
                raise PermissionError('UI audit network disabled')
            elif event in ('subprocess.Popen', 'os.system', 'os.startfile'):
                self.attempts.append(dict(api=event, blocked='process'))
                raise PermissionError('UI audit child processes disabled')
        sys.addaudithook(audit)
        for name in ('stat', 'lstat', 'access', 'readlink'):
            original = getattr(os, name)
            def checked(path, *args, _original=original, _name=name, **kwargs):
                self.check(path, api='os.' + _name)
                return _original(path, *args, **kwargs)
            setattr(os, name, checked)


def prepare(argv):
    """Called before application imports. Normal execution never calls this."""
    import_data = None
    if len(argv) == 4 and argv[1] == '--ui-import-audit':
        from ui_import_audit import prepare_import_audit
        import_data = prepare_import_audit(argv)
    elif len(argv) != 3 or argv[1] not in ('--ui-audit', '--ui-interaction-audit'):
        raise SystemExit('Usage: application.exe --ui-audit NEW_ABSOLUTE_OUTPUT_DIRECTORY')
    if not getattr(sys, 'frozen', False) or not getattr(sys, '_MEIPASS', None):
        raise SystemExit('--ui-audit requires the actual packaged executable')
    output = import_data['output'] if import_data else Path(argv[2])
    if not output.is_absolute() or str(output).startswith(('\\\\', '//')):
        raise SystemExit('--ui-audit requires a local absolute output directory')
    output = output.resolve()
    protected = (Path(sys.executable).resolve().parent, Path(sys._MEIPASS).resolve(),
                 Path(os.environ['SystemRoot']).resolve())
    if any(PathGuard.within(PathGuard.canonical(output), PathGuard.canonical(root)) for root in protected):
        raise SystemExit('--ui-audit output must be outside package and Windows directories')
    if import_data is None:
        output.mkdir(exist_ok=False)  # Never reuse or overwrite a user's directory.
    for name in ('temp', 'live', 'memory', 'custom'):
        (output / name).mkdir()
    for key, name in (('TEMP', 'temp'), ('TMP', 'temp'),
                      ('IDENTITYV_NIGHTMARE_LIVE_ROOT', 'live'),
                      ('IDENTITYV_NIGHTMARE_MEMORY_ROOT', 'memory'),
                      ('XINGTU_CUSTOM_MAPS_ROOT', 'custom')):
        os.environ[key] = str(output / name)
    # External overrides are not accepted by this fixed packaged-resource case.
    for key in tuple(os.environ):
        if key.startswith(('IDENTITYV_', 'NIGHTMARE_', 'XINGTU_', 'STAR_')) and key not in (
            'IDENTITYV_NIGHTMARE_LIVE_ROOT', 'IDENTITYV_NIGHTMARE_MEMORY_ROOT', 'XINGTU_CUSTOM_MAPS_ROOT'):
            os.environ.pop(key, None)
    os.chdir(output)
    sys.dont_write_bytecode = True
    reads = [output, Path(sys.executable).resolve().parent, Path(sys._MEIPASS).resolve(),
             Path(os.environ['SystemRoot']).resolve()]
    guard = PathGuard(output, reads)
    scenario = 'import' if import_data else 'interaction' if argv[1] == '--ui-interaction-audit' else 'standard'
    state = dict(output=output, guard=guard, scenario=scenario, report=dict(
        status='bootstrap', frozen=True, executable=sys.executable, meipass=sys._MEIPASS,
        actualConstructor=False, stages=[], errors=[], filesystemAttempts=guard.attempts,
        readRoots=[str(path) for path in reads], writeRoot=str(output),
        limits=['Python API guard, not OS/native DLL filesystem isolation',
                'No game input/capture; no displayed FPS or visual-fidelity claim',
                'DPI stages override only the production monitor-context snapshot']))
    if import_data:
        state['importInputs'] = import_data['inputs']
        state['importOwnership'] = import_data['ownership']
    save(state)
    guard.install()
    return state


def save(state):
    (state['output'] / 'ui-audit.json').write_text(json.dumps(state['report'], indent=2), encoding='utf-8')


def fail(state, error):
    state['report']['status'] = 'failed'
    state['report']['errors'].append(type(error).__name__ + ': ' + str(error))
    save(state)


def abort(state, error):
    """Import-time failures must not reach a windowed bootloader error dialog."""
    try:
        fail(state, error)
    finally:
        os._exit(2)


def run_interaction_scenario(assistant, ui, stage, pump, map_invisible):
    """Fixed daily-operation sequence; no game, capture or identity synthesis."""
    stage('startup', ui.display.refresh, 500)
    for repetition in range(3):
        for page in ('preferences', 'maps', 'help', 'home'):
            stage(f'nav_{repetition}_{page}', ui._nav_buttons[page].invoke, 250)
    map_invisible(assistant.assistant_shell)
    for repetition in range(3):
        stage(f'control_{repetition}_expand', assistant.expand_control, 250)
        if assistant.control_collapsed or not assistant.assistant_shell.winfo_viewable():
            raise RuntimeError('Control expansion did not produce the actual expanded shell')
        height = assistant.assistant_shell.winfo_height()
        stage(f'control_{repetition}_collapse', assistant.collapse_control, 250)
        if not assistant.control_collapsed or assistant.assistant_shell.winfo_height() >= height:
            raise RuntimeError('Control collapse did not reduce actual shell height')
    stage('control_expand_for_alpha', assistant.expand_control, 250)
    view = assistant.game_control_view
    for surface, variable in (('overlay', assistant.opacity_var),
                              ('reference', assistant.reference_opacity_var)):
        original = variable.get()
        slider = view.opacity_scales[surface]
        def drag(widget=slider):
            for value in (5, 25, 45, 65, 85, 100):
                widget.set(value)
                pump(18)
        stage('alpha_' + surface + '_drag', drag, 350)
        stage('alpha_' + surface + '_restore', lambda w=slider, v=original: w.set(v), 350)
        if variable.get() != original:
            raise RuntimeError('Alpha did not restore its original value')
    for width, height in ((700, 500), (1280, 900), (1000, 720)):
        stage('ordinary_resize_' + str(width),
              lambda w=width, h=height: ui.window.geometry(f'{w}x{h}-32000-32000'), 350)
    stage('idle_repeated_operations', lambda: None, 1000)


def run(daily, state):
    """Full production constructor and bound callbacks, in this PE process."""
    from dataclasses import replace
    import faulthandler
    import tkinter as tk
    from ctypes import wintypes
    import desktop_main_ui

    report, output = state['report'], state['output']
    report['buildFingerprint'] = daily.BUILD_FINGERPRINT
    report['pid'] = os.getpid()
    if state.get('importOwnership') and daily.BUILD_FINGERPRINT != state['importOwnership']['buildFingerprint']:
        raise RuntimeError('Import diagnostic build identity changed')
    profile = None
    if state.get('scenario') == 'interaction':
        from ui_interaction_profile import InteractionProfile
        profile = InteractionProfile.install(daily)
    threading.excepthook = lambda args: report['errors'].append(
        args.exc_type.__name__ + ': ' + str(args.exc_value))
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    mutex = kernel.CreateMutexW(None, False, 'Local\\XingtuPackagedUiAudit')
    if not mutex or kernel.GetLastError() == 183:
        if mutex:
            kernel.CloseHandle(mutex)
        raise RuntimeError('Another isolated UI audit is already running')
    user = ctypes.windll.user32
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    foreground = user.GetForegroundWindow()
    original_deiconify, original_lift = tk.Wm.deiconify, tk.Misc.lift
    original_attributes = tk.Wm.attributes
    original_override = tk.Wm.overrideredirect
    original_toplevel = tk.Toplevel.__init__
    calls = report['boundaryCalls'] = {}
    def blocked(name):
        def call(*args, **kwargs):
            calls[name] = calls.get(name, 0) + 1
        return call
    tk.Wm.deiconify = tk.Wm.wm_deiconify = blocked('deiconify')
    tk.Misc.focus_force = blocked('focus_force')
    tk.Misc.focus = tk.Misc.focus_set = blocked('focus_set')
    tk.Misc.lift = tk.Misc.tkraise = lambda widget, *a, **kw: (
        blocked('toplevel_lift')() if isinstance(widget, (tk.Tk, tk.Toplevel))
        else original_lift(widget, *a, **kw))
    def hidden_toplevel(widget, *args, **kwargs):
        original_toplevel(widget, *args, **kwargs)
        hwnd = isolate_window(widget, original_attributes, user)
        report.setdefault('isolatedWindows', []).append(dict(kind='toplevel', hwnd=hwnd))
        save(state)
    tk.Toplevel.__init__ = hidden_toplevel
    def overrideredirect(widget, boolean=None):
        return set_isolated_override(widget, boolean, original_override, original_attributes, user)
    tk.Wm.overrideredirect = tk.Wm.wm_overrideredirect = overrideredirect
    def attributes(widget, *args, **kwargs):
        if len(args) >= 2 and args[0] in ('-alpha', '-topmost', '-disabled'):
            args = (args[0], {'-alpha': 0.0, '-topmost': False, '-disabled': True}[args[0]], *args[2:])
        return original_attributes(widget, *args, **kwargs)
    tk.Wm.attributes = tk.Wm.wm_attributes = attributes
    # Do not initiate update network work. Any unexpected socket/process request
    # is separately rejected and makes the report fail through the API guard.
    desktop_main_ui.DesktopMainUI._check_updates = blocked('update_dispatch')
    install_mode = daily.DailyAssistant._install_map_mode
    def isolated_mode(assistant, mode, **kwargs):
        kwargs['memory_root'] = output / 'memory' / str(mode)
        return install_mode(assistant, mode, **kwargs)
    daily.DailyAssistant._install_map_mode = isolated_mode
    daily.DailyAssistant.apply_hotkey = blocked('apply_hotkey')
    daily.DailyAssistant._play_ui_sound = blocked('ui_sound')
    # An installed user's pending import receipt is unrelated to this fresh
    # diagnostic profile; keep real empty import-state initialization instead.
    daily.DailyAssistant._load_map_package_import_receipt = daily.DailyAssistant._ensure_map_package_import_state
    trace = (output / 'stacks.txt').open('w', encoding='utf-8')
    faulthandler.enable(trace)
    root = assistant = watchdog = None
    beats = []
    def timed_out():
        try:
            report['status'] = 'timeout'
            save(state)
            faulthandler.dump_traceback(file=trace, all_threads=True)
            trace.flush()
        finally:
            os._exit(124)  # Only this explicitly launched diagnostic process.
    def arm():
        nonlocal watchdog
        if watchdog:
            watchdog.cancel()
        watchdog = threading.Timer(45, timed_out)
        watchdog.daemon = True
        watchdog.start()
    def pump(milliseconds):
        import _tkinter
        until = time.perf_counter() + milliseconds / 1000
        while time.perf_counter() < until:
            # Process one event per iteration. update() drains *all* currently
            # pending events and may never return with periodic producers;
            # that would time out the diagnostic even while mainloop progresses.
            root.tk.dooneevent(_tkinter.ALL_EVENTS | _tkinter.DONT_WAIT)
            time.sleep(.001)
    def stage(name, action, settle=350):
        report['phase'] = name
        arm()
        beats.clear()
        last[0] = time.perf_counter()
        started = last[0]
        if profile:
            profile.begin(name)
        action()
        action_ms = (time.perf_counter() - started) * 1000
        checkpoint = []
        root.after_idle(lambda: checkpoint.append((time.perf_counter() - started) * 1000))
        pump(settle)
        browser = assistant.desktop_main_ui.map_browser
        values = sorted(beats)
        shell = assistant.assistant_shell
        row = dict(phase=name, actionMs=round(action_ms, 3),
                   tkIdleCheckpointMs=round(checkpoint[0], 3) if checkpoint else None,
                   checkpointMeaning='Tk idle checkpoint after action return, not verified screen presentation',
                   heartbeatMaxMs=max(values, default=0),
                   heartbeatP95Ms=values[int((len(values)-1)*.95)] if values else None,
                   imageCount=len(root.tk.call('image', 'names')),
                   timerCount=len(root.tk.call('after', 'info')),
                   pending=len(browser._pending), futures=len(browser._preview_futures),
                   waiting=len(browser._preview_waiting), resultQueue=browser._results.qsize(),
                   resourceTab=browser._tab,
                   metadataPending=browser._browse_work is not None or browser._browse_pending is not None,
                   state=ui.window.state(), viewable=bool(ui.window.winfo_viewable()),
                   width=ui.window.winfo_width(), height=ui.window.winfo_height(),
                   shell=dict(state=shell.state(), viewable=bool(shell.winfo_viewable()),
                              width=shell.winfo_width(), height=shell.winfo_height(),
                              collapsed=assistant.control_collapsed))
        if profile:
            row['profile'] = profile.end(name)
        report['stages'].append(row)
        report['foregroundUnchanged'] = foreground == user.GetForegroundWindow()
        if not report['foregroundUnchanged']:
            raise RuntimeError('Foreground changed during diagnostic')
        save(state)
    try:
        report['phase'] = 'constructor'
        save(state)  # Preserve PID/FP and the active phase before native setup.
        arm()
        settings = output / 'settings.json'
        settings.write_text(json.dumps(dict(onboardingCompleted=True, gameMode='nightmare',
            orbVisible=False, reduceMotion=False, uiSoundEnabled=False)), encoding='utf-8')
        daily.DPI_AWARENESS_MODE = daily.enable_per_monitor_dpi_awareness()
        root = tk.Tk()
        hwnd = isolate_window(root, original_attributes, user)
        report.setdefault('isolatedWindows', []).append(dict(kind='root', hwnd=hwnd))
        save(state)
        root.report_callback_exception = lambda kind, error, tb: report['errors'].append(kind.__name__ + ': ' + str(error))
        last = [time.perf_counter()]
        def heartbeat():
            now = time.perf_counter()
            beats.append((now-last[0])*1000)
            last[0] = now
            root.after(10, heartbeat)
        root.after(10, heartbeat)
        started = time.perf_counter()
        assistant = daily.DailyAssistant(root, start_background=False, debug_feedback=False,
            settings_path=settings, nightmare_resource_root=Path(
                state.get('resourceRoot') or getattr(sys, '_MEIPASS', daily.RESOURCE_ROOT)))
        report['actualConstructor'] = True
        report['constructorMs'] = (time.perf_counter()-started)*1000
        report['resourceRoot'] = str(assistant.pack.resource_root)
        report['mapCount'] = len(assistant.pack.maps)
        report['packageMetadata'] = assistant.package_metadata
        report['replacements'] = ['start_background=False; isolated supported memory_root and settings_path',
            'update dispatch, hotkey application, UI sound, existing-user import receipt disabled',
            'own Tk windows invisible/disabled/noactivate; focus/top-level lift suppressed',
            'fixed DPI stages use production monitor snapshot with DPI replacement']
        if assistant.mode_resource_error or assistant.startup_resource_error or not assistant.pack.maps:
            raise RuntimeError('Constructor resource initialization failed')
        ui = assistant.desktop_main_ui
        def map_invisible(window):
            window.attributes('-alpha', 0.0)
            window.attributes('-disabled', True)
            window.attributes('-topmost', False)
            window.geometry('1000x720-32000-32000')
            window.update_idletasks()
            hwnd = user.GetAncestor(window.winfo_id(), 2)
            flags = user.GetWindowLongW(hwnd, -20)
            user.SetWindowLongW(hwnd, -20, flags | 0x08000000 | 0x80 | 0x20)
            original_deiconify(window)
        map_invisible(ui.window)
        if state.get('scenario') == 'import':
            from ui_import_audit import run_import_scenario
            report['importScenario'] = run_import_scenario(assistant, ui, output, pump, stage,
                                                          state['importInputs'])
            report['status'] = 'cleaning_up'
            save(state)
        elif state.get('scenario') == 'interaction':
            run_interaction_scenario(assistant, ui, stage, pump, map_invisible)
        else:
            stage('startup', ui.display.refresh, 800)
            for page in ('preferences', 'maps', 'help', 'home', 'maps'):
                stage('page_' + page, ui._nav_buttons[page].invoke)
            stage('gallery', lambda: activate_resource_tab(ui.map_browser, 'installed'), 800)
            stage('gallery_repeat', ui._nav_buttons['maps'].invoke)
            stage('gallery_hide', ui._nav_buttons['preferences'].invoke)
            stage('gallery_return', ui._nav_buttons['maps'].invoke, 800)
            stage('overview', lambda: activate_resource_tab(ui.map_browser, 'discover'), 800)
            for width, height in ((700, 500), (1280, 900), (1000, 720)):
                stage('resize_' + str(width), lambda w=width, h=height: ui.window.geometry(f'{w}x{h}-32000-32000'))
            context = assistant._ui_context_for_window_snapshot
            try:
                for dpi in (120, 144, 96):
                    assistant._ui_context_for_window_snapshot = lambda window, value=dpi: replace(context(window), dpi=value)
                    stage('dpi_' + str(dpi), ui.display.refresh, 700)
            finally:
                assistant._ui_context_for_window_snapshot = context
            stage('native_dpi_restore', ui.display.refresh, 700)
            map_invisible(assistant.assistant_shell)
            stage('control_expand', assistant.expand_control)
            if assistant.control_collapsed or not assistant.assistant_shell.winfo_viewable():
                raise RuntimeError('Control expansion did not produce a mapped expanded shell')
            expanded_height = assistant.assistant_shell.winfo_height()
            stage('control_collapse', assistant.collapse_control)
            if not assistant.control_collapsed or not assistant.assistant_shell.winfo_viewable():
                raise RuntimeError('Control collapse did not produce a mapped collapsed shell')
            if assistant.assistant_shell.winfo_height() >= expanded_height:
                raise RuntimeError('Control collapse did not reduce actual native shell height')
            stage('control_expand_repeat', assistant.expand_control)
            if assistant.control_collapsed or assistant.assistant_shell.winfo_height() <= report['stages'][-2]['shell']['height']:
                raise RuntimeError('Control expansion did not restore actual native shell height')
            stage('idle', lambda: None, 1000)
        report['status'] = 'cleaning_up'
        save(state)
    finally:
        report['phase'] = 'shutdown'
        arm()
        try:
            executors = []
            if assistant is not None:
                desktop = assistant.desktop_main_ui
                executors = [desktop.map_browser._executor,
                             desktop.map_browser._metadata_executor,
                             getattr(desktop, '_home_preview_executor', None)]
                assistant.stop_event.set()
                assistant.closed = True
                assistant._event_log.close()
                if assistant.aligned_overlay is not None:
                    assistant.aligned_overlay.close()
            if root is not None:
                root.destroy()
            # Production destroy invalidates results and requests cancellation;
            # this diagnostic additionally joins its own workers while bounded,
            # so PE atexit cannot hang after a prematurely successful report.
            for executor in executors:
                if executor is not None:
                    executor.shutdown(wait=True, cancel_futures=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(mutex)
        finally:
            watchdog.cancel()
            trace.close()
            if profile:
                report['profileRestore'] = profile.finish()
    report['status'] = 'failed' if report['errors'] or state['guard'].attempts else 'completed'
    report['foregroundUnchanged'] = foreground == user.GetForegroundWindow()
    if not report['foregroundUnchanged']:
        report['status'] = 'failed'
    save(state)
    return 0 if report['status'] == 'completed' else 2
