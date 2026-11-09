using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Alignment;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using StarMap.Core.Session;
using StarMap.Vision.Steady;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private sealed record SteadyFloorKey(string Map, string Floor, string Resource, string Window,
        CaptureEvidenceKind Evidence, int Width, int Height, int Left, int Top, uint Dpi);
    private sealed record CommittedSteadyFloor(object Owner, SharedStructureData Reference, double Scale,
        SteadyCoverageState? Coverage);
    private sealed record PreparedSteadyFit(JsonObject Fit, PreparedRuntimeDisplay Display);
    private readonly Dictionary<SteadyFloorKey, CommittedSteadyFloor> _steadyFloors = new();
    private string? _rememberedMaterial;

    private bool SteadyEnabled => _options.Idvb0059Enabled && Environment.GetEnvironmentVariable("IDENTITYV_IDVB0059_STEADY") != "0";

    private static SteadyFloorKey? SteadyKey(CapturedClientFrame frame, string map, string floor, string resource)
    {
        if (frame.Context is not { } context) return null;
        var g = context.Geometry;
        return new(map, floor, resource, frame.Stamp.WindowIdentity, context.EvidenceKind,
            frame.Width, frame.Height, g.Left, g.Top, g.Dpi);
    }

    private void ResetSteady()
    { _steadyFloors.Clear(); _rememberedMaterial = null; }

    /// <summary>Unacknowledged current material only; it never proposes identity or a new transform.</summary>
    public RuntimeRenderPacket? ReadMaterialToRemember()
    {
        lock (_sync) return SteadyEnabled && RenderCurrent() && _frame?.Context is not null
            && _render!.MaterialToken != _rememberedMaterial ? _render : null;
    }

    // Uses the existing private Session work lane. This method cannot publish
    // recognition state, overwrite a pose or grant display/live permission.
    public async ValueTask RememberRenderMaterialAsync(string renderToken, RuntimeContext lifetime, CancellationToken cancellationToken)
    {
        await _worker.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            CapturedClientFrame frame; CanonicalGrant canonical; BoundResources bound; SteadyFloorKey key;
            object owner; long epoch; SteadyCoverageState? previous;
            lock (_sync)
            {
                if (!SteadyEnabled || !RenderCurrent() || _render!.MaterialToken != renderToken
                    || Context() != lifetime || _rememberedMaterial == renderToken) return;
                frame = _frame!; canonical = _canonical!; bound = _resources!; owner = _roundFitOwner; epoch = _epoch;
                var scoped = SteadyKey(frame, _mapId!, _floor!, bound.Id);
                var matrix = canonical.NativeToClient;
                if (scoped is null || !matrix.IsFinite || Math.Abs(matrix.M01) > 1e-8 || Math.Abs(matrix.M10) > 1e-8
                    || Math.Abs(matrix.M11 - matrix.M00) > 1e-8) return;
                key = scoped;
                previous = _steadyFloors.TryGetValue(key, out var row) && ReferenceEquals(row.Owner, owner)
                    && ReferenceEquals(row.Reference, canonical.NativeReference) ? row.Coverage : null;
            }
            cancellationToken.ThrowIfCancellationRequested();
            SteadyCoverageState? coverage = canonical.ProposedCoverage;
            if (coverage is null)
            {
                using var pixels = ToBgr(frame);
                try { coverage = Idvb0059Steady.ObserveCoverage(pixels, canonical.NativeReference, canonical.NativeToClient.ToArray(), previous); }
                catch (Exception error) when (error is ArgumentException or InvalidDataException or OpenCVException)
                { coverage = previous; }
            }
            lock (_sync)
            {
                if (_closed || cancellationToken.IsCancellationRequested || epoch != _epoch
                    || !ReferenceEquals(bound, _resources) || !ReferenceEquals(owner, _roundFitOwner)
                    || !ReferenceEquals(canonical, _canonical) || Context() != lifetime || !RenderCurrent()
                    || _render!.MaterialToken != renderToken) return;
                _steadyFloors[key] = new(owner, canonical.NativeReference, canonical.NativeToClient.M00, coverage);
                _rememberedMaterial = renderToken;
            }
        }
        finally { _worker.Release(); }
    }

    private PreparedSteadyFit? TrySteady(long epoch, CancellationToken token, CapturedClientFrame captured,
        BoundResources bound, ResourceCatalogEntry entry, SharedStructureData reference, Mat frame,
        string preferred, string? expectedPack, out JsonObject diagnostic)
    {
        CommittedSteadyFloor? row;
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            var key = SteadyKey(captured, entry.MapId, entry.Floor, bound.Id);
            row = key is not null ? _steadyFloors.GetValueOrDefault(key) : null;
            if (!SteadyEnabled)
            { diagnostic = new() { ["attempted"] = false, ["reason"] = "steady-disabled" }; return null; }
            if (row is null || !ReferenceEquals(row.Owner, _roundFitOwner) || !ReferenceEquals(row.Reference, reference))
            { diagnostic = new() { ["attempted"] = false, ["reason"] = "trusted-target-floor-scale-unavailable" }; return null; }
        }
        var started = Stopwatch.GetTimestamp();
        var solved = Idvb0059Steady.Solve(frame, reference, row.Scale, row.Coverage,
            cancelled: () => Obsolete(epoch, token, captured, bound));
        diagnostic = new() { ["attempted"] = true, ["accepted"] = solved.Accepted, ["reason"] = solved.Reason,
            ["trustedScale"] = row.Scale, ["diagnostics"] = solved.Diagnostics.DeepClone() };
        CheckCurrent(epoch, token, captured, bound);
        if (!solved.Accepted || solved.MatrixSourceToClient is not { Length: 2 } matrix) return null;
        double[][] native = [matrix[0].ToArray(), matrix[1].ToArray(), [0, 0, 1]];
        var display = PrepareDisplay(bound, entry, native, reference, preferred, token) with { ProposedCoverage = solved.ProposedCoverage };
        var basis = display.CanonicalBasis;
        // This is the actual validated static reference/canonical bridge, not an
        // author-image match or a fabricated validation-spec acknowledgement.
        if (basis?.Accepted != true || basis.CanonicalToClient is null || string.IsNullOrEmpty(basis.CanonicalPackId)
            || expectedPack is not null && basis.CanonicalPackId != expectedPack)
        {
            diagnostic["accepted"] = false; diagnostic["reason"] = "steady-canonical-spec-unavailable"; return null;
        }
        var residual = AlignmentObservationEvidence.CanonicalResidualEvidence(
            new Dictionary<string, object?> { ["verified"] = true }, "game-rendered-shared-structure", basis.CanonicalPackId);
        var evidence = JsonSerializer.SerializeToNode(residual)!.AsObject();
        evidence["verificationMethod"] = "idvb0059-vpsg3-current-frame";
        evidence["idvb0059"] = solved.Diagnostics.DeepClone();
        var fit = new JsonObject { ["success"] = true, ["reason"] = "idvb0059-steady",
            ["matrixSourceToClient"] = JsonSerializer.SerializeToNode(native), ["coordinateSpace"] = "shared-structure-to-client",
            ["routeProfileId"] = "shared-structure", ["selectedRouteCurrentFrameResidual"] = evidence,
            ["idvb0059Diagnostics"] = solved.Diagnostics.DeepClone(), ["idvb0059Coverage"] = solved.ProposedCoverage?.Snapshot(),
            ["totalMs"] = Stopwatch.GetElapsedTime(started).TotalMilliseconds };
        return new(fit, display);
    }
}
