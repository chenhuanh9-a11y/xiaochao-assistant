using System.Diagnostics;

namespace StarMap.Core.Input;

public sealed record MapInputEdge(string Kind, long Sequence, bool OpenIntent, string Source, double CreatedAtSeconds);

/// <summary>Physical key edges have order even while capture is still preparing.
/// This is only intent; it never establishes a visual map, floor or pose.</summary>
public sealed class OrderedMapInput(Func<double>? clock = null)
{
    readonly Func<double> now = clock ?? (() => Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency);
    public long Sequence { get; private set; }
    public bool OpenIntent { get; private set; }

    public void Reset(bool openIntent = false) => OpenIntent = openIntent;

    public bool Reconcile(bool openIntent, long inputSequence)
    {
        if (inputSequence != Sequence) return false;
        OpenIntent = openIntent; return true;
    }

    public MapInputEdge Press(string source = "hotkey", double? createdAtSeconds = null)
    {
        OpenIntent = !OpenIntent;
        return Next("map-key", source, createdAtSeconds);
    }

    public MapInputEdge Close(string source = "escape", double? createdAtSeconds = null)
    {
        OpenIntent = false;
        return Next("map-close", source, createdAtSeconds);
    }

    MapInputEdge Next(string kind, string source, double? createdAt)
        => new(kind, checked(++Sequence), OpenIntent, source, createdAt ?? now());
}
