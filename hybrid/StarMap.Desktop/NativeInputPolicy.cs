using System.Diagnostics;

namespace StarMap.Desktop;

public sealed record KeyboardShortcut(string Action, string Name, int VirtualKey, int[] Modifiers);
public readonly record struct InputKeyEdge(int VirtualKey, int PhysicalVirtualKey, bool IsDown,
    bool WasDown, long Timestamp, nint ForegroundHwnd, long Generation);
public sealed record ShortcutIntent(string Action, long Sequence, bool OpenIntent, double AgeSeconds);

/// <summary>Ordered input intent only. Python owns round, capture and pose decisions.</summary>
public sealed class NativeInputPolicy(IReadOnlyList<KeyboardShortcut> bindings)
{
    private static readonly HashSet<string> Management = ["toggle-panel", "toggle-sheet-visibility", "start-round", "toggle-round", "end-round"];
    private readonly HashSet<int> held = [];
    private long generation;
    public long Sequence { get; private set; }
    public bool OpenIntent { get; private set; }

    public void BeginSource(long sourceGeneration, IEnumerable<int> initialHeld)
    { generation = sourceGeneration; held.Clear(); held.UnionWith(initialHeld); }
    public void ResetMap() => OpenIntent = false;
    public void Reconcile(long sequence, bool open)
    { if (sequence == Sequence) OpenIntent = open; }

    public ShortcutIntent? Accept(InputKeyEdge edge, long now, bool gameForeground, bool textEntry)
    {
        if (edge.Generation != generation) return null;
        if (edge.IsDown) held.Add(edge.PhysicalVirtualKey); else held.Remove(edge.PhysicalVirtualKey);
        if (!edge.IsDown || edge.WasDown || textEntry) return null;
        var age = (now - (double)edge.Timestamp) / Stopwatch.Frequency;
        if (!double.IsFinite(age) || age < 0 || age > 2) return null;
        if (held.Contains(0x5b) || held.Contains(0x5c)) return null;
        var key = Generic(edge.VirtualKey);
        foreach (var binding in bindings)
        {
            if (binding.VirtualKey != key || key is 0x10 or 0x11 or 0x12) continue;
            if (new[] { 0x10, 0x11, 0x12 }.Any(modifier => IsHeld(modifier) != binding.Modifiers.Contains(modifier))) continue;
            if (!Management.Contains(binding.Action) && !gameForeground) continue;
            if (binding.Action is "map" or "escape")
            {
                OpenIntent = binding.Action == "map" && !OpenIntent;
                Sequence = checked(Sequence + 1);
            }
            return new(binding.Action, Sequence, OpenIntent, age);
        }
        return null;
    }

    private bool IsHeld(int key) => held.Any(value => Generic(value) == key);
    private static int Generic(int key) => key switch
    { 0xa0 or 0xa1 => 0x10, 0xa2 or 0xa3 => 0x11, 0xa4 or 0xa5 => 0x12, _ => key };
}
