using StarMap.Core.Selection;

internal static class StructurePoolRegression
{
    internal static void Run()
    {
        var pool = new StructureCandidatePool("round1", "resource1", ["a", "b", "c"]);
        string Observe(bool verified, string[] evaluated, string[] bad, params StructureIndexCheck[] checks)
            => pool.Observe(verified, evaluated, bad, checks);
        void Require(bool value, string message) { if (!value) throw new InvalidOperationException(message); }
        Require(Observe(true, ["a", "b", "c"], ["c"], new("a", false), new("b", false), new("c", true)) == "structure-pool-narrowed"
            && pool.Remaining.SequenceEqual(["a", "b"]), "positive contradictions narrow the round pool");
        Require(Observe(false, ["a", "b"], [], []) == "structure-coordinate-unknown" && pool.Remaining.SequenceEqual(["a", "b"]),
            "new-floor insufficient structure retains the first floor's elimination");
        Require(Observe(true, ["a", "b", "c"], [], new("a", false), new("b", false), new("c", false)) == "structure-coordinate-unknown"
            && pool.Remaining.SequenceEqual(["a", "b"]), "a late full-catalogue result cannot restore eliminated candidates");
        Require(Observe(false, ["a", "b"], [], new("a", true), new("b", true)) == "structure-coordinate-conflict-retained"
            && pool.Remaining.SequenceEqual(["a", "b"]), "complete contradiction is retained as conflict instead of restoring the catalogue");
        Require(Observe(true, ["a", "b"], ["b"], new("a", false), new("b", true)) == "structure-pool-narrowed"
            && pool.Remaining.SequenceEqual(["a"]) && pool.LockedMapId is null, "a later floor narrows to one without granting material");
        Require(!pool.AcceptCurrentMaterial("b") && pool.AcceptCurrentMaterial("a") && pool.LockedMapId == "a",
            "only the surviving map's current material can finalize identity");
        Require(Observe(false, ["a"], [], []) == "structure-pool-identity-retained" && pool.Remaining.SequenceEqual(["a"]),
            "locked identity survives a sparse later floor without granting that floor a pose");
        var next = new StructureCandidatePool("round2", "resource1", ["a", "b", "c"]);
        Require(next.Remaining.Count == 3 && pool.Remaining.Count == 1, "only a new round creates a full pool");
    }
}
