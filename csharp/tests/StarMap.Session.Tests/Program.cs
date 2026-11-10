using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Reliability;
using StarMap.Core.Session;

var failures = 0;
void Check(bool passed, string name)
{
    if (!passed) throw new InvalidOperationException(name);
}
void Run(string name, Action test)
{
    try { test(); Console.WriteLine("ok " + name); }
    catch (Exception error) { failures++; Console.Error.WriteLine("FAIL " + name + " " + error.Message); }
}

Run("unchanged-python-pure-state-oracle", () =>
{
    var root = FindRoot();
    var python = Environment.GetEnvironmentVariable("STARMAP_PYTHON") ?? "python";
    var start = new ProcessStartInfo(python) { RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false };
    start.ArgumentList.Add(Path.Combine(root, "csharp", "tests", "StarMap.Session.Tests", "reference_oracle.py"));
    start.ArgumentList.Add(root);
    using var process = Process.Start(start) ?? throw new InvalidOperationException("python-start-failed");
    var output = process.StandardOutput.ReadToEndAsync();
    var errors = process.StandardError.ReadToEndAsync();
    if (!process.WaitForExit(15000)) throw new InvalidOperationException("python-oracle-timeout");
    if (process.ExitCode != 0) throw new InvalidOperationException("python-oracle: " + errors.GetAwaiter().GetResult());
    var expected = JsonNode.Parse(output.GetAwaiter().GetResult())!;
    var machine = new ImmediateUiActionStateMachine();
    var ui = new List<object>();
    UiActionToken Take(UiActionDecision d)
    {
        var s = d.Snapshot;
        var phase = s.Phase switch { UiActionPhase.VisualConfirmed => "visual-confirmed", UiActionPhase.BackgroundEnqueued => "background-enqueued",
            UiActionPhase.Settled => "settled", UiActionPhase.Failed => "failed", _ => "cancelled" };
        ui.Add(new { generation = s.Token.Generation, phase, accepted = d.Accepted, coalesced = d.Coalesced,
            stale = d.Stale, enqueue = d.ShouldEnqueueBackground, attempted = d.Token.Generation,
            target = s.TargetState, visible = s.VisibleState, input = s.InputAtMs, visual = s.VisualAtMs,
            enqueued = s.EnqueuedAtMs, settled = s.SettledAtMs, count = s.CoalescedInputs, reason = s.Reason });
        return d.Token;
    }
    var one = Take(machine.Begin("capture", "map-1", "requested", 100));
    Take(machine.MarkEnqueued(one, 101));
    Take(machine.Begin("capture", "map-1", "ignored", 102));
    var two = Take(machine.Begin("capture", "map-2", "requested", 103));
    Take(machine.Settle(one, "wrong", 104));
    Take(machine.MarkEnqueued(two, 105));
    Take(machine.Fail(two, "failed", "fixture", 106));
    var three = Take(machine.Begin("capture", "map-2", "requested", 110));
    Take(machine.Cancel(three, reason: "closed", nowMs: 111));
    Take(machine.Settle(three, nowMs: 112));
    Take(machine.Cancel(new("unknown", 9), nowMs: 113));
    Check(JsonNode.DeepEquals(expected["ui"], JsonSerializer.SerializeToNode(ui)), "UI intent trace differs from Python");
    var policy = new List<object?>();
    foreach (var isOpen in new[] { false, true }) foreach (var capture in new[] { false, true }) foreach (var silent in new[] { false, true })
        policy.Add(SessionStateMachine.MapHotkeyTransition(isOpen, capture, silent));
    foreach (var isOpen in new[] { false, true }) foreach (var misses in new[] { 0, 1, 2 }) foreach (var probe in new bool?[] { null, false, true })
    foreach (var allowed in new[] { false, true })
    { var p = SessionStateMachine.MapProbe(isOpen, misses, probe, allowed); policy.Add(new object?[] { p.Misses, p.Transition }); }
    foreach (var observed in new int?[] { null, -1, 0, 1, 2, 3 }) foreach (var manual in new string?[] { null, "1", "2", "B1", "B4" })
    { var p = SessionStateMachine.ResolveRuntimeFloor(observed, manual); policy.Add(new object[] { p.FloorId, p.Reason }); }
    foreach (var observations in new int?[][] { [], [1], [1, null], [1, 2], [-1], [2, 99] })
        policy.Add(SessionStateMachine.StableFloorFromObservations(observations));
    foreach (var selected in new int?[] { null, -1, 0, 1, 2, 3 }) foreach (var detected in new[] { false, true })
    {
        var evidence = new MapCanvasEvidence(detected, true, 0.8);
        policy.Add(SessionStateMachine.VerifiedMapHotkeyFrame(evidence, selected));
        policy.Add(SessionStateMachine.LockedMapCanvasWithoutFloorIsAuthorized(evidence, selected, true));
    }
    foreach (var e in new long?[] { null, 1, 2 })
    { policy.Add(SessionStateMachine.RankedEventIsCurrent(e, 2)); policy.Add(SessionStateMachine.DisplayEventIsCurrent(e, null)); policy.Add(SessionStateMachine.DisplayEventIsCurrent(e, 2)); }
    foreach (var (confirmed, candidate) in new (string?, string?)[] { (null, null), ("a", null), ("a", "a"), ("a", "b") })
        policy.Add(SessionStateMachine.ConfirmedOverlaySourceIsAllowed(confirmed, candidate));
    foreach (var (game, foreground) in new (nint?, nint?)[] { (null, null), (0, 1), (1, 1), (1, 2) })
    { policy.Add(SessionStateMachine.GlobalKeyIsAllowed(game, foreground)); policy.Add(SessionStateMachine.TrustedForegroundIsAllowed(game, foreground, 5, 5)); }
    Check(JsonNode.DeepEquals(expected["policy"], JsonSerializer.SerializeToNode(policy)), "pure session policy differs from Python");
    var s1 = SessionStateMachine.ResolveRuntimeFloor(1, null, "s1");
    Check(s1 == new FloorResolution("", "floor-contract-unavailable"), "missing target floor contract fails closed");
});

