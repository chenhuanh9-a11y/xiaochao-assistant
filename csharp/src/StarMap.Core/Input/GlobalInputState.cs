namespace StarMap.Core.Input;

/// <summary>
/// Native-edge state from client_native_keys.py. Single owner: the native message thread.
/// The optional level reader only reconciles OTHER keys on a new bare-modifier press;
/// it is not a polling input backend. No native API or WPF dependency exists here.
/// </summary>
public sealed class GlobalInputState
{
    public const double ModifierTapMaximumSeconds = .5;
    public static IReadOnlyList<int> ModifierKeys { get; } = Array.AsReadOnly(new[] {
        0x10, 0x11, 0x12, 0x5b, 0x5c, 0xa0, 0xa1, 0xa2, 0xa3, 0xa4, 0xa5 });
    readonly HashSet<int> keys = [];
    readonly HashSet<int> held = [];
    readonly HashSet<int> tapPressed = [];
    readonly HashSet<int> bareModifiers = [];
    readonly Dictionary<int, Tap> taps = [];
    sealed class Tap(bool used, long foreground, double started)
    {
        public bool Used = used;
        public readonly long Foreground = foreground;
        public readonly double Started = started;
    }

    public long Generation { get; private set; }
    public bool MouseObservationAvailable { get; set; }
    public bool WantsMouseButtons => keys.Overlaps([2, 4, 5, 6]);
    public bool WantsModifierActivity => bareModifiers.Count != 0;
    public GlobalInputSnapshot Snapshot => new(Generation, Array.AsReadOnly(held.Order().ToArray()));

    public GlobalInputSnapshot Reset(GlobalInputBindings bindings, long generation, IEnumerable<int>? initiallyHeld = null)
    {
        keys.Clear(); keys.UnionWith(bindings.PrimaryVirtualKeys); keys.UnionWith(ModifierKeys);
        bareModifiers.Clear(); bareModifiers.UnionWith(bindings.PrimaryVirtualKeys.Where(IsBareModifier));
        held.Clear(); tapPressed.Clear(); taps.Clear();
        // Seed physical sides only. A generic + physical duplicate would stick after key-up.
        var initial = (initiallyHeld ?? []).Where(key => key is > 0 and < 256 && !IsBareModifier(key)).ToArray();
        held.UnionWith(initial.Where(keys.Contains));
        if (WantsModifierActivity) tapPressed.UnionWith(initial);
        Generation = generation;
        return Snapshot;
    }

    public void CancelModifierTaps() => taps.Clear();

    public void ObserveMouseActivity(ushort flags)
    {
        if (!WantsModifierActivity || flags == 0) return;
        foreach (var tap in taps.Values) tap.Used = true;
        foreach (var (down, up, key) in new[] { (1, 2, 1), (4, 8, 2), (16, 32, 4), (64, 128, 5), (256, 512, 6) })
        {
            if ((flags & down) != 0) tapPressed.Add(key);
            if ((flags & up) != 0) tapPressed.Remove(key);
        }
    }

    public GlobalInputKeyEvent? Transition(int vk, int scan, uint flags, bool down, long foreground,
        double createdAtSeconds, Func<int, bool>? isCurrentlyDown = null)
    {
        var physical = PhysicalKey(vk, scan, flags);
        var previous = held.Contains(physical);
        Tap? completedTap = null;
        if (WantsModifierActivity && physical is > 0 and < 256)
        {
            var wasTapPressed = tapPressed.Contains(physical);
            if (down)
            {
                if (!wasTapPressed)
                {
                    if (bareModifiers.Contains(GenericModifier(physical)) && isCurrentlyDown is not null)
                    {
                        try
                        {
                            // Materialize before committing: an unknown level remains a blocker.
                            var released = tapPressed.Where(other => other != physical && !isCurrentlyDown(other)).ToArray();
                            tapPressed.ExceptWith(released); held.ExceptWith(released);
                            foreach (var other in released) taps.Remove(other);
                        }
                        catch { taps.Clear(); }
                    }
                    foreach (var tap in taps.Values) tap.Used = true;
                    if (bareModifiers.Contains(GenericModifier(physical)))
                        taps[physical] = new(tapPressed.Count != 0, foreground, createdAtSeconds);
                }
                tapPressed.Add(physical);
            }
            else
            {
                tapPressed.Remove(physical);
                taps.Remove(physical, out completedTap);
            }
        }
        else if (WantsModifierActivity)
            foreach (var tap in taps.Values) tap.Used = true;

        if (!keys.Contains(vk)) return null;
        if (down) held.Add(physical); else held.Remove(physical);
        var modifierTap = MouseObservationAvailable && completedTap is not null && !completedTap.Used
            && completedTap.Started <= createdAtSeconds
            && createdAtSeconds <= completedTap.Started + ModifierTapMaximumSeconds
            && tapPressed.Count == 0 && completedTap.Foreground == foreground;
        return new(vk, physical, down, Snapshot.Held, createdAtSeconds, foreground, Generation,
            modifierTap, down && previous, flags);
    }

    public static bool IsBareModifier(int vk) => vk is 0x10 or 0x11 or 0x12;
    public static int GenericModifier(int vk) => vk switch {
        0xa0 or 0xa1 => 0x10, 0xa2 or 0xa3 => 0x11, 0xa4 or 0xa5 => 0x12, _ => vk };
    public static int PhysicalKey(int vk, int scan, uint flags) => vk switch {
        0x10 => scan == 0x36 ? 0xa1 : 0xa0,
        0x11 => (flags & 1) != 0 ? 0xa3 : 0xa2,
        0x12 => (flags & 1) != 0 ? 0xa5 : 0xa4, _ => vk };
}
