using System.Globalization;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using OpenCvSharp;

internal static class CaptureProof
{
    // The index supplies paths and user season/mode only. Re-read the original
    // capture record; detector outputs, annotations and map truth are not inputs.
    internal static JsonObject Validate(JsonObject sample, Mat frame)
    {
        string journal = Path.GetFullPath(ReplayJson.Text(sample, "captureRecord"));
        string image = Path.GetFullPath(ReplayJson.Text(sample, "image"));
        var record = ReplayJson.Read(journal).AsObject();
        string declared = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(journal)!, ReplayJson.Text(record, "samplePath")));
        var pathComparison = OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
        if (!string.Equals(image, declared, pathComparison) ||
            !string.Equals(Path.GetDirectoryName(image), Path.GetDirectoryName(journal), pathComparison))
            throw new InvalidDataException("Original capture record must bind this same-directory full-client image.");
        if (ReplayJson.Text(record, "evidenceKind") != "live-client-capture" ||
            ReplayJson.Text(record, "eventKind") != "physical-g-client-capture" ||
            record["cropProvenance"]?.ToString() == "color-component-unvalidated")
            throw new InvalidDataException("Expected the original physical G full-client capture, without replacement crops.");
        string rawTime = ReplayJson.Text(record, "capturedAt");
        string indexTime = ReplayJson.Text(sample, "capturedAt");
        if (!(rawTime.EndsWith('Z') || Regex.IsMatch(rawTime, @"[+-]\d{2}:\d{2}$")) ||
            !DateTimeOffset.TryParse(rawTime, CultureInfo.InvariantCulture, DateTimeStyles.None, out var captured) ||
            !DateTimeOffset.TryParse(indexTime, CultureInfo.InvariantCulture, DateTimeStyles.None, out var indexed) ||
            captured != indexed || DateOnly.FromDateTime(captured.Date) < new DateOnly(2026, 11, 5))
            throw new InvalidDataException("Capture timestamp is old, unzoned, or not bound to the input index.");
        var label = record["userLabel"]!.AsObject();
        if (!JsonNode.DeepEquals(label, record["labelAtCommit"]) || record["labelIsUserSupplied"]?.GetValue<bool>() != true ||
            ReplayJson.Text(label, "Season") != ReplayJson.Text(sample, "season") ||
            ReplayJson.Text(label, "Mode") != ReplayJson.Text(sample, "mode") ||
            !JsonNode.DeepEquals(label["Generation"], sample["labelGeneration"]) ||
            record["Sequence"]!.GetValue<int>() != sample["event"]!.GetValue<int>())
            throw new InvalidDataException("User season/mode/label generation changed during capture or differs from the index.");
        var before = record["Metadata"]!["Before"]!.AsObject();
        var after = record["Metadata"]!["After"]!.AsObject();
        if (!JsonNode.DeepEquals(before, after) || !JsonNode.DeepEquals(before, sample["physicalClient"]) ||
            frame.Empty() || frame.Type() != MatType.CV_8UC3 ||
            frame.Cols != before["Width"]!.GetValue<int>() || frame.Rows != before["Height"]!.GetValue<int>())
            throw new InvalidDataException("Full-client geometry changed, or original PNG dimensions do not match capture metadata.");
        return new JsonObject
        {
            ["captureRecord"] = journal, ["image"] = image, ["capturedAt"] = rawTime,
            ["physicalClient"] = before.DeepClone(), ["decodedWidth"] = frame.Cols, ["decodedHeight"] = frame.Rows,
            ["label"] = label.DeepClone(), ["fullClientValidated"] = true,
            ["scope"] = "Original capture binding/date/client geometry; no independent map or pose truth"
        };
    }
}