Run("unattached-no-production-authority", () =>
{
    var service = new SessionService(new OwnerDispatcher());
    Check(service.Dispatch("start_round").Disposition == SessionDispatchDisposition.Unavailable, "unattached dispatch rejected");
    var view = service.Read();
    Check(!view.Runtime.EngineConnected && !view.Runtime.LiveAuthorization && !view.Runtime.RoundActive && view.LastReceipt is null, "no fabricated live state");
    service.Shutdown();
});

Run("immediate-feedback-coalesces-and-returns-on-owner", () =>
{
    using var h = new Harness();
    var notifications = new List<int>();
    h.Service.Changed += () => notifications.Add(Environment.CurrentManagedThreadId);
    var one = h.Service.Dispatch("capture_now", JsonSerializer.SerializeToElement(new { a = 1, b = 2 }));
    var two = h.Service.Dispatch("capture_now", JsonSerializer.SerializeToElement(new { b = 2, a = 1 }));
    Check(one.Disposition == SessionDispatchDisposition.Enqueued && two.Disposition == SessionDispatchDisposition.Coalesced && one.Token == two.Token, "same intent coalesced");
    Check(h.Service.Read().Busy && h.Service.Read().Actions.Single().CoalescedInputs == 1, "same-turn visual acknowledgement");
    var call = h.Call(0);
    Check(call.ThreadId != Environment.CurrentManagedThreadId, "runtime executes off owner");
    call.Complete(); h.Drain();
    Check(h.Runtime.Calls.Count == 1 && notifications.All(t => t == Environment.CurrentManagedThreadId), "single dispatch and owner notifications");
    Check(h.Service.Read().LastReceipt?.Success == true && !h.Service.Read().Runtime.RoundActive && !h.Service.Read().Runtime.LiveAuthorization,
        "successful receipt never creates round or identity authority");
});

foreach (var action in new[] { "capture_now", "set_context" })
Run("worker-cancellation-completion-retires-intent-" + action, () =>
{
    using var h = new Harness();
    var selection = h.Service.Read().AppliedSelection;
    var finished = new List<SessionSelection>();
    h.Service.ContextFinished += finished.Add;
    void Submit()
    {
        if (action == "set_context")
            Check(h.Service.Reconfigure(selection.Mode, selection.ProfileId, selection.Season, refreshDisplay: true), "display refresh submitted");
        else Check(h.Service.Dispatch(action).Disposition == SessionDispatchDisposition.Enqueued, "capture submitted");
    }
    Submit();
    var first = h.Call(0);
    // Controlled host cancellation, matching the real WPF material-preparation
    // regression. Session completion must retire its intent even when no owner
    // CancelPending/Expire callback ran first. The runtime alone is substituted.
    var active = typeof(SessionService).GetField("_active", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!.GetValue(h.Service)!;
    ((CancellationTokenSource)active.GetType().GetProperty("Cancellation")!.GetValue(active)!).Cancel();
    first.Complete(); h.Drain();
    var view = h.Service.Read();
    Check(!view.WorkerActive && !view.Busy && view.LastReceipt is null
        && view.Actions.Single(a => a.Token.Action == action).Phase == UiActionPhase.Cancelled,
        "cancelled completion cannot leave an orphan pending intent");
    Check(finished.Count == (action == "set_context" ? 1 : 0), "context completion notified once");
    Submit();
    var retry = h.Call(1);
    retry.Complete(); h.Drain();
    Check(h.Runtime.Calls.Count == 2 && h.Runtime.MaxConcurrent == 1 && h.Service.Read().LastReceipt?.Success == true,
        "identical retry dispatches fresh work after cancellation");
    Check(finished.Count == (action == "set_context" ? 2 : 0), "retry context completion notified once");
});

Run("latest-queued-intent-replaces-cancelled-work-without-concurrency", () =>
{
    using var h = new Harness();
    h.Service.Dispatch("capture_now"); var first = h.Call(0);
    h.Service.Dispatch("set_floor", JsonSerializer.SerializeToElement(new { floorId = "2" }));
    h.Service.Dispatch("confirm_route", JsonSerializer.SerializeToElement(new { mapId = "map-b" }));
    Check(first.Token.IsCancellationRequested && h.Service.Read().QueuedCount == 1 && h.Runtime.Calls.Count == 1, "running cancellation leaves only latest queued");
    first.Complete(); var latest = h.Call(1);
    Check(latest.Request!.Action == "confirm_route" && h.Runtime.MaxConcurrent == 1, "cancelled queued set_floor never executed");
    latest.Complete(); h.Drain();
    Check(h.Service.Read().LastReceipt?.Action == "confirm_route" && h.Service.Read().StaleResults == 1, "late primary cannot replace latest receipt");
});

Run("replacement-token-callback-shutdown-prevents-outer-issue-and-runtime-entry", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var first = h.Call(0);
    using var registration = first.Token.Register(h.Service.Shutdown);
    // This is the real CancellationToken callback, synchronously invoked while
    // replacing A. It is not a direct Shutdown call before the replacement.
    var replacement = h.Service.Dispatch("confirm_route", JsonSerializer.SerializeToElement(new { mapId = "map-b" }));
    var closed = h.Service.Read();
    Check(replacement.Disposition == SessionDispatchDisposition.Closed && closed.Sequence.Closed
        && closed.QueuedCount == 0 && !closed.Busy && closed.WorkerActive, "shutdown callback wins without session-closed exception");
    first.Complete(); h.Drain();
    Check(h.Runtime.Calls.Count == 1 && h.Runtime.MaxConcurrent == 1 && h.Service.Read().LastReceipt is null,
        "outer replacement never reaches runtime after callback shutdown");
});

