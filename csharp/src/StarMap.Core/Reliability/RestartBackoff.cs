namespace StarMap.Core.Reliability;

/// <summary>
/// Placeholder crash-restart budget. Phase 1 does not spawn or restart a
/// process. Returning null means "do not restart": shutdown, or the attempt
/// cap, both stop the loop so a crash cannot become a restart storm.
/// The delays are not an accepted production budget.
/// </summary>
public sealed class RestartBackoff
{
    public const int MaxAttempts = 5;
    public static readonly TimeSpan InitialDelay = TimeSpan.FromMilliseconds(200);
    public static readonly TimeSpan DelayCap = TimeSpan.FromSeconds(5);

    private int _attempts;
    private bool _shutdown;

    public int Attempts => _attempts;
    public bool Shutdown => _shutdown;

    public void MarkShutdown() => _shutdown = true;

    public TimeSpan? NextDelayAfterCrash()
    {
        if (_shutdown)
            return null;
        _attempts++;
        if (_attempts > MaxAttempts)
            return null;
        var milliseconds = InitialDelay.TotalMilliseconds * Math.Pow(2, _attempts - 1);
        if (milliseconds > DelayCap.TotalMilliseconds)
            milliseconds = DelayCap.TotalMilliseconds;
        return TimeSpan.FromMilliseconds(milliseconds);
    }
}
