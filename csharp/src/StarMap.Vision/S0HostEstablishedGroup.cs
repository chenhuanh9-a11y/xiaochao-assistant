using System.Text.Json.Nodes;

namespace StarMap.Vision;

public sealed partial class S0HostFit
{
    private bool TargetCurrent(StarMap.Core.Resources.ResourceCatalogEntry target) =>
        target.Season == catalog.Season && target.Mode == catalog.Mode
        && catalog.MapProfiles.TryGetValue(target.MapId, out string? profile) && target.Profile == profile
        && ReferenceEquals(target, resources.Find(target.Mode, target.Season, target.Profile, target.MapId, target.Floor));

    /// <summary>Same original shared group producer used by Fit; this proposal grants no authority.</summary>
    public EstablishedGroupResult EvaluateEstablishedGroup(S0HostStageContext context)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        EstablishedGroupResult Reject(string reason) => new(Failure(reason), new());
        if (!TargetCurrent(context.Target)) return Reject("s0-host-target-resource-context-mismatch");
        var group = context.Options.Group;
        var view = group?.Geometry;
        if (group is null || view is null || !ReferenceEquals(group.OwnerScope, context.Options.OwnerScope)
            || context.Options.OwnerScope is null || group.Epoch != context.Options.Epoch
            || group.Mode != context.Target.Mode || group.Season != context.Target.Season || group.MapId != context.Target.MapId
            || view.MapId != context.Target.MapId || view.Floor != context.Target.Floor
            || view.RouteProfileId != "shared-structure"
            || !view.VerifiedFloors.SetEquals(group.VerifiedFloors) || !view.RelationFloors.SetEquals(group.RelationFloors))
            return Reject("s0-host-group-context-mismatch");
        var reference = Prepare(context.Target, out string unavailable);
        if (reference is null) return Reject(unavailable);
        if (!ReferenceEquals(context.Reference, reference.Geometry.Source) || context.CanonicalPackId != reference.CanonicalPackId)
            return Reject("s0-host-group-reference-context-mismatch");
        var providers = new EstablishedGroupProviders(EntryScaleRecovery: input =>
        {
            // Original locator.rank(frame,floor,map_id=...,route=...) is an
            // entry proposal only; the established solver rechecks its residual.
            var ranked = main.Rank(input.Frame, input.Group.Floor, knownMapId: input.Group.MapId);
            return new(ranked.Rows.Select(row => row!.DeepClone().AsObject()).ToArray(), ranked.Diagnostics.DeepClone().AsObject());
        });
        return EstablishedGroupFit.Evaluate(context.Frame, reference.Geometry, view,
            matrix => CanonicalResidual(context.Frame, reference, matrix), providers, context.Cancelled);
    }
}
