using System.Runtime.InteropServices;
using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Alignment;
using StarMap.Core.Display;
using StarMap.Core.Selection;
using StarMap.Core.Resources;
using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

/// <summary>
/// Actual managed producer/consumer path. This object alone commits its runtime
/// state under one lock; heavy capture/vision executes outside that lock and is
/// admitted by a serial worker. Invalidation never waits for vision or capture.
/// </summary>
public sealed partial class LiveRuntime : ISessionRuntime, IRuntimeRecoveryControl, ISessionRuntimeProgress, ISessionMaintenanceRuntime
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    private static readonly string[] Gaps = ["steady-tracking-fallback-unavailable",
        "locked-canvas-floor-reuse-unavailable", "native-identity-strong-conflict-producer-unavailable",
        "pose-index-candidate-provider-unavailable",
        "retained-group-fit-context-unavailable", "manual-confirmation-fresh-capture-unavailable",
        "floor-click-intent-policy-unavailable"];
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
    private bool _alignedVisible;
    private long _epoch, _operation;
    private long _progressEpoch;
    private SessionRequestContext? _progressRequest;
    private CancellationToken _progressCancellation;
    private CapturedClientFrame? _frame;
    private NativeGeometryPose? _pose;
    private RuntimeCandidate[] _candidates = [];
    private readonly Dictionary<string, JsonObject> _entryCandidates = new(StringComparer.Ordinal);
    private object _roundFitOwner = new();
    private int _selected = -1;
    private StructureEvidenceSession? _structureSession;
    private StarMap.Core.Selection.StructureCandidatePool? _structurePool;
    private string _recognitionPath = "ordinary";
    private NativeSimilarityState? _nativeIdentity;
    private ObservationTicket? _structureTicket;
    private JsonElement _diagnostics = JsonSerializer.SerializeToElement(new { });
    private LiveRuntimeState _publishedState = null!;
    private RuntimeSnapshot _publishedSnapshot = null!;
    private bool _publishedBindingState;

    public LiveRuntime(ICapturePort capture, string resourceRoot, LiveRuntimeOptions? options = null)
    {
        _capture = capture ?? throw new ArgumentNullException(nameof(capture));
        _resourceRoot = Path.GetFullPath(resourceRoot);
        _options = options is null ? new() : options with { FloorSettings = options.FloorSettings?.Clone() };
        _alignedVisible = _options.SheetVisibilityEnabled;
        _steadySettingEnabled = _options.Idvb0059Enabled;
        if (_options.PreparationTimeout <= TimeSpan.Zero || _options.PreparationInterval <= TimeSpan.Zero
            || _options.MainEntryBudget <= TimeSpan.Zero) throw new ArgumentException("runtime-budget-invalid");
        lock (_sync) Publish();
    }

    // No I/O, state mutation, image decode, or algorithm in either read method.
    public LiveRuntimeState ReadState()
    {
        lock (_sync)
        {
            if (_frame is not null && !_capture.IsObservationContextCurrent(_frame)) return Withhold(_publishedState);
            var recovery = RecoveryStatus();
            return recovery == _publishedState.MapOpenRecovery ? _publishedState
                : _publishedState with { MapOpenRecovery = recovery };
        }
    }

    public RuntimeSnapshot ReadSnapshot()
    {
        lock (_sync)
        {
            if (_frame is not null && !_capture.IsObservationContextCurrent(_frame)) return Snapshot(Withhold(_publishedState));
            return _mapRecovery is null && _capture.IsBound == _publishedBindingState
                ? _publishedSnapshot : Snapshot(_publishedState with { MapOpenRecovery = RecoveryStatus() });
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
    { lock (_sync) { if (_closed) return; if (reason == "window-binding-changed") { ResetSteady(); _geometryGroup = null; } _epoch++; RevokeFrame(reason); Publish(); } }

    /// <summary>Explicit host observation, never a side effect of a read. Like
    /// the original aligned foreground watcher, idle context loss retires the
    /// published cycle; returning to the old geometry cannot revive its grant.</summary>
    public bool InvalidateObsoleteDisplay()
    {
        lock (_sync)
        {
            if (_closed || _workActive || _worker.CurrentCount == 0 || !_mapOpen || _render is null || _frame is null
                || _capture.IsObservationContextCurrent(_frame)) return false;
            _epoch++; RevokeFrame("capture-context-changed"); Publish(); return true;
        }
    }

    public void InvalidateResources(string reason = "resources-invalidated")
    {
        lock (_sync)
        {
            if (_closed) return;
            _epoch++; RevokeFrame(reason); _resources = null; _roundActive = false; _roundId = null; _structurePool = null;
            ClearIdentity(); ClearFloor(); Publish();
        }
    }

    public void CancelMapOpenRecovery(string reason) => InvalidateCapture(reason);

    /// <summary>Presentation preference only; never supersedes an admitted capture/fit or its material.</summary>
    public bool SetAlignedVisibility(bool visible)
    {
        lock (_sync)
        {
            if (_closed) return false;
            if (_alignedVisible != visible) { _alignedVisible = visible; Publish(); }
            return true;
        }
    }

    /// <summary>Final revocation only; the caller separately owns capture-port shutdown.</summary>
    public void Shutdown()
    {
        lock (_sync)
        {
            if (_closed) return;
            _closed = true; _epoch++; RevokeFrame("runtime-closed"); ClearIdentity();
            _roundActive = false; _roundId = null; _structurePool = null; ClearFloor(); _resources = null;
            if (_worker.CurrentCount > 0) DisposeStructureIndex(); Publish();
        }
    }

    public async ValueTask<RuntimeOperationResult> DispatchAsync(SessionRuntimeRequest request, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(request);
        try
        {
            if (PassiveObservation(request.Action))
            {
                if (!await _worker.WaitAsync(0, cancellationToken).ConfigureAwait(false))
                    return Receipt(request, true, request.Action=="refresh_map"?"refresh-coalesced":"floor-observation-coalesced");
            }
            else await _worker.WaitAsync(cancellationToken).ConfigureAwait(false);
        }
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
                {
                    if (request.Action == "select_group_member") DiscardGroupMaterial("group-choice-cancelled");
                    else { _epoch++; RevokeFrame("cancelled"); }
                    Publish();
                }
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
            ReleaseRuntimeWorker();
        }
    }

    private void ReleaseRuntimeWorker()
    {
        lock (_sync)
        {
            if (_closed) DisposeStructureIndex();
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
                if ((request.Action is "capture_now" or "observe_manual_floor") && request.Payload.TryGetProperty("inputObservationSequence", out var observationSequence))
                {
                    if (observationSequence.ValueKind != JsonValueKind.Number || !observationSequence.TryGetInt64(out var sequence)
                        || sequence < _lastMapInputSequence) return Receipt(request, false, "map-input-sequence-expired");
                    _lastMapInputSequence = sequence;
                }
            }
            return request.Action switch
            {
                "set_context" => SetContext(request, epoch, cancellationToken),
                "toggle_sheet_visibility" => ToggleSheetVisibility(request),
                "start_round" => StartRound(request, epoch, cancellationToken),
                "toggle_round" => IsRoundActive() ? EndRound(request) : StartRound(request, epoch, cancellationToken),
                "end_round" or "stop_round" or "reset_round" or "cancel" => EndRound(request),
                "close_map" => CloseMap(request),
                "map" => await MapInput(request, epoch, cancellationToken).ConfigureAwait(false),
                "capture_now" => await CaptureAndSolve(request, epoch, cancellationToken).ConfigureAwait(false),
                "recover_map_open" => await RecoverMapOpen(request, epoch, cancellationToken).ConfigureAwait(false),
                "refresh_map" => await RefreshMap(request, epoch, cancellationToken).ConfigureAwait(false),
                "observe_manual_floor" => await ObserveS1Floor(request,epoch,cancellationToken,true).ConfigureAwait(false),
                "observe_candidate_floor" => await ObserveS1Floor(request,epoch,cancellationToken,false).ConfigureAwait(false),
                "confirm_route" => ConfirmMap(request, epoch, cancellationToken),
                "select_group_member" => SelectGroupMember(request, epoch, cancellationToken),
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
            if (desired.ProfileId != _selection.ProfileId)
            { _displayGeneration++; DiscardGroupMaterial("group-author-preference-changed"); }
            _selection = desired; _reason = "context-bound"; Publish();
        }
        bool reprojected = !reload && RefreshDisplayPreference(epoch, token, desired.ProfileId);
        return Receipt(request, true, reprojected ? "route-display-reprojected" : "context-bound");
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
                _probeMisses = 0;
                _structureSession = _resources.StructureCatalog is { } catalog ? new(catalog, _roundId) : null;
                _structurePool = _resources.StructureCatalog is { } structure
                    ? new(_roundId, structure.ResourceKey, structure.MapIds) : null;
                _recognitionPath = "ordinary";
                _structureIndexEntered = false;
            }
            _reason = "round-started"; Publish(); return Receipt(request, true, _reason);
        }
    }

    private RuntimeOperationResult EndRound(SessionRuntimeRequest request)
    {
        lock (_sync)
        {
            AdvanceOwnedEpoch(); RevokeFrame("round-ended"); ClearIdentity(); _roundActive = false;
            _roundId = null; _structurePool = null; ClearFloor(); Publish(); return Receipt(request, true, _reason);
        }
    }

    private RuntimeOperationResult CloseMap(SessionRuntimeRequest request)
    {
        lock (_sync) {
            if (request.Payload.TryGetProperty("inputSequence", out var value) && value.ValueKind == JsonValueKind.Number
                && value.TryGetInt64(out var sequence))
                _lastMapInputSequence = Math.Max(_lastMapInputSequence, sequence);
            AdvanceOwnedEpoch(); RevokeFrame("map-closed"); Publish(); return Receipt(request, true, _reason); }
    }

    private RuntimeOperationResult ToggleSheetVisibility(SessionRuntimeRequest request)
    {
        lock (_sync)
        {
            _alignedVisible = !_alignedVisible; Publish();
            return Receipt(request, true, "visibility-toggled");
        }
    }

    private async ValueTask<RuntimeOperationResult> CaptureAndSolve(SessionRuntimeRequest request, long epoch,
        CancellationToken token, RecoveryTicket? recovery = null)
    {
        BoundResources bound;
        CaptureBindingScope? captureBinding;
        bool wasOpen;
        bool s1Manual;
        bool preparing = recovery is null && (request.Action == "map" || Flag(request.Payload, "foregroundReturn"));
        lock (_sync)
        {
            RequireCurrent(epoch, token);
            if (!_roundActive) return Receipt(request, false, "start-round-before-capture");
            if (_resources is null) return Receipt(request, false, "resources-not-bound");
            if (!_capture.IsBound) return Receipt(request, false, "capture-not-bound");
            bound = _resources;
            s1Manual=S1FloorObserverAvailable && S1ManualIdentity;
            captureBinding = _capture.BindingScope;
            wasOpen = _mapOpen;
            if (request.Action is "refresh_map" or "observe_manual_floor") _reason = "map-refreshing";
            else RevokeFrame("map-open-preparing", retainRecovery: recovery is not null);
            _workActive = true; Publish();
        }
        var started = _options.TimeProvider.GetTimestamp();
        var limit = recovery is not null ? RecoveryLifetime - _options.TimeProvider.GetElapsedTime(recovery.Started)
            : preparing ? _options.PreparationTimeout : TimeSpan.MaxValue;
        var period = _options.PreparationInterval;
        bool locked;
        lock (_sync) locked = _mapId is not null;
        var nextProbe = !preparing || locked ? TimeSpan.Zero : period;
        int maxProbes = preparing ? Math.Max(1, checked((int)Math.Ceiling(limit.TotalSeconds / period.TotalSeconds))) : 1;
        CapturedClientFrame? captured = null;
        FloorObservationResult? floorRead = null;
        string? observedFloor = null;
        int probes = 0;
        double captureMs = 0, decodeMs = 0, observationMs = 0;
        string? capturedFrameId = null;
        object Preparation(string phase) => new {
            stage = "map-open-preparation", phase, probes, firstProbeImmediate = locked,
            elapsedMs = _options.TimeProvider.GetElapsedTime(started).TotalMilliseconds,
            budgetMs = limit.TotalMilliseconds, captureMs, decodeMs, observationMs,
            capturedFrameId, map = floorRead?.MapObservation, floor = floorRead?.FloorRead
        };
        RuntimeOperationResult Unprepared(string phase)
        {
            if (_capture.BindingScope != captureBinding)
                return FailCurrent(request, epoch, "capture-context-obsolete", Preparation("binding-changed"));
            if (request.Action=="observe_manual_floor")
                return S1ManualObservationMiss(request,epoch,token,captured,bound,floorRead,Preparation(phase));
            if (request.Action == "refresh_map")
                return PassiveProbeMiss(request, epoch, token, captured, bound, floorRead, Preparation(phase));
            if (recovery is null)
                return preparing ? PreparationFailed(request, epoch, Preparation(phase))
                    : FailCurrent(request, epoch, "map-not-observed", Preparation(phase));
            lock (_sync)
            {
                RequireCurrent(epoch, token);
                if (!ReferenceEquals(_mapRecovery, recovery) || recovery.Scope != CurrentRecoveryScope())
                    return Receipt(request, false, "cancelled-or-stale");
                bool pending = recovery.Remaining > 0 && _options.TimeProvider.GetElapsedTime(recovery.Started) < RecoveryLifetime;
                if (!pending) _mapRecovery = null;
                _workActive = false; _reason = pending ? "map-open-recovery-pending" : "map-open-recovery-exhausted";
                _diagnostics = JsonSerializer.SerializeToElement(Preparation(phase), Json); Publish();
                return Receipt(request, false, _reason);
            }
        }
        for (int attempt = 0; attempt < maxProbes; attempt++)
        {
            CheckCurrent(epoch, token);
            if (_capture.BindingScope != captureBinding)
                return FailCurrent(request, epoch, "capture-context-obsolete", Preparation("binding-changed"));
            if (recovery is not null)
                lock (_sync)
                    if (!ReferenceEquals(_mapRecovery, recovery) || recovery.Scope != CurrentRecoveryScope())
                        return Receipt(request, false, "cancelled-or-stale");
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
            // Initial preparation still observes a late current capture: the
            // original host retains a detected map with unknown floor for
            // manual correction. A recovery probe has no such expiry exception.
            if (!preparing && _options.TimeProvider.GetElapsedTime(started) >= limit)
                return Unprepared("capture-finished-after-deadline");
            if (acquisition.Frame is null) return recovery is not null ? Unprepared("capture-unavailable")
                : FailCurrent(request, epoch, acquisition.Reason.Length > 0 ? acquisition.Reason : "capture-unavailable", Preparation("capture-unavailable"));
            captured = acquisition.Frame;
            if (_capture.BindingScope != captureBinding || !_capture.IsCurrent(captured.Stamp))
                return FailCurrent(request, epoch, "capture-context-obsolete", Preparation("capture-obsolete"));
            stageStarted = Stopwatch.GetTimestamp();
            using var frame = ToBgr(captured);
            decodeMs = Stopwatch.GetElapsedTime(stageStarted).TotalMilliseconds;
            stageStarted = Stopwatch.GetTimestamp();
            floorRead = FloorObservation.Observe(frame, bound.Passports.Season, bound.Passports.Mode,
                _options.FloorSettings is { } settings ? JsonNode.Parse(settings.GetRawText())?.AsObject() : null,
                s1Manual ? S1FloorUse.ManualMap : bound.StructureCatalog is not null ? S1FloorUse.StructureCandidate : _options.S1FloorUse, frameCurrent: true);
            observationMs = Stopwatch.GetElapsedTime(stageStarted).TotalMilliseconds;
            lock (_sync)
            {
                RequireCurrent(epoch, token, captured, bound);
            }
            // Preparation classifies raw current evidence only. The manual
            // override/expiry policy belongs to an accepted observation below;
            // a late rejected usable frame must not clear the user's floor.
            observedFloor = floorRead.AutomaticEligible ? floorRead.FloorId : null;
            // A positive map with unreadable floor is retained for manual
            // correction, not treated as a missing-map recovery opportunity.
            // Late usable geometry still cannot pass the preparation deadline.
            if (_options.TimeProvider.GetElapsedTime(started) >= limit)
            {
                if (preparing && observedFloor is null && floorRead.MapObservation["detected"]?.GetValue<bool>() == true) break;
                return Unprepared("observation-finished-after-deadline");
            }
            if (floorRead.MapOpen == true && observedFloor is not null) break;
            nextProbe = probeStarted + period;
            if (attempt == maxProbes - 2)
            {
                var work = _options.TimeProvider.GetElapsedTime(started) - probeStarted;
                var lastProbe = limit - TimeSpan.FromMilliseconds(Math.Min(period.TotalMilliseconds, Math.Max(1, work.TotalMilliseconds)));
                if (lastProbe > nextProbe) nextProbe = lastProbe;
            }
        }
        bool mapObserved = bound.Passports.Mode == "difficult" ? floorRead?.MapOpen == true
            : floorRead?.MapObservation["detected"]?.GetValue<bool>() == true || floorRead?.MapOpen == true;
        if (captured is null || floorRead is null || !mapObserved) return Unprepared("probe-loop-ended-without-map");
        var preparation = JsonSerializer.SerializeToElement(Preparation("prepared"), Json);
        string? floor, map;
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            if (recovery is not null && (!ReferenceEquals(_mapRecovery, recovery) || recovery.Scope != CurrentRecoveryScope()))
                return Receipt(request, false, "cancelled-or-stale");
            if(s1Manual)
            {
                // The original dedicated S1 observer never inherits an old
                // operator floor and retires it on the first eligible read.
                _observedFloor=floorRead.MapOpen==true && floorRead.AutomaticEligible?floorRead.FloorId:null;
                if(_observedFloor is not null){_manualFloor=null;_manualFloorStarted=null;}
                floor=_observedFloor;
            }
            else floor = ResolveObservedFloor(floorRead);
        }
        if ((request.Action is "refresh_map" or "observe_manual_floor") && floor is not null)
        {
            var renewed = TryRenewCurrentPose(request, epoch, token, captured, bound, floor, floorRead, preparation);
            if (renewed is not null) return renewed.Receipt.Success
                ? RetryDesiredGroupChoice(request, epoch, token, renewed) : renewed;
        }
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            if (request.Action is "refresh_map" or "observe_manual_floor")
            {
                RevokeDisplay(floor is null ? "current-floor-unavailable" : "new-capture");
                _entryCandidates.Clear();
                _structureSession?.RevokeCurrent(); _structureTicket = null;
            }
            if(s1Manual && (floor is null || floor!=_floor)){ResetSteady();_geometryGroup=null;}
            if (observedFloor is not null) _probeMisses = 0;
            if (!wasOpen) _mapCycle++;
            _frame = captured; _mapOpen = true; _floor = floor; _pose = null;
            _diagnostics = JsonSerializer.SerializeToElement(new { stage = "floor-observation", map = floorRead.MapObservation, floor = floorRead.FloorRead, preparation }, Json);
            _reason = floor is null ? s1Manual?"s1-current-floor-unavailable":"current-floor-unavailable" : "current-floor-observed";
            map = _mapId;
            // The recovered observation now owns this opening. Clearing only
            // its ticket must not revoke the frame/material it is about to fit.
            if (recovery is not null) _mapRecovery = null;
            Publish();
        }
        if (floor is null) return Receipt(request, false, s1Manual?"s1-current-floor-unavailable":"current-floor-unavailable");
        using var current = ToBgr(captured);
        // Original Runtime re-enters identity ranking only when no map is
        // selected. A provisional group display member also owns this slot;
        // reopening with less structure must not restart discovery implicitly.
        if (map is null || IsStructureProvisional)
        {
            if (bound.StructureCatalog is not null)
                return ScanStructure(request, epoch, token, captured, bound, floor, current, preparation);
            if (bound.Passports.Mode == "difficult" && floor == "2")
            {
                lock (_sync)
                {
                    RequireCurrent(epoch, token, captured, bound);
                    _reason = "difficult-f2-entry-recognition-disabled"; Publish();
                    return Receipt(request, false, _reason);
                }
            }
            map = RankS0(epoch, token, captured, bound, floor, current, preparation);
        }
        if (map is null) return Receipt(request, false, "candidates-awaiting-confirmation");
        return RetryDesiredGroupChoice(request, epoch, token, Fit(request, epoch, token, captured, bound, map, floor, current));
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
            if (_structurePool is { } pool && !pool.Remaining.Contains(map))
                return Receipt(request, false, "map-excluded-this-round");
            LockIdentity(map, "manual");
            // Identity commits before any preparation/fit, and survives fit failure.
            RevokeDisplay("identity-confirmed-pose-pending"); _pose = null; _reason = "identity-locked-pose-pending";
            captured = _mapOpen ? _frame : null; floor = _floor;
            _workActive = captured is not null && floor is not null && _capture.IsCurrent(captured.Stamp); Publish();
        }
        if (captured is null || floor is null || !_capture.IsCurrent(captured.Stamp)) return Receipt(request, true, "identity-locked-pose-pending");
        using var frame = ToBgr(captured);
        return Fit(request, epoch, token, captured, bound, map, floor, frame);
    }

    private sealed record PreparedRuntimeFit(ResourceCatalogEntry Entry, JsonObject Result, double[][]? Matrix,
        PreparedRuntimeDisplay? Display, JsonArray? Stages, JsonArray? Events, JsonObject SteadyDiagnostic);

    // Produces a candidate without changing the current identity, pose or pixels.
    // Ordinary fits and a staged group choice consume this same numerical path.
    private PreparedRuntimeFit? PrepareFit(long epoch, CancellationToken token,
        CapturedClientFrame captured, BoundResources bound, string map, string floor, Mat frame)
    {
        CheckCurrent(epoch, token, captured, bound);
        var profile = bound.Passports.MapProfiles[map];
        var entry = bound.Catalog.Find(bound.Passports.Mode, bound.Passports.Season, profile, map, floor);
        if (entry is null) return null;
        JsonObject? entryCandidate; object owner; string preferred;
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            owner = _roundFitOwner; preferred = _selection.ProfileId;
            entryCandidate = _entryCandidates.GetValueOrDefault(map)?.DeepClone().AsObject();
        }
        JsonObject fit; SharedStructureData? source; JsonArray? stages = null, events = null;
        PreparedSteadyFit? steady = null;
        JsonObject steadyDiagnostic = new() { ["attempted"] = false, ["reason"] = "steady-disabled" };
        if (bound.StructureCatalog is not null)
        {
            // client_live_runtime._solve_observation calls the S1 Host with
            // explicit full-client bounds; that path does not request steady.
            steadyDiagnostic["reason"] = "explicit-domain-independent-fit";
            var pipeline = GetStructureFits(bound);
            fit = pipeline.Fit(frame, entry, () => Obsolete(epoch, token, captured, bound));
            source = StructureEvidence.VerifiedPose(fit) ? bound.LoadReference(entry).Value : null;
        }
        else
        {
            var group = GroupForFit(epoch, owner, captured, bound, entry);
            S0HostFit? targetHost = null;
            var services = new S0HostFitServices(Steady: context =>
            {
                steady = TrySteady(epoch, token, captured, bound, entry, context.Reference, frame, preferred,
                    context.CanonicalPackId, out steadyDiagnostic);
                return steady?.Fit;
            }, EstablishedGroup: context => context.Options.Group?.Geometry is null
                ? new JsonObject { ["success"] = false, ["reason"] = "structure-no-reliable-scale" }
                : targetHost!.EvaluateEstablishedGroup(context).Fit);
            using var host = targetHost = new S0HostFit(bound.Catalog, bound.Passports, services, bound.LoadReference);
            var result = host.Fit(frame, entry, entryCandidate, new() { OwnerScope = owner, Epoch = epoch, Group = group,
                SteadyRequested = SteadyEnabled },
                () => Obsolete(epoch, token, captured, bound), token);
            fit = result.Fit; source = result.Reference; stages = result.Stages; events = result.Events;
        }
        bool verified = StructureEvidence.VerifiedPose(fit);
        var matrix = verified ? fit["matrixSourceToClient"]!.Deserialize<double[][]>()! : null;
        var display = verified && source is not null ? steady?.Display ?? PrepareDisplay(bound, entry, matrix!, source, preferred, token) : null;
        return new(entry, fit, matrix, display, stages, events, steadyDiagnostic);
    }

    private RuntimeOperationResult Fit(SessionRuntimeRequest request, long epoch, CancellationToken token,
        CapturedClientFrame captured, BoundResources bound, string map, string floor, Mat frame, PreparedRuntimeFit? preparedFit = null)
    {
        var prepared = preparedFit ?? PrepareFit(epoch, token, captured, bound, map, floor, frame);
        if (prepared is null) return FailCurrent(request, epoch, "target-floor-resource-unavailable");
        var (entry, fit, matrix, display, stages, events, steadyDiagnostic) = prepared;
        bool verified = matrix is not null;
        if(verified && display is not null) display=display with { Scene=PrepareMapScene(bound,entry,display,token) };
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            var proposedPose = verified ? new NativeGeometryPose(map, floor, bound.Id, captured.Stamp.FrameId, matrix!) : null;
            string? commitFailure = null;
            if (proposedPose is not null && display is not null)
            {
                var commit = PrepareCanonicalCommit(display, ReadResidualEvidence(fit["selectedRouteCurrentFrameResidual"]!), captured, map, floor, _alignment);
                if (commit.Grant is not null) { _pose = proposedPose; ApplyCanonicalCommit(commit); }
                else { commitFailure = commit.Display.Reason; _pose = null; RevokeDisplay(commitFailure); verified = false; }
            }
            else { _pose = null; RevokeDisplay("target-floor-pose-unverified"); }
            _reason = commitFailure ?? fit["reason"]?.GetValue<string>() ?? (verified ? "current-frame-structure-verified" : "structure-fit-unavailable");
            var main = _diagnostics.TryGetProperty("stage", out var stage) && stage.GetString() == "main-entry"
                ? _diagnostics : JsonSerializer.SerializeToElement(new { });
            JsonElement? floorEvidence = _diagnostics.TryGetProperty("floor", out var currentFloor) ? currentFloor : null;
            _diagnostics = JsonSerializer.SerializeToElement(new { stage = bound.StructureCatalog is null ? "s0-host-fit" : "shared-fit", identitySource = _identitySource,
                floor = floorEvidence,
                nativeReferenceKey = entry.StableKey, priorUsed = stages?.Any(row => row?["stage"]?.ToString() == "fast-prior") == true,
                fit, stages, events, main, steadyAttempt = steadyDiagnostic, geometryGroup = _geometryGroup?.Model.Snapshot() }, Json);
            Publish();
            return Receipt(request, verified, _reason);
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
        if (_mapId != mapId) { ResetSteady(); _geometryGroup = null; RevokeDisplay("identity-changed"); _alignment = null; }
        if (source is not ("group-default" or "group-manual")) { ClearGroupChoice(); _nativeIdentity = null; }
        _mapId = mapId; _s1ProvisionalMapId = null; _identitySource = source;
    }
    private void ClearIdentity()
    { ResetSteady(); _geometryGroup = null; ClearGroupChoice(); _mapId = null; _s1ProvisionalMapId = null; _identitySource = ""; _nativeIdentity = null; _structureSession?.EndRound(); _structureSession = null; _structureTicket = null; _alignment = null; _roundFitOwner = new(); }
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
    private void RevokeFrame(string reason, bool retainRecovery = false)
    { _frame = null; _pose = null; _mapOpen = false; _workActive = false; _candidates = []; _selected = -1; _reason = reason;
        if (!retainRecovery) _mapRecovery = null;
        RevokeDisplay(reason);
        _entryCandidates.Clear();
        _structureSession?.RevokeCurrent(); _structureTicket = null; _diagnostics = JsonSerializer.SerializeToElement(new { }); }
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
            { _pose = null; RevokeDisplay(reason); _workActive = false; _reason = reason; if (diagnostic is not null) _diagnostics = JsonSerializer.SerializeToElement(diagnostic, Json); Publish(); }
            return Receipt(request, false, reason);
        }
    }
    private RuntimeOperationResult Receipt(SessionRuntimeRequest request, bool success, string reason, string? message = null)
    {
        lock (_sync)
        {
            var snapshot = ReadSnapshot();
            return new(new(request.Action, success, request.Context, snapshot.Context, reason, message ?? reason), snapshot);
        }
    }
    private RuntimeContext Context() => new(_selection.Season, _selection.Mode, _selection.ProfileId,
        _roundId, _mapId, _floor, _resources?.Id, _frame?.Stamp.FrameId);
    private void Publish()
    {
        _publishedState = new(Context(), _resources is not null, _roundActive, _mapOpen, _workActive,
            _mapId is not null && !IsStructureProvisional, _identitySource, _pose is not null, _pose, Array.AsReadOnly(_candidates.ToArray()), _selected,
            _reason, _diagnostics.Clone(), Array.AsReadOnly(Gaps))
        { Closed = _closed, ManualFloor = _manualFloor, ObservedFloor = _observedFloor, Display = _display, AlignedVisible = _alignedVisible,
            MapOpenRecovery = RecoveryStatus(),
            RemainingMapIds = _structurePool?.Remaining ?? Array.Empty<string>(),
            EliminatedMapIds = _structurePool?.Eliminated ?? Array.Empty<string>(), RecognitionPath = _recognitionPath,
            ProvisionalMapId = _s1ProvisionalMapId,
            ProbeMisses = _probeMisses,
            IdentityKind = _identitySource == "group-default" ? "group" : IsStructureProvisional ? "candidate" : _mapId is null ? "unresolved" : "map",
            MapDisplayName = _resources?.Passports.Maps.FirstOrDefault(map => map.MapId == EffectiveMapId)?.Name,
            IdentityGroupId = HasRegisteredGroup ? _nativeIdentity!.GroupId : null,
            IdentityMemberIds = HasRegisteredGroup ? _nativeIdentity!.MemberIds : Array.Empty<string>(), GroupChoice = ReadGroupChoice() };
        _publishedSnapshot = Snapshot(_publishedState);
        _publishedBindingState = _capture.IsBound;
    }
    private RuntimeSnapshot Snapshot(LiveRuntimeState state)
    {
        var commands = new List<string> { "set_context", "end_round", "stop_round", "reset_round", "cancel", "close_map", "toggle_sheet_visibility" };
        if (state.ResourcesBound) commands.AddRange(["start_round", "toggle_round"]);
        if (state.RoundActive && state.ResourcesBound)
        {
            commands.AddRange(["set_floor", "reselect_map"]);
            commands.AddRange(["capture_now", "map"]);
            if (state.MapOpenRecovery.Pending) commands.Add("recover_map_open");
            if (state.MapOpen) commands.AddRange(["cycle_floor", "refresh_map"]);
            if(S1FloorObserverAvailable)
            {
                if(S1ManualIdentity && (state.MapOpen || state.MapOpenRecovery.Pending))commands.Add("observe_manual_floor");
                else if(!S1ManualIdentity && state.MapOpen)commands.Add("observe_candidate_floor");
            }
            if (state.Candidates.Count > 0 && !state.IdentityLocked) commands.AddRange(["select_candidate", "previous_candidate", "next_candidate", "confirm_route"]);
            if (state.IdentityMemberIds.Count > 1 && state.Context.FloorId is not null) commands.Add("select_group_member");
        }
        if (state.Closed) commands.Clear();
        return new(state.Context, engineConnected: !state.Closed, commands, state.RoundActive,
            roundWorkActive: state.WorkActive, liveEngineAvailable: !state.Closed && state.ResourcesBound && _capture.IsBound,
            liveAuthorization: RenderCurrent() && _render!.IsLive, manualMapPickAvailable: state.RoundActive && state.ResourcesBound && !state.WorkActive,
            message: state.Reason, state: JsonSerializer.SerializeToElement(state, Json));
    }
    private static LiveRuntimeState Withhold(LiveRuntimeState state) => state with
    { Context = state.Context with { FrameId = null }, MapOpen = false, NativeGeometryVerified = false, NativePose = null,
        Candidates = Array.Empty<RuntimeCandidate>(), SelectedCandidateIndex = -1, Reason = "capture-context-obsolete",
        Display = new(false, false, false, "capture-context-obsolete") };
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
    private sealed class BoundResources(ResourceCatalog catalog, EntryPassportCatalog passports, S0IdentityRankPipeline? identity, StructureCatalog? structureCatalog)
    {
        private readonly object referenceSync = new();
        private readonly LinkedList<(string Key, SharedStructureData Reference)> references = new();
        private readonly Lazy<AuthorCoordinateAdapter> coordinates = new(() => ReadCoordinates(passports));
        public AuthorCoordinateAdapter Coordinates => coordinates.Value;
        public string Id { get; } = Guid.NewGuid().ToString("N");
        public ResourceCatalog Catalog { get; } = catalog;
        public DisplayMaterialPreparer DisplayMaterials { get; } = new(Math.Max(1,passports.Floors.Count*2));
        public List<RuntimeSceneSources> Scenes { get; } = [];
        public EntryPassportCatalog Passports { get; } = passports;
        public S0IdentityRankPipeline? Identity { get; } = identity;
        public StructureCatalog? StructureCatalog { get; } = structureCatalog;
        private static AuthorCoordinateAdapter ReadCoordinates(EntryPassportCatalog passports)
        {
            string relative = "references/nightmare-canonical-coordinate-pack.json";
            if (passports.Mode != "nightmare")
            {
                using var manifest = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(passports.ModeRoot, "difficult-native-manifest.json")));
                relative = manifest.RootElement.GetProperty("runtimeInterfaces").GetProperty("coordinatePack").GetString()!;
            }
            string root = Path.GetFullPath(passports.ModeRoot), path = Path.GetFullPath(Path.Combine(root, relative));
            if (Path.IsPathRooted(relative) || !path.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("canonical-coordinate-resource-outside-context");
            using var document = JsonDocument.Parse(File.ReadAllBytes(path)); return new(document.RootElement);
        }
        public ResourceLoadResult<SharedStructureData> LoadReference(ResourceCatalogEntry entry)
        {
            // Like the original SharedReferenceProvider: declarations belong to
            // this resource instance, and only six typed references stay hot.
            if (!Catalog.Entries.Any(row => ReferenceEquals(row, entry))) return Catalog.LoadSharedStructure(entry);
            lock (referenceSync)
            {
                for (var node = references.First; node is not null; node = node.Next)
                    if (node.Value.Key == entry.StableKey)
                    {
                        var hit = node.Value; references.Remove(node); references.AddLast(hit);
                        return new(hit.Reference, null);
                    }
                var loaded = Catalog.LoadSharedStructure(entry);
                if (loaded.Value is { } reference)
                {
                    references.AddLast((entry.StableKey, reference));
                    if (references.Count > 6) references.RemoveFirst();
                }
                return loaded;
            }
        }
        public static BoundResources Load(string root, SessionSelection selection)
        {
            var catalog = ResourceCatalog.Load(root);
            var passports = EntryPassportCatalog.Load(root, selection.Season, selection.Mode);
            if (!catalog.Entries.Any(e => e.Mode == selection.Mode && e.Season == selection.Season && e.StructureStatus == ResourceBindingStatus.Ready))
                throw new InvalidDataException("context-structure-resources-unavailable");
            FloorObservation.PrepareResources(selection.Season);
            return new(catalog, passports, selection.Season == "s0" ? new(catalog, passports) : null,
                selection.Season == "s1" ? StructureCatalog.FromResources(catalog, selection.Mode) : null);
        }
    }
}
