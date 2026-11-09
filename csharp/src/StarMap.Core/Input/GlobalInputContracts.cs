using System.Collections.ObjectModel;
using StarMap.Core.Settings;

namespace StarMap.Core.Input;

[Flags]
public enum GlobalInputModifiers { None = 0, Ctrl = 1, Alt = 2, Shift = 4 }

public sealed record GlobalInputBinding(string Action, string Name, int VirtualKey, GlobalInputModifiers Modifiers);

/// <summary>Immutable, validated settings projection. Uses the settings parser, including its aliases.</summary>
public sealed class GlobalInputBindings
{
    public static IReadOnlyDictionary<string, int> PickerKeys { get; } = new ReadOnlyDictionary<string, int>(
        new Dictionary<string, int> { ["F1"] = 0x70, ["F2"] = 0x71, ["F3"] = 0x72, ["F4"] = 0x73,
            ["PageUp"] = 0x21, ["PageDown"] = 0x22, ["Backspace"] = 0x08, ["Escape"] = 0x1b });

    private GlobalInputBindings(IEnumerable<GlobalInputBinding> items)
    {
        Items = Array.AsReadOnly(items.ToArray());
        PrimaryVirtualKeys = Array.AsReadOnly(Items.Select(item => item.VirtualKey).Where(key => key != 0)
            .Concat(PickerKeys.Values).Distinct().Order().ToArray());
    }

    public IReadOnlyList<GlobalInputBinding> Items { get; }
    public IReadOnlyList<int> PrimaryVirtualKeys { get; }

    public static GlobalInputBindings FromSettings(SettingsDocument settings)
        => FromSettings(settings.Hotkey, settings.ActionHotkeys);

    public static GlobalInputBindings FromSettings(string mapKey, IReadOnlyList<string> actions)
    {
        ArgumentNullException.ThrowIfNull(mapKey);
        ArgumentNullException.ThrowIfNull(actions);
        if (actions.Count != SettingsDocument.ActionOrder.Length)
            throw new ArgumentException("快捷键必须包含现有产品顺序的十个动作。", nameof(actions));
        HotkeyBindings.ValidateUnique(mapKey, actions);
        GlobalInputBinding Convert(string action, string text)
        {
            var chord = HotkeyBindings.Parse(text);
            return new(action, chord.Name, chord.VirtualKey, (GlobalInputModifiers)chord.Held);
        }
        return new(new[] { Convert("map", mapKey) }
            .Concat(actions.Select((text, index) => Convert(SettingsDocument.ActionOrder[index], text)))
            .Append(new("escape", "ESCAPE", 0x1b, GlobalInputModifiers.None)));
    }
}

public sealed record GlobalInputSnapshot(long Generation, IReadOnlyList<int> Held);

/// <param name="CreatedAtSeconds">Monotonic source time, before dispatch to subscribers.</param>
public sealed record GlobalInputKeyEvent(int VirtualKey, int PhysicalVirtualKey, bool IsDown,
    IReadOnlyList<int> Held, double CreatedAtSeconds, long ForegroundWindow, long Generation,
    bool ModifierTap, bool IsRepeat, uint NativeFlags = 0)
{
    public bool PassedToSystem => true;
    public bool Consumed => false;
}

/// <summary>Physical screen pixels from MSG.pt; never device-relative mouse counts.</summary>
public sealed record GlobalInputPointerEvent(string Phase, int X, int Y, double CreatedAtSeconds,
    long ForegroundWindow, long TargetWindow, long Generation, long PointerGeneration, string? Reason = null,
    string? ContextId = null, long Sequence = 0, long GestureId = 0,
    int DeltaX = 0, int DeltaY = 0, int TotalDeltaX = 0, int TotalDeltaY = 0);

/// <summary>Identity of a cached immutable runtime context; no business callback runs on the native thread.</summary>
public sealed record GlobalInputPointerTarget(long Window, string ContextId, double ObservationCreatedAtSeconds);

/// <summary>Read-only dispatch-time window facts and explicitly granted listening intent.</summary>
public sealed record GlobalInputScope(long GameWindow = 0, bool GameWindowExists = false,
    long CurrentForegroundWindow = 0, bool ForegroundIsAssistant = false,
    bool GameEnabled = false, bool ManagementEnabled = false,
    bool TextEntryActive = false, bool EditingBindings = false, double GameEventsAfterSeconds = 0);

public sealed record GlobalInputDispatch(IReadOnlyList<string> Actions, string? PickerKey)
{
    public static GlobalInputDispatch Empty { get; } = new(Array.Empty<string>(), null);
}

public enum GlobalInputNotificationKind { Started, Key, Pointer, Warning, Failed, Stopped }

/// <summary>
/// Stopped always carries an empty Snapshot: clear subscriber held/tap state even if a release
/// was lost. This is a logical source reset, not a synthetic physical key release.
/// </summary>
public sealed record GlobalInputNotification(GlobalInputNotificationKind Kind, long Generation,
    GlobalInputSnapshot? Snapshot = null, GlobalInputKeyEvent? Key = null,
    GlobalInputPointerEvent? Pointer = null, string? Code = null, string? Message = null);

/// <summary>Raw mouse packet decoding shared by the native receiver and offline sequence tests.</summary>
public static class GlobalInputMouse
{
    public static IReadOnlyList<(int VirtualKey, bool IsDown)> ButtonTransitions(ushort flags)
    {
        var result = new List<(int, bool)>();
        foreach (var (bit, key, down) in new (int, int, bool)[] {
            (4, 2, true), (8, 2, false), (16, 4, true), (32, 4, false),
            (64, 5, true), (128, 5, false), (256, 6, true), (512, 6, false) })
            if ((flags & bit) != 0) result.Add((key, down));
        return result;
    }

    public static IReadOnlyList<string> PointerPhases(ushort buttons, ushort flags, int lastX, int lastY)
    {
        var result = new List<string>();
        if ((buttons & 1) != 0) result.Add("down");
        else if (lastX != 0 || lastY != 0 || (flags & 1) != 0) result.Add("move");
        if ((buttons & 2) != 0) result.Add("up");
        return result;
    }
}
