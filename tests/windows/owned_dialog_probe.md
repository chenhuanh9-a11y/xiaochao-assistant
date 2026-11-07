# Owned synthetic dialog capability probe

This is a separate, disposable test fixture, not an installer controller. No Setup,
map resources, real application, user installation, security setting or desktop
window discovery is involved. No runtime dependencies or production files change.
Only Windows PowerShell 5.1 and Windows' .NET Framework assemblies are used.

Run on an explicitly disposable Windows runner from a checkout containing these files:

```powershell
powershell.exe -NoProfile -NonInteractive -File tests/windows/owned_dialog_probe.ps1
```

Or use the existing Python unittest mechanism:

```powershell
$env:XINGTU_RUN_OWNED_DIALOG_PROBE = '1'
python -m unittest discover -s tests -p test_owned_dialog_probe.py -v
```

Set the containing CI step/job timeout to 2 minutes. Do not dot-source the script:
it exits its PowerShell process. The controller has a 45-second hard bound, and
kills only its own test process on timeout (exit 124). A failure is exit 1. Timeout
never invokes confirmation, never relaxes identity checks, and never retries an
uncertain action. A compilation failure is a failure, not a skipped capability.
Do not bypass execution policy or security restrictions if the runner rejects it.

Expected successful output contains these six PASS records:

- identity mismatches and unknown handles rejected before UIA
- explicit confirm exactly once; duplicate rejected/ignored
- explicit cancel; no confirmation
- expired action rejected; late callback ignored; no auto-confirm
- closed fixture rejects late action before UIA
- synthetic-only UIAutomation probe; Setup/game/UAC NOT tested

The STA WinForms message loop is separate from the MTA UIAutomation controller.
Only explicitly registered self-created HWNDs are accessible. Identity binds PID,
process start time, executable path, SHA-256 of the executing wrapper and C# source,
HWND, and a fresh random marker. The marker is also checked on the window and its
unique immediate child button. PID/creation/image/script/source/marker mutations,
an unregistered same-PID decoy window and HWND zero all reject before UIA. No
other desktop window title or contents are read or logged. The fixture counts
actual callback attempts as well as accepted confirm/cancel transitions. Explicit
owned callback replay proves duplicate/expired callbacks do not change the result.
Closing a fixture revokes subsequent controller actions before UIA access. Cleanup
closes only the host, decoy and fixture windows created in this process.

The allowlist and identities are an in-process test boundary, not a hostile-process
security proof. This does not test PID reuse, malicious provider/handle substitution,
cross-process automation, UIPI, elevation, UAC/secure desktop, unattended interactive
runner availability, installer wording, or any real Setup workflow. Direct callback
replay is deliberately limited to known owned synthetic buttons. The source-scope
Python checks are only tripwires, not proof of Windows behavior. Linux skips the
Windows execution; report that separately. No Windows execution evidence is supplied
by merely adding or compiling this fixture.
