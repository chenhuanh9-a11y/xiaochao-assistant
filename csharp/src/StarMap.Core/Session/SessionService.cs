using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Reliability;

namespace StarMap.Core.Session;

/// <summary>
/// Optional atomic bounded read of a snapshot and its attribution to the exact
/// request that produced it. This only preserves that request while publishing its own
/// progress; it grants no identity/pose authority and never replaces final
/// receipt/request/result-context checks. External invalidation must revoke it.
/// </summary>
public interface ISessionRuntimeProgress
{
    SessionRuntimeRead ReadSnapshotFor(SessionRequestContext request);
}

/// <summary>Attribution is true at this read's linearization point, never a continuing authorization.</summary>
public sealed record SessionRuntimeRead(RuntimeSnapshot Snapshot, bool OwnedByRequest);

/// <summary>
/// Runtime ingress ported from experiments/qt_quick/client_session.py. All state
/// mutations and notifications belong to the injected owner dispatcher. At most
/// one runtime call executes, even after cancellation or timeout. The queue holds
/// one latest foreground intent and at most two private material operations.
/// Nothing here starts capture, a round, a window listener, or a live runtime.
/// </summary>
public sealed class SessionService
{
    private readonly ISessionRuntime? _runtime;
    private readonly ISessionDispatcher _dispatcher;
    private readonly TimeProvider _time;
    private readonly Func<SessionSelection, bool> _selectionAllowed;
    private readonly SessionSequencer _sequencer = new();
    private readonly ResultGate _gate;
    private readonly ImmediateUiActionStateMachine _actions = new();
    private readonly List<Work> _queue = [];
    private readonly Queue<Action> _effects = new();
    private int _mutationDepth;
    private bool _flushingEffects;
    private RuntimeSnapshot _state = new(new());
    private SessionSelection _desired = new();
    private Work? _active;
    private Work? _foreground;
    private string? _lease;
    private string _message = "Live engine is not attached.";
    private OperationReceipt? _lastReceipt;
    private SessionFailure? _lastFailure;
    private long _staleResults;
    private TaskCompletionSource _drained = CompletedDrain();

    public SessionService(ISessionDispatcher dispatcher, ISessionRuntime? runtime = null,
        TimeProvider? timeProvider = null, Func<SessionSelection, bool>? selectionAllowed = null)
    {
        _dispatcher = dispatcher ?? throw new ArgumentNullException(nameof(dispatcher));
        _runtime = runtime;
        _time = timeProvider ?? TimeProvider.System;
        // Season registration belongs to the resource/context provider. The
        // defaults mirror the currently registered source seasons, not assets.
        _selectionAllowed = selectionAllowed ?? (s => s.Season is "s0" or "s1"
            && (s.Mode is "nightmare" or "difficult" || s.Season == "s1" && s.Mode == "difficulty2"));
        _gate = new(_sequencer);
        VerifyOwner();
        if (runtime is not null) RefreshRuntime();
    }

    public event Action? Changed;
    public event Action<SessionRuntimeRequest>? ActionRequested;
    public event Action<SessionSelection>? ContextFinished;
    public event Action<SessionCompletion>? ResultConsumed;
    public event Action<SessionFailure>? MaintenanceFailed;

    public SessionView Read()
    {
        VerifyOwner();
        var sequence = _sequencer.Read();
        var contextPending = _desired != _state.Context.Selection;
        return new(sequence, _desired, _state.Context.Selection,
            sequence.Closed ? _state.Withheld(disconnected: true) : contextPending ? _state.Withheld() : _state,
            contextPending, _foreground is not null, _foreground?.Action ?? "",
            _active is not null, _queue.Count, _lease is not null, _message,
            _lastReceipt, _lastFailure, _staleResults, _actions.ReadAll());
    }

