using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Resources;

namespace StarMap.Vision.Steady;

public static partial class Idvb0059Steady
{
    sealed record CoverageContext(string Key,Dictionary<string,SteadyCoverageEntry> Entries,SteadyCoverageEntry Previous,bool Valid);

    static CoverageContext CoverageFor(Mat frame,SharedStructureData reference,Prepared prepared,CurrentObservation observation,SteadyCoverageState? state)
    {
        string key=$"{frame.Cols}x{frame.Rows}/{observation.Width}x{observation.Height}";
        bool valid=state is not null&&ReferenceEquals(state.Reference,reference)&&state.BindingIdentity==prepared.BindingIdentity;
        var entries=valid?new Dictionary<string,SteadyCoverageEntry>(state!.Entries,StringComparer.Ordinal):new(StringComparer.Ordinal);
        return new(key,entries,entries.GetValueOrDefault(key)??new(0,false),valid);
    }

    static (SteadyCoverageState State,JsonObject Measurement) Observe(CurrentObservation observation,Prepared prepared,
        SharedStructureData reference,double[][] matrix,CoverageContext context,bool refreshed)
    {
        using var observed=Geometry.FromBytes(observation.Width,observation.Height,observation.Observed);
        using var supported=new Mat();using var kernel=Geometry.Kernel(5);Cv2.Dilate(observed,supported,kernel);
        var pixels=Geometry.Bytes(supported);int hits=0;
        foreach(var point in prepared.ReferencePoints)
        {
            int x=Numerics.Rint(point.X*matrix[0][0]+point.Y*matrix[0][1]+matrix[0][2]-observation.Origin.X);
            int y=Numerics.Rint(point.X*matrix[1][0]+point.Y*matrix[1][1]+matrix[1][2]-observation.Origin.Y);
            if(Lookup(pixels,observation.Width,observation.Height,x,y))hits++;
        }
        int total=prepared.ReferencePoints.Length;double coverage=(double)hits/Math.Max(1,total);
        int reached=new[] {0,10,15,20,25,30,40,50}.Where(p=>coverage*100+1e-9>=p).Max();
        bool pending=refreshed?false:context.Previous.RefreshPending;if(reached>context.Previous.ReachedPercent)pending=true;
        var entries=new Dictionary<string,SteadyCoverageEntry>(context.Entries,StringComparer.Ordinal)
            {[context.Key]=new(Math.Max(reached,context.Previous.ReachedPercent),pending)};
        var state=new SteadyCoverageState(reference,prepared.BindingIdentity,prepared.Binding,entries);
        return(state,new() { ["hits"]=hits,["total"]=total,["ratio"]=coverage,["reachedPercent"]=entries[context.Key].ReachedPercent,
            ["refreshNextAlignment"]=pending,["refreshCompleted"]=refreshed });
    }

    /// <summary>Measure an already committed pose. This cannot consume an outstanding scale refresh.</summary>
    public static SteadyCoverageState ObserveCoverage(Mat frame,SharedStructureData reference,double[][] committedMatrix,SteadyCoverageState? state=null)
    {
        var matrix=committedMatrix;
        bool lastRowValid=matrix is {Length:3}&&matrix[2] is {Length:3}&&matrix[2][0]==0&&matrix[2][1]==0&&matrix[2][2]==1;
        if(lastRowValid)matrix=matrix.Take(2).ToArray();
        if(matrix is not {Length:2}||matrix.Any(row=>row is not {Length:3}||row.Any(v=>!double.IsFinite(v))))
            throw new ArgumentException("CommittedCoverageTransformInvalid");
        var prepared=Prepare(reference);var observation=Extract(frame);var context=CoverageFor(frame,reference,prepared,observation,state);
        return Observe(observation,prepared,reference,matrix,context,false).State;
    }

    /// <summary>
    /// Full current-frame 0059 wrapper. Only a caller's trusted, committed same-floor scale is admitted.
    /// A result proposes pose and coverage together; only the caller can commit them or run recovery.
    /// </summary>
    public static SteadySolveResult Solve(Mat frame,SharedStructureData reference,double? trustedFloorScale,
        SteadyCoverageState? coverageState=null,bool allowScaleLock=true,bool lowStructure=false,Func<bool>? cancelled=null)
        => SolveCore(frame,reference,trustedFloorScale,coverageState,allowScaleLock,lowStructure,cancelled,Now);

