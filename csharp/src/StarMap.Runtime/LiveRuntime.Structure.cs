using System.Text.Json;
using OpenCvSharp;
using StarMap.Core.Selection;
using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private RuntimeOperationResult ScanStructure(SessionRuntimeRequest request, long epoch, CancellationToken token,
        CapturedClientFrame captured, BoundResources bound, string floor, Mat frame, JsonElement preparation)
    {
        StructureEvidenceSession session; ObservationTicket ticket; string floorSource;
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            session = _structureSession ??= new(bound.StructureCatalog!, _roundId!);
            ticket = session.BeginObservation(floor); _structureTicket = ticket;
            floorSource = _manualFloor is null ? "current-frame-verified" : "operator-declared";
        }
        using var pipeline = new SharedStructureFitPipeline(bound.Catalog);
        var scanner = pipeline.CreateScanner(bound.StructureCatalog!);
        var report = scanner.Scan(frame, new(floor, floorSource,
            CandidateOrder: session.PriorityOrder(floor),
            Cancelled: () => Obsolete(epoch, token, captured, bound), OnProgress: progress =>
            {
                lock (_sync)
                {
                    if (Obsolete(epoch, token, captured, bound) || _structureTicket != ticket) return;
                    if (session.Commit(progress, ticket)) PublishStructure(progress);
                }
            }));
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            if (_structureTicket != ticket || !session.Commit(report, ticket))
                return FailCurrent(request, epoch, "structure-observation-obsolete");
            PublishStructure(report);
            return Receipt(request, _candidates.Length > 0, _reason);
        }

        void PublishStructure(System.Text.Json.Nodes.JsonObject result)
        {
            // A supported pose remains a candidate, including a single survivor.
            // UNKNOWN fits do not establish negative identity evidence.
            _candidates = result["supportedMapIds"]!.AsArray().Select(id => new RuntimeCandidate(
                id!.GetValue<string>(), "shared-structure", 0, false, true)).ToArray();
            _selected = _candidates.Length > 0 ? 0 : -1;
            _identitySource = "s1-structure-candidates";
            _reason = _candidates.Length > 0 ? "s1-structure-candidates-awaiting-confirmation" : "s1-structure-candidates-unresolved";
            _diagnostics = JsonSerializer.SerializeToElement(new { stage = "s1-structure", floor = preparation.GetProperty("floor"), report = result,
                session = session.Snapshot(), preparation }, Json);
            Publish();
        }
    }
}
