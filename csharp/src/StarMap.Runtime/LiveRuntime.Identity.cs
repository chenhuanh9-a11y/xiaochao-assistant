using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private string? RankS0(long epoch, CancellationToken token,
        CapturedClientFrame captured, BoundResources bound, string floor, Mat frame, JsonElement preparation)
    {
        var pipeline = bound.Identity!;
        NativeSimilarityState? previous; NativeIdentityContext context;
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            previous = _nativeIdentity;
            // A group's display member is not a confirmed unique map.
            context = pipeline.Context(_roundId!, floor, HasRegisteredGroup ? null : _mapId);
        }
        var started = Stopwatch.GetTimestamp();
        var prior = previous?.Prior();
        var rank = prior is null
            ? pipeline.Rank(frame, floor, _options.MainEntryBudget, context: context, cancellation: token)
            : pipeline.RankEstablished(frame, floor, prior, context, _options.MainEntryBudget, token);
        var decision = S0IdentityDecision.Consume(rank, previous, context, IdentityObservationId(frame, token),
            existingConfirmation: context.MapId);
        var detail = decision.Summary();
        var automatic = decision.CandidateSummaries;
        var strategy = rank.Strategy;
        var elapsed = Stopwatch.GetElapsedTime(started).TotalMilliseconds;
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            bool desiredMatches = decision.IsRegisteredGroup && _desiredGroupChoice is { } desired
                && desired.Round == _roundId && desired.Resource == bound.Id
                && desired.Members.ToHashSet(StringComparer.Ordinal).SetEquals(decision.NativeState.MemberIds);
            bool sameGroup = decision.IsRegisteredGroup && (desiredMatches || previous?.RegisteredGroup == true
                && previous.MemberIds.ToHashSet(StringComparer.Ordinal).SetEquals(decision.NativeState.MemberIds));
            if (decision.IsRegisteredGroup && !sameGroup) ClearGroupChoice(resetCycle: false);
            else if (!decision.IsRegisteredGroup) DiscardGroupMaterial("registered-group-not-observed");
            if (desiredMatches)
                _desiredGroupChoice = _desiredGroupChoice! with { Group = decision.NativeState.GroupId!, Members = decision.NativeState.MemberIds.ToArray() };
            _nativeIdentity = decision.NativeState;
            _entryCandidates.Clear();
            foreach (var entry in decision.EntryCandidatesByMap)
                _entryCandidates.Add(entry.Key, entry.Value!.AsObject());
            _candidates = automatic.Select(node =>
            {
                var row = node!.AsObject();
                string map = row["mapId"]?.GetValue<string>() ?? "";
                return new RuntimeCandidate(map, bound.Passports.MapProfiles.GetValueOrDefault(map) ?? "",
                    row["score"]?.GetValue<double>() ?? 0, row["identityHardGatePassed"]?.GetValue<bool>() == true,
                    decision.EntryCandidate(map)?["verification"]?["success"]?.GetValue<bool>() == true)
                {
                    IdentityKind = row["identityKind"]?.GetValue<string>() ?? "map",
                    GroupId = row["groupId"]?.GetValue<string>(), Name = row["name"]?.GetValue<string>(),
                    MemberIds = Array.AsReadOnly(row["memberIds"]?.AsArray().Select(id => id!.GetValue<string>()).ToArray() ?? [])
                };
            }).ToArray();
            _selected = _candidates.Length > 0 ? 0 : -1;
            _diagnostics = JsonSerializer.SerializeToElement(new { stage = "main-entry",
                floor = preparation.GetProperty("floor"), identityDecision = detail,
                sourceAdvisory = rank.SourceAdvisory, strategy = strategy.Diagnostics,
                main = strategy.Main?.Diagnostics, frontGate = strategy.FrontGate, corners = strategy.Main?.Corners,
                passport = strategy.Passport.Diagnostics, side = strategy.Side?.Diagnostics, preparation,
                rankElapsedMs = elapsed, rankBudgetMs = _options.MainEntryBudget.TotalMilliseconds }, Json);
            string? selected = decision.SelectedProvisionalMapId;
            if (decision.IsRegisteredGroup && selected is not null)
                LockIdentity(sameGroup && _heldGroupMember is not null ? _heldGroupMember : selected,
                    sameGroup && _heldGroupMember is not null ? "group-manual" : "group-default");
            else if (decision.UniqueHardIdentityCandidate && selected is not null)
                LockIdentity(selected, "automatic-hard-gate");
            else if (_identitySource is "group-default" or "group-manual")
            {
                // Current evidence withdrew the provisional group. Warm geometry
                // cannot preserve an automatic identity on its own.
                ResetSteady(); _geometryGroup = null; _mapId = null; _identitySource = "";
                RevokeDisplay("registered-group-current-evidence-withdrawn"); _alignment = null;
            }
            if (_mapId is null) _identitySource = decision.IsRegisteredGroup ? "group-default" : "native-similarity";
            _reason = _mapId is null ? "candidates-awaiting-confirmation" : "identity-locked-pose-pending";
            Publish();
            return _mapId;
        }
    }

    // Original stage_similarity_rank identifies observation content with the
    // BGR pixel digest. Capture IDs remain distinct freshness tokens, so they
    // cannot deduplicate repeated pixels in the condition evidence ledger.
    private static string IdentityObservationId(Mat frame, CancellationToken cancellation)
    {
        using var digest = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var row = new byte[checked(frame.Width * 3)];
        int height = frame.Height;
        for (int y = 0; y < height; y++)
        {
            if ((y & 63) == 0) cancellation.ThrowIfCancellationRequested();
            Marshal.Copy(frame.Ptr(y), row, 0, row.Length);
            digest.AppendData(row);
        }
        return Convert.ToHexStringLower(digest.GetHashAndReset());
    }
}
