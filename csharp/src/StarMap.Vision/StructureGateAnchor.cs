using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision;

/// <summary>A current-frame glyph observation. Raster size never supplies map scale or identity.</summary>
public sealed record StructureGateAnchor
{
    public required string Status { get; init; }
    public required string Reason { get; init; }
    public Point2d? CenterPx { get; init; }
    public double? LocalizationUncertaintyPx { get; init; }
    public double[]? CenterSupportBoundsPx { get; init; }
    public int[]? GlyphRasterSizePx { get; init; }
    public int[]? RasterSupportRangePx { get; init; }
    public double? Score { get; init; }
    public double? DistinctSecondScore { get; init; }
    public double? ScoreMargin { get; init; }
    public required string TemplateSha256 { get; init; }
    public required string ProfileSha256 { get; init; }
    public required JsonObject Diagnostics { get; init; }
    public bool AllowStructureFallback => true;
    internal Mat? Frame { get; init; }

    public JsonObject ToJson() => new()
    {
        ["status"] = Status, ["reason"] = Reason,
        ["center_px"] = CenterPx is { } center ? new JsonArray(center.X, center.Y) : null,
        ["localization_uncertainty_px"] = LocalizationUncertaintyPx,
        ["center_support_bounds_px"] = CenterSupportBoundsPx is { } bounds ? JsonSerializer.SerializeToNode(bounds) : null,
        ["glyph_raster_size_px"] = GlyphRasterSizePx is { } size ? JsonSerializer.SerializeToNode(size) : null,
        ["raster_support_range_px"] = RasterSupportRangePx is { } range ? JsonSerializer.SerializeToNode(range) : null,
        ["score"] = Score, ["distinct_second_score"] = DistinctSecondScore, ["score_margin"] = ScoreMargin,
        ["template_sha256"] = TemplateSha256, ["profile_sha256"] = ProfileSha256,
        ["diagnostics"] = Diagnostics.DeepClone(), ["allow_structure_fallback"] = true
    };
}

/// <summary>Candidate-independent port of s1_gate_anchor.GateAnchorLocator.</summary>
public sealed class StructureGateAnchorLocator : IDisposable
{
    // The source's validated default profile; this adapter does not silently tune it per map/mode.
    private const int MinimumRaster = 19, MaximumRaster = 45, PeaksPerScale = 3, MaximumProposals = 12;
    private const double MinimumScore = .84, MinimumMargin = .12, NearPeakDrop = .10;
    private readonly Dictionary<int, Mat> _fine = [];
    private readonly List<(int Width, Mat Template)> _coarse = [];
    private bool _disposed;
    public string TemplateSha256 { get; }
    public string ProfileSha256 { get; }

