using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using static StarMap.Core.Identity.NativeIdentityJson;

namespace StarMap.Vision;

internal sealed class S0NativeConditionProposal(NativeConditionCatalog catalog)
{
    private sealed record View(string PoseId,string ReferenceId,string MapId,double[][] Matrix,HashSet<int> Hits,bool Eligible,
        bool Continuation=false,HashSet<string>? Established=null);

    internal JsonObject Propose(Mat frame,JsonArray rows,JsonArray front,JsonObject frontDiagnostics,JsonArray side,JsonObject? sideDiagnostics,
        string floor,Func<bool> expired,IReadOnlySet<string> required,NativeConditionPrior? prior,NativeIdentityContext? context,CancellationToken cancellation)
    {
        var result=new JsonObject { ["status"]="conditions",["mode"]=catalog.Mode,["floor"]=floor,["groupId"]="conditions",["resourceKey"]=catalog.ResourceKey,
            ["conditionObservations"]=new JsonArray(),["complete"]=false,["candidateMemberId"]=null,["memberIds"]=new JsonArray(),
            ["originalIdentityHardGatePassed"]=false,["authorizesIdentity"]=false,["authorizesPose"]=false,["reason"]="condition-budget-incomplete" };
        bool Stop() { cancellation.ThrowIfCancellationRequested(); return expired(); }
        try
        {
            if(catalog.Error is not null) return new() { ["status"]="none",["reason"]=catalog.Error };
            var verified=rows.Where(row=>Flag(row,"identityHardGatePassed")).ToArray();
            if(verified.Length==1)
            {
                string map=Text(verified[0],"mapId"),rid=$"{catalog.Mode}/{map}/{floor}";
                if(catalog.HasReference(rid))
                { result["complete"]=true; result["reason"]="original-identity-pending"; result["candidateMemberId"]=map; result["memberIds"]=Strings(new[]{map}); result["originalIdentityHardGatePassed"]=true; result["winnerReference"]=catalog.Reference(rid); return result; }
            }
            if(Stop()) return result;
            var matched=new Dictionary<string,List<View>>(StringComparer.Ordinal);
            foreach(var node in front)
            {
                if(Stop()) return result;
                var row=node!.AsObject(); if(Flag(row["entryCrossCheck"],"contradicted")) continue;
                string rid=$"{catalog.Mode}/{Text(row,"mapId")}/{floor}"; if(!catalog.HasReference(rid)) continue;
                var pairs=Array(row["hypothesis"]?["pairs"]); var matrix=CandidateMatrix(row);
                if(pairs.Count==0 || pairs.Any(pair=>pair is not JsonArray {Count:2}) || matrix is null) continue;
                var feature=catalog.Feature(rid,"front"); var hits=pairs.Select(pair=>(int)Number(pair![1])).ToHashSet();
                if(hits.Count>0 && (hits.Min()<0 || hits.Max()>=feature.Count)) continue;
                if(Text(row,"sourceProduct") != feature.Row.GetProperty("sourceProduct").GetString()) continue;
                if(hits.Count(feature.IsMain)<2) continue;
                bool eligible=Number(row["score"])>=10 && Flag(row["verification"]?["regions"],"regionalEdgeVerified") && Flag(row["verification"]?["residual"],"verified");
                foreach(string pid in catalog.PoseIds)
                {
                    if(Stop()) return result;
                    var pose=catalog.Pose(pid); if(Text(pose,"target")!=rid) continue;
                    string did=Text(pose,"domainId");
                    if(!matched.TryGetValue(did,out var views)) matched.Add(did,views=[]);
                    views.Add(new(pid,rid,Text(row,"mapId"),Multiply(matrix,Inverse(Matrix(pose["targetReferenceToCondition"]!))),hits,eligible));
                }
            }
            var established=new JsonObject();
            if(prior?.Matches(context,catalog.Mode,floor,catalog.ResourceKey)==true)
            {
                established=prior.Domains;
                foreach(var item in established)
                {
                    string did=item.Key; if(!catalog.HasDomain(did)) continue;
                    var cids=Strings(item.Value).ToHashSet(StringComparer.Ordinal);
                    if(cids.Any(cid=>!catalog.HasCondition(cid) || Text(catalog.Condition(cid),"domainId")!=did)) throw new InvalidDataException("similarity-domain-changed");
                    foreach(var node in side)
                    {
                        if(Stop()) return result;
                        var row=node!.AsObject(); if(Flag(row["entryCrossCheck"],"contradicted") || !Flag(row["verification"],"success") || !Flag(row["verification"]?["residual"],"verified")) continue;
                        string rid=$"{catalog.Mode}/{Text(row,"mapId")}/{floor}"; var matrix=CandidateMatrix(row); if(matrix is null || !catalog.HasReference(rid)) continue;
                        var reference=catalog.Reference(rid); string source=reference["features"]?["side"]?["sourceProduct"]?.ToString() ?? Text(reference,"resourcePath");
                        if(Text(row,"profileId")!=Text(reference,"profile") || Text(row,"sourceProduct")!=source) continue;
                        foreach(string pid in catalog.PoseIds)
                        {
                            var pose=catalog.Pose(pid); if(Text(pose,"domainId")!=did || Text(pose,"target")!=rid) continue;
                            if(!matched.TryGetValue(did,out var views)) matched.Add(did,views=[]);
                            if(views.Any(view=>view.PoseId==pid)) continue;
                            views.Add(new(pid,rid,Text(row,"mapId"),Multiply(matrix,Inverse(Matrix(pose["targetReferenceToCondition"]!))),[],true,true,cids));
                        }
                    }
                }
            }
            if(Stop()) return result;
            matched=matched.Where(pair=>catalog.ConditionsFor(pair.Key).Any(cid=>pair.Value.Any(view=>view.Eligible && Strings(catalog.Condition(cid)["poseIds"]).Contains(view.PoseId,StringComparer.Ordinal) && Witness(view,catalog.Condition(cid)))))
                .ToDictionary(pair=>pair.Key,pair=>pair.Value,StringComparer.Ordinal);
            if(Stop()) return result;
            using var observation=matched.Count==0?null:EntryObservation.Capture(frame);
            if(matched.Count>0 && observation is null) { result["reason"]="condition-current-geometry-unavailable"; return result; }
            if(Stop()) return result;
            if(matched.Count>0) _=catalog.Samples(0);
            foreach(var item in matched)
            {
                if(Stop()) return result;
                string did=item.Key; var views=item.Value; bool duplicated=views.GroupBy(view=>view.PoseId).Any(group=>group.Count()>1);
                foreach(string cid in catalog.ConditionsFor(did))
                {
                    if(Stop()) return result;
                    var condition=catalog.Condition(cid); var poseIds=Strings(condition["poseIds"]).ToHashSet(StringComparer.Ordinal);
                    var current=views.Where(view=>poseIds.Contains(view.PoseId) && view.Eligible && Witness(view,condition)).ToArray(); if(current.Length==0) continue;
                    catalog.ValidateDomains(new[]{did});
                    var members=poseIds.Select(pid=>Text(catalog.Reference(Text(catalog.Pose(pid),"target")),"mapId")).Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray();
                    var poseMaps=new JsonObject(); foreach(string pid in poseIds) poseMaps[pid]=Text(catalog.Reference(Text(catalog.Pose(pid),"target")),"mapId");
                    var observed=new JsonObject { ["domainId"]=did,["conditionId"]=cid,["radiusUnits"]=condition["radiusUnits"]!.DeepClone(),
                        ["memberIds"]=Strings(members),["poseMaps"]=poseMaps,["activeViews"]=Strings(current.Select(view=>view.PoseId).Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal)),["pairs"]=new JsonArray(),
                        ["expectedPoseIds"]=Strings(poseIds.Order(StringComparer.Ordinal)),["entryEstablished"]=Strings(established[did]).Contains(cid,StringComparer.Ordinal),
                        ["completeForeignMapCompetition"]=condition["completeForeignMapCompetition"]!.DeepClone(),
                        ["currentCompetitionComplete"]=Text(condition,"kind")=="side"?Flag(sideDiagnostics,"complete"):Flag(frontDiagnostics,"complete") && Flag(frontDiagnostics,"entryInventoryComplete"),
                        ["ambiguousViews"]=duplicated,["outsideCompetitors"]=Strings(required.Except(members,StringComparer.Ordinal).Order(StringComparer.Ordinal)),
                        ["coverage"]="partial-positive-observation; unseen window pixels are unknown",["sameMapAlternativePoses"]=condition["sameMapAlternativePoses"]?.DeepClone() ?? JsonValue.Create("UNKNOWN") };
                    result["conditionObservations"]!.AsArray().Add(observed); if(duplicated) continue;
                    bool contradictory=false;
                    foreach(var comparison in catalog.ComparisonsFor(cid))
                    {
                        if(Stop()) return result;
                        string a=Text(comparison,"poseA"),b=Text(comparison,"poseB"); var winners=new HashSet<string>(StringComparer.Ordinal);
                        foreach(var view in current)
                        {
                            if(Stop()) return result;
                            var record=new JsonObject { ["poseA"]=a,["poseB"]=b,["viewerPoseId"]=view.PoseId };
                            foreach(string sideName in new[]{"a","b"})
                            {
                                var points=catalog.Samples((int)Number(comparison[sideName+"Only"]?["sampleSetId"]));
                                var samples=new JsonArray(); var hits=new JsonArray();
                                foreach(var point in points)
                                {
                                    samples.Add(new JsonArray(point.X,point.Y));
                                    if(Supported(point,view.Matrix,observation!.Geometry)) hits.Add(new JsonArray(point.X,point.Y));
                                }
                                record[sideName]=new JsonObject { ["samples"]=samples,["observed"]=hits };
                            }
                            observed["pairs"]!.AsArray().Add(record);
                            double fa=Array(record["a"]?["observed"]).Count/(double)Math.Max(1,Array(record["a"]?["samples"]).Count),fb=Array(record["b"]?["observed"]).Count/(double)Math.Max(1,Array(record["b"]?["samples"]).Count);
                            if(fa>fb) winners.Add(a); if(fb>fa) winners.Add(b);
                        }
                        if(winners.Count>1) contradictory=true;
                    }
                    observed["conflictingViews"]=contradictory;
                }
            }
            if(Stop()) return result;
            result["complete"]=true; result["reason"]="finite-conditions-evaluated";
            result["memberIds"]=Strings(Array(result["conditionObservations"]).SelectMany(row=>Strings(row?["memberIds"])).Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal)); return result;
        }
        catch(Exception error) when(error is IOException or ArgumentException or KeyNotFoundException or InvalidOperationException or OpenCVException)
        { result["complete"]=false; result["reason"]=error.Message; return result; }
    }

    private bool Witness(View view,JsonObject condition)
    {
        if(view.Continuation) return view.Established?.Contains(Text(condition,"conditionId"))==true;
        var pose=catalog.Pose(view.PoseId); var feature=catalog.Feature(view.ReferenceId,"front");
        var window=Array(pose["windows"]).FirstOrDefault(row=>Number(row?["radiusUnits"])==Number(condition["radiusUnits"]));
        HashSet<int> eligible;
        if(window is not null) eligible=Array(window["targetFeatureIds"]).Select(value=>(int)Number(value)).ToHashSet();
        else
        {
            var matrix=Matrix(pose["targetReferenceToCondition"]!); eligible=[];
            for(int i=0;i<feature.Count;i++) { var p=feature.Point(i); double x=p.X*matrix[0][0]+p.Y*matrix[0][1]+matrix[0][2],y=p.X*matrix[1][0]+p.Y*matrix[1][1]+matrix[1][2]; if(Math.Max(Math.Abs(x),Math.Abs(y))<=Number(condition["radiusUnits"])*60+1e-8) eligible.Add(i); }
        }
        var hits=view.Hits.Intersect(eligible).ToArray(); return hits.Length>=4 && hits.Count(feature.IsMain)>=2;
    }
    private static bool Supported(NativeSamplePoint point,double[][] matrix,Observation context)
    {
        double z=point.X*matrix[2][0]+point.Y*matrix[2][1]+matrix[2][2];
        int x=Numerics.Rint((point.X*matrix[0][0]+point.Y*matrix[0][1]+matrix[0][2])/z-context.Bounds.X),y=Numerics.Rint((point.X*matrix[1][0]+point.Y*matrix[1][1]+matrix[1][2])/z-context.Bounds.Y);
        // Preserve the original _support coordinate consumer exactly. Its
        // query is a full-client canvas; a separate migration must not silently
        // change the original positive evidence by rebasing the query here.
        return x>=0 && y>=0 && x<context.Query.Cols && y<context.Query.Rows && context.Query.At<byte>(y,x)!=0;
    }
    private static double[][]? CandidateMatrix(JsonObject row)
    {
        JsonNode? value=row["verification"]?["matrixSourceToClient"] ?? row["matrixSourceToClient"];
        if(value is null && row["hypothesis"]?["scale"] is { } scale && row["hypothesis"]?["translation"] is JsonArray {Count:2} t)
        { double s=Number(scale); return [[s,0,Number(t[0])],[0,s,Number(t[1])],[0,0,1]]; }
        return FiniteMatrix(value)?Matrix(value!):null;
    }
    private static double[][] Multiply(double[][] a,double[][] b) => Enumerable.Range(0,3).Select(y=>Enumerable.Range(0,3).Select(x=>Enumerable.Range(0,3).Sum(k=>a[y][k]*b[k][x])).ToArray()).ToArray();
    private static double[][] Inverse(double[][] m)
    {
        using var source=new Mat(3,3,MatType.CV_64FC1); using var result=new Mat();
        for(int y=0;y<3;y++) for(int x=0;x<3;x++) source.Set(y,x,m[y][x]);
        if(Cv2.Invert(source,result,DecompTypes.LU)==0) throw new InvalidDataException("similarity-local-reference-matrix");
        return Enumerable.Range(0,3).Select(y=>Enumerable.Range(0,3).Select(x=>result.At<double>(y,x)).ToArray()).ToArray();
    }
}
