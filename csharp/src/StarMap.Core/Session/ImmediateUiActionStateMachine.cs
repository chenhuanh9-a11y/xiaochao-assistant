using System.Diagnostics;

namespace StarMap.Core.Session;

public enum UiActionPhase { VisualConfirmed, BackgroundEnqueued, Settled, Failed, Cancelled }
public readonly record struct UiActionToken(string Action, long Generation);

public sealed record UiActionSnapshot(
    UiActionToken Token, UiActionPhase Phase, object? TargetState, object? VisibleState,
    double InputAtMs, double VisualAtMs, double? EnqueuedAtMs = null,
    double? SettledAtMs = null, int CoalescedInputs = 0, string? Reason = null)
{
    public double VisualLatencyMs => Math.Max(0, VisualAtMs - InputAtMs);
    public double? EnqueueLatencyMs => EnqueuedAtMs is double time ? Math.Max(0, time - InputAtMs) : null;
    public double? SettleLatencyMs => SettledAtMs is double time ? Math.Max(0, time - InputAtMs) : null;
    public double? BackgroundLatencyMs => EnqueuedAtMs is double start && SettledAtMs is double end
        ? Math.Max(0, end - start) : null;
}

public sealed record UiActionDecision(UiActionSnapshot Snapshot, bool Accepted,
    bool Coalesced = false, bool Stale = false, bool ShouldEnqueueBackground = false,
    UiActionToken? AttemptedToken = null)
{
    public UiActionToken Token => AttemptedToken ?? Snapshot.Token;
}

/// <summary>
/// Direct port of app/ui_action_state.py's immediate intent lifecycle. The owner
/// calls Begin synchronously for visible acknowledgement; worker results return
/// to that same owner before Settle/Fail/Cancel. It owns action tokens only,
/// never the session generation owned by SessionSequencer.
/// </summary>
public sealed class ImmediateUiActionStateMachine
{
    private readonly Func<double> _clockMs;
    private readonly Dictionary<string, long> _generations = new(StringComparer.Ordinal);
    private readonly Dictionary<string, UiActionSnapshot> _actions = new(StringComparer.Ordinal);

    public ImmediateUiActionStateMachine(Func<double>? clockMs = null)
        => _clockMs = clockMs ?? (() => Stopwatch.GetTimestamp() * 1000.0 / Stopwatch.Frequency);

    public UiActionSnapshot? Snapshot(string action) => _actions.GetValueOrDefault(action);
    public IReadOnlyList<UiActionSnapshot> ReadAll() => Array.AsReadOnly(_actions.Values.ToArray());

    public UiActionDecision Begin(string action, object? targetState, object? visualState, double? nowMs = null)
    {
        action = action.Trim();
        ArgumentException.ThrowIfNullOrWhiteSpace(action);
        var now = nowMs ?? _clockMs();
        var current = Snapshot(action);
        if (current is not null && Pending(current.Phase) && Equals(current.TargetState, targetState))
        {
            current = current with { CoalescedInputs = current.CoalescedInputs + 1 };
            _actions[action] = current;
            return new(current, true, Coalesced: true);
        }
        var generation = _generations.GetValueOrDefault(action) + 1;
        _generations[action] = generation;
        current = new(new(action, generation), UiActionPhase.VisualConfirmed,
            targetState, visualState, now, now);
        _actions[action] = current;
        return new(current, true, ShouldEnqueueBackground: true);
    }

    public bool AcceptsBackgroundResult(UiActionToken token) => CurrentPending(token) is not null;

    public UiActionDecision MarkEnqueued(UiActionToken token, double? nowMs = null)
    {
        var current = CurrentPending(token);
        if (current is null) return Rejected(token);
        if (current.Phase == UiActionPhase.BackgroundEnqueued) return new(current, true, Coalesced: true);
        var now = nowMs ?? _clockMs();
        if (now < current.VisualAtMs) throw new ArgumentException("enqueue timestamp precedes visual confirmation");
        current = current with { Phase = UiActionPhase.BackgroundEnqueued, EnqueuedAtMs = now };
        _actions[token.Action] = current;
        return new(current, true);
    }

    public UiActionDecision Settle(UiActionToken token, object? finalState = null, double? nowMs = null)
        => Finish(token, UiActionPhase.Settled, finalState, null, nowMs);

    public UiActionDecision Fail(UiActionToken token, object? fallbackState, string reason, double? nowMs = null)
        => Finish(token, UiActionPhase.Failed, fallbackState, reason, nowMs);

    public UiActionDecision Cancel(UiActionToken token, object? visibleState = null,
        string reason = "cancelled", double? nowMs = null)
        => Finish(token, UiActionPhase.Cancelled, visibleState, reason, nowMs);

    private UiActionDecision Finish(UiActionToken token, UiActionPhase phase, object? visible,
        string? reason, double? nowMs)
    {
        var current = CurrentPending(token);
        if (current is null) return Rejected(token);
        var now = nowMs ?? _clockMs();
        if (now < current.InputAtMs || current.EnqueuedAtMs is double enqueued && now < enqueued)
            throw new ArgumentException("terminal timestamp precedes input or enqueue");
        current = current with
        {
            Phase = phase, SettledAtMs = now, Reason = reason,
            VisibleState = phase == UiActionPhase.Settled ? visible ?? current.TargetState
                : phase == UiActionPhase.Cancelled ? visible ?? current.VisibleState : visible,
        };
        _actions[token.Action] = current;
        return new(current, true);
    }

    private UiActionSnapshot? CurrentPending(UiActionToken token)
        => Snapshot(token.Action) is { } current && current.Token == token && Pending(current.Phase) ? current : null;

    private UiActionDecision Rejected(UiActionToken token) => new(
        Snapshot(token.Action) ?? new(token, UiActionPhase.Cancelled, null, null, 0, 0, Reason: "unknown-action"),
        false, Stale: true, AttemptedToken: token);

    private static bool Pending(UiActionPhase phase)
        => phase is UiActionPhase.VisualConfirmed or UiActionPhase.BackgroundEnqueued;
}