    /// <summary>Refresh on the owner, irrespective of whether any page is visible.</summary>
    public void RefreshRuntime()
    {
        VerifyOwner();
        using var mutation = BeginMutation();
        if (Closed || _runtime is null) return;
        try
        {
            var work = _foreground;
            if (work is not null && _active == work && work.Request is not null && IsCurrentForeground(work)
                && _runtime is ISessionRuntimeProgress progress)
            {
                // Snapshot and attribution must come from one producer read.
                // A second ownership query could race the next owned context.
                var observed = progress.ReadSnapshotFor(work.Request.Context);
                if (!observed.OwnedByRequest)
                {
                    // Once accepted, progress must remain attributable until
                    // completion. An epoch-only revocation can keep all visible
                    // context fields equal (for example an already-null frame).
                    if (work.ProgressContext is not null && IsCurrentForeground(work))
                        Invalidate("runtime-progress-revoked");
                    ApplyObserved(observed.Snapshot);
                }
                else if (!ApplyOwnedProgress(work, observed.Snapshot))
                    ApplyObserved(observed.Snapshot);
            }
            else ApplyObserved(_runtime.ReadSnapshot());
            _message = _state.Message.Length > 0 ? _state.Message : "Runtime ready.";
        }
        catch (Exception error)
        {
            Invalidate("runtime-snapshot-failed");
            _state = _state.Withheld(disconnected: true);
            _desired = _state.Context.Selection;
            _lastFailure = new("", "snapshot", Describe(error));
            _message = "Runtime snapshot unavailable: " + Describe(error);
        }
        Notify();
    }

    public SessionDispatchResult Dispatch(string action, JsonElement? payload = null, TimeSpan? timeout = null)
    {
        VerifyOwner();
        SessionDispatchResult result;
        using (BeginMutation()) result = DispatchCore(action, payload, timeout);
        // Cancellation/notification callbacks run at commit and can replace this
        // intent or close the session before the synchronous API returns.
        if (Closed) return new(SessionDispatchDisposition.Closed);
        if (result.Token is { } token && !_actions.AcceptsBackgroundResult(token))
            return new(SessionDispatchDisposition.Superseded, token);
        return result;
    }

    private SessionDispatchResult DispatchCore(string action, JsonElement? payload, TimeSpan? timeout)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(action);
        if (timeout is { } duration && duration <= TimeSpan.Zero) throw new ArgumentOutOfRangeException(nameof(timeout));
        var data = payload is { } value && value.ValueKind != JsonValueKind.Undefined
            ? value.Clone() : JsonSerializer.SerializeToElement(new { });
        if (data.ValueKind != JsonValueKind.Object) throw new ArgumentException("payload must be an object", nameof(payload));
        if (Closed) return new(SessionDispatchDisposition.Closed);
        if (_lease is not null) return Reject(SessionDispatchDisposition.ResourceLocked, "Resource operation is in progress.");
        if (!Available(action, data)) return Reject(SessionDispatchDisposition.Unavailable, "Unavailable command: " + action);

        // A terminal command superseding set_context must use the actual applied
        // selection. Do not retain its cancelled desired selection or old grant.
        if (_foreground is { Action: "set_context" } && action != "set_context")
        {
            Invalidate("context-superseded");
            RestoreAppliedContext();
            if (!Available(action, data)) return Reject(SessionDispatchDisposition.Unavailable,
                "Unavailable command after context cancellation: " + action);
        }

        var target = new ActionTarget(CurrentContext(), data);
        var existing = _actions.Snapshot(action);
        if (existing is not null && Equals(existing.TargetState, target) && _actions.AcceptsBackgroundResult(existing.Token))
        {
            var coalesced = _actions.Begin(action, target, "requested");
            Notify();
            return new(SessionDispatchDisposition.Coalesced, coalesced.Token);
        }

