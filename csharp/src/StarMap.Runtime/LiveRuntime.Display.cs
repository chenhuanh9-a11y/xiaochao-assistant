using System.Text.Json;
using StarMap.Core.Alignment;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using StarMap.Vision;

namespace StarMap.Runtime;

/// <summary>Small serializable render status; pixel ownership stays outside snapshots.</summary>
public sealed record RuntimeDisplayState(bool Prepared, bool CanonicalVerified, bool LiveAuthorized,
    string Reason, string? PreferredProfile = null, string? EffectiveProfile = null, string? DisplayKind = null);

/// <summary>Immutable material plus the exact captured-client and core authorization lifetime.</summary>
public sealed class RuntimeRenderPacket
{
    internal RuntimeRenderPacket(PreparedDisplayLayer material, CapturedClientFrame frame, DisplayAuthorization authorization,
        long generation, long epoch)
    { Material = material; Stamp = frame.Stamp; Client = frame.Context; Authorization = authorization; Generation = generation; Epoch = epoch; }
    public PreparedDisplayLayer Material { get; }
    public CaptureStamp Stamp { get; }
    public ClientFrameContext? Client { get; }
    public long Generation { get; }
    internal long Epoch { get; }
    internal DisplayAuthorization Authorization { get; }
    public bool IsLive => Client?.EvidenceKind == CaptureEvidenceKind.LiveClient;
}

public sealed partial class LiveRuntime
{
    private AlignmentCoreV2? _alignment;
    private RuntimeRenderPacket? _render;
    private CanonicalGrant? _canonical;
    private long _frameGeneration;
    private RuntimeDisplayState _display = new(false, false, false, "pose-not-verified");

    // This read performs bounded lifetime checks only. Resource hashing/decoding
    // belongs to the worker, never the UI pulse or the overlay reader.
    public bool TryReadRender(out RuntimeRenderPacket? packet)
    {
        lock (_sync)
        {
            packet = RenderCurrent() ? _render : null;
            return packet is not null;
        }
    }

    private bool CanonicalCurrent() => !_closed && _mapOpen && _frame is not null && _canonical is { } grant
        && grant.Epoch == _epoch && grant.Stamp == _frame.Stamp && _pose is not null
        && _capture.IsCurrent(grant.Stamp) && _alignment?.ActiveObservation is { } observation
        && grant.Authorization.Current(observation.ObservationId, new(_roundId!), _mapId!, _floor!, _frameGeneration);

    private bool RenderCurrent() => CanonicalCurrent() && _render is { } render && render.Epoch == _epoch
        && render.Stamp == _canonical!.Stamp && ReferenceEquals(render.Authorization, _canonical.Authorization);

    private sealed record CanonicalGrant(CaptureStamp Stamp, long Epoch, DisplayAuthorization Authorization,
        string PackId, DisplayAffine Matrix, SharedStructureData Reference);

    private sealed record PreparedRuntimeDisplay(DisplaySelectionResult Selection, DisplayMaterialResult Material,
        DisplayBridgeResult? CanonicalBasis);

    private PreparedRuntimeDisplay PrepareDisplay(BoundResources bound, ResourceCatalogEntry entry, double[][] matrix,
        SharedStructureData fittedReference, string preferred, CancellationToken token)
    {
        if (!DisplayAffine.TryRead(JsonSerializer.SerializeToElement(matrix), out var referenceToClient))
            throw new InvalidDataException("display-fit-affine-unavailable");
        var validator = new OpenCvDisplayRasterValidator();
        var selection = new DisplayLayerSelector(validator).Select(bound.Catalog, entry, preferred, referenceToClient);
        var preparer = new DisplayMaterialPreparer();
        var material = preparer.Prepare(selection, fittedReference, token);
        // Missing custom/author pixels cannot erase a successful geometric fit.
        // Validate its exact fitted reference independently of display preference.
        var basis = material.Layer?.Bridge;
        if (basis is null)
        {
            var neutral = new DisplayBridgeResolver(validator).ResolveDisplayReference(bound.Catalog, entry,
                "shared-structure", referenceToClient, neutral: true);
            basis = preparer.Prepare(neutral, fittedReference, token).Layer?.Bridge;
        }
        return new(selection, material, basis);
    }

    private static IReadOnlyDictionary<string, object?> ReadResidualEvidence(System.Text.Json.Nodes.JsonNode residual)
    {
        using var json = JsonDocument.Parse(residual.ToJsonString());
        return json.RootElement.EnumerateObject().ToDictionary(p => p.Name, p => p.Value.ValueKind switch
        {
            JsonValueKind.True => (object?)true, JsonValueKind.False => false, JsonValueKind.String => p.Value.GetString(),
            JsonValueKind.Null => null, JsonValueKind.Number => p.Value.GetDouble(), _ => p.Value.Clone()
        }, StringComparer.Ordinal);
    }

