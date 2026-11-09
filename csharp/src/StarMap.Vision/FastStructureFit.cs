using OpenCvSharp;
using StarMap.Core.Resources;
using System.Text.Json.Nodes;

namespace StarMap.Vision;

/// <summary>A prior in this exact reference's native pixel coordinates, already scoped by the caller to this session.</summary>
public sealed record StructurePrior(SharedStructureData Reference, double[][] Matrix);

/// <summary>Current-frame geometric evidence; the session owns identity, canonical bridge and display decisions.</summary>
public sealed record FastFitResult(bool GeometryVerified, string Reason, double[][]? Matrix,
    FastProposalResult? Proposal = null, JsonObject? Point = null, JsonObject? Regions = null,
    JsonObject? Wall = null, JsonObject? Residual = null);

public static class FastStructureFit
{
    public static FastFitResult Evaluate(Mat frame, Observation observation, StructureGeometry reference,
        FastPrepared prepared, StructurePrior? prior = null, bool localOnly = false, Func<bool>? cancelled = null)
    {
        observation.RequireFrame(frame);
        if (!ReferenceEquals(reference.Source, prepared.Source)) return new(false,"fast-prepared-reference-mismatch",null);
        if (prior is not null && !ReferenceEquals(reference.Source,prior.Reference)) return new(false,"fast-prior-reference-mismatch",null);
        var proposal = FastStructure.Propose(frame, observation, prepared, prior?.Matrix, localOnly, cancelled);
        if (proposal.Poses.Count == 0)
            return new(false, (string)proposal.Diagnostics["reason"]!, null, proposal);
        var pose = proposal.Poses[0];
        double[][] matrix = [[pose.Scale,0,pose.Translation[0]], [0,pose.Scale,pose.Translation[1]], [0,0,1]];
        bool hasKnown = reference.KnownBoundary is not null;
        var geometry = hasKnown
            ? RegionalGeometry.GraphPoints(frame, reference, matrix, observation, allowScaleRecovery: prior is null)
            : PointGeometry.NativePointGeometry(frame, prepared, matrix, observation, lockScale: prior is not null);
        string? model = geometry["structureModel"]?.GetValue<string>();
        var selected = model is null ? null : geometry[model];
        bool verified = selected?["success"]?.GetValue<bool>() == true;
        JsonObject? regions = null;
        if (verified)
        {
            matrix = Numerics.Matrix(selected!["matrixSourceToClient"]!.AsArray());
            using var projected = Geometry.Warp(reference.Clean, matrix, frame.Size());
            regions = RegionalGeometry.Evaluate(frame, observation, projected);
            verified = regions["regionalEdgeVerified"]!.GetValue<bool>();
        }
        if (cancelled?.Invoke() == true) return new(false,"structure-work-obsolete",null,proposal,geometry,regions);
        var wall = !verified || prior is not null || hasKnown
            ? PartialWalls.Fit(frame, reference, matrix, observation, allowScaleRecovery: prior is null)
            : new JsonObject { ["success"] = false, ["reason"] = "native-point-heldout-verified" };
        bool wallPassed = wall["success"]?.GetValue<bool>() == true;
        if (hasKnown) verified = wallPassed;
        if (wallPassed)
        {
            verified = true;
            matrix = Numerics.Matrix(wall["matrix"]!.AsArray());
        }
        if (prior is not null)
            for (int row = 0; row < 2; row++)
            for (int col = 0; col < 2; col++)
                if (Math.Abs(matrix[row][col] - prior.Matrix[row][col]) > 1e-10) verified = false;
        if (!verified) return new(false,"fast-existing-validation-rejected",null,proposal,geometry,regions,wall);
        if (cancelled?.Invoke() == true) return new(false,"structure-work-obsolete",null,proposal,geometry,regions,wall);
        var residual = FrameResidual.Evaluate(frame, observation, reference.Validation, matrix);
        if (cancelled?.Invoke() == true) return new(false,"structure-work-obsolete",null,proposal,geometry,regions,wall,residual);
        if (residual["verified"]?.GetValue<bool>() != true)
            return new(false,"fast-existing-validation-rejected",null,proposal,geometry,regions,wall,residual);
        return new(true,"fast-current-frame-geometry-verified",matrix,proposal,geometry,regions,wall,residual);
    }
}
