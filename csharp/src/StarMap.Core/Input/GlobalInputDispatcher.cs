namespace StarMap.Core.Input;

/// <summary>
/// Pure subscriber-side shortcut policy from NativeShortcutBackend._key_event.
/// Returns observations for an explicit runtime subscriber; never executes game actions.
/// Feed every edge, including rejected/stale releases, in source order on one owner thread.
/// </summary>
public sealed class GlobalInputDispatcher
{
    public const double MaximumEventAgeSeconds = 2;
    static readonly HashSet<string> ManagementActions = ["toggle-panel", "toggle-sheet-visibility", "start-round", "toggle-round", "end-round"];
    GlobalInputBindings bindings;
    readonly HashSet<int> down = [];
    readonly Dictionary<int, (long Foreground, double Created)> modifierPresses = [];
    HashSet<string> pickerKeys = [];
    bool? textEntry, editing;
    public long Generation { get; private set; }

    public GlobalInputDispatcher(GlobalInputBindings bindings) => this.bindings = bindings;

    public void Reset(GlobalInputBindings currentBindings, GlobalInputSnapshot snapshot)
    {
        bindings = currentBindings; Generation = snapshot.Generation;
        down.Clear(); down.UnionWith(snapshot.Held); modifierPresses.Clear();
        textEntry = editing = null;
    }

    public void SetPickerKeys(IEnumerable<string> visibleKeys)
        => pickerKeys = visibleKeys.Where(GlobalInputBindings.PickerKeys.ContainsKey).ToHashSet(StringComparer.Ordinal);

    public void CancelModifierTaps() => modifierPresses.Clear();

    public GlobalInputDispatch Dispatch(GlobalInputKeyEvent input, GlobalInputScope scope, double nowSeconds)
    {
        if (input.Generation != Generation) return GlobalInputDispatch.Empty;
        if (textEntry != scope.TextEntryActive || editing != scope.EditingBindings) modifierPresses.Clear();
        textEntry = scope.TextEntryActive; editing = scope.EditingBindings;
        var vk = GlobalInputState.GenericModifier(input.VirtualKey);
        var physical = input.PhysicalVirtualKey;
        var previous = down.Contains(physical);
        if (input.IsDown) down.Add(physical); else down.Remove(physical);
        (long Foreground, double Created) press = default;
        var hasPress = !input.IsDown && modifierPresses.Remove(physical, out press);
        var modifierRelease = !input.IsDown && GlobalInputState.IsBareModifier(vk) && input.ModifierTap && hasPress;
        if ((!input.IsDown && !modifierRelease) || (input.IsDown && previous) || scope.TextEntryActive || scope.EditingBindings)
            return GlobalInputDispatch.Empty;
        var age = nowSeconds - input.CreatedAtSeconds;
        if (!double.IsFinite(age) || age < 0 || age > MaximumEventAgeSeconds) return GlobalInputDispatch.Empty;

        var game = scope.GameWindow != 0 && scope.GameWindowExists && input.ForegroundWindow == scope.GameWindow
            && scope.CurrentForegroundWindow == input.ForegroundWindow;
        var assistant = input.ForegroundWindow != 0 && scope.CurrentForegroundWindow == input.ForegroundWindow && scope.ForegroundIsAssistant;
        var held = input.Held.ToHashSet();
        bool Held(int key) => key switch {
            0x10 => held.Overlaps([0x10, 0xa0, 0xa1]), 0x11 => held.Overlaps([0x11, 0xa2, 0xa3]),
            0x12 => held.Overlaps([0x12, 0xa4, 0xa5]), _ => held.Contains(key) };
        bool Match(GlobalInputBinding chord)
        {
            if (chord.VirtualKey == 0 || Held(0x5b) || Held(0x5c)) return false;
            return (chord.VirtualKey == 0x11 || Held(0x11) == chord.Modifiers.HasFlag(GlobalInputModifiers.Ctrl))
                && (chord.VirtualKey == 0x12 || Held(0x12) == chord.Modifiers.HasFlag(GlobalInputModifiers.Alt))
                && (chord.VirtualKey == 0x10 || Held(0x10) == chord.Modifiers.HasFlag(GlobalInputModifiers.Shift));
        }
        string? picker = input.IsDown ? pickerKeys.FirstOrDefault(name => GlobalInputBindings.PickerKeys[name] == vk) : null;
        if (picker is not null && (!(game || assistant) || GlobalInputState.ModifierKeys.Any(Held))) picker = null;
        var actions = new List<string>();
        foreach (var chord in bindings.Items)
        {
            if (chord.VirtualKey != vk || !Match(chord)) continue;
            var management = ManagementActions.Contains(chord.Action);
            var allowed = management ? scope.ManagementEnabled : scope.GameEnabled && game
                && (scope.GameEventsAfterSeconds == 0 || input.CreatedAtSeconds >= scope.GameEventsAfterSeconds);
            // Preserve the existing app-local keyboard owner; native-only VKs have one global owner.
            if (chord.Action == "toggle-panel" && assistant && HasApplicationLocalPanelOwner(vk)) allowed = false;
            if (GlobalInputState.IsBareModifier(vk))
            {
                if (input.IsDown)
                {
                    if (allowed) modifierPresses[physical] = (input.ForegroundWindow, input.CreatedAtSeconds);
                    continue;
                }
                allowed &= press.Foreground == input.ForegroundWindow && (management || scope.GameEventsAfterSeconds == 0
                    || press.Created >= scope.GameEventsAfterSeconds);
            }
            if (picker is not null && chord.Action != "escape" && chord.Modifiers == GlobalInputModifiers.None
                && pickerKeys.Any(name => GlobalInputBindings.PickerKeys[name] == chord.VirtualKey)) allowed = false;
            if (allowed) actions.Add(chord.Action);
        }
        return new(Array.AsReadOnly(actions.ToArray()), picker);
    }

    public static bool HasApplicationLocalPanelOwner(int virtualKey) => !IsNativeOnly(virtualKey);

    static bool IsNativeOnly(int vk) => vk is 2 or 4 or 5 or 6 or 0x10 or 0x11 or 0x12
        or >= 0x30 and <= 0x39 or >= 0x41 and <= 0x5a or >= 0x60 and <= 0x6f
        or 0xba or 0xbb or 0xbc or 0xbd or 0xbe or 0xbf or 0xc0 or 0xdb or 0xdc or 0xdd or 0xde or 0xe2;
}