    private void RefreshDisplayPreference(long epoch, CancellationToken token, string preferred)
    {
        BoundResources bound; CapturedClientFrame frame; NativeGeometryPose pose; CanonicalGrant previous;
        lock (_sync)
        {
            RequireCurrent(epoch, token);
            if (!CanonicalCurrent()) return;
            bound = _resources!; frame = _frame!; pose = _pose!; previous = _canonical!;
        }
        var entry = bound.Catalog.Find(bound.Passports.Mode, bound.Passports.Season,
            bound.Passports.MapProfiles[pose.MapId], pose.MapId, pose.FloorId);
        if (entry is null) return;
        var prepared = PrepareDisplay(bound, entry, pose.ToMatrix(), previous.Reference, preferred, token);
        lock (_sync)
        {
            RequireCurrent(epoch, token, frame, bound);
            if (!ReferenceEquals(_canonical, previous) || !CanonicalCurrent()) return;
            var layer = prepared.Material.Layer;
            // Preference changes replace pixels, never the canonical pose/token.
            if (prepared.CanonicalBasis is not { } basis || basis.CanonicalPackId != previous.PackId
                || basis.CanonicalToClient is not { } canonical || !canonical.AlmostEquals(previous.Matrix))
            {
                RevokeDisplay("display-preference-canonical-basis-changed");
            }
            else if (layer is null)
            {
                _render = null;
                _display = new(false, true, false, prepared.Material.Reason, preferred,
                    prepared.Selection.EffectiveProfileId, prepared.Selection.DisplayKind);
            }
            else
            {
                _render = new(layer, frame, previous.Authorization, _frameGeneration, _epoch);
                _display = new(true, true, _render.IsLive, "display-prepared", preferred,
                    prepared.Selection.EffectiveProfileId, prepared.Selection.DisplayKind);
            }
            Publish();
        }
    }

    private void CommitDisplay(PreparedRuntimeDisplay prepared, IReadOnlyDictionary<string, object?> residual,
        CapturedClientFrame frame, string map, string floor)
    {
        _render = null; _canonical = null;
        var selection = prepared.Selection;
        var layer = prepared.Material.Layer;
        var bridge = prepared.CanonicalBasis;
        if (bridge?.CanonicalToClient is not { } canonical || string.IsNullOrEmpty(bridge.CanonicalPackId)
            || !residual.TryGetValue("coordinatePackId", out var pack) || pack?.ToString() != bridge.CanonicalPackId
            || !AlignmentObservationEvidence.RenderedProfileResidualIsVerified(residual))
        {
            _display = new(false, false, false, layer is null ? prepared.Material.Reason : "canonical-fit-basis-mismatch",
                selection.PreferredProfileId, selection.EffectiveProfileId, selection.DisplayKind);
            return;
        }
        _alignment ??= new(floors: _resources!.Passports.Floors);
        _alignment.BeginObservation(new(_roundId!), map, floor, _frameGeneration, [frame.Stamp.FrameId, _resources!.Id]);
        var candidate = new AlignmentCandidate(map, floor, new(canonical.ToArray()), "initial-structure-v2", _frameGeneration,
            currentFrameVerified: true, residualVerified: true, globalResidualVerified: true,
            metadata: new Dictionary<string, object?> { ["routeProfileId"] = "shared-canonical", ["independentFloorAnchor"] = true });
        var verdict = _alignment.Adjudicate(candidate);
        var authorization = _alignment.Authorize(verdict);
        if (authorization is null || !_alignment.Commit(authorization))
        {
            _alignment.RevokeDisplay("runtime-canonical-commit-rejected");
            _display = new(false, false, false, verdict.Reason);
            return;
        }
        _canonical = new(frame.Stamp, _epoch, authorization, bridge.CanonicalPackId, canonical, bridge.Reference!);
        _render = layer is null ? null : new(layer, frame, authorization, _frameGeneration, _epoch);
        _display = new(layer is not null, true, _render?.IsLive == true,
            layer is not null ? "display-prepared" : prepared.Material.Reason, selection.PreferredProfileId,
            selection.EffectiveProfileId, selection.DisplayKind);
    }

    private void RevokeDisplay(string reason)
    {
        _alignment?.Close(reason: reason); _render = null; _canonical = null; _frameGeneration++;
        _display = new(false, false, false, reason);
    }
}
