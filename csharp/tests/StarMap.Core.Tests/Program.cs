using StarMap.Core.Frames;
using StarMap.Core.Input;
using StarMap.Core.Protocol;
using StarMap.Core.Recognition;
using StarMap.Core.Reliability;
using StarMap.Core.Session;

var failed = 0;
void Check(bool condition, string name)
{
    if (condition)
    {
        Console.WriteLine("ok " + name);
        return;
    }
    failed++;
    Console.Error.WriteLine("FAIL " + name);
}

var session = new SessionSequencer("session-fixed");
var first = session.Issue();
var second = session.Issue();
Check(first.Sequence == 1 && second.Sequence == 2, "sequence-monotonic");
Check(first.SessionId == "session-fixed" && second.Generation == 0, "session-identity");
var late = new ResultGate(session);
Check(late.Submit(first, first.Sequence, DateTimeOffset.UnixEpoch) == ResultDisposition.StaleSequence,
    "late-sequence-discarded");
Check(late.Submit(second, second.Sequence, DateTimeOffset.UnixEpoch) == ResultDisposition.Accepted,
    "current-sequence-accepted");
Check(late.Submit(second, second.Sequence, DateTimeOffset.UnixEpoch) == ResultDisposition.Duplicate,
    "duplicate-discarded");

var mismatched = new SessionSequencer("other-session").Issue();
Check(late.Submit(mismatched, mismatched.Sequence, DateTimeOffset.UnixEpoch) == ResultDisposition.SessionMismatch,
    "session-mismatch");
Check(late.Submit(second, 99, DateTimeOffset.UnixEpoch) == ResultDisposition.FutureSequence,
    "future-sequence");

session.Invalidate("floor-change");
Check(session.Read().Generation == 1, "generation-bumped");
var afterInvalidate = session.Issue();
Check(late.Submit(second, second.Sequence, DateTimeOffset.UnixEpoch) == ResultDisposition.GenerationMismatch,
    "old-generation-discarded");
Check(afterInvalidate.Sequence == 3 && afterInvalidate.Generation == 1, "sequence-not-reset");

var ordered = new List<string>();
var timing = new ResultGate(session);
var current = session.Issue();
timing.ExpiredObserved += _ => ordered.Add("expired");
timing.CancelObserved += _ => ordered.Add("cancelled");
timing.ExpireThenCancel(current);
Check(ordered.Count == 2 && ordered[0] == "expired" && ordered[1] == "cancelled", "expire-then-cancel");
Check(timing.Submit(current, current.Sequence, DateTimeOffset.UnixEpoch) == ResultDisposition.Expired,
    "expired-before-cancelled");

var deadlineSession = new SessionSequencer("deadline");
var deadlineGate = new ResultGate(deadlineSession);
var due = deadlineSession.Issue(DateTimeOffset.UnixEpoch.AddMinutes(1));
Check(deadlineGate.Submit(due, due.Sequence, DateTimeOffset.UnixEpoch.AddMinutes(2)) == ResultDisposition.Expired,
    "deadline-expired");
var open = deadlineSession.Issue(DateTimeOffset.UnixEpoch.AddMinutes(5));
Check(deadlineGate.Submit(open, open.Sequence, DateTimeOffset.UnixEpoch.AddMinutes(2)) == ResultDisposition.Accepted,
    "deadline-still-open");

deadlineSession.Close();
Check(deadlineGate.Submit(open, open.Sequence, DateTimeOffset.UnixEpoch) == ResultDisposition.SessionClosed,
    "closed-session");
var closedIssue = false;
try
{
    deadlineSession.Issue();
}
catch (InvalidOperationException error)
{
    closedIssue = error.Message == "session-closed";
}
Check(closedIssue, "closed-session-rejects-issue");

var backoff = new RestartBackoff();
var delays = new List<double>();
for (int i = 0; i < 5; i++)
    delays.Add(backoff.NextDelayAfterCrash()!.Value.TotalMilliseconds);