Run("replacement-token-callback-dispatch-keeps-only-newest-intent-before-runtime", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var first = h.Call(0);
    SessionDispatchResult? fromCallback = null;
    using var registration = first.Token.Register(() => fromCallback = h.Service.Dispatch("set_floor",
        JsonSerializer.SerializeToElement(new { floorId = "2" })));
    var outer = h.Service.Dispatch("confirm_route", JsonSerializer.SerializeToElement(new { mapId = "map-b" }));
    var view = h.Service.Read();
    Check(outer.Disposition == SessionDispatchDisposition.Superseded && fromCallback?.Disposition == SessionDispatchDisposition.Enqueued
        && view.QueuedCount == 1 && view.PendingAction == "set_floor" && h.Runtime.Calls.Count == 1,
        "newer callback intent supersedes the atomically admitted outer intent");
    Check(view.Actions.Single(a => a.Token.Action == "confirm_route").Phase == UiActionPhase.Cancelled,
        "outer intent is cancelled before its real side effects");
    first.Complete(); var newest = h.Call(1);
    Check(newest.Request!.Action == "set_floor" && newest.Request.Context.Ticket.Sequence == h.Service.Read().Sequence.LastIssuedSequence,
        "only current sequence enters runtime");
    newest.Complete(); h.Drain();
    Check(h.Runtime.Calls.Count == 2 && h.Runtime.MaxConcurrent == 1 && h.Service.Read().LastReceipt?.Action == "set_floor",
        "no obsolete foreground call was executed or consumed");
});

Run("reconfigure-token-callback-command-preserves-newer-applied-context", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var first = h.Call(0);
    using var registration = first.Token.Register(() => h.Service.Dispatch("set_floor"));
    Check(!h.Service.Reconfigure("difficult", "author-b", "s1"), "superseded context attempt reports false");
    var view = h.Service.Read();
    Check(view.PendingAction == "set_floor" && view.QueuedCount == 1 && view.DesiredSelection == h.Runtime.State.Context.Selection,
        "outer reconfigure cannot overwrite callback command's restored selection");
    first.Complete(); var newest = h.Call(1);
    Check(newest.Request!.Action == "set_floor" && newest.Request.Context.Runtime.Selection == h.Runtime.State.Context.Selection,
        "superseded set_context never enters runtime");
    newest.Complete(); h.Drain(); Check(h.Runtime.Calls.Count == 2 && h.Runtime.MaxConcurrent == 1, "reconfigure retains one real worker");
});

Run("cancel-token-callback-reconfigure-survives-outer-context-reconciliation", () =>
{
    using var h = new Harness(); Check(h.Service.Reconfigure("difficult", "old-author"), "first context submitted"); var first = h.Call(0);
    bool? callbackAccepted = null;
    using var registration = first.Token.Register(() => callbackAccepted = h.Service.Reconfigure("nightmare", "new-author"));
    h.Service.CancelPending("cancel-context");
    var target = h.Runtime.State.Context with { ProfileId = "new-author" };
    Check(callbackAccepted == true && h.Service.Read().DesiredSelection == target.Selection
        && h.Service.Read().PendingAction == "set_context" && h.Service.Read().QueuedCount == 1,
        "outer cancel restores before callback, never over its new context");
    first.Complete(); var newest = h.Call(1);
    Check(newest.Request!.Context.Runtime.Selection == target.Selection, "new callback context sealed intact");
    h.Runtime.State = TestRuntime.Snapshot(target); newest.Complete(resultContext: target); h.Drain();
    Check(h.Service.Read().AppliedSelection == target.Selection && h.Runtime.MaxConcurrent == 1, "callback context genuinely commits");
});

foreach (var delayedTimer in new[] { false, true })
Run("timeout-token-callback-reconfigure-preserves-newer-intent-" + (delayedTimer ? "completion-first" : "timer-first"), () =>
{
    using var h = new Harness(); Check(h.Service.Reconfigure("difficult", "old-author", timeout: TimeSpan.FromSeconds(1)), "timed context submitted");
    var first = h.Call(0);
    using var registration = first.Token.Register(() => h.Service.Reconfigure("nightmare", "new-author"));
    if (delayedTimer)
    {
        first.Complete();
        var wait = Stopwatch.StartNew();
        while (!h.Dispatcher.HasCallbacks)
        { if (wait.Elapsed > TimeSpan.FromSeconds(5)) throw new TimeoutException("completion callback missing"); Thread.Sleep(1); }
    }
    h.Clock.Advance(TimeSpan.FromSeconds(1)); h.Dispatcher.Pump();
    var target = h.Runtime.State.Context with { ProfileId = "new-author" };
    Check(h.Service.Read().DesiredSelection == target.Selection && h.Service.Read().PendingAction == "set_context"
        && h.Service.Read().Message == "Requested: set_context", "expired outer context cannot overwrite newer desired selection or feedback");
    if (!delayedTimer) first.Complete();
    var newest = h.Call(1);
    Check(newest.Request!.Context.Runtime.Selection == target.Selection, "timeout callback context sealed intact");
    h.Runtime.State = TestRuntime.Snapshot(target); newest.Complete(resultContext: target); h.Drain();
    Check(h.Runtime.Calls.Count == 2 && h.Runtime.MaxConcurrent == 1 && h.Service.Read().AppliedSelection == target.Selection,
        "timeout reentry has one current runtime operation");
});

