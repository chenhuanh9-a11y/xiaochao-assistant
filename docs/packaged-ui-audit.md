# Packaged UI audit

This opt-in diagnostic was added after `6ac37837`; it requires a new executable
built from the commit containing the entry point. Earlier host-Python full
constructor replay is separate evidence and is not PE bootloader validation.

Run the actual built executable with exactly:

```text
application.exe --ui-audit C:\absolute\new-diagnostic-directory
```

The output directory must not exist and must be outside the executable package
and Windows directories. No script, module, source checkout or resource override
argument is accepted. Normal startup is unchanged. Source Python rejects this
flag; tests do not spoof executable identity. `sys.executable`, `sys.frozen` and
`sys._MEIPASS` retain their actual bootloader values.

The early bootstrap establishes fresh live/custom/memory/temp paths before app
imports. The full production `DailyAssistant` constructor uses its supported
settings/resource parameters; the diagnostic supplies the existing
`_install_map_mode(memory_root=...)` parameter for each mode. Existing-user import
receipts, update dispatch, sound and hotkey application are disabled. Game/input
workers are disabled with `start_background=False`. A separate named audit mutex
prevents concurrent audits without opening the normal application's instance.

The fixed sequence invokes real page buttons (home/settings/resources/help),
gallery open/repeat/hide/return, overview, native resize, production DPI refresh,
and real control expansion/collapse. Main and control windows are own mapped Tk
windows, alpha zero, disabled and NOACTIVATE; focus/top-level lift are suppressed.
It sends no user input. The DPI cases override the monitor-context snapshot;
they are not OS DPI changes. There is no visibility predicate shim. Control
native height/state changes are asserted. No displayed FPS, game performance or
pixel visual-fidelity claim follows from this test.

`ui-audit.json` records actual bootloader identity, build fingerprint and package
metadata, resource count/root, constructor and per-stage action timing, heartbeat,
Tk images/timers, resource queues, native window dimensions/state, foreground
preservation, errors and guard attempts. A caught forbidden attempt still fails
the run. `completed` is written only after cleanup returns without failure;
require both that status and exit code zero. A per-stage/constructor/shutdown
45-second watchdog exits only this diagnostic process with code 124 and leaves
`timeout` plus `stacks.txt`. An outer runner should also enforce its own timeout
against only its own launched process. Import/runtime failures bypass the normal
application error dialog and return nonzero. Invalid arguments/pre-existing
output return nonzero without modifying that output.

The filesystem guard covers Python audited opens/enumeration/mutations and
explicit stat/lstat/access/readlink wrappers. Read roots are actual package,
`_MEIPASS`, the new output and Windows system directory; writes are restricted to
the new output. Network/child-process dispatch is blocked. Canonical component
containment rejects sibling prefixes, unknown UNC/device and drive-relative
paths. Existing file descriptors/file objects are not new path requests.
**This is a Python API guard, not OS-wide filesystem isolation:** native DLL,
Tcl, Win32 and extension direct file APIs are not all intercepted. It cannot be
reported as proof that every native read was sandboxed.

Validation: `python -m unittest discover -s tests -p test_ui_packaged_audit.py -v`
checks early dispatch, no identity assignment, host/extra-argument rejection,
component boundaries and actual guarded file/stat/delete/socket attempts. These
unit tests do not establish that a newly built PE completed the diagnostic.
