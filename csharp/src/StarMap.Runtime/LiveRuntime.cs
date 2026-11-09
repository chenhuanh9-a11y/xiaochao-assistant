using System.Runtime.InteropServices;
using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

/// <summary>
/// Actual managed producer/consumer path. This object alone commits its runtime
/// state under one lock; heavy capture/vision executes outside that lock and is
/// admitted by a serial worker. Invalidation never waits for vision or capture.
/// </summary>
public sealed class LiveRuntime : ISessionRuntime, IRuntimeRecoveryControl, ISessionRuntimeProgress
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    private static readonly string[] Gaps = ["side-entry-ranking-unavailable", "steady-tracking-fallback-unavailable",
        "canonical-display-bridge-unavailable", "author-route-display-unavailable", "automatic-map-open-recovery-unavailable",
        "locked-canvas-floor-reuse-unavailable", "similarity-group-competition-unavailable"];
    private readonly object _sync = new();
    private readonly SemaphoreSlim _worker = new(1, 1);
    private readonly ICapturePort _capture;
    private readonly LiveRuntimeOptions _options;
    private string _resourceRoot;
    private BoundResources? _resources;
    private SessionSelection _selection = new();
    private string? _roundId, _mapId, _floor, _manualFloor, _observedFloor;
    private long? _manualFloorStarted;
    private string _identitySource = "", _reason = "context-not-bound";
    private bool _roundActive, _mapOpen, _workActive;
    private bool _closed;
    private long _epoch, _operation;
    private long _progressEpoch;
    private SessionRequestContext? _progressRequest;
    private CancellationToken _progressCancellation;
    private CapturedClientFrame? _frame;
    private NativeGeometryPose? _pose;
    private RuntimeCandidate[] _candidates = [];
    private int _selected = -1;
    private JsonElement _diagnostics = JsonSerializer.SerializeToElement(new { });
    private readonly Dictionary<string, NativeGeometryPose> _trustedFloors = new(StringComparer.Ordinal);
    private LiveRuntimeState _publishedState = null!;
    private RuntimeSnapshot _publishedSnapshot = null!;
    private bool _publishedBindingState;

    public LiveRuntime(ICapturePort capture, string resourceRoot, LiveRuntimeOptions? options = null)
    {
        _capture = capture ?? throw new ArgumentNullException(nameof(capture));
        _resourceRoot = Path.GetFullPath(resourceRoot);
        _options = options is null ? new() : options with { FloorSettings = options.FloorSettings?.Clone() };
        if (_options.PreparationTimeout <= TimeSpan.Zero || _options.PreparationInterval <= TimeSpan.Zero
            || _options.MainEntryBudget <= TimeSpan.Zero) throw new ArgumentException("runtime-budget-invalid");
        lock (_sync) Publish();
    }

    // No I/O, state mutation, image decode, or algorithm in either read method.
    public LiveRuntimeState ReadState()
    {
        lock (_sync)
        {
            if (_frame is not null && !_capture.IsCurrent(_frame.Stamp)) return Withhold(_publishedState);
            return _publishedState;
        }
    }

    public RuntimeSnapshot ReadSnapshot()
    {
        lock (_sync)
        {
            if (_frame is not null && !_capture.IsCurrent(_frame.Stamp)) return Snapshot(Withhold(_publishedState));
            return _capture.IsBound == _publishedBindingState ? _publishedSnapshot : Snapshot(_publishedState);
        }
    }

    public SessionRuntimeRead ReadSnapshotFor(SessionRequestContext request)
    {
        lock (_sync)
        {
            // ReadSnapshot uses this same reentrant lock and checks the exact
            // capture/window/geometry lifetime. No producer can publish the next
            // context between this snapshot and its request/epoch attribution.
            var snapshot = ReadSnapshot();
            bool owned = !_closed && !_progressCancellation.IsCancellationRequested && _progressRequest == request
                && _progressEpoch == _epoch && snapshot.Context == _publishedState.Context;
            return new(snapshot, owned);
        }
    }

    public void InvalidateCapture(string reason = "capture-invalidated")
    { lock (_sync) { if (_closed) return; _epoch++; RevokeFrame(reason); Publish(); } }

    public void InvalidateResources(string reason = "resources-invalidated")
    {
        lock (_sync)
        {
            if (_closed) return;
            _epoch++; RevokeFrame(reason); _resources = null; _roundActive = false; _roundId = null;
            ClearIdentity(); ClearFloor(); Publish();
        }
    }

    public void CancelMapOpenRecovery(string reason) => InvalidateCapture(reason);

    /// <summary>Final revocation only; the caller separately owns capture-port shutdown.</summary>
    public void Shutdown()
    {
        lock (_sync)
        {
            if (_closed) return;
            _closed = true; _epoch++; RevokeFrame("runtime-closed"); ClearIdentity();
            _roundActive = false; _roundId = null; ClearFloor(); _resources = null; Publish();
        }
    }

    public async ValueTask<RuntimeOperationResult> DispatchAsync(SessionRuntimeRequest request, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(request);
        try { await _worker.WaitAsync(cancellationToken).ConfigureAwait(false); }
        catch (OperationCanceledException) { return Receipt(request, false, "cancelled"); }
        long operation, epoch;
        lock (_sync)
        {
            operation = ++_operation; epoch = _epoch;
            _progressRequest = null;
        }
        using var registration = cancellationToken.Register(() =>
        {
            lock (_sync)
                if (!_closed && _operation == operation && _progressRequest == request.Context)
                { _epoch++; RevokeFrame("cancelled"); Publish(); }
        });
        try
        {
            var completed = await DispatchOwned(request, epoch, cancellationToken).ConfigureAwait(false);
            lock (_sync)
            {
                if (_operation == operation && _workActive) { _workActive = false; Publish(); }
                var settled = ReadSnapshotFor(request.Context);
                if (!settled.OwnedByRequest || completed.Snapshot.Context != settled.Snapshot.Context)
                {
                    // Do not rebind an earlier successful operation to a new
                    // epoch/snapshot just because finalization ran after revoke.
                    var reason = completed.Receipt.Success ? "cancelled-or-obsolete" : completed.Receipt.Reason;
                    return new(new(request.Action, false, request.Context, settled.Snapshot.Context, reason, reason), settled.Snapshot);
                }
                // Only the work-active fact changed during settlement. Preserve
                // the producer's original receipt and committed result context.
                return new(completed.Receipt, settled.Snapshot);
            }
        }
        finally
        {
            _worker.Release();
        }
    }

    private async ValueTask<RuntimeOperationResult> DispatchOwned(SessionRuntimeRequest request, long epoch, CancellationToken cancellationToken)
    {
        try
        {
            cancellationToken.ThrowIfCancellationRequested();
            lock (_sync)
            {
                if (_closed) return Receipt(request, false, "runtime-closed");
                if (!RequestMatches(request)) return Receipt(request, false, "request-runtime-context-mismatch");
                RequireCurrent(epoch, cancellationToken);
                _progressRequest = request.Context; _progressEpoch = epoch; _progressCancellation = cancellationToken;
            }
            return request.Action switch
            {
                "set_context" => SetContext(request, epoch, cancellationToken),
                "start_round" => StartRound(request, epoch, cancellationToken),
                "toggle_round" => IsRoundActive() ? EndRound(request) : StartRound(request, epoch, cancellationToken),
                "end_round" or "stop_round" or "reset_round" or "cancel" => EndRound(request),
                "close_map" => CloseMap(request),
                "map" => IsMapOpen() ? CloseMap(request) : await CaptureAndSolve(request, epoch, cancellationToken).ConfigureAwait(false),
                "capture_now" => await CaptureAndSolve(request, epoch, cancellationToken).ConfigureAwait(false),
                "confirm_route" => ConfirmMap(request, epoch, cancellationToken),
                "select_candidate" or "previous_candidate" or "next_candidate" => SelectCandidate(request),
                "set_floor" or "cycle_floor" => await SetFloor(request, epoch, cancellationToken).ConfigureAwait(false),
                "reselect_map" => ReselectMap(request),
                _ => Receipt(request, false, "capability-unavailable:" + request.Action),
            };
        }
        catch (OperationCanceledException) { return Receipt(request, false, "cancelled-or-obsolete"); }
        catch (Exception error)
        {
            lock (_sync)
                if (epoch == _epoch) { RevokeFrame("runtime-error:" + error.GetType().Name); Publish(); }
            return Receipt(request, false, "runtime-error:" + error.GetType().Name + ":" + error.Message);
        }
    }

    private RuntimeOperationResult SetContext(SessionRuntimeRequest request, long epoch, CancellationToken token)
    {
        var desired = new SessionSelection(Text(request.Payload, "season") ?? request.Context.Runtime.Season,
            Text(request.Payload, "mode") ?? request.Context.Runtime.Mode,
            Text(request.Payload, "profileId") ?? request.Context.Runtime.ProfileId);
        string root;
        bool reload;
        lock (_sync)
        {
            if (_roundActive && (desired.Mode != _selection.Mode || desired.Season != _selection.Season))
                return Receipt(request, false, "end-round-before-changing-mode");
            root = Path.GetFullPath(Text(request.Payload, "resourceRoot") ?? _resourceRoot);
            reload = _resources is null || desired.Mode != _selection.Mode || desired.Season != _selection.Season
                || root != _resourceRoot || Flag(request.Payload, "reloadResources");
            if (reload && _roundActive) return Receipt(request, false, "end-round-before-rebinding-resources");
        }
        // An author preference never selects identity/structure resources.
        BoundResources? bound = reload ? BoundResources.Load(root, desired) : null;
        lock (_sync)
        {
            RequireCurrent(epoch, token);
            if (reload)
            {
                AdvanceOwnedEpoch(); RevokeFrame("context-bound"); ClearIdentity(); ClearFloor();
                _resources = bound; _resourceRoot = root;
            }
            _selection = desired; _reason = "context-bound"; Publish();
            return Receipt(request, true, _reason);
        }
    }

    private RuntimeOperationResult StartRound(SessionRuntimeRequest request, long epoch, CancellationToken token)
    {
        lock (_sync)
        {
            RequireCurrent(epoch, token);
            if (_resources is null) return Receipt(request, false, "resources-not-bound");
            if (_selection is { Season: "s1", Mode: "difficulty2" }) return Receipt(request, false, "retired-mode");
            if (!_roundActive)
            {
                AdvanceOwnedEpoch(); RevokeFrame("round-started"); ClearIdentity(); ClearFloor();
                _roundActive = true; _roundId = Guid.NewGuid().ToString("N");
            }
            _reason = "round-started"; Publish(); return Receipt(request, true, _reason);
        }
    }

    private RuntimeOperationResult EndRound(SessionRuntimeRequest request)
    {
        lock (_sync)
        {
            AdvanceOwnedEpoch(); RevokeFrame("round-ended"); ClearIdentity(); _roundActive = false;
            _roundId = null; ClearFloor(); Publish(); return Receipt(request, true, _reason);
        }
    }

    private RuntimeOperationResult CloseMap(SessionRuntimeRequest request)
    {
        lock (_sync) { AdvanceOwnedEpoch(); RevokeFrame("map-closed"); Publish(); return Receipt(request, true, _reason); }
    }

    private async ValueTask<RuntimeOperationResult> CaptureAndSolve(SessionRuntimeRequest request, long epoch, CancellationToken token)
    {
        BoundResources bound;
        lock (_sync)
        {
            RequireCurrent(epoch, token);
            if (!_roundActive) return Receipt(request, false, "start-round-before-capture");
            if (_resources is null) return Receipt(request, false, "resources-not-bound");
            if (!_capture.IsBound) return Receipt(request, false, "capture-not-bound");
            if (_selection.Season != "s0") return Receipt(request, false, "capability-unavailable:s1-structure-identity-consumer");
            bound = _resources; RevokeFrame("map-open-preparing"); _workActive = true; Publish();
        }
        var started = _options.TimeProvider.GetTimestamp();
        var limit = _options.PreparationTimeout;
        var period = _options.PreparationInterval;
        bool locked;
        lock (_sync) locked = _mapId is not null;
        var nextProbe = locked ? TimeSpan.Zero : period;
        int maxProbes = Math.Max(1, checked((int)Math.Ceiling(limit.TotalSeconds / period.TotalSeconds)));
        CapturedClientFrame? captured = null;
        FloorObservationResult? floorRead = null;
        string? preparedFloor = null;
        int probes = 0;
        double captureMs = 0, decodeMs = 0, observationMs = 0;
        string? capturedFrameId = null;
        object Preparation(string phase) => new {
            stage = "map-open-preparation", phase, probes, firstProbeImmediate = locked,
            elapsedMs = _options.TimeProvider.GetElapsedTime(started).TotalMilliseconds,
            budgetMs = limit.TotalMilliseconds, captureMs, decodeMs, observationMs,
            capturedFrameId, map = floorRead?.MapObservation, floor = floorRead?.FloorRead
        };
        for (int attempt = 0; attempt < maxProbes; attempt++)
        {
            CheckCurrent(epoch, token);
            var elapsed = _options.TimeProvider.GetElapsedTime(started);
            if (elapsed >= limit) break;
            while (elapsed < nextProbe)
            {
                await Task.Delay(TimeSpan.FromMilliseconds(Math.Min(25,
                    Math.Min((nextProbe - elapsed).TotalMilliseconds, (limit - elapsed).TotalMilliseconds))),
                    _options.TimeProvider, token).ConfigureAwait(false);
                CheckCurrent(epoch, token);
                elapsed = _options.TimeProvider.GetElapsedTime(started);
                if (elapsed >= limit) break;
            }
            if (elapsed >= limit) break;
            var probeStarted = elapsed;
            probes++;
            var stageStarted = Stopwatch.GetTimestamp();
            var acquisition = await _capture.CaptureAsync(token).ConfigureAwait(false);
            captureMs = Stopwatch.GetElapsedTime(stageStarted).TotalMilliseconds;
            capturedFrameId = acquisition.Frame?.Stamp.FrameId;
            CheckCurrent(epoch, token);
            if (_options.TimeProvider.GetElapsedTime(started) >= limit)
                return FailCurrent(request, epoch, "map-open-preparation-deadline", Preparation("capture-finished-after-deadline"));
            if (acquisition.Frame is null) return FailCurrent(request, epoch, acquisition.Reason.Length > 0 ? acquisition.Reason : "capture-unavailable", Preparation("capture-unavailable"));
            captured = acquisition.Frame;
            if (!_capture.IsCurrent(captured.Stamp)) return FailCurrent(request, epoch, "capture-context-obsolete", Preparation("capture-obsolete"));
            stageStarted = Stopwatch.GetTimestamp();
            using var frame = ToBgr(captured);
            decodeMs = Stopwatch.GetElapsedTime(stageStarted).TotalMilliseconds;
            stageStarted = Stopwatch.GetTimestamp();
            floorRead = FloorObservation.Observe(frame, bound.Passports.Season, bound.Passports.Mode,
                _options.FloorSettings is { } settings ? JsonNode.Parse(settings.GetRawText())?.AsObject() : null,
                _options.S1FloorUse, frameCurrent: true);
            observationMs = Stopwatch.GetElapsedTime(stageStarted).TotalMilliseconds;
            // The source rechecks the deadline after observation, not merely
            // before starting capture. Late usable pixels are never prepared.
            if (_options.TimeProvider.GetElapsedTime(started) >= limit)
                return FailCurrent(request, epoch, "map-open-preparation-deadline", Preparation("observation-finished-after-deadline"));
            lock (_sync)
            {
                RequireCurrent(epoch, token, captured, bound);
                preparedFloor = ResolveObservedFloor(floorRead);
            }
            if (floorRead.MapOpen == true && preparedFloor is not null) break;
            nextProbe = probeStarted + period;
            if (attempt == maxProbes - 2)
            {
                var work = _options.TimeProvider.GetElapsedTime(started) - probeStarted;
                var lastProbe = limit - TimeSpan.FromMilliseconds(Math.Min(period.TotalMilliseconds, Math.Max(1, work.TotalMilliseconds)));
                if (lastProbe > nextProbe) nextProbe = lastProbe;
            }
        }
        if (captured is null || floorRead?.MapOpen != true) return FailCurrent(request, epoch, "map-open-preparation-deadline", Preparation("probe-loop-ended-without-map"));
        var preparation = JsonSerializer.SerializeToElement(Preparation("prepared"), Json);
        string? floor, map;
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            floor = preparedFloor;
            _frame = captured; _mapOpen = true; _floor = floor; _pose = null;
            _diagnostics = JsonSerializer.SerializeToElement(new { stage = "floor-observation", map = floorRead.MapObservation, floor = floorRead.FloorRead, preparation }, Json);
            _reason = floor is null ? "current-floor-unavailable" : "current-floor-observed";
            map = _mapId; Publish();
        }
        if (floor is null) return Receipt(request, false, "current-floor-unavailable");
        using var current = ToBgr(captured);
        if (map is null)
        {
            // This is the actual unconstrained current-frame production ranker.
            var rankStarted = Stopwatch.GetTimestamp();
            var ranked = bound.Ranker.Rank(current, floor, _options.MainEntryBudget, cancellation: token);
            var rankElapsedMs = Stopwatch.GetElapsedTime(rankStarted).TotalMilliseconds;
            lock (_sync)
            {
                RequireCurrent(epoch, token, captured, bound);
                _candidates = ranked.Rows.Select(row => new RuntimeCandidate(
                    row!["mapId"]!.GetValue<string>(), row["profileId"]!.GetValue<string>(), row["score"]!.GetValue<double>(),
                    row["identityVerified"]?.GetValue<bool>() == true, row["poseVerified"]?.GetValue<bool>() == true)).ToArray();
                _selected = _candidates.Length > 0 ? 0 : -1;
                _diagnostics = JsonSerializer.SerializeToElement(new { stage = "main-entry", floor = floorRead.FloorRead,
                    main = ranked.Diagnostics, frontGate = ranked.FrontGate, corners = ranked.Corners, rows = ranked.Rows,
                    preparation, rankElapsedMs, rankBudgetMs = _options.MainEntryBudget.TotalMilliseconds }, Json);
                var verified = _candidates.Where(c => c.IdentityVerified).ToArray();
                if (verified.Length == 1)
                { LockIdentity(verified[0].MapId, "main-entry-current-frame"); map = _mapId; }
                _reason = map is null ? "candidates-awaiting-confirmation" : "identity-locked-pose-pending";
                Publish();
            }
        }
        if (map is null) return Receipt(request, false, "candidates-awaiting-confirmation");
        return Fit(request, epoch, token, captured, bound, map, floor, current);
    }

    private RuntimeOperationResult ConfirmMap(SessionRuntimeRequest request, long epoch, CancellationToken token)
    {
        BoundResources bound; CapturedClientFrame? captured; string map; string? floor;
        lock (_sync)
        {
            RequireCurrent(epoch, token);
            if (!_roundActive || _resources is null) return Receipt(request, false, "start-round-before-confirming-map");
            bound = _resources;
            map = Text(request.Payload, "mapId") ?? (_selected >= 0 && _selected < _candidates.Length ? _candidates[_selected].MapId : "");
            if (!bound.Passports.MapProfiles.ContainsKey(map)) return Receipt(request, false, "invalid-manual-map");
            LockIdentity(map, "manual");
            // Identity commits before any preparation/fit, and survives fit failure.
            _pose = null; _reason = "identity-locked-pose-pending";
            captured = _mapOpen ? _frame : null; floor = _floor;
            _workActive = captured is not null && floor is not null && _capture.IsCurrent(captured.Stamp); Publish();
        }
        if (captured is null || floor is null || !_capture.IsCurrent(captured.Stamp)) return Receipt(request, true, "identity-locked-pose-pending");
        using var frame = ToBgr(captured);
        return Fit(request, epoch, token, captured, bound, map, floor, frame);
    }

    private RuntimeOperationResult Fit(SessionRuntimeRequest request, long epoch, CancellationToken token,
        CapturedClientFrame captured, BoundResources bound, string map, string floor, Mat frame)
    {
        CheckCurrent(epoch, token, captured, bound);
        var profile = bound.Passports.MapProfiles[map];
        var entry = bound.Catalog.Find(bound.Passports.Mode, bound.Passports.Season, profile, map, floor);
        if (entry is null) return FailCurrent(request, epoch, "target-floor-resource-unavailable");
        if (!bound.Prepared.TryGetValue(entry.StableKey, out var prepared))
        {
            var loaded = bound.Catalog.LoadSharedStructure(entry);
            if (loaded.Value is null) return FailCurrent(request, epoch, loaded.Error?.Code ?? "target-floor-structure-unavailable");
            var prebuilt = Path.Combine(bound.Passports.ModeRoot, "references", "fast-structure",
                Path.GetFileNameWithoutExtension(entry.StructureNpzPath) + "-fast.npz");
            if (!File.Exists(prebuilt)) return FailCurrent(request, epoch, "fast-prepared-unavailable");
            var fast = FastStructure.LoadPrepared(prebuilt, loaded.Value);
            prepared = new(loaded.Value, fast); bound.Prepared[entry.StableKey] = prepared;
        }
        StructurePrior? prior = null;
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            // A prior can only originate from this exact map/floor/resource's
            // previous verified fit. No rank row or another floor seeds scale.
            if (_trustedFloors.TryGetValue(floor, out var trusted) && trusted.MapId == map && trusted.ResourceId == bound.Id)
                prior = new(prepared.Source, trusted.ToMatrix());
        }
        using var geometry = StructureGeometry.Open(prepared.Source);
        using var observation = Geometry.Current(frame);
        var fit = observation is null ? new FastFitResult(false, "fast-observation-unavailable", null)
            : FastStructureFit.Evaluate(frame, observation, geometry, prepared.Fast, prior, cancelled: () => Obsolete(epoch, token, captured, bound));
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            _pose = fit.GeometryVerified && fit.Matrix is not null
                ? new(map, floor, bound.Id, captured.Stamp.FrameId, fit.Matrix) : null;
            if (_pose is not null) _trustedFloors[floor] = _pose;
            _reason = fit.Reason;
            var main = _diagnostics.TryGetProperty("stage", out var stage) && stage.GetString() == "main-entry"
                ? _diagnostics : JsonSerializer.SerializeToElement(new { });
            _diagnostics = JsonSerializer.SerializeToElement(new { stage = "fast-fit", identitySource = _identitySource,
                nativeReferenceKey = entry.StableKey, priorUsed = prior is not null, fit, main }, Json);
            Publish();
            return Receipt(request, fit.GeometryVerified, fit.Reason);
        }
    }

    private RuntimeOperationResult SelectCandidate(SessionRuntimeRequest request)
    {
        lock (_sync)
        {
            if (!_roundActive || _mapId is not null || _frame is null || !_capture.IsCurrent(_frame.Stamp))
                return Receipt(request, false, "candidate-control-unavailable");
            if (_candidates.Length == 0) return Receipt(request, false, "no-candidates");
            var requested = Text(request.Payload, "candidateMapId");
            int index = request.Action == "select_candidate" ? requested is not null
                ? Array.FindIndex(_candidates, c => c.MapId == requested) : Integer(request.Payload, "index", -1)
                : (_selected + (request.Action == "previous_candidate" ? _candidates.Length - 1 : 1)) % _candidates.Length;
            if (index < 0 || index >= _candidates.Length) return Receipt(request, false, "invalid-candidate-index");
            _selected = index; _reason = "candidate-selected-awaiting-confirmation"; Publish();
            return Receipt(request, true, _reason);
        }
    }

    private async ValueTask<RuntimeOperationResult> SetFloor(SessionRuntimeRequest request, long epoch, CancellationToken token)
    {
        bool recapture;
        lock (_sync)
        {
            RequireCurrent(epoch, token);
            if (_resources is null || !_roundActive) return Receipt(request, false, "floor-control-unavailable");
            if (request.Action == "cycle_floor" && !_mapOpen) return Receipt(request, false, "floor-control-unavailable");
            var floors = _resources.Passports.Floors;
            var (valid, floor) = FloorTarget(request.Payload);
            if (request.Action == "cycle_floor")
            { var index = floors.ToList().IndexOf(_floor ?? ""); floor = floors[(index + 1) % floors.Count]; }
            if (!valid || floor is not null && !floors.Contains(floor)) return Receipt(request, false, "unsupported-floor");
            recapture = _mapOpen; AdvanceOwnedEpoch(); epoch = _epoch;
            RevokeFrame("floor-changed"); _manualFloor = floor;
            _manualFloorStarted = floor is null ? null : _options.TimeProvider.GetTimestamp();
            _floor = floor ?? _observedFloor; Publish();
        }
        return recapture ? await CaptureAndSolve(request, epoch, token).ConfigureAwait(false)
            : Receipt(request, true, "floor-selected-pose-pending");
    }

    private RuntimeOperationResult ReselectMap(SessionRuntimeRequest request)
    {
        lock (_sync)
        {
            AdvanceOwnedEpoch(); RevokeFrame("map-reselection"); ClearIdentity(); Publish();
            return Receipt(request, true, _reason);
        }
    }

    private void LockIdentity(string mapId, string source)
    {
        if (_mapId != mapId) { _trustedFloors.Clear(); _resources?.Prepared.Clear(); }
        _mapId = mapId; _identitySource = source;
    }
    private void ClearIdentity()
    { _mapId = null; _identitySource = ""; _trustedFloors.Clear(); }
    private void ClearFloor()
    { _floor = _manualFloor = _observedFloor = null; _manualFloorStarted = null; }
    private void AdvanceOwnedEpoch() { _epoch++; _progressEpoch = _epoch; }

    private string? ResolveObservedFloor(FloorObservationResult read)
    {
        _observedFloor = read.AutomaticEligible ? read.FloorId : null;
        // floor_observation_policy.py: an unknown latest read neither fills
        // from history nor ends an explicit manual override. Fresh valid
        // evidence releases the non-hard-locked override after two seconds.
        if (_observedFloor is not null && _manualFloor is not null && _manualFloorStarted is { } started
            && _options.TimeProvider.GetElapsedTime(started) >= TimeSpan.FromSeconds(2))
        { _manualFloor = null; _manualFloorStarted = null; }
        return _manualFloor ?? _observedFloor;
    }
    private void RevokeFrame(string reason)
    { _frame = null; _pose = null; _mapOpen = false; _workActive = false; _candidates = []; _selected = -1; _reason = reason; _diagnostics = JsonSerializer.SerializeToElement(new { }); }
    private bool IsRoundActive() { lock (_sync) return _roundActive; }
    private bool IsMapOpen() { lock (_sync) return _mapOpen; }

    private bool RequestMatches(SessionRuntimeRequest request)
    {
        if (request.Action is "end_round" or "stop_round" or "reset_round" or "cancel" or "close_map") return true;
        var actual = ReadSnapshot().Context; var input = request.Context.Runtime;
        if (request.Action != "set_context" && input.Selection != actual.Selection) return false;
        // New capture and explicit user actions deliberately replace/revoke the
        // previous frame; none consumes its pixels from a caller's payload.
        if (input.RoundId != actual.RoundId || input.ResourceId != actual.ResourceId || input.MapId != actual.MapId) return false;
        return request.Action is not ("select_candidate" or "previous_candidate" or "next_candidate")
            || input.FloorId == actual.FloorId && input.FrameId == actual.FrameId;
    }
    private bool Obsolete(long epoch, CancellationToken token, CapturedClientFrame? frame = null, BoundResources? bound = null)
        { lock (_sync) return _closed || token.IsCancellationRequested || epoch != _epoch || bound is not null && !ReferenceEquals(bound, _resources)
            || frame is not null && !_capture.IsCurrent(frame.Stamp); }
    private void RequireCurrent(long epoch, CancellationToken token, CapturedClientFrame? frame = null, BoundResources? bound = null)
    { if (Obsolete(epoch, token, frame, bound)) throw new OperationCanceledException("runtime-work-obsolete", token); }
    private void CheckCurrent(long epoch, CancellationToken token, CapturedClientFrame? frame = null, BoundResources? bound = null)
    { lock (_sync) RequireCurrent(epoch, token, frame, bound); }

    private RuntimeOperationResult FailCurrent(SessionRuntimeRequest request, long epoch, string reason, object? diagnostic = null)
    {
        lock (_sync)
        {
            if (_epoch == epoch)
            { _pose = null; _workActive = false; _reason = reason; if (diagnostic is not null) _diagnostics = JsonSerializer.SerializeToElement(diagnostic, Json); Publish(); }
            return Receipt(request, false, reason);
        }
    }
    private RuntimeOperationResult Receipt(SessionRuntimeRequest request, bool success, string reason)
    {
        lock (_sync)
        {
            var snapshot = ReadSnapshot();
            return new(new(request.Action, success, request.Context, snapshot.Context, reason, reason), snapshot);
        }
    }
    private RuntimeContext Context() => new(_selection.Season, _selection.Mode, _selection.ProfileId,
        _roundId, _mapId, _floor, _resources?.Id, _frame?.Stamp.FrameId);
    private void Publish()
    {
        _publishedState = new(Context(), _resources is not null, _roundActive, _mapOpen, _workActive,
            _mapId is not null, _identitySource, _pose is not null, _pose, Array.AsReadOnly(_candidates.ToArray()), _selected,
            _reason, _diagnostics.Clone(), _selection.Season == "s0" ? Array.AsReadOnly(Gaps)
                : Array.AsReadOnly(Gaps.Concat(["s1-floor-vertical-consumer-unavailable", "s1-structure-identity-consumer-unavailable"]).ToArray()))
        { Closed = _closed, ManualFloor = _manualFloor, ObservedFloor = _observedFloor };
        _publishedSnapshot = Snapshot(_publishedState);
        _publishedBindingState = _capture.IsBound;
    }
    private RuntimeSnapshot Snapshot(LiveRuntimeState state)
    {
        var commands = new List<string> { "set_context", "end_round", "stop_round", "reset_round", "cancel", "close_map" };
        if (state.ResourcesBound) commands.AddRange(["start_round", "toggle_round"]);
        if (state.RoundActive && state.ResourcesBound)
        {
            commands.AddRange(["set_floor", "reselect_map"]);
            if (state.Context.Season == "s0") commands.AddRange(["capture_now", "map"]);
            if (state.MapOpen) commands.Add("cycle_floor");
            if (state.Candidates.Count > 0 && !state.IdentityLocked) commands.AddRange(["select_candidate", "previous_candidate", "next_candidate", "confirm_route"]);
        }
        if (state.Closed) commands.Clear();
        return new(state.Context, engineConnected: !state.Closed, commands, state.RoundActive,
            roundWorkActive: state.WorkActive, liveEngineAvailable: !state.Closed && state.ResourcesBound && _capture.IsBound,
            liveAuthorization: false, manualMapPickAvailable: state.RoundActive && state.ResourcesBound && !state.WorkActive,
            message: state.Reason, state: JsonSerializer.SerializeToElement(state, Json));
    }
    private static LiveRuntimeState Withhold(LiveRuntimeState state) => state with
    { Context = state.Context with { FrameId = null }, MapOpen = false, NativeGeometryVerified = false, NativePose = null,
        Candidates = Array.Empty<RuntimeCandidate>(), SelectedCandidateIndex = -1, Reason = "capture-context-obsolete" };
    private static string? Text(JsonElement payload, string key) => payload.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;
    private static bool Flag(JsonElement payload, string key) => payload.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.True;
    private static int Integer(JsonElement payload, string key, int fallback) => payload.TryGetProperty(key, out var value) && value.TryGetInt32(out var number) ? number : fallback;
    private static (bool Valid, string? Floor) FloorTarget(JsonElement payload)
    {
        if (!payload.TryGetProperty("floor", out var value) && !payload.TryGetProperty("floorId", out value)) return (true, null);
        if (value.ValueKind == JsonValueKind.Null) return (true, null);
        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number)) return (true, number.ToString(System.Globalization.CultureInfo.InvariantCulture));
        if (value.ValueKind != JsonValueKind.String) return (false, null);
        var text = value.GetString();
        return (true, text is null or "" or "auto" ? null : text);
    }
    private static Mat ToBgr(CapturedClientFrame frame)
    {
        using var bgra = new Mat(frame.Height, frame.Width, MatType.CV_8UC4);
        for (int row = 0; row < frame.Height; row++)
            Marshal.Copy(frame.Bgra.Slice(row * frame.Stride, frame.Width * 4).ToArray(), 0, bgra.Ptr(row), frame.Width * 4);
        var bgr = new Mat(); Cv2.CvtColor(bgra, bgr, ColorConversionCodes.BGRA2BGR); return bgr;
    }
    private sealed record PreparedReference(SharedStructureData Source, FastPrepared Fast);
    private sealed class BoundResources(ResourceCatalog catalog, EntryPassportCatalog passports, MainEntryRanker ranker)
    {
        public string Id { get; } = Guid.NewGuid().ToString("N");
        public ResourceCatalog Catalog { get; } = catalog;
        public EntryPassportCatalog Passports { get; } = passports;
        public MainEntryRanker Ranker { get; } = ranker;
        public Dictionary<string, PreparedReference> Prepared { get; } = new(StringComparer.Ordinal);
        public static BoundResources Load(string root, SessionSelection selection)
        {
            var catalog = ResourceCatalog.Load(root);
            var passports = EntryPassportCatalog.Load(root, selection.Season, selection.Mode);
            if (!catalog.Entries.Any(e => e.Mode == selection.Mode && e.Season == selection.Season && e.StructureStatus == ResourceBindingStatus.Ready))
                throw new InvalidDataException("context-structure-resources-unavailable");
            return new(catalog, passports, new(passports, catalog));
        }
    }
}