foreach (var field in new[] { "season", "mode", "profile", "round", "map", "floor", "resource", "frame" })
Run("fresh-snapshot-rejects-old-" + field, () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var call = h.Call(0);
    var old = h.Runtime.State;
    var c = old.Context;
    var changed = field switch { "season" => c with { Season = "s1" }, "mode" => c with { Mode = "difficult" },
        "profile" => c with { ProfileId = "author-b" }, "round" => c with { RoundId = "round-2" },
        "map" => c with { MapId = "map-b" }, "floor" => c with { FloorId = "2" },
        "resource" => c with { ResourceId = "resource-2" }, _ => c with { FrameId = "frame-2" } };
    h.Runtime.State = TestRuntime.Snapshot(changed);
    call.Complete(returned: old); h.Drain();
    Check(h.Service.Read().StaleResults == 1 && h.Service.Read().LastReceipt is null && h.Service.Read().Runtime.Context == changed,
        "fresh state rejects stale receipt without UI polling");
});

Run("explicit-request-result-pair-permits-intended-transition-only", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var call = h.Call(0);
    var target = h.Runtime.State.Context with { RoundId = "round-2", MapId = "map-b", FloorId = "2", FrameId = "frame-2", ResourceId = "resource-2" };
    h.Runtime.State = TestRuntime.Snapshot(target);
    call.Complete(resultContext: target); h.Drain();
    Check(h.Service.Read().LastReceipt?.Success == true && h.Service.Read().Runtime.Context == target, "explicit context pair accepted");
    h.Service.Dispatch("capture_now"); var wrong = h.Call(1);
    wrong.Complete(requestContext: wrong.Request!.Context with { Ticket = wrong.Request.Context.Ticket with { Generation = 999 } }); h.Drain();
    Check(h.Service.Read().LastReceipt is null && h.Service.Read().StaleResults == 1, "wrong request echo rejected");
});

Run("refresh-invalidates-pending-context-before-cancellation", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var call = h.Call(0);
    var before = h.Service.Read().Sequence.Generation;
    var observed = false;
    using var registration = call.Token.Register(() => observed = h.Service.Read().Sequence.Generation > before && !h.Service.Read().Busy);
    h.Runtime.State = TestRuntime.Snapshot(h.Runtime.State.Context with { FloorId = "2" });
    h.Service.RefreshRuntime();
    Check(observed && call.Token.IsCancellationRequested, "generation invalidation precedes cancellation observer");
    call.Complete(); h.Drain();
    Check(h.Service.Read().LastReceipt is null && h.Service.Read().StaleResults == 1, "refreshed floor owns state");
});

Run("refresh-owned-progress-keeps-original-request-and-final-context-pair", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var call = h.Call(0);
    var target = h.Runtime.State.Context with { FloorId = "2", FrameId = "owned-progress-frame" };
    h.Runtime.ProgressOwner = call.Request!.Context;
    h.Runtime.State = TestRuntime.Snapshot(target);
    h.Service.RefreshRuntime(); h.Service.RefreshRuntime();
    Check(h.Service.Read().Busy && !call.Token.IsCancellationRequested && h.Service.Read().Runtime.Context == target,
        "repeat refresh publishes only explicitly owned current-request progress");
    call.Complete(resultContext: target); h.Drain();
    Check(h.Service.Read().LastReceipt?.RequestContext == call.Request.Context && h.Service.Read().StaleResults == 0,
        "original request echo remains unchanged and final pair passes the existing result gate");
});

Run("progress-cannot-attribute-a-different-request-or-exempt-bad-final-echo", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var first = h.Call(0);
    h.Runtime.ProgressOwner = first.Request!.Context with { Ticket = first.Request.Context.Ticket with { Sequence = 999 } };
    h.Runtime.State = TestRuntime.Snapshot(h.Runtime.State.Context with { FrameId = "unowned-progress" });
    h.Service.RefreshRuntime();
    Check(first.Token.IsCancellationRequested && !h.Service.Read().Busy, "a different ticket cannot claim active request progress");
    first.Complete(); h.Drain();
    h.Service.Dispatch("capture_now"); var second = h.Call(1);
    var target = h.Runtime.State.Context with { FrameId = "owned-then-invalid-final" };
    h.Runtime.ProgressOwner = second.Request!.Context; h.Runtime.State = TestRuntime.Snapshot(target); h.Service.RefreshRuntime();
    second.Complete(resultContext: target, requestContext: second.Request.Context with { Ticket = second.Request.Context.Ticket with { Generation = 999 } }); h.Drain();
    Check(h.Service.Read().LastReceipt is null && h.Service.Read().StaleResults == 2, "owned progress does not relax malformed final request rejection");
});

Run("atomic-progress-read-keeps-snapshot-when-same-producer-advances-before-return", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var call = h.Call(0);
    var a = h.Runtime.State.Context with { FloorId = "2", FrameId = "progress-a" };
    var b = a with { FrameId = "progress-b" };
    h.Runtime.ProgressOwner = call.Request!.Context; h.Runtime.State = TestRuntime.Snapshot(a);
    h.Runtime.AfterProgressRead = () => h.Runtime.State = TestRuntime.Snapshot(b);
    h.Service.RefreshRuntime();
    Check(h.Service.Read().Busy && !call.Token.IsCancellationRequested && h.Service.Read().Runtime.Context == a,
        "one atomic result attributes its own snapshot despite a newer same-request context");
    call.Complete(resultContext: b); h.Drain();
    Check(h.Service.Read().StaleResults == 0 && h.Service.Read().LastReceipt?.RequestContext == call.Request.Context
        && h.Service.Read().Runtime.Context == b, "final pair independently checks the newer committed context");
});

