namespace StarMap.Core.Session;

/// <summary>
/// Sole writer of session identity and request sequence numbers.
/// UI, recognition workers, and <see cref="Reliability.ResultGate"/> may read
/// <see cref="Read"/> and may submit a result, but they cannot advance
/// <see cref="SessionSnapshot.LastIssuedSequence"/> or
/// <see cref="SessionSnapshot.Generation"/>.
/// Mirrors Qt <c>SessionService</c> owning <c>_generation</c> and the request
/// context (<c>experiments/qt_quick/client_session.py</c>).
/// </summary>
public sealed class SessionSequencer
{
    private long _generation;
    private long _lastIssued;
    private long _supersededThrough;
    private bool _closed;

    public SessionSequencer()
        : this(Guid.NewGuid().ToString("N"))
    {
    }

    public SessionSequencer(string sessionId)
    {
        if (string.IsNullOrWhiteSpace(sessionId))
            throw new ArgumentException("session-id", nameof(sessionId));
        SessionId = sessionId;
    }

    public string SessionId { get; }

    public SessionSnapshot Read()
        => new(SessionId, _generation, _lastIssued, _supersededThrough, _closed);

    /// <summary>
    /// Issues the next request. The previous sequence is superseded: a late
    /// result for it must be discarded.
    /// </summary>
    public RequestTicket Issue(DateTimeOffset? deadlineUtc = null)
    {
        if (_closed)
            throw new InvalidOperationException("session-closed");
        _lastIssued++;
        _supersededThrough = _lastIssued - 1;
        return new RequestTicket(SessionId, _generation, _lastIssued, deadlineUtc);
    }

    /// <summary>
    /// Drops every outstanding request. Used for close-map, floor change,
    /// map change, round end, resource swap, and shutdown.
    /// </summary>
    public void Invalidate(string reason)
    {
        if (string.IsNullOrWhiteSpace(reason))
            throw new ArgumentException("reason", nameof(reason));
        if (_closed)
            return;
        _generation++;
        _supersededThrough = _lastIssued;
    }

    public void Close() => _closed = true;
}

public readonly record struct SessionSnapshot(
    string SessionId,
    long Generation,
    long LastIssuedSequence,
    long SupersededThrough,
    bool Closed);

public readonly record struct RequestTicket(
    string SessionId,
    long Generation,
    long Sequence,
    DateTimeOffset? DeadlineUtc);
