using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Alignment;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private sealed record FloorFitPose(SharedStructureData Reference, DisplayAffine NativeToClient);
    // Scope/cache metadata only. All live geometry belongs to this existing Core model.
    private sealed record GeometryGroup(RoundLayerGroup Model, BoundResources Resources, string Round,
        string Window, ClientFrameContext Capture, IReadOnlyDictionary<string, FloorFitPose> FloorPoses);
    private GeometryGroup? _geometryGroup;

    private bool GeometryGroupCurrent(GeometryGroup? group, CapturedClientFrame frame, BoundResources bound, string map) =>
        group is not null && frame.Context is not null && ReferenceEquals(group.Resources, bound)
        && group.Round == _roundId && group.Model.MapId == map && group.Window == frame.Stamp.WindowIdentity
        && group.Capture.EvidenceKind == frame.Context.EvidenceKind && group.Capture.Geometry.Equals(frame.Context.Geometry);

    private S0HostGroupState? GroupForFit(long epoch, object owner, CapturedClientFrame frame,
        BoundResources bound, ResourceCatalogEntry entry)
    {
        GeometryGroup? previous;
        lock (_sync) previous = GeometryGroupCurrent(_geometryGroup, frame, bound, entry.MapId) ? _geometryGroup : null;
        if (previous is null || !previous.Model.VerifiedFloors.Contains(entry.Floor)
            || !previous.FloorPoses.TryGetValue(entry.Floor,out var stored)) return null;
        var reference=bound.LoadReference(entry).Value;
        if(reference is null || !ReferenceEquals(stored.Reference,reference))return null;
        var model = previous.Model;
        var verified = model.VerifiedFloors.ToHashSet(StringComparer.Ordinal);
        var relations = model.Relations.Keys.ToHashSet(StringComparer.Ordinal);
        // RootPose/relations belong to the shared display scene. Another
        // floor may update that root; solver hints use this floor's own commit.
        var native=stored.NativeToClient.ToArray();
        var geometry=new EstablishedGroupContext(entry.MapId,entry.Floor,"shared-structure",native,verified,
            model.CanonicalGeometryVerifiedFloors.ToHashSet(StringComparer.Ordinal),relations);
        var committed=new S0HostPosePrior(owner,epoch,entry,native);
        return new(owner, epoch, entry.Mode, entry.Season, entry.MapId, model.RouteProfileId,
            verified, relations, committed, geometry);
    }

    private GeometryGroup? StageGeometryGroup(PreparedRuntimeDisplay prepared, IReadOnlyDictionary<string, object?> residual,
        CapturedClientFrame frame, string map, string floor, DisplayAffine canonical)
    {
        if (frame.Context is null || prepared.CanonicalBasis?.ReferenceToCanonical is null) return null;
        var bound = _resources!;
        var previous = GeometryGroupCurrent(_geometryGroup, frame, bound, map) ? _geometryGroup : null;
        var profile = prepared.Material.Layer is not null ? prepared.Selection.EffectiveProfileId
            : previous?.Model.RouteProfileId ?? "shared-structure";
        RoundLayerGroup candidate;
        if (previous is null || !previous.Model.Relations.ContainsKey(floor))
        {
            candidate = new(map, profile, floor, new(canonical.ToArray()), cycleId: new(_mapCycle), source: "initial-structure-v2",
                poseClientSize: [frame.Width, frame.Height], poseDisplaySignature: DisplaySignature(frame), floors: bound.Passports.Floors);
            PrimeRelations(candidate, bound.Coordinates);
        }
        else
        {
            candidate = previous.Model.Copy();
            if (candidate.AdoptFloorCanonicalPose(floor, new(canonical.ToArray()), "initial-structure-v2", new(_mapCycle))["success"] is not true)
                return null;
        }
        bool canonicalVerified = residual.GetValueOrDefault("poseVerifiedAgainstCanonicalGeometry") is true;
        bool sourceVerified = residual.GetValueOrDefault("poseVerifiedAgainstSourceStructure") is true;
        candidate.MarkGeometryVerified(floor, canonicalVerified, sourceVerified);
        if (prepared.Material.Layer is { } material)
            candidate.MarkDisplayed(floor, GroupPixels(material), false,
                renderProfileVerified: residual.GetValueOrDefault("poseVerifiedAgainstRenderedProfile") is true,
                canonicalGeometryVerified: canonicalVerified, sourceStructureVerified: sourceVerified);
        var poses = previous?.FloorPoses.ToDictionary(pair => pair.Key, pair => pair.Value, StringComparer.Ordinal)
            ?? new Dictionary<string, FloorFitPose>(StringComparer.Ordinal);
        if (previous is null || !previous.Model.Relations.ContainsKey(floor)) poses.Clear();
        poses[floor] = new(prepared.NativeReference, prepared.NativeToClient);
        return new(candidate, bound, _roundId!, frame.Stamp.WindowIdentity, frame.Context, poses);
    }

    private static object?[] DisplaySignature(CapturedClientFrame frame) =>
        [new[] { frame.Width, frame.Height }, new[] { frame.Context!.Geometry.Left, frame.Context.Geometry.Top,
            frame.Context.Geometry.Left + frame.Width, frame.Context.Geometry.Top + frame.Height }, frame.Context.Geometry.Dpi];

    private static byte[,,] GroupPixels(PreparedDisplayLayer material)
    {
        var rgba = new byte[material.Height, material.Width, 4]; var bytes = material.CopyPixels();
        for (int y = 0; y < material.Height; y++)
            Buffer.BlockCopy(bytes, y * material.Stride, rgba, y * material.Width * 4, material.Width * 4);
        for (int y = 0; y < material.Height; y++) for (int x = 0; x < material.Width; x++)
            (rgba[y, x, 0], rgba[y, x, 2]) = (rgba[y, x, 2], rgba[y, x, 0]);
        return rgba;
    }

    private static void PrimeRelations(RoundLayerGroup group, AuthorCoordinateAdapter coordinates)
    {
        var graph = group.Floors.ToDictionary(floor => floor,
            _ => new List<(string Target, Matrix3 Matrix, IReadOnlyDictionary<string, object?> Metadata)>(), StringComparer.Ordinal);
        void AddEdge(string from, string to, Matrix3 matrix, IReadOnlyDictionary<string, object?> metadata)
        {
            // Original graph admission uses abs(numpy.linalg.det(linear)) >= 1e-8.
            // Preserve its pivoted LU/log-determinant rounding at that boundary.
            double a = matrix[0, 0], b = matrix[0, 1], c = matrix[1, 0], d = matrix[1, 1];
            if (Math.Abs(c) > Math.Abs(a)) { (a, c) = (c, a); (b, d) = (d, b); }
            double determinant = a == 0 ? 0 : Math.Exp(Math.Log(Math.Abs(a)) + Math.Log(Math.Abs(d - c / a * b)));
            if (determinant >= 1e-8) graph[from].Add((to, matrix, metadata));
        }
        foreach (var relation in group.Relations)
        {
            if (relation.Key == group.AnchorFloor) continue;
            var metadata = group.RelationMetadata.GetValueOrDefault(relation.Key) ?? new Dictionary<string, object?>();
            AddEdge(group.AnchorFloor, relation.Key, relation.Value, metadata);
            if (relation.Value.Inverse() is { } inverse) AddEdge(relation.Key, group.AnchorFloor, inverse, metadata);
        }
        foreach (var pair in new[] { (From: "1", To: "2"), (From: "2", To: "1") })
        {
            if (!graph.ContainsKey(pair.From) || !graph.ContainsKey(pair.To)) continue;
            var seed = coordinates.CrossFloorSearchSeed(group.MapId, pair.From, pair.To, Matrix3.Identity, group.RouteProfileId);
            if (seed?["targetToSourceMatrix"] is not { } raw || Matrix3.TryRead(JsonSerializer.SerializeToElement(raw)) is not { } matrix) continue;
            var metadata = seed.Where(pair => !pair.Key.EndsWith("ToClient", StringComparison.Ordinal))
                .ToDictionary(pair => pair.Key, pair => (object?)JsonSerializer.SerializeToElement(pair.Value), StringComparer.Ordinal);
            metadata["roundGroupSharedLinear"] = metadata["singlePoseOwner"] = true;
            AddEdge(pair.From, pair.To, matrix, metadata);
        }
        var matrices = new Dictionary<string, Matrix3>(StringComparer.Ordinal) { [group.AnchorFloor] = Matrix3.Identity };
        var queue = new Queue<string>(); queue.Enqueue(group.AnchorFloor);
        group.SetRelation(group.AnchorFloor, Matrix3.Identity, new Dictionary<string, object?> {
            ["fromFloor"] = group.AnchorFloor, ["toFloor"] = group.AnchorFloor, ["transferSource"] = "round-group-anchor", ["singlePoseOwner"] = true });
        while (queue.TryDequeue(out string? from)) foreach (var edge in graph[from])
        {
            if (matrices.ContainsKey(edge.Target) || Matrix3.Multiply(matrices[from], edge.Matrix) is not { } relation) continue;
            matrices[edge.Target] = relation;
            var metadata = new Dictionary<string, object?>(edge.Metadata, StringComparer.Ordinal) {
                ["fromFloor"] = group.AnchorFloor, ["viaFloor"] = from, ["toFloor"] = edge.Target, ["singlePoseOwner"] = true };
            group.SetRelation(edge.Target, relation, metadata); queue.Enqueue(edge.Target);
        }
    }

    private static void SeedGroupCore(AlignmentCoreV2 core, GeometryGroup? source)
    {
        if (source is null) return;
        var group = source.Model;
        if (core.PoseByMap.TryGetValue(group.MapId, out var existing))
        {
            var updated = existing;
            foreach (var relation in group.Relations) if (!updated.FloorRelations.ContainsKey(relation.Key)) updated = updated.WithFloorRelation(relation.Key, relation.Value);
            if (!ReferenceEquals(updated, existing)) core.SetInitialPose(updated, "round-group-relations");
        }
        else core.SetInitialPose(new(group.MapId, group.RouteProfileId, group.AnchorFloor, group.RootPose, group.Relations,
            group.Generation, true, "round-layer-group-v2-seed", group.Floors), "round-group-seed");
    }
}