Run("accepted-progress-ownership-loss-cancels-even-with-equal-visible-context", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var call = h.Call(0);
    var target = h.Runtime.State.Context with { FloorId = "2", FrameId = "owned-before-revocation" };
    h.Runtime.ProgressOwner = call.Request!.Context; h.Runtime.State = TestRuntime.Snapshot(target);
    h.Service.RefreshRuntime(); Check(h.Service.Read().Busy, "progress attribution first established");
    h.Runtime.ProgressOwner = null; // Runtime epoch was revoked, while visible fields may stay equal.
    h.Service.RefreshRuntime();
    Check(!h.Service.Read().Busy && call.Token.IsCancellationRequested, "loss of accepted producer ownership revokes active work");
    call.Complete(resultContext: target); h.Drain();
    Check(h.Service.Read().LastReceipt is null && h.Service.Read().StaleResults == 1, "equal context does not admit a revoked producer's result");
});

foreach (var success in new[] { false, true })
Run("final-consumption-checks-revoked-owner-without-refresh-" + (success ? "success" : "failure"), () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var call = h.Call(0);
    var target = h.Runtime.State.Context with { FloorId = "2", FrameId = null };
    h.Runtime.ProgressOwner = call.Request!.Context; h.Runtime.State = TestRuntime.Snapshot(target);
    h.Service.RefreshRuntime(); Check(h.Service.Read().Busy, "own cA progress accepted");
    var consumed = new List<SessionCompletion>(); h.Service.ResultConsumed += consumed.Add;
    h.Runtime.ProgressOwner = null;
    call.Complete(success: success, resultContext: target); h.Drain(); // No refresh between revocation and final consumption.
    Check(h.Service.Read().LastReceipt is null && h.Service.Read().StaleResults == 1
        && consumed.Count == 1 && consumed[0].Disposition != "Accepted",
        "neither old success nor old failure can be accepted after its producer loses the current epoch");
});

Run("cancelled-worker-retains-exclusive-slot-and-shutdown-is-final", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var call = h.Call(0);
    h.Service.CancelPending("foreground-lost");
    Check(!h.Service.Read().Busy && h.Service.Read().WorkerActive && h.Service.AcquireResourceLease() is null, "cooperative cancellation is not worker exit");
    h.Service.Shutdown();
    Check(h.Service.Dispatch("start_round").Disposition == SessionDispatchDisposition.Closed && !h.Service.Read().Runtime.LiveAuthorization,
        "closed cannot restart or grant authority");
    Check(!h.Service.DrainAsync().IsCompleted, "shutdown never lies about drain");
    call.Complete(); h.Drain();
    Check(h.Service.Read().Sequence.Closed && h.Service.Read().LastReceipt is null && h.Runtime.Calls.Count == 1, "late closed result discarded");
});

Run("timeout-marks-failure-before-cancel-and-keeps-worker-slot", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now", timeout: TimeSpan.FromSeconds(1)); var call = h.Call(0);
    var observed = false;
    using var registration = call.Token.Register(() => observed = h.Service.Read().LastFailure?.Phase == "timeout" && !h.Service.Read().Busy);
    h.Clock.Advance(TimeSpan.FromSeconds(1)); h.Dispatcher.Pump();
    Check(observed && h.Service.Read().WorkerActive && h.Service.Read().LastReceipt is null, "expiration is visible before cancellation");
    h.Service.Dispatch("set_floor"); Check(h.Runtime.Calls.Count == 1, "timeout cannot start overlapping worker");
    call.Complete(); var next = h.Call(1); next.Complete(); h.Drain();
    Check(h.Runtime.MaxConcurrent == 1 && h.Service.Read().LastReceipt?.Action == "set_floor", "worker recovery after timeout");
});

Run("queued-timeout-never-dispatches-expired-intent", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var call = h.Call(0);
    h.Service.Dispatch("set_floor", timeout: TimeSpan.FromSeconds(1));
    h.Clock.Advance(TimeSpan.FromSeconds(1)); h.Dispatcher.Pump();
    Check(h.Service.Read().QueuedCount == 0 && h.Service.Read().LastFailure?.Phase == "timeout", "queued timeout drops work");
    call.Complete(); h.Drain(); Check(h.Runtime.Calls.Count == 1, "expired queued runtime never called");
});

Run("completion-queued-before-timer-still-expires-at-owner-consumption", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now", timeout: TimeSpan.FromSeconds(1)); var call = h.Call(0);
    call.Complete();
    var wait = Stopwatch.StartNew();
    while (!h.Dispatcher.HasCallbacks)
    { if (wait.Elapsed > TimeSpan.FromSeconds(5)) throw new TimeoutException("completion callback missing"); Thread.Sleep(1); }
    // No owner pump: completion is now ahead of the timer in the UI queue.
    h.Clock.Advance(TimeSpan.FromSeconds(1)); h.Drain();
    Check(h.Service.Read().LastReceipt is null && h.Service.Read().LastFailure?.Phase == "timeout"
        && call.Token.IsCancellationRequested, "deadline enforced even when timer callback loses the queue race");
});

Run("shutdown-is-final-before-synchronous-observers-can-reenter", () =>
{
    using var h = new Harness(); Check(h.Service.Reconfigure("difficult", "author-b"), "context pending"); var call = h.Call(0);
    var attempts = new List<SessionDispatchDisposition>();
    h.Service.ContextFinished += _ => attempts.Add(h.Service.Dispatch("capture_now").Disposition);
    h.Service.Changed += () => { if (h.Service.Read().Sequence.Closed) attempts.Add(h.Service.Dispatch("capture_now").Disposition); };
    h.Service.Shutdown();
    Check(attempts.Count > 0 && attempts.All(d => d == SessionDispatchDisposition.Closed) && h.Service.Read().QueuedCount == 0,
        "neither context nor changed observer can enqueue during shutdown");
    call.Complete(); h.Drain();
});

