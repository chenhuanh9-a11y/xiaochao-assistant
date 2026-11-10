namespace StarMap.Core.Selection;

public sealed record StructureIndexCheck(string MapId, bool ExplicitContradiction);

/// <summary>One round's structural identity evidence. Floors share the remaining
/// IDs, never their scale or pose. Only the round owner can replace this pool.</summary>
public sealed class StructureCandidatePool
{
    private readonly string[] all;
    private string[] remaining;
    public string RoundId { get; }
    public string ResourceId { get; }
    public string? LockedMapId { get; private set; }
    public long Revision { get; private set; }
    public int ObservationCount { get; private set; }
    public IReadOnlyList<string> Remaining => Array.AsReadOnly(remaining);
    public IReadOnlyList<string> Eliminated => Array.AsReadOnly(all.Except(remaining).ToArray());

    public StructureCandidatePool(string roundId, string resourceId, IEnumerable<string> mapIds)
    {
        all = mapIds.ToArray(); remaining = all.ToArray(); RoundId = roundId; ResourceId = resourceId;
        if (string.IsNullOrEmpty(roundId) || string.IsNullOrEmpty(resourceId) || all.Length == 0
            || all.Any(string.IsNullOrEmpty) || all.Distinct(StringComparer.Ordinal).Count() != all.Length)
            throw new ArgumentException("structure-pool-scope-invalid");
    }

    public string Observe(bool coordinateVerified, IReadOnlyList<string> evaluatedMapIds,
        IReadOnlyList<string> contradictedMapIds, IReadOnlyList<StructureIndexCheck> checks)
    {
        if (LockedMapId is not null) return "structure-pool-identity-retained";
        var evaluated = evaluatedMapIds.ToHashSet(StringComparer.Ordinal);
        if (evaluated.Count != evaluatedMapIds.Count || !evaluated.SetEquals(remaining)
            || checks.Count != remaining.Length || checks.Select(row => row.MapId).Distinct(StringComparer.Ordinal).Count() != checks.Count
            || !checks.Select(row => row.MapId).ToHashSet(StringComparer.Ordinal).SetEquals(remaining))
            return "structure-coordinate-unknown";
        if (checks.All(row => row.ExplicitContradiction)) return "structure-coordinate-conflict-retained";
        var bad = contradictedMapIds.ToHashSet(StringComparer.Ordinal);
        if (bad.Count != contradictedMapIds.Count || !bad.IsSubsetOf(evaluated)
            || !bad.SetEquals(checks.Where(row => row.ExplicitContradiction).Select(row => row.MapId)))
            return "structure-coordinate-unknown";
        // A complete contradiction is a diagnostic conflict, never permission
        // to restore eliminated maps or empty a previously useful round pool.
        if (!coordinateVerified) return "structure-coordinate-unknown";
        ObservationCount++;
        if (bad.Count == 0) return "structure-pool-unchanged";
        remaining = remaining.Where(id => !bad.Contains(id)).ToArray(); Revision++;
        return "structure-pool-narrowed";
    }

    /// <summary>Identity selection follows a real current material commit;
    /// this does not grant any future frame a pose or display authorization.</summary>
    public bool AcceptCurrentMaterial(string mapId)
    {
        if (remaining.Length != 1 || remaining[0] != mapId) return false;
        LockedMapId = mapId; return true;
    }
}