    public StructureGateAnchorLocator(string assetDirectory)
    {
        using var manifestFile = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(assetDirectory, "template_manifest.json")));
        var manifest = manifestFile.RootElement;
        if (manifest.ValueKind != JsonValueKind.Object || !manifest.TryGetProperty("template_sha256", out var templateDigest)
            || templateDigest.ValueKind != JsonValueKind.String || !manifest.TryGetProperty("gray_pixels_sha256", out var grayDigest)
            || grayDigest.ValueKind != JsonValueKind.String || !manifest.TryGetProperty("template_size_px", out var size)
            || size.ValueKind != JsonValueKind.Array || size.GetArrayLength() != 2
            || size.EnumerateArray().Any(value => value.ValueKind != JsonValueKind.Number || !value.TryGetInt32(out int n) || n <= 0))
            throw new InvalidDataException("Invalid glyph template manifest");
        byte[] data = File.ReadAllBytes(Path.Combine(assetDirectory, "native_glyph_template.png"));
        TemplateSha256 = Convert.ToHexStringLower(SHA256.HashData(data));
        if (TemplateSha256 != templateDigest.GetString())
            throw new InvalidDataException("Glyph template digest does not match offline provenance");
        using var bgr = Cv2.ImDecode(data, ImreadModes.Color);
        var shape = size.EnumerateArray().Select(v => v.GetInt32()).ToArray();
        if (bgr.Empty() || !shape.SequenceEqual(new[] { bgr.Cols, bgr.Rows }))
            throw new InvalidDataException("Invalid glyph template shape");
        using var gray = new Mat(); Cv2.CvtColor(bgr, gray, ColorConversionCodes.BGR2GRAY);
        if (Convert.ToHexStringLower(SHA256.HashData(Geometry.Bytes(gray))) != grayDigest.GetString())
            throw new InvalidDataException("Decoded grayscale glyph differs from pinned calibration");
        try
        {
            for (int width = MinimumRaster; width <= MaximumRaster; width++)
            {
                var template = new Mat(); Cv2.Resize(gray, template, new Size(width, width), interpolation: InterpolationFlags.Area);
                _fine.Add(width, template);
            }
            var widths = Enumerable.Range(0, (MaximumRaster - MinimumRaster) / 4 + 1).Select(i => MinimumRaster + 4 * i).ToList();
            if (widths[^1] != MaximumRaster) widths.Add(MaximumRaster);
            foreach (int width in widths)
            {
                int half = Math.Max(4, Numerics.Rint(width / 2.0));
                var template = new Mat(); Cv2.Resize(gray, template, new Size(half, half), interpolation: InterpolationFlags.Area);
                _coarse.Add((width, template));
            }
        }
        catch { Dispose(); throw; }
        // Exact Python json.dumps(profile, sort_keys=True), including its spaces and default numeric spelling.
        string profile = "{\"algorithm\": \"half-resolution-NCC-local-full-resolution-v1\", \"config\": {\"coarse_step_px\": 4, \"max_raster_px\": 45, \"max_spatial_proposals\": 12, \"min_distinct_margin\": 0.12, \"min_raster_px\": 19, \"min_score\": 0.84, \"near_peak_drop\": 0.1, \"peaks_per_scale\": 3}, \"template_sha256\": \"" + TemplateSha256 + "\"}";
        ProfileSha256 = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(profile)));
    }

    public StructureGateAnchor Detect(Mat frame, Func<bool>? cancelled = null)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (frame.Depth() != MatType.CV_8U || frame.Channels() is not (1 or 3 or 4))
            throw new ArgumentException("Expected uint8 gray, BGR or BGRA pixels", nameof(frame));
        using var gray = new Mat();
        if (frame.Channels() == 1) frame.CopyTo(gray);
        else Cv2.CvtColor(frame, gray, frame.Channels() == 3 ? ColorConversionCodes.BGR2GRAY : ColorConversionCodes.BGRA2GRAY);
        int coarseScans = 0, fineScans = 0;
        var diagnostics = new JsonObject
        {
            ["full_frame_coarse_scans"] = 0, ["spatial_proposals"] = 0, ["fine_scans"] = 0,
            ["template_raster_size_px"] = new JsonArray(60, 60), ["raster_size_is_map_zoom"] = false,
            ["uncertainty_kind"] = "near-peak pixel support, not a calibrated probability",
            ["profile_validated_on_limited_static_samples"] = true
        };
        StructureGateAnchor Unknown(string reason, double? score = null, double? second = null, double? margin = null) => new()
        {
            Status = "unknown", Reason = reason, Score = score, DistinctSecondScore = second, ScoreMargin = margin,
            TemplateSha256 = TemplateSha256, ProfileSha256 = ProfileSha256, Diagnostics = diagnostics, Frame = frame
        };
        if (cancelled?.Invoke() == true) return Unknown("gate-work-obsolete");
        if (Math.Min(gray.Rows, gray.Cols) < MinimumRaster) return Unknown("frame_too_small");
        using var small = new Mat();
        using (var even = new Mat(gray, new Rect(0, 0, gray.Cols / 2 * 2, gray.Rows / 2 * 2)))
            Cv2.Resize(even, small, new Size(gray.Cols / 2, gray.Rows / 2), interpolation: InterpolationFlags.Area);
        var proposals = new List<Proposal>();
        foreach (var (width, template) in _coarse)
        {
            if (cancelled?.Invoke() == true) return Unknown("gate-work-obsolete");
            if (Math.Min(small.Rows, small.Cols) < template.Rows) continue;
            using var response = new Mat(); Cv2.MatchTemplate(small, template, response, TemplateMatchModes.CCoeffNormed);
            diagnostics["full_frame_coarse_scans"] = ++coarseScans;
            for (int i = 0; i < PeaksPerScale; i++)
            {
                Cv2.MinMaxLoc(response, out _, out double score, out _, out Point peak);
                if (score < 0) break;
                int n = template.Rows;
                proposals.Add(new(score, 2 * peak.X + n, 2 * peak.Y + n, width));
                var rect = new Rect(Math.Max(0, peak.X - n), Math.Max(0, peak.Y - n),
                    Math.Min(response.Cols, peak.X + n + 1) - Math.Max(0, peak.X - n),
                    Math.Min(response.Rows, peak.Y + n + 1) - Math.Max(0, peak.Y - n));
                using var region = new Mat(response, rect); region.SetTo(Scalar.All(-1));
            }
        }
        var distinct = new List<Proposal>();
        foreach (var proposal in proposals.OrderByDescending(p => p.Score))
        {
            if (distinct.Any(old => Distance(proposal, old) < Math.Max(12, Math.Min(proposal.Width, old.Width) * .65))) continue;
            distinct.Add(proposal); if (distinct.Count >= MaximumProposals) break;
        }
        diagnostics["spatial_proposals"] = distinct.Count;
        var fine = new List<Proposal>();
        foreach (var proposal in distinct)
        {
            int cx = (int)proposal.X, cy = (int)proposal.Y, width = proposal.Width, pad = Math.Max(42, width + 4);
            int x0 = Math.Max(0, cx - pad), y0 = Math.Max(0, cy - pad);
            using var patch = new Mat(gray, new Rect(x0, y0, Math.Min(gray.Cols, cx + pad + 1) - x0, Math.Min(gray.Rows, cy + pad + 1) - y0));
            for (int w = Math.Max(MinimumRaster, width - 4); w <= Math.Min(MaximumRaster, width + 4); w++)
            {
                if (cancelled?.Invoke() == true) return Unknown("gate-work-obsolete");
                if (Math.Min(patch.Rows, patch.Cols) < w) continue;
                using var response = new Mat(); Cv2.MatchTemplate(patch, _fine[w], response, TemplateMatchModes.CCoeffNormed);
                Cv2.MinMaxLoc(response, out _, out double score, out _, out Point peak);
                fine.Add(new(score, x0 + peak.X + w / 2.0, y0 + peak.Y + w / 2.0, w));
                diagnostics["fine_scans"] = ++fineScans;
            }
        }
        if (fine.Count == 0) return Unknown("no_valid_proposals");
        fine = fine.OrderByDescending(p => p.Score).ToList();
        var best = fine[0]; var alternative = fine.FirstOrDefault(p => Distance(p, best) > Math.Max(12, best.Width));
        double? secondScore = alternative?.Score, marginScore = secondScore is { } score2 ? best.Score - score2 : null;
        diagnostics["best_candidate_for_diagnostics_only"] = new JsonObject { ["center_px"] = new JsonArray(best.X, best.Y), ["raster_px"] = best.Width };
        if (best.Score < MinimumScore) return Unknown("insufficient_glyph_evidence", best.Score, secondScore, marginScore);
        if (secondScore is null) return Unknown("insufficient_ambiguity_coverage", best.Score, secondScore, marginScore);
        if (marginScore < MinimumMargin) return Unknown("ambiguous_distinct_glyphs", best.Score, secondScore, marginScore);
        var near = fine.Where(p => p.Score >= best.Score - NearPeakDrop && Distance(p, best) <= Math.Max(5, best.Width / 4.0)).ToArray();
        double[] bounds = [near.Min(p => p.X) - .5, near.Min(p => p.Y) - .5, near.Max(p => p.X) + .5, near.Max(p => p.Y) + .5];
        double uncertainty = Math.Max(1, new[] { bounds[0], bounds[2] }.SelectMany(x => new[] { bounds[1], bounds[3] }.Select(y => Math.Sqrt((x-best.X)*(x-best.X)+(y-best.Y)*(y-best.Y)))).Max());
        return new() { Status = "located", Reason = "glyph_observed", CenterPx = new(best.X,best.Y), LocalizationUncertaintyPx = uncertainty,
            CenterSupportBoundsPx = bounds, GlyphRasterSizePx = [best.Width,best.Width], RasterSupportRangePx = [near.Min(p => p.Width),near.Max(p => p.Width)],
            Score = best.Score, DistinctSecondScore = secondScore, ScoreMargin = marginScore, TemplateSha256 = TemplateSha256,
            ProfileSha256 = ProfileSha256, Diagnostics = diagnostics, Frame = frame };
    }

    private sealed record Proposal(double Score, double X, double Y, int Width);
    private static double Distance(Proposal a, Proposal b) => Math.Sqrt((a.X-b.X)*(a.X-b.X)+(a.Y-b.Y)*(a.Y-b.Y));
    public void Dispose()
    {
        if (_disposed) return; _disposed = true;
        foreach (var template in _fine.Values) template.Dispose(); _fine.Clear();
        foreach (var (_, template) in _coarse) template.Dispose(); _coarse.Clear();
    }
}