Run("worker-error-recovers-and-snapshot-failure-revokes-authority", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); h.Call(0).Fail(new InvalidOperationException("fixture failure")); h.Drain();
    Check(h.Service.Read().LastFailure?.Phase == "operation" && h.Service.Read().Actions.Single().Phase == UiActionPhase.Failed, "worker error visible");
    h.Service.Dispatch("capture_now"); h.Call(1).Complete(); h.Drain();
    Check(h.Service.Read().LastReceipt?.Success == true && h.Service.Read().LastFailure is null, "later operation recovers");
    h.Runtime.State = TestRuntime.Snapshot(h.Runtime.State.Context, roundActive: true, authority: true);
    h.Service.RefreshRuntime(); h.Runtime.SnapshotError = true; h.Service.RefreshRuntime();
    var v = h.Service.Read();
    Check(v.Runtime.RoundActive && !v.Runtime.LiveAuthorization && !v.Runtime.EngineConnected && v.Runtime.AvailableCommands.Count == 0,
        "snapshot failure preserves actual round lock but removes live grants");
    h.Runtime.SnapshotError = false;
});

Run("active-round-mode-guard-and-pending-author-failure-restore-applied-context", () =>
{
    using var h = new Harness();
    h.Runtime.State = TestRuntime.Snapshot(h.Runtime.State.Context, roundActive: true, authority: true); h.Service.RefreshRuntime();
    h.Service.Dispatch("capture_now"); var call = h.Call(0); var sequence = h.Service.Read().Sequence;
    Check(!h.Service.Reconfigure("difficult", "author-b") && h.Service.Read().Sequence == sequence && !call.Token.IsCancellationRequested,
        "active mode refusal neither invalidates nor cancels");
    Check(h.Service.Reconfigure("nightmare", "author-b"), "same-mode author intent allowed");
    var pending = h.Service.Read();
    Check(pending.ContextPending && pending.Runtime.RoundActive && !pending.Runtime.LiveAuthorization, "pending author keeps round lock while withholding identity");
    call.Complete(); var context = h.Call(1);
    context.Complete(success: false, reason: "author unavailable"); h.Drain();
    var restored = h.Service.Read();
    Check(restored.DesiredSelection.ProfileId == "" && restored.LastReceipt?.Reason == "author unavailable" && !restored.ContextPending,
        "real context refusal restores applied author and preserves reason");
});

Run("atomic-context-supersedes-and-stop-notifies-once", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var call = h.Call(0);
    var finished = new List<SessionSelection>(); h.Service.ContextFinished += finished.Add;
    Check(h.Service.Reconfigure("difficult", "author-b", "s1"), "atomic desired context enqueued");
    call.Complete(); var context = h.Call(1);
    Check(context.Request!.Context.Runtime.Selection == new SessionSelection("s1", "difficult", "author-b"), "all desired fields sealed together");
    h.Service.CancelPending("cancel-context");
    Check(h.Service.Read().DesiredSelection == h.Runtime.State.Context.Selection && finished.Count == 1, "cancel restores actual selection once");
    context.Complete(); h.Drain(); Check(finished.Count == 1 && h.Service.Read().LastReceipt is null, "late context cannot repeat notification");
});

Run("context-commit-and-superseding-terminal-command-use-real-applied-state", () =>
{
    using var h = new Harness(); Check(h.Service.Reconfigure("difficult", "author-b", "s1"), "context submitted");
    var context = h.Call(0); var target = h.Runtime.State.Context with { Season = "s1", Mode = "difficult", ProfileId = "author-b" };
    h.Runtime.State = TestRuntime.Snapshot(target); context.Complete(resultContext: target); h.Drain();
    Check(h.Service.Read().AppliedSelection == target.Selection && !h.Service.Read().ContextPending, "real committed context applied");
    Check(h.Service.Reconfigure("nightmare", "author-c"), "second context submitted"); var second = h.Call(1);
    h.Service.Dispatch("end_round"); second.Complete(); var terminal = h.Call(2);
    Check(terminal.Request!.Context.Runtime.Selection == target.Selection, "terminal command seals applied rather than cancelled desired selection");
    terminal.Complete(); h.Drain();
});

Run("maintenance-bounded-private-and-blocks-resource-lease-until-real-exit", () =>
{
    using var h = new Harness(); h.Service.Dispatch("capture_now"); var foreground = h.Call(0);
    var generation = h.Service.Read().Sequence.Generation;
    Check(h.Service.DispatchMaintenance("one") && h.Service.DispatchMaintenance("two") && !h.Service.DispatchMaintenance("three")
        && !h.Service.DispatchMaintenance("one"), "maintenance bounded and deduplicated");
    Check(!foreground.Token.IsCancellationRequested && h.Service.Read().Sequence.Generation == generation && h.Service.Read().PendingAction == "capture_now",
        "maintenance cannot supersede primary or issue sequence");
    foreground.Complete(); var maintenance = h.Call(1); maintenance.Complete(); var second = h.Call(2); second.Complete(); h.Drain();
    Check(h.Service.Read().LastReceipt?.Action == "capture_now", "maintenance cannot overwrite operation receipt");
    var lease = h.Service.AcquireResourceLease(); Check(lease is not null, "idle lease acquired");
    Check(h.Service.Dispatch("capture_now").Disposition == SessionDispatchDisposition.ResourceLocked && !h.Service.Reconfigure("difficult", "x"), "lease blocks dispatch and preferences");
    h.Service.ReleaseResourceLease("wrong"); Check(h.Service.Read().ResourceLocked, "wrong lease cannot unlock");
    h.Service.ReleaseResourceLease(lease!); Check(!h.Service.Read().ResourceLocked, "matching lease releases");
});

