using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;

namespace StarMap.Vision;

public sealed record EntryPassportPreflight(bool NeedsLocalGeometry, JsonObject Diagnostics);

/// <summary>One private BGR snapshot and its no-context exit observations.</summary>
public sealed class EntryPassportFrame : IDisposable
{
    private readonly EntryPassportCatalog catalog;
    private readonly Mat frame;
    private SideDoorObservation? detector;
    private JsonArray? observations;
    private bool disposed;

    public EntryPassportFrame(EntryPassportCatalog catalog, Mat frame)
    {
        this.catalog = catalog;
        this.frame = frame.Clone();
    }

    public void Dispose()
    {
        if (disposed) return;
        detector?.Dispose();
        frame.Dispose();
        disposed = true;
    }

    private JsonArray Observe(CancellationToken cancellation)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        cancellation.ThrowIfCancellationRequested();
        if (observations != null) return observations;
        detector ??= new SideDoorObservation(catalog.ExitIconPath ??
            throw new NotSupportedException("This mode has no declared side-exit observer."));
        return observations = detector.Observe(frame, cancellation: cancellation).Observations;
    }

    /// <summary>Runs rank_entry's coverage and exit checks, up to the first request for local geometry.</summary>
    public EntryPassportPreflight Inspect(string? floor, TimeSpan? budget = null, CancellationToken cancellation = default)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        cancellation.ThrowIfCancellationRequested();
        long started = System.Diagnostics.Stopwatch.GetTimestamp();
        var meta = new JsonObject {
            ["exitObserved"] = false, ["entryObserved"] = false, ["complete"] = true,
            ["verificationComplete"] = false, ["coverageComplete"] = false,
            ["identityAmbiguous"] = true, ["poseAmbiguous"] = true, ["examined"] = new JsonArray()
        };
        if (floor != null && !catalog.Floors.Contains(floor)) return Finish("entry-unsupported-floor");
        if (System.Diagnostics.Stopwatch.GetElapsedTime(started) >= (budget ?? TimeSpan.FromSeconds(2)))
        {
            meta["complete"] = false;
            return Finish("entry-budget-incomplete");
        }
        var coverage = catalog.Coverage.Where(c => floor == null || c.GetProperty("floor").GetString() == floor).ToArray();
        if (floor != null && catalog.NativePassportError == null &&
            !catalog.Passports.Any(p => p.GetProperty("floor").GetString() == floor) &&
            !coverage.Any(c => c.GetProperty("complete").GetBoolean()))
        {
            meta["coverageGaps"] = Rows(coverage);
            meta["exitObserved"] = null;
            meta["observationStatus"] = "source-inventory-incomplete";
            return Finish("entry-coverage-incomplete");
        }
        var current = Observe(cancellation);
        meta["observations"] = current.DeepClone();
        bool exitObserved = current.Any(o => o!["reliable"]!.GetValue<bool>());
        meta["exitObserved"] = exitObserved;
        if (!exitObserved)
        {
            meta["verificationComplete"] = true;
            return Finish("entry-no-reliable-exit");
        }
        var gaps = coverage.Where(c => !c.GetProperty("complete").GetBoolean()).ToArray();
        meta["coverageGaps"] = Rows(gaps);
        bool complete = coverage.Length > 0 && gaps.Length == 0 && catalog.NativePassportError == null;
        meta["coverageComplete"] = complete;
        if (catalog.NativePassportError != null) return Finish("entry-passport-unavailable");
        if (!complete) return Finish("entry-coverage-incomplete");
        // The source continues into wall fitting, room evidence and competition here.
        meta["complete"] = false;
        return new(true, meta);

        EntryPassportPreflight Finish(string reason)
        {
            meta["reason"] = reason;
            return new(false, meta);
        }
    }

    /// <summary>Checks a proposed pose. A previous observation from this instance can be reused on this same frame.</summary>
    public JsonObject Check(JsonObject candidate, CancellationToken cancellation = default)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        cancellation.ThrowIfCancellationRequested();
        return EntryPassportEvidence.Check(catalog, candidate, observations, () => Observe(cancellation));
    }

    private static JsonArray Rows(IEnumerable<JsonElement> rows) =>
        new(rows.Select(row => JsonNode.Parse(row.GetRawText())).ToArray());
}
