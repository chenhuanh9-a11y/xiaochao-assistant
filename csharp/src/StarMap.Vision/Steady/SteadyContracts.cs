using System.Collections.ObjectModel;
using System.Text.Json.Nodes;
using StarMap.Core.Resources;

namespace StarMap.Vision.Steady;

/// <summary>A proposal only. A rejected solve contains neither a pose nor coverage to commit.</summary>
public sealed record SteadySolveResult(bool Accepted, string Reason, double[][]? MatrixSourceToClient,
    double? Confidence, JsonObject Diagnostics, SteadyCoverageState? ProposedCoverage);

public sealed record SteadyCoverageEntry(int ReachedPercent, bool RefreshPending);

/// <summary>
/// Immutable, caller-owned floor coverage. Exact reference identity is retained strongly;
/// a new resource object cannot inherit coverage even when its metadata happens to match.
/// </summary>
public sealed class SteadyCoverageState
{
    internal SharedStructureData Reference { get; }
    internal string BindingIdentity { get; }
    private readonly JsonObject binding;
    public IReadOnlyDictionary<string, SteadyCoverageEntry> Entries { get; }

    internal SteadyCoverageState(SharedStructureData reference, string bindingIdentity, JsonObject binding,
        IDictionary<string, SteadyCoverageEntry> entries)
    {
        Reference = reference; BindingIdentity = bindingIdentity;
        this.binding = (JsonObject)binding.DeepClone();
        Entries = new ReadOnlyDictionary<string, SteadyCoverageEntry>(new Dictionary<string, SteadyCoverageEntry>(entries, StringComparer.Ordinal));
    }

    /// <summary>Portable diagnostic fields only: process-local object identity is deliberately omitted.</summary>
    public JsonObject Snapshot()
    {
        var rows = new JsonObject();
        foreach (var (key, entry) in Entries)
            rows[key] = new JsonObject { ["reachedPercent"] = entry.ReachedPercent, ["refreshPending"] = entry.RefreshPending };
        return new JsonObject { ["binding"] = binding.DeepClone(), ["entries"] = rows };
    }
}