Run("lease-acquire-changed-shutdown-never-returns-revoked-grant", () =>
{
    using var h = new Harness();
    var observedLocked = false;
    h.Service.Changed += () =>
    {
        if (!observedLocked && h.Service.Read().ResourceLocked)
        {
            observedLocked = true;
            h.Service.Shutdown();
        }
    };
    var acquired = h.Service.AcquireResourceLease();
    var view = h.Service.Read();
    Check(observedLocked && acquired is null && view.Sequence.Closed && !view.ResourceLocked,
        "Changed actually observed the grant then revoked it before return");
    Check(h.Runtime.Calls.Count == 0, "lease notification never starts runtime");
});

foreach (var reacquire in new[] { false, true })
Run("lease-denied-call-cannot-return-callback-" + (reacquire ? "replacement" : "released") + "-grant", () =>
{
    using var h = new Harness();
    var original = h.Service.AcquireResourceLease();
    Check(original is not null, "original caller legitimately owns the lease");
    var callbackRan = false;
    string? replacement = null;
    h.Service.Changed += () =>
    {
        if (callbackRan) return;
        callbackRan = true;
        h.Service.ReleaseResourceLease(original!);
        if (reacquire) replacement = h.Service.AcquireResourceLease();
    };
    // This second caller is refused while the original lease is still held.
    // Its real Changed callback releases the known token and may acquire a new
    // token for the callback's own caller. The refused outer call owns neither.
    var denied = h.Service.AcquireResourceLease();
    Check(callbackRan && denied is null && !h.Service.Read().Sequence.Closed,
        "refused outer call never receives another caller's grant");
    if (reacquire)
    {
        Check(replacement is not null && replacement != original && h.Service.Read().ResourceLocked, "callback owns distinct current lease");
        h.Service.ReleaseResourceLease(original!);
        Check(h.Service.Read().ResourceLocked, "old token cannot revoke callback's replacement");
        h.Service.ReleaseResourceLease(replacement!);
    }
    Check(!h.Service.Read().ResourceLocked, "released or replacement lease reaches real unlocked state");
    var fresh = h.Service.AcquireResourceLease();
    Check(fresh is not null && fresh != original && fresh != replacement, "later caller can acquire its own current lease");
    h.Service.ReleaseResourceLease(fresh!);
});

Run("lease-acquire-changed-wrong-release-keeps-own-current-grant", () =>
{
    using var h = new Harness();
    var observed = false;
    h.Service.Changed += () =>
    {
        if (observed || !h.Service.Read().ResourceLocked) return;
        observed = true;
        h.Service.ReleaseResourceLease("not-the-current-lease");
    };
    var acquired = h.Service.AcquireResourceLease();
    Check(observed && acquired is not null && h.Service.Read().ResourceLocked, "incorrect callback token does not suppress a valid grant");
    h.Service.ReleaseResourceLease(acquired!);
    Check(!h.Service.Read().ResourceLocked, "returned token truly controls the current lease");
});

Run("manual-picker-uses-its-own-current-runtime-availability", () =>
{
    using var h = new Harness(); h.Runtime.State = TestRuntime.Snapshot(h.Runtime.State.Context, manualPick: true, commands: []); h.Service.RefreshRuntime();
    Check(h.Service.Dispatch("confirm_route", JsonSerializer.SerializeToElement(new { mapId = "map-a" })).Disposition == SessionDispatchDisposition.Enqueued, "manual picker distinct availability");
    h.Call(0).Complete(); h.Drain(); h.Runtime.State = TestRuntime.Snapshot(h.Runtime.State.Context, commands: []);
    Check(h.Service.Dispatch("confirm_route", JsonSerializer.SerializeToElement(new { mapId = "map-b" })).Disposition == SessionDispatchDisposition.Unavailable,
        "picker checks current flag even without UI refresh");
});

Run("wrong-thread-cannot-mutate-session", () =>
{
    using var h = new Harness();
    var rejected = Task.Run(() => { try { h.Service.Dispatch("capture_now"); return false; }
        catch (InvalidOperationException error) { return error.Message == "session-owner-thread-required"; } }).GetAwaiter().GetResult();
    Check(rejected && h.Runtime.Calls.Count == 0, "owner constraint explicit");
});

Run("result-gate-observers-see-terminal-state-before-cancel-event", () =>
{
    var sequencer = new SessionSequencer(); var gate = new ResultGate(sequencer); var ticket = sequencer.Issue();
    var observed = false;
    gate.ExpiredObserved += t => observed = gate.Submit(t, t.Sequence, DateTimeOffset.UnixEpoch) == ResultDisposition.Expired;
    gate.ExpireThenCancel(ticket); Check(observed, "expired state stored before observers");
});

return failures == 0 ? 0 : 1;

static string FindRoot()
{
    var directory = new DirectoryInfo(AppContext.BaseDirectory);
    while (directory is not null)
    { if (File.Exists(Path.Combine(directory.FullName, "app", "session_state_machine.py"))) return directory.FullName; directory = directory.Parent; }
    throw new InvalidOperationException("source-root-not-found");
}

sealed class OwnerDispatcher : ISessionDispatcher
{
    private readonly int _owner = Environment.CurrentManagedThreadId;
    private readonly ConcurrentQueue<Action> _callbacks = new();
    public bool CheckAccess() => Environment.CurrentManagedThreadId == _owner;
    public bool HasCallbacks => !_callbacks.IsEmpty;
    public void Post(Action action) => _callbacks.Enqueue(action);
    public void Pump() { while (_callbacks.TryDequeue(out var action)) action(); }
}