        Invalidate("superseded");
        var deadline = timeout is { } limit ? _time.GetUtcNow() + limit : (DateTimeOffset?)null;
        var context = new SessionRequestContext(_sequencer.Issue(deadline), CurrentContext());
        var decision = _actions.Begin(action, new ActionTarget(context.Runtime, data), "requested");
        var work = new Work(new SessionRuntimeRequest(action, data, context), decision.Token);
        _foreground = work;
        _queue.Add(work);
        _actions.MarkEnqueued(decision.Token);
        if (timeout is { } due)
            work.Timer = _time.CreateTimer(_ => _dispatcher.Post(() => Expire(work)), null, due, Timeout.InfiniteTimeSpan);
        _message = "Requested: " + action;
        EnsureDrainPending();
        Notify(); // Same-turn acknowledgement precedes all background work.
        Defer(() => { if (IsCurrentForeground(work)) ActionRequested?.Invoke(work.Request!); });
        return new(SessionDispatchDisposition.Enqueued, decision.Token);
    }

    public bool Reconfigure(string mode, string profileId, string season = "s0", TimeSpan? timeout = null)
    {
        VerifyOwner();
        bool accepted;
        using (BeginMutation()) accepted = ReconfigureCore(mode, profileId, season, timeout);
        var target = new SessionSelection(season, mode, profileId);
        return accepted && !Closed && _desired == target && (_state.Context.Selection == target
            || _foreground is { Action: "set_context" } && _foreground.Lifetime.Selection == target);
    }

    private bool ReconfigureCore(string mode, string profileId, string season, TimeSpan? timeout)
    {
        if (Closed) return false;
        if (_lease is not null) { Reject(SessionDispatchDisposition.ResourceLocked, "Resource operation is in progress."); return false; }
        var selection = new SessionSelection(season, mode, profileId);
        if (!_selectionAllowed(selection)) { Reject(SessionDispatchDisposition.Unavailable, "Unknown runtime context."); return false; }
        if (_runtime is null) { Reject(SessionDispatchDisposition.Unavailable, "Unavailable command: set_context"); return false; }
        RuntimeSnapshot actual;
        try { actual = _runtime.ReadSnapshot(); }
        catch (Exception error) { SnapshotFailed(error); return false; }
        var starting = _foreground?.Action is "start_round" or "toggle_round";
        if ((starting || actual.RoundActive || actual.RoundPreparing || actual.RoundWorkActive)
            && (selection.Mode != actual.Context.Mode || selection.Season != actual.Context.Season))
        {
            Reject(SessionDispatchDisposition.Unavailable, "请先结束本局，再切换游戏模式");
            return false;
        }
        if (selection == _desired && (actual.Context.Selection == selection
            || _foreground is { Action: "set_context" } && !_foreground.Cancellation.IsCancellationRequested)) return true;
        if (!_state.AvailableCommands.Contains("set_context"))
        { Reject(SessionDispatchDisposition.Unavailable, "Unavailable command: set_context"); return false; }
        Invalidate("context-reconfigured");
        _desired = selection;
        return DispatchCore("set_context", JsonSerializer.SerializeToElement(new { season, mode, profileId }), timeout)
            .Disposition is SessionDispatchDisposition.Enqueued or SessionDispatchDisposition.Coalesced;
    }

    public bool SetMode(string mode) => Reconfigure(mode, _desired.ProfileId, _desired.Season);
    public bool SetRouteProfile(string profileId) => Reconfigure(_desired.Mode, profileId, _desired.Season);

    public bool DispatchMaintenance(string renderToken)
    {
        VerifyOwner();
        using var mutation = BeginMutation();
        if (Closed || _runtime is not ISessionMaintenanceRuntime || _lease is not null || string.IsNullOrEmpty(renderToken)) return false;
        var maintenance = _queue.Append(_active).Where(w => w is { IsMaintenance: true }).ToArray();
        if (maintenance.Length >= 2 || maintenance.Any(w => w!.RenderToken == renderToken)) return false;
        _queue.Add(new Work(renderToken, CurrentContext()));
        EnsureDrainPending();
        Notify();
        return true;
    }

    public string? AcquireResourceLease()
    {
        VerifyOwner();
        string? acquired = null;
        using (BeginMutation())
        {
            if (!Closed && _lease is null && _active is null && _queue.Count == 0) RefreshRuntime();
            if (Closed || _lease is not null || _active is not null || _queue.Count > 0
                || _state.RoundActive || _state.RoundPreparing || _state.RoundWorkActive)
                Reject(SessionDispatchDisposition.ResourceLocked, "Resources require an idle session.");
            else
            {
                acquired = Guid.NewGuid().ToString("N");
                _lease = acquired;
                Notify();
            }
        }
        // Notifications can close the session or replace/release a lease. Only
        // this call's still-current grant may leave the synchronous API.
        return !Closed && acquired is not null && _lease == acquired ? acquired : null;
    }

    public void ReleaseResourceLease(string lease)
    {
        VerifyOwner();
        using var mutation = BeginMutation();
        if (_lease != lease) return;
        _lease = null;
        Notify();
    }

    public void CancelPending(string reason = "cancelled")
    {
        VerifyOwner();
        using var mutation = BeginMutation();
        if (Closed) return;
        CancelPendingCore(reason, close: false);
    }

    /// <summary>The presentation owner has already retired issued work; keep its published observation and opening.</summary>
    public void CancelPendingForPresentation(string reason)
    {
        VerifyOwner();
        using var mutation = BeginMutation();
        if (!Closed) CancelPendingCore(reason, close: false, cancelRecovery: false);
    }

    private void CancelPendingCore(string reason, bool close, bool cancelRecovery = true)
    {
        var contextPending = _foreground?.Action == "set_context";
        Invalidate(reason, close);
        // Invalidation precedes cooperative runtime cancellation.
        if (cancelRecovery && _runtime is IRuntimeRecoveryControl recovery)
        {
            Defer(() =>
            {
                try { recovery.CancelMapOpenRecovery(reason); }
                catch (Exception error) { _lastFailure = new("", "cancel-recovery", Describe(error)); }
            });
        }
        if (contextPending) RestoreAppliedContext();
        _message = close ? "Session closed." : "Pending requests cancelled.";
        if (close) _lease = null;
        Notify();
    }

    /// <summary>Final and nonblocking. A non-cooperative runtime is never killed.</summary>
    public void Shutdown()
    {
        VerifyOwner();
        using var mutation = BeginMutation();
        if (Closed) return;
        // Close before any cancellation/context/Changed observer can reenter.
        CancelPendingCore("session-stopped", close: true);
    }

    /// <summary>Await while the owner dispatcher continues pumping; never block the UI on this task.</summary>
    public Task DrainAsync() { VerifyOwner(); return _drained.Task; }

    private bool Closed => _sequencer.Read().Closed;
    private RuntimeContext CurrentContext() => _state.Context with
    { Season = _desired.Season, Mode = _desired.Mode, ProfileId = _desired.ProfileId };

    private void Invalidate(string reason, bool close = false)
    {
        var finished = _foreground;
        _sequencer.Invalidate(reason);
        if (close) _sequencer.Close();
        _lastReceipt = null;
        _lastFailure = null;
        _foreground = null;
        var cancelled = _queue.ToArray();
        _queue.Clear();
        if (_active is not null) CancelWork(_active, reason);
        foreach (var queued in cancelled) { CancelWork(queued, reason); Defer(queued.Dispose); }
        if (finished?.Action == "set_context") Defer(() => ContextFinished?.Invoke(finished.Lifetime.Selection));
    }

    private void CancelWork(Work work, string reason)
    {
        work.Timer?.Dispose();
        if (work.Request is { } request) _gate.Cancel(request.Context.Ticket);
        if (work.Token is { } token) _actions.Cancel(token, reason: reason);
        SignalCancellation(work);
    }

    private void Expire(Work work)
    {
        VerifyOwner();
        using var mutation = BeginMutation();
        if (Closed || _foreground != work || work.Request is null) return;
        _gate.ExpireThenCancel(work.Request.Context.Ticket);
        // Mark invalid before notifying cancellation callbacks or a queued worker.
        _actions.Fail(work.Token!.Value, "failed", "timeout");
        _foreground = null;
        _lastReceipt = null;
        _lastFailure = new(work.Action, "timeout", "timeout");
        _message = "Request timed out: " + work.Action;
        work.Timer?.Dispose();
        SignalCancellation(work);
        if (_queue.Remove(work)) Defer(work.Dispose);
        if (work.Action == "set_context") { RestoreAppliedContext(); FinishContext(work); }
        Notify();
    }

    // Called only after an owner mutation and all its synchronous callbacks
    // have finished. A stale queued item must never enter the actual runtime.
    private bool StartNext()
    {
        if (_active is not null || Closed || _queue.Count == 0) return false;
        var work = _queue[0];
        if (!work.IsMaintenance && !IsCurrentForeground(work))
        {
            _queue.RemoveAt(0);
            if (_foreground == work) _foreground = null;
            CancelWork(work, "superseded-before-dispatch");
            Defer(work.Dispose);
            Notify();
            return true;
        }
        if (work.Request?.Context.Ticket.DeadlineUtc is { } deadline && _time.GetUtcNow() >= deadline)
        {
            Expire(work);
            return true;
        }
        _queue.RemoveAt(0);
        _active = work;
        _ = Task.Run(async () =>
        {
            RuntimeOperationResult? result = null;
            Exception? failure = null;
            try
            {
                work.Cancellation.Token.ThrowIfCancellationRequested();
                if (work.IsMaintenance)
                    await ((ISessionMaintenanceRuntime)_runtime!).RememberRenderMaterialAsync(
                        work.RenderToken!, work.Lifetime, work.Cancellation.Token).ConfigureAwait(false);
                else result = await _runtime!.DispatchAsync(work.Request!, work.Cancellation.Token).ConfigureAwait(false);
            }
            catch (Exception error) { failure = error; }
            _dispatcher.Post(() => Complete(work, result, failure));
        });
        return false;
    }

    private void Complete(Work work, RuntimeOperationResult? result, Exception? error)
    {
        VerifyOwner();
        using var mutation = BeginMutation();
        if (_active != work) return;
        _active = null;
        try
        {
            if (work.IsMaintenance)
            {
                if (error is not null && !work.Cancellation.IsCancellationRequested && !Closed)
                    Defer(() => MaintenanceFailed?.Invoke(new(work.Action, "maintenance", Describe(error))));
                return;
            }
            var request = work.Request!;
            var ticket = request.Context.Ticket;
            var disposition = _gate.Submit(ticket, ticket.Sequence, _time.GetUtcNow());
            if (_foreground == work) _foreground = null;
            if (disposition != ResultDisposition.Accepted || work.Cancellation.IsCancellationRequested
                || (work.ProgressContext ?? request.Context.Runtime) != CurrentContext() || !_actions.AcceptsBackgroundResult(work.Token!.Value))
            {
                _staleResults++;
                if (disposition == ResultDisposition.Expired && _actions.AcceptsBackgroundResult(work.Token!.Value))
                {
                    _gate.ExpireThenCancel(ticket);
                    _actions.Fail(work.Token.Value, "failed", "timeout");
                    _lastFailure = new(work.Action, "timeout", "timeout");
                    _message = "Request timed out: " + work.Action;
                    SignalCancellation(work);
                    if (work.Action == "set_context") RestoreAppliedContext();
                    FinishContext(work);
                }
                Defer(() => ResultConsumed?.Invoke(new(work.Action, ticket, disposition.ToString())));
                return;
            }
            RuntimeSnapshot fresh;
            bool producerCurrent;
            try
            {
                if (_runtime is ISessionRuntimeProgress progress)
                {
                    var observed = progress.ReadSnapshotFor(request.Context);
                    fresh = observed.Snapshot;
                    producerCurrent = observed.OwnedByRequest;
                }
                else { fresh = _runtime!.ReadSnapshot(); producerCurrent = true; }
            }
            catch (Exception snapshotError)
            {
                SnapshotFailed(snapshotError);
                _actions.Fail(work.Token.Value, "failed", "runtime-snapshot-failed");
                FinishContext(work);
                Defer(() => ResultConsumed?.Invoke(new(work.Action, ticket, "SnapshotFailed")));
                return;
            }
            if (!producerCurrent)
            {
                // Completion may be the first owner read since an external
                // epoch was revoked. Equal visible fields cannot admit that
                // old operation's failure, success, or worker exception.
                _staleResults++;
                _actions.Cancel(work.Token.Value, reason: "runtime-progress-revoked");
                ApplyObserved(fresh);
                _message = "Discarded a result from a superseded runtime context.";
                FinishContext(work);
                Defer(() => ResultConsumed?.Invoke(new(work.Action, ticket, "OwnershipRevoked")));
                return;
            }
            if (error is not null)
            {
                if (work.Action == "set_context") { _state = fresh; _desired = fresh.Context.Selection; }
                _actions.Fail(work.Token.Value, "failed", Describe(error));
                _lastFailure = new(work.Action, "operation", Describe(error));
                _message = Describe(error);
                FinishContext(work);
                Defer(() => ResultConsumed?.Invoke(new(work.Action, ticket, "WorkerFailed")));
                return;
            }
            var receipt = result?.Receipt;
            var snapshot = result?.Snapshot;
            var rejectedContext = work.Action == "set_context" && receipt?.Success == false;
            var matches = receipt is not null && snapshot is not null && receipt.Action == work.Action
                && receipt.RequestContext == request.Context && snapshot.Context == fresh.Context
                && (rejectedContext || snapshot.Context.Selection == request.Context.Runtime.Selection)
                && (rejectedContext || (receipt.ResultContext is { } output
                    ? output == snapshot.Context : snapshot.Context == request.Context.Runtime));
            if (!matches)
            {
                _staleResults++;
                _actions.Cancel(work.Token.Value, reason: "runtime-result-context-mismatch");
                ApplyObserved(fresh);
                _message = "Discarded a result from a superseded runtime context.";
                FinishContext(work);
                Defer(() => ResultConsumed?.Invoke(new(work.Action, ticket, "ContextMismatch")));
                return;
            }
            // Only the fresh runtime snapshot supplies state. Receipt success
            // is not consulted to create round, identity, pose, or display data.
            _state = fresh;
            _desired = fresh.Context.Selection;
            _lastReceipt = receipt;
            _lastFailure = null;
            if (receipt!.Success) _actions.Settle(work.Token.Value);
            else _actions.Fail(work.Token.Value, "failed", receipt.Reason.Length > 0 ? receipt.Reason : "rejected");
            _message = receipt.Message.Length > 0 ? receipt.Message : receipt.Reason.Length > 0
                ? receipt.Reason : fresh.Message.Length > 0 ? fresh.Message : "Request completed.";
            FinishContext(work);
            Defer(() => ResultConsumed?.Invoke(new(work.Action, ticket, "Accepted")));
        }
        finally
        {
            Defer(work.Dispose);
            Notify();
        }
    }

    private void FinishContext(Work work)
    { if (work.Action == "set_context") Defer(() => ContextFinished?.Invoke(work.Lifetime.Selection)); }

    private void ApplyObserved(RuntimeSnapshot state)
    {
        ArgumentNullException.ThrowIfNull(state);
        if (CurrentContext() != state.Context) Invalidate("runtime-context-changed");
        _state = state;
        _desired = state.Context.Selection;
    }

    private bool ApplyOwnedProgress(Work work, RuntimeSnapshot state)
    {
        if (_active != work || work.Request is null || !IsCurrentForeground(work)) return false;
        work.ProgressContext = state.Context;
        _state = state;
        _desired = state.Context.Selection;
        return true;
    }

    private void RestoreAppliedContext()
    {
        try { _state = _runtime!.ReadSnapshot(); }
        catch { _state = _state.Withheld(disconnected: true); }
        _desired = _state.Context.Selection;
    }

    private void SnapshotFailed(Exception error)
    {
        Invalidate("runtime-snapshot-failed");
        _state = _state.Withheld(disconnected: true);
        _desired = _state.Context.Selection;
        _lastFailure = new("", "snapshot", Describe(error));
        _message = "Runtime snapshot unavailable: " + Describe(error);
        Notify();
    }

    private bool Available(string action, JsonElement payload)
    {
        if (_runtime is null) return false;
        if (_state.AvailableCommands.Contains(action)) return true;
        if (action != "confirm_route" || !payload.TryGetProperty("mapId", out var id)
            || id.ValueKind != JsonValueKind.String || string.IsNullOrEmpty(id.GetString())) return false;
        try { return _runtime.ReadSnapshot().ManualMapPickAvailable; }
        catch { return false; }
    }

    private SessionDispatchResult Reject(SessionDispatchDisposition disposition, string message)
    { _message = message; Notify(); return new(disposition); }
    private void VerifyOwner()
    { if (!_dispatcher.CheckAccess()) throw new InvalidOperationException("session-owner-thread-required"); }
    private void Notify() => Defer(() => Changed?.Invoke());
    private void SignalCancellation(Work work) => Defer(() =>
    {
        try { work.Cancellation.Cancel(); }
        catch (AggregateException error) { _lastFailure = new(work.Action, "cancel", Describe(error)); }
    });

    private bool IsCurrentForeground(Work work)
    {
        if (Closed || _foreground != work || work.Request is null || work.Cancellation.IsCancellationRequested
            || !_actions.AcceptsBackgroundResult(work.Token!.Value)) return false;
        var current = _sequencer.Read();
        var request = work.Request.Context;
        return request.Ticket.SessionId == current.SessionId && request.Ticket.Generation == current.Generation
            && request.Ticket.Sequence == current.LastIssuedSequence && request.Ticket.Sequence > current.SupersededThrough
            && (work.ProgressContext ?? request.Runtime) == CurrentContext();
    }

    private Mutation BeginMutation()
    {
        VerifyOwner();
        _mutationDepth++;
        return new(this);
    }

    private void EndMutation()
    {
        if (--_mutationDepth == 0) FlushEffects();
    }

    private void Defer(Action effect) => _effects.Enqueue(effect);

    private void FlushEffects()
    {
        if (_flushingEffects) return;
        _flushingEffects = true;
        try
        {
            do
            {
                // A callback may reenter any public mutation. That newer intent
                // commits before its own effects are appended here, and before
                // a worker is allowed to start. No outer state write follows it.
                while (_effects.TryDequeue(out var effect)) effect();
            } while (StartNext());
            CompleteDrainIfIdle();
        }
        finally { _flushingEffects = false; }
    }

    private sealed class Mutation(SessionService owner) : IDisposable
    {
        public void Dispose() => owner.EndMutation();
    }
    private static string Describe(Exception error) => error.GetType().Name + ": " + error.Message;
    private void EnsureDrainPending()
    { if (_drained.Task.IsCompleted) _drained = new(TaskCreationOptions.RunContinuationsAsynchronously); }
    private void CompleteDrainIfIdle()
    { if (_active is null && _queue.Count == 0) _drained.TrySetResult(); }
    private static TaskCompletionSource CompletedDrain()
    { var result = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously); result.SetResult(); return result; }

    private sealed class Work : IDisposable
    {
        public Work(SessionRuntimeRequest request, UiActionToken token)
        { Request = request; Token = token; Lifetime = request.Context.Runtime; }
        public Work(string renderToken, RuntimeContext lifetime) { RenderToken = renderToken; Lifetime = lifetime; }
        public SessionRuntimeRequest? Request { get; }
        public RuntimeContext? ProgressContext { get; set; }
        public UiActionToken? Token { get; }
        public RuntimeContext Lifetime { get; }
        public string? RenderToken { get; }
        public bool IsMaintenance => Request is null;
        public string Action => Request?.Action ?? "remember_render_material";
        public CancellationTokenSource Cancellation { get; } = new();
        public ITimer? Timer { get; set; }
        public void Dispose() { Timer?.Dispose(); Cancellation.Dispose(); }
    }

    private sealed class ActionTarget(RuntimeContext context, JsonElement payload) : IEquatable<ActionTarget>
    {
        private readonly JsonNode? _payload = JsonNode.Parse(payload.GetRawText());
        public RuntimeContext Context { get; } = context;
        public bool Equals(ActionTarget? other)
            => other is not null && Context == other.Context && JsonNode.DeepEquals(_payload, other._payload);
        public override bool Equals(object? obj) => obj is ActionTarget target && Equals(target);
        public override int GetHashCode() => Context.GetHashCode();
    }
}