Check(delays.SequenceEqual(new double[] { 200, 400, 800, 1600, 3200 }), "backoff-schedule");
Check(backoff.NextDelayAfterCrash() is null, "backoff-cap");
var stopped = new RestartBackoff();
stopped.MarkShutdown();
Check(stopped.NextDelayAfterCrash() is null && stopped.Attempts == 0, "backoff-shutdown");

var ok = ProtocolHandshake.Exchange(new HandshakeHello(1, "starmap-recognition-slice", "grid-slice-v1"));
Check(ok.Accepted && ok.ProtocolVersion == 1, "handshake-accept");
Check(!ProtocolHandshake.Exchange(new HandshakeHello(2, "starmap-recognition-slice", "grid-slice-v1")).Accepted,
    "handshake-version");
Check(!ProtocolHandshake.Exchange(new HandshakeHello(1, "", "grid-slice-v1")).Accepted, "handshake-component");
Check(!ProtocolHandshake.Exchange(new HandshakeHello(1, "starmap-recognition-slice", "passport")).Accepted,
    "handshake-capability");

foreach (var key in new[] { 0x5B, 0x09, 0x73, 0x1B, 0x47, 0x70 })
{
    var observation = HotkeyPolicy.Observe(key);
    Check(observation.PassedToSystem && !observation.Consumed && !HotkeyPolicy.TryConsume(key),
        "hotkey-not-consumed-" + key.ToString("X2"));
}

var edge = new byte[] { 1, 1, 0, 1 };
var sampled = GridSlice.Evaluate(edge, 2, 2, new ushort[] { 0 }, 1, 1, 1.0, null, null, null);
Check(sampled.Reason == GridSlice.InsufficientEdges && sampled.GatePointCount == 3, "tiny-mask-insufficient");
Check(sampled.HashedPoints.Length == 3
    && sampled.HashedPoints[0].X == 0 && sampled.HashedPoints[0].Y == 0
    && sampled.HashedPoints[1].X == 1 && sampled.HashedPoints[1].Y == 0
    && sampled.HashedPoints[2].X == 1 && sampled.HashedPoints[2].Y == 1,
    "nonzero-order");

var missing = false;
try
{
    RecordedFrame.Load(Path.Combine("no-such-user", "C:\\Users\\secret"));
}
catch (InvalidOperationException error)
{
    missing = error.Message == "recorded-frame-unreadable"
        && !error.Message.Contains("Users", StringComparison.OrdinalIgnoreCase)
        && !error.Message.Contains("secret", StringComparison.OrdinalIgnoreCase);
}
Check(missing, "loader-omits-path");

var fixture = FindFixture("black-frame");
if (fixture is null)
{
    Check(false, "black-frame-fixture-present");
}
else
{
    var loaded = RecordedFrame.Load(fixture);
    var black = GridSlice.Evaluate(
        loaded.Edge, loaded.EdgeHeight, loaded.EdgeWidth, loaded.Distance,
        loaded.PreparedHeight, loaded.PreparedWidth, loaded.Scale, loaded.PriorX, loaded.PriorY, null);
    Check(black.Reason == GridSlice.InsufficientEdges && black.GatePointCount == 0 && black.Rows.Length == 0,
        "black-frame");
    var priorDir = FindFixture("prior-local");
    Check(priorDir is not null, "prior-fixture-present");
    if (priorDir is not null)
    {
        var prior = RecordedFrame.Load(priorDir);
        var cancelled = GridSlice.Evaluate(
            prior.Edge, prior.EdgeHeight, prior.EdgeWidth, prior.Distance,
            prior.PreparedHeight, prior.PreparedWidth, prior.Scale, prior.PriorX, prior.PriorY,
            () => true);
        Check(cancelled.Cancelled && cancelled.Rows.Length == 0 && cancelled.Reason == GridSlice.GridResult,
            "cancel-returns-empty");
    }
}

return failed == 0 ? 0 : 1;

static string? FindFixture(string id)
{
    var dir = new DirectoryInfo(AppContext.BaseDirectory);
    while (dir is not null)
    {
        var candidate = Path.Combine(dir.FullName, "csharp", "fixtures", id);
        if (File.Exists(Path.Combine(candidate, "manifest.json")))
            return candidate;
        dir = dir.Parent;
    }
    return null;
}