sealed class Harness : IDisposable
{
    public OwnerDispatcher Dispatcher { get; } = new();
    public TestRuntime Runtime { get; } = new();
    public TestClock Clock { get; } = new();
    public SessionService Service { get; }
    public Harness() => Service = new(Dispatcher, Runtime, Clock);
    public RuntimeCall Call(int index)
    { Until(() => Runtime.Calls.Count > index); return Runtime.Calls.ElementAt(index); }
    public void Drain() => Until(() => Service.DrainAsync().IsCompleted);
    public void Until(Func<bool> done)
    {
        var timeout = Stopwatch.StartNew();
        while (true)
        {
            Dispatcher.Pump(); if (done()) return;
            if (timeout.Elapsed > TimeSpan.FromSeconds(5)) throw new TimeoutException("fixture owner/worker did not drain");
            Thread.Sleep(1);
        }
    }
    public void Dispose()
    {
        Runtime.SnapshotError = false; Service.Shutdown();
        foreach (var call in Runtime.Calls) call.Complete();
        Drain();
    }
}

sealed class TestRuntime : ISessionRuntime, ISessionMaintenanceRuntime, ISessionRuntimeProgress
{
    private RuntimeSnapshot _state = Snapshot(new(RoundId: "round-1", MapId: "map-a", FloorId: "1", ResourceId: "resource-1", FrameId: "frame-1"));
    private int _active;
    private int _maxConcurrent;
    public int MaxConcurrent => _maxConcurrent;
    public bool SnapshotError { get; set; }
    private SessionRequestContext? _progressOwner;
    private bool _progressOverride;
    private SessionRequestContext? _completedOwner;
    public SessionRequestContext? ProgressOwner
    {
        get => _progressOwner;
        set { _progressOwner = value; _progressOverride = true; }
    }
    public SessionRuntimeRead ReadSnapshotFor(SessionRequestContext request)
    {
        if (SnapshotError) throw new InvalidOperationException("fixture snapshot failure");
        var snapshot = State;
        var read = new SessionRuntimeRead(snapshot, _progressOverride ? ProgressOwner == request : _completedOwner == request);
        var after = AfterProgressRead; AfterProgressRead = null;
        after?.Invoke();
        return read;
    }
    public Action? AfterProgressRead { get; set; }
    public RuntimeSnapshot State { get => Volatile.Read(ref _state); set => Volatile.Write(ref _state, value); }
    public ConcurrentQueue<RuntimeCall> Calls { get; } = new();
    public RuntimeSnapshot ReadSnapshot() => SnapshotError ? throw new InvalidOperationException("fixture snapshot failure") : State;
    public async ValueTask<RuntimeOperationResult> DispatchAsync(SessionRuntimeRequest request, CancellationToken cancellationToken)
    {
        _progressOwner = null; _progressOverride = false; _completedOwner = null;
        var call = Start(request, cancellationToken);
        try { return (await call.Result.Task.ConfigureAwait(false))!; }
        finally { _completedOwner = request.Context; Interlocked.Decrement(ref _active); }
    }
    public async ValueTask RememberRenderMaterialAsync(string renderToken, RuntimeContext lifetime, CancellationToken cancellationToken)
    {
        var call = Start(null, cancellationToken);
        try { await call.Result.Task.ConfigureAwait(false); }
        finally { Interlocked.Decrement(ref _active); }
    }
    private RuntimeCall Start(SessionRuntimeRequest? request, CancellationToken token)
    {
        var active = Interlocked.Increment(ref _active);
        Interlocked.Exchange(ref _maxConcurrent, Math.Max(_maxConcurrent, active));
        var call = new RuntimeCall(this, request, token); Calls.Enqueue(call); return call;
    }
    public static RuntimeSnapshot Snapshot(RuntimeContext context, bool roundActive = false, bool authority = false,
        bool manualPick = false, IEnumerable<string>? commands = null)
        => new(context, true, commands ?? ["capture_now", "set_floor", "confirm_route", "set_context", "start_round", "end_round", "toggle_round"],
            roundActive: roundActive, liveEngineAvailable: true, liveAuthorization: authority, manualMapPickAvailable: manualPick,
            state: JsonSerializer.SerializeToElement(new { marker = "real fixture state" }));
}

sealed class RuntimeCall(TestRuntime runtime, SessionRuntimeRequest? request, CancellationToken token)
{
    public SessionRuntimeRequest? Request { get; } = request;
    public CancellationToken Token { get; } = token;
    public int ThreadId { get; } = Environment.CurrentManagedThreadId;
    public TaskCompletionSource<RuntimeOperationResult?> Result { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public void Complete(bool success = true, RuntimeContext? resultContext = null, RuntimeSnapshot? returned = null,
        SessionRequestContext? requestContext = null, string reason = "")
    {
        if (Request is null) { Result.TrySetResult(null); return; }
        Result.TrySetResult(new(new(Request.Action, success, requestContext ?? Request.Context, resultContext, reason), returned ?? runtime.State));
    }
    public void Fail(Exception error) => Result.TrySetException(error);
}

sealed class TestClock : TimeProvider
{
    private DateTimeOffset _now = DateTimeOffset.UnixEpoch;
    private readonly List<ClockTimer> _timers = [];
    public override DateTimeOffset GetUtcNow() => _now;
    public override ITimer CreateTimer(TimerCallback callback, object? state, TimeSpan dueTime, TimeSpan period)
    { var timer = new ClockTimer(this, callback, state, dueTime); _timers.Add(timer); return timer; }
    public void Advance(TimeSpan duration)
    { _now += duration; foreach (var timer in _timers.ToArray()) timer.Fire(); }
    private sealed class ClockTimer(TestClock clock, TimerCallback callback, object? state, TimeSpan dueTime) : ITimer
    {
        private DateTimeOffset? _due = dueTime == Timeout.InfiniteTimeSpan ? null : clock._now + dueTime;
        public bool Change(TimeSpan dueTime, TimeSpan period) { _due = dueTime == Timeout.InfiniteTimeSpan ? null : clock._now + dueTime; return true; }
        public void Dispose() => _due = null;
        public ValueTask DisposeAsync() { Dispose(); return ValueTask.CompletedTask; }
        public void Fire() { if (_due is { } due && due <= clock._now) { _due = null; callback(state); } }
    }
}
