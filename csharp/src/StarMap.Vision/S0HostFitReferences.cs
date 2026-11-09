using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Alignment;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using static StarMap.Vision.S1StructureObservations;

namespace StarMap.Vision;

public sealed partial class S0HostFit
{
    private sealed record Prepared(StructureGeometry Geometry, FastPrepared? Fast, string CanonicalPackId);

    private Prepared? Prepare(ResourceCatalogEntry target, out string reason)
    {
        reason = "shared-reference-unavailable";
        if (prepared.TryGetValue(target.StableKey, out var found)) return found;
        var loaded = resources.LoadSharedStructure(target);
        if (loaded.Value is null) return null;
        var geometry = StructureGeometry.Open(loaded.Value);
        try
        {
            FastPrepared? fast = null;
            if (target.StructureNpzPath is { } npz)
            {
                string path = Path.Combine(Path.GetDirectoryName(Path.GetDirectoryName(npz))!, "fast-structure", Path.GetFileNameWithoutExtension(npz) + "-fast.npz");
                if (File.Exists(path))
                    try { fast = FastStructure.LoadPrepared(path, loaded.Value); }
                    catch (Exception error) when (ResourceFailure(error)) { fast = null; }
            }
            string relative;
            if (target.Mode == "nightmare") relative = "references/nightmare-canonical-coordinate-pack.json";
            else
            {
                using var manifest = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(catalog.ModeRoot, "difficult-native-manifest.json")));
                relative = manifest.RootElement.GetProperty("runtimeInterfaces").GetProperty("coordinatePack").GetString()!;
            }
            string packPath = Path.GetFullPath(Path.Combine(catalog.ModeRoot, relative));
            if (Path.IsPathRooted(relative) || !packPath.StartsWith(Path.GetFullPath(catalog.ModeRoot) + Path.DirectorySeparatorChar,
                OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal)) throw new InvalidDataException("canonical-coordinate-resource-outside-context");
            using var pack = JsonDocument.Parse(File.ReadAllBytes(packPath));
            var result = new Prepared(geometry, fast, new AuthorCoordinateAdapter(pack.RootElement).PackId);
            prepared.Add(target.StableKey, result); return result;
        }
        catch (Exception error) when (ResourceFailure(error) || error is JsonException)
        { geometry.Dispose(); reason = "canonical-coordinate-resource-unavailable"; return null; }
    }

    private static JsonObject CanonicalResidual(Mat frame, Prepared reference, double[][] matrix, Observation? visible = null)
    {
        JsonObject residual;
        if (visible is not null) residual = FrameResidual.Evaluate(frame, visible, reference.Geometry.Validation, matrix);
        else
        {
            var bounds = EntryObservation.VisibleBounds(frame);
            if (bounds is null) residual = new() { ["verified"] = false, ["reason"] = "no-visible-map-component" };
            else
            {
                using var observation = new Observation(frame, new Mat(), bounds.Value, new());
                residual = FrameResidual.Evaluate(frame, observation, reference.Geometry.Validation, matrix);
            }
        }
        var evidence = AlignmentObservationEvidence.CanonicalResidualEvidence(new Dictionary<string, object?> { ["verified"] = Flag(residual, "verified") },
            "game-rendered-shared-structure", reference.CanonicalPackId);
        foreach (var pair in evidence) residual[pair.Key] = JsonSerializer.SerializeToNode(pair.Value);
        return residual;
    }

    private static bool ResidualVerified(JsonNode? residual) => Flag(residual, "verified") &&
        (Flag(residual, "poseVerifiedAgainstCanonicalGeometry") && Flag(residual, "profileRenderDerivedFromCanonical") &&
            residual?["validationBasis"]?.ToString() == "shared-canonical-geometry-current-frame" && !string.IsNullOrEmpty(residual["coordinatePackId"]?.ToString()) ||
         Flag(residual, "poseVerifiedAgainstRenderedProfile") && residual?["validationBasis"]?.ToString() == "actual-rendered-profile-structural-mask");

    private static JsonObject? MarkerPeerRejection(Mat frame, ResourceCatalogEntry target, Prepared reference, double[][] matrix, Func<bool> obsolete)
    {
        // Original implicit S0 checks peers inside its existing default ROI. It
        // does not broaden to the explicit full-frame S1 scan domain.
        int left = (int)(frame.Cols * .16), top = (int)(frame.Rows * .22);
        var domain = new Rect(left, top, (int)(frame.Cols * .76) - left, (int)(frame.Rows * .80) - top);
        var islands = Islands(frame, domain);
        if (islands.Count > 32) return new() { ["success"] = false, ["reason"] = "manual-component-budget-unresolved", ["componentCount"] = islands.Count, ["componentBudget"] = 32 };
        Rect? selected = islands.FirstOrDefault()?.Bounds;
        foreach (var island in islands)
        {
            if (obsolete()) return Failure("structure-work-obsolete");
            if (island.Bounds == selected) continue;
            var clipped = Rect.Intersect(island.Bounds, domain); if (clipped.Width <= 0 || clipped.Height <= 0) continue;
            using var current = Geometry.FromComponent(frame, clipped, island.Component.DeepClone().AsObject());
            if (current is null) continue;
            var lines = Geometry.Lines(frame, current);
            if (reference.Geometry.Unknown is not null)
            {
                using var unknown = Geometry.Warp(reference.Geometry.Unknown, matrix, frame.Size());
                var observed = Geometry.Bytes(Geometry.Observed(frame, current)); var excluded = Geometry.Bytes(unknown);
                for (int i = 0; i < observed.Length; i++) if (excluded[i] > 0) observed[i] = 0;
                using var mask = Geometry.FromBytes(frame.Cols, frame.Rows, observed);
                lines = Geometry.VisibleWalls(current.Query, mask, Geometry.Nodes(current));
            }
            bool independent = true;
            for (int axis = 0; axis < 2; axis++)
            {
                var planes = new List<double>();
                foreach (var line in lines.Where(line => line.Axis == axis)) if (!planes.Any(old => Math.Abs(old - line.Normal) <= 3)) planes.Add(line.Normal);
                if (planes.Count < 2 || planes.Max() - planes.Min() < 24) { independent = false; break; }
            }
            if (!independent) continue;
            using var projected = Geometry.Warp(reference.Geometry.Clean, matrix, frame.Size());
            using var unknownMask = reference.Geometry.Unknown is null ? null : Geometry.Warp(reference.Geometry.Unknown, matrix, frame.Size());
            bool consistent = Flag(RegionalGeometry.Evaluate(frame, current, projected, unknownMask), "regionalEdgeVerified") &&
                ResidualVerified(CanonicalResidual(frame, reference, matrix, current));
            if (obsolete()) return Failure("structure-work-obsolete");
            if (!consistent) return new() { ["success"] = false, ["reason"] = "manual-components-inconsistent", ["conflictingBounds"] = Bounds(current.Bounds) };
        }
        return obsolete() ? Failure("structure-work-obsolete") : null;
    }

    public void Dispose()
    {
        if (disposed) return; disposed = true;
        foreach (var reference in prepared.Values) reference.Geometry.Dispose(); prepared.Clear();
    }
}
