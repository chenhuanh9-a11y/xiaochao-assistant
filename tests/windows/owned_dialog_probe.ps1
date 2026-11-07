# Run in a disposable Windows runner with Windows PowerShell 5.1, not dot-sourced.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'Windows required; no emulation fallback.' }
$source = Join-Path $PSScriptRoot 'owned_dialog_probe.cs'
Add-Type -Path $source -ReferencedAssemblies @(
    'System', 'System.Core', 'System.Windows.Forms', 'System.Drawing',
    'WindowsBase', 'UIAutomationClient', 'UIAutomationTypes'
)
exit [OwnedDialogProbe]::Run($PSCommandPath, $source)
