using System.Runtime.CompilerServices;

[assembly: InternalsVisibleTo("StarMap.KeyboardTests")]

namespace StarMap.Windows;

/// <summary>One pass-through keyboard edge observed by the native hook thread.</summary>
public sealed record KeyboardEdge(
    int VirtualKey,
    int PhysicalVirtualKey,
    bool IsDown,
    bool WasDown,
    long Timestamp,
    nint ForegroundHwnd,
    long Generation);

/// <summary>Startup facts. Held keys are physical VKs relevant to the initial subscription.</summary>
public sealed record KeyboardStartInfo(
    long Generation,
    IReadOnlyList<int> InitialHeldKeys);

/// <summary>Result of the bounded shutdown attempt. Stopped also requires hook removal to succeed.</summary>
public sealed record KeyboardCloseResult(
    bool Stopped,
    string? FailureMessage);