    // Per-call deterministic numeric seam, inaccessible to production callers. It changes only precision's clock.
    internal static SteadySolveResult SolveWithPrecisionClock(Mat frame,SharedStructureData reference,double? trustedFloorScale,
        SteadyCoverageState? coverageState,bool allowScaleLock,bool lowStructure,Func<bool>? cancelled,Func<double> precisionMilliseconds)
        => SolveCore(frame,reference,trustedFloorScale,coverageState,allowScaleLock,lowStructure,cancelled,precisionMilliseconds);

    static SteadySolveResult SolveCore(Mat frame,SharedStructureData reference,double? trustedFloorScale,SteadyCoverageState? coverageState,
        bool allowScaleLock,bool lowStructure,Func<bool>? cancelled,Func<double> precisionClock)
    {
        double started=Now();var timing=new JsonObject();
        var wrapper=new JsonObject { ["algorithm"]="idvb0059-steady-wrapper",["timingMs"]=timing,["inputScale"]=trustedFloorScale,
            ["lowStructure"]=lowStructure,["sameFrameExtractionCount"]=0 };
        void Selected(BootstrapResult result)
        {
            wrapper["selectedBootstrap"]=result.Diagnostics.DeepClone();
            foreach(string key in new[] {"apertureMargin","requiredVerificationScore","distinctRunnerUpDistance","best","runnerUp","refinedCandidates"})
                if(result.Diagnostics.ContainsKey(key))wrapper[key]=result.Diagnostics[key]?.DeepClone();
        }
        SteadySolveResult Reject(string reason,BootstrapResult? bootstrap=null)
        {timing["total"]=Now()-started;if(bootstrap is not null)Selected(bootstrap);return new(false,reason,null,null,wrapper,null);}
        if(Cancelled(cancelled))return Reject("Cancelled");
        if(trustedFloorScale is null)return Reject("TrustedFloorScaleMissing");double scale=trustedFloorScale.Value;
        if(!double.IsFinite(scale)||scale<MinScale||scale>MaxScale)return Reject("TrustedFloorScaleOutOfRange");
        double mark=Now();Prepared prepared;
        try{prepared=Prepare(reference);}catch(Exception error)when(error is ArgumentException or InvalidDataException){return Reject(error.Message);}
        timing["prepare"]=Now()-mark;mark=Now();CurrentObservation observation;
        try{observation=Extract(frame);}catch(ArgumentException error){return Reject(error.Message);}
        timing["extract"]=Now()-mark;wrapper["sameFrameExtractionCount"]=1;
        wrapper["referenceBinding"]=prepared.Binding.DeepClone();wrapper["referenceRepresentation"]=Representation;
        wrapper["observationBounds"]=new JsonArray(observation.Origin.X,observation.Origin.Y,observation.Width,observation.Height);
        wrapper["sparsePointCount"]=observation.Points.Length;wrapper["observedEdgePixels"]=observation.Observed.Count(p=>p!=0);wrapper["proposalEdgePixels"]=observation.Proposal.Count(p=>p!=0);
        if(Cancelled(cancelled))return Reject("Cancelled");if(observation.Points.Length==0)return Reject("NoCurrentFrameStructuralPoints");
        var context=CoverageFor(frame,reference,prepared,observation,coverageState);
        bool pending=context.Previous.RefreshPending&&allowScaleLock&&!lowStructure;
        bool selectedLock=lowStructure||(allowScaleLock&&!pending);
        wrapper["scalePolicy"]=new JsonObject { ["coverageKey"]=context.Key,["previousReachedPercent"]=context.Previous.ReachedPercent,
            ["refreshPending"]=pending,["lockingAllowed"]=allowScaleLock,["restoredState"]=context.Valid,["initialScaleLocked"]=selectedLock };
        mark=Now();var result=selectedLock?Bootstrap(observation,prepared,scale,cancelled):FreshScaleSolve(observation,prepared,cancelled);
        timing["initialBootstrap"]=Now()-mark;int tested=result.TestedScaleHypotheses;
        JsonObject Summary(BootstrapResult value)=>new() { ["accepted"]=value.Accepted,["reason"]=value.Reason,["scale"]=value.Scale,
            ["confidence"]=value.Confidence,["diagnostics"]=value.Diagnostics.DeepClone() };
        if(pending&&!Cancelled(cancelled))
        {
            mark=Now();var fresh=result;BootstrapResult? bestPrior=null;var hypotheses=new JsonArray();
            foreach(double candidateScale in new[] {scale,scale*.995,scale*1.005})
            {
                if(candidateScale<MinScale||candidateScale>MaxScale||Cancelled(cancelled))continue;
                var candidate=Bootstrap(observation,prepared,candidateScale,cancelled);tested+=candidate.TestedScaleHypotheses;hypotheses.Add(Summary(candidate));
                if(candidate.Accepted&&(bestPrior is null||candidate.Confidence!.Value>bestPrior.Confidence!.Value+.015))bestPrior=candidate;
            }
            selectedLock=bestPrior is not null&&(!fresh.Accepted||fresh.Confidence!.Value<=bestPrior.Confidence!.Value+.015);
            result=selectedLock?bestPrior!:fresh;
            wrapper["coverageRefresh"]=new JsonObject { ["fresh"]=Summary(fresh),["priorHypotheses"]=hypotheses,["selectedPriorHypothesis"]=selectedLock };
            timing["scaleRefreshComparison"]=Now()-mark;
        }
        else if(!lowStructure&&selectedLock&&result.Accepted&&result.Confidence!.Value<.75&&!Cancelled(cancelled))
        {
            mark=Now();var locked=result;var fresh=FreshScaleSolve(observation,prepared,cancelled);tested+=fresh.TestedScaleHypotheses;
            bool replace=fresh.Accepted&&fresh.Confidence!.Value>=locked.Confidence!.Value+.05&&Math.Abs(fresh.Scale!.Value-scale)/scale>.005;
            if(replace){result=fresh;selectedLock=false;}
            wrapper["weakSteadyScaleRecheck"]=new JsonObject { ["locked"]=Summary(locked),["fresh"]=Summary(fresh),["selectedFresh"]=replace,
                ["minimumScoreImprovement"]=.05,["minimumRelativeScaleChange"]=.005 };
            timing["weakScaleRecheck"]=Now()-mark;
        }
        wrapper["testedScaleHypotheses"]=tested;wrapper["scaleLocked"]=selectedLock;
        if(Cancelled(cancelled))return Reject("Cancelled",result);if(!result.Accepted)return Reject(result.Reason,result);
        var matrix=result.Matrix!.Select(row=>row.ToArray()).ToArray();
        var precision=PrecisionRefine(observation,prepared,matrix[0][0],new(matrix[0][2],matrix[1][2]),selectedLock,cancelled,precisionClock);
        wrapper["precision"]=precision;timing["precision"]=precision["milliseconds"]!.DeepClone();
        if(precision["calibrated"]!.GetValue<bool>())
        {
            matrix[0][0]=matrix[1][1]=precision["scale"]!.GetValue<double>();
            matrix[0][2]=precision["offset"]![0]!.GetValue<double>();matrix[1][2]=precision["offset"]![1]!.GetValue<double>();
        }
        if(Cancelled(cancelled))return Reject("Cancelled",result);
        mark=Now();var dense=Nonzero(observation.Observed,observation.Width);double? forward=null;
        if(dense.Length>0)
        {
            double sum=0;foreach(var p in dense)
            {
                var xy=new Point2d((p.X+observation.Origin.X-matrix[0][2])/matrix[0][0],(p.Y+observation.Origin.Y-matrix[1][2])/matrix[0][0]);
                bool inside=xy.X>=0&&xy.Y>=0&&xy.X<prepared.Width-1&&xy.Y<prepared.Height-1;
                sum+=inside?Bilinear(prepared,xy)*matrix[0][0]:50;
            }
            forward=sum/dense.Length;
        }
        timing["forwardMean"]=Now()-mark;wrapper["measuredForwardMeanPixels"]=forward;wrapper["forwardMeanPointCount"]=dense.Length;
        wrapper["chamferQuality"]=Math.Clamp(1-(forward??double.PositiveInfinity)/3,0,1);
        wrapper["forwardMeanPolicy"]="reported-by-0059-wrapper; no-added-rejection-threshold";
        mark=Now();var proposal=Observe(observation,prepared,reference,matrix,context,pending);
        proposal.Measurement["scaleChangedOnRefresh"]=pending&&Math.Abs(matrix[0][0]-scale)/scale>.005;
        wrapper["coverage"]=proposal.Measurement;timing["coverage"]=Now()-mark;Selected(result);wrapper["finalTransform"]=MatrixJson(matrix);timing["total"]=Now()-started;
        if(Cancelled(cancelled))return Reject("Cancelled",result);
        return new(true,"",matrix,result.Confidence,wrapper,proposal.State);
    }
}
