using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision;

public sealed partial class SideEntryRanker
{
    /// <summary>Validate one producer-owned proposal on this frame; this method never grants identity or refits a pose.</summary>
    public JsonObject VerifyCandidate(Mat frame,JsonObject candidate,CancellationToken cancellation=default)
    {
        cancellation.ThrowIfCancellationRequested(); ValidateContext(candidate);
        using var pixels=frame.Clone(); using var current=EntryObservation.Capture(pixels);
        if(SideEntryRankerIndex.Contradicted(candidate)) return new() { ["success"]=false,["reason"]="side-door-boundary-contradiction" };
        using var product=OpenProduct(candidate["mapId"]!.ToString(),candidate["floor"]!.ToString());
        if(product==null) return new() { ["success"]=false,["reason"]="side-resource-unavailable" };
        if(current==null) return new() { ["success"]=false,["reason"]="side-observation-unavailable" };
        JsonElement? passport=FindPassport(candidate);
        var result=SideEntryRankerVerification.Verify(pixels,current.Geometry,candidate,product,passport);
        cancellation.ThrowIfCancellationRequested(); return result;
    }

    /// <summary>Only validates a caller-owned committed pose. No identification, floor inference or fitting occurs here.</summary>
    public JsonObject VerifyExistingPose(Mat frame,string mapId,string floor,double[][] matrix,JsonObject? passportObservation=null,
        CancellationToken cancellation=default)
    {
        cancellation.ThrowIfCancellationRequested(); var clock=Stopwatch.StartNew(); var spans=new JsonObject();
        JsonObject? checks=null;
        bool Close(double x,double y)=>Math.Abs(x-y)<=1e-8+1e-5*Math.Abs(y);
        bool valid=matrix!=null && matrix.Length==3 && matrix.All(row=>row!=null && row.Length==3 && row.All(double.IsFinite)) && matrix[0][0]>0 &&
            Close(matrix[2][0],0) && Close(matrix[2][1],0) && Close(matrix[2][2],1) && Close(matrix[0][1],0) && Close(matrix[1][0],0) && Close(matrix[1][1],matrix[0][0]);
        if(!valid || !catalog.Floors.Contains(floor)) return Finish(new() { ["success"]=false,["reason"]="existing-pose-invalid-input" });
        using var pixels=frame.Clone(); using var observation=new EntryPassportFrame(catalog,pixels);
        var row=new JsonObject { ["mapId"]=mapId,["floor"]=floor,["matrixSourceToClient"]=MatrixJson(matrix!) };
        using var product=OpenProduct(mapId,floor);
        if(product==null) return Finish(new() { ["success"]=false,["reason"]="side-resource-unavailable" });
        if(passportObservation!=null)
        {
            var failure=CheckExits(); if(failure!=null) return Finish(failure);
        }
        var at=clock.Elapsed;
        using var current=EntryObservation.Capture(pixels); spans["geometry"]=(clock.Elapsed-at).TotalMilliseconds;
        if(current==null) return Finish(new() { ["success"]=false,["reason"]="side-observation-unavailable",["allObservedExits"]=checks?.DeepClone() });
        var references=new List<(string Name,Mat Clean,JsonNode? Basis)> { ("source-present",product.Clean,null) };
        var outlines=new Dictionary<string,Point[]>(StringComparer.Ordinal);
        try
        {
            foreach(var item in catalog.Passports.Where(p=>Text(p,"mapId")==mapId && Text(p,"floor")==floor))
            {
                if(!item.TryGetProperty("optionalRooms",out var rooms)) continue;
                double unit=item.GetProperty("unit").GetDouble(); var center=SideEntryRankerSource.Point(item.GetProperty("center"));
                foreach(var room in rooms.EnumerateArray())
                {
                    cancellation.ThrowIfCancellationRequested();
                    string instance=Text(room,"instance");
                    var outline=room.GetProperty("outline").EnumerateArray().Select(p=>SideEntryRankerSource.Point(p)*unit+center)
                        .Select(p=>new Point(Numerics.Rint(p.X),Numerics.Rint(p.Y))).ToArray();
                    if(outlines.TryGetValue(instance,out var previous))
                    {
                        if(!previous.SequenceEqual(outline)) return Finish(new() { ["success"]=false,["reason"]="existing-pose-variant-source-conflict",["allObservedExits"]=checks?.DeepClone() });
                        continue;
                    }
                    outlines.Add(instance,outline); var absent=product.Clean.Clone();
                    try { Cv2.FillPoly(absent,[outline],Scalar.Black); references.Add(("absent:"+instance,absent,JsonNode.Parse(room.GetProperty("basis").GetRawText()))); }
                    catch { absent.Dispose(); throw; }
                }
            }
            at=clock.Elapsed;
            var result=SideEntryRankerVerification.Verify(pixels,current.Geometry,row,product,suppliedVariants:references);
            spans["structureAndAuthor"]=(clock.Elapsed-at).TotalMilliseconds;
            if(IsTrue(result,"success") && checks==null)
            {
                var failure=CheckExits(); if(failure!=null) foreach(var pair in failure) result[pair.Key]=pair.Value?.DeepClone();
            }
            result["allObservedExits"]=checks?.DeepClone();
            result["entryObservationStatus"]=checks!=null?checks["observationStatus"]?.ToString()??"checked":spans.ContainsKey("entryObservation")?"unavailable":"skipped-pose-failed";
            result["passportAvailable"]=catalog.NativePassportError==null;
            result["optionalInstances"]=new JsonArray(outlines.Keys.Select(k=>(JsonNode)JsonValue.Create(k)!).ToArray());
            cancellation.ThrowIfCancellationRequested(); return Finish(result);
        }
        finally { foreach(var reference in references.Skip(1)) reference.Clean.Dispose(); }

        JsonObject? CheckExits()
        {
            cancellation.ThrowIfCancellationRequested(); var started=clock.Elapsed;
            try
            {
                checks=passportObservation!=null?EntryPassportEvidence.CheckObserved(catalog,row,passportObservation["observations"]?.AsArray()??new()):observation.Check(row,cancellation);
            }
            catch(OperationCanceledException) { throw; }
            catch { return new() { ["success"]=false,["reason"]="existing-pose-entry-observation-unavailable" }; }
            finally { spans["entryObservation"]=(clock.Elapsed-started).TotalMilliseconds; }
            return IsTrue(checks,"contradicted")?new() { ["success"]=false,["reason"]="entry-other-current-exit-contradiction",["allObservedExits"]=checks!.DeepClone() }:null;
        }
        JsonObject Finish(JsonObject result)
        {
            result["scope"]="existing-pose-current-frame-only"; result["identityVerified"]=false;
            result["spansMs"]=spans.DeepClone(); result["elapsedMs"]=clock.Elapsed.TotalMilliseconds; return result;
        }
    }

    /// <summary>Production refinement of one bounded proposal, followed by the same fixed-pose verifier.</summary>
    public JsonObject RefineCandidate(Mat frame,JsonObject candidate,CancellationToken cancellation=default)
    {
        cancellation.ThrowIfCancellationRequested(); ValidateContext(candidate); var clock=Stopwatch.StartNew();
        using var pixels=frame.Clone(); using var product=OpenProduct(candidate["mapId"]!.ToString(),candidate["floor"]!.ToString());
        using var current=EntryObservation.Capture(pixels);
        if(product==null || current==null) return candidate.DeepClone().AsObject();
        var refined=PartialWalls.Fit(pixels,product,Numerics.Matrix(candidate["matrixSourceToClient"]!.AsArray()),current.Geometry);
        var result=candidate.DeepClone().AsObject(); result["continuousRefinementReason"]=refined["reason"]!.DeepClone();
        if(IsTrue(refined,"success"))
        {
            result["matrixSourceToClient"]=refined["matrix"]!.DeepClone(); result["initialVerification"]=candidate["verification"]?.DeepClone();
            result["verification"]=SideEntryRankerVerification.Verify(pixels,current.Geometry,result,product,FindPassport(result));
        }
        result["continuousRefinementMs"]=clock.Elapsed.TotalMilliseconds;
        cancellation.ThrowIfCancellationRequested(); return result;
    }

    private JsonElement? FindPassport(JsonObject candidate)
    {
        if(candidate["entryEvidence"] is not { } evidence) return null;
        foreach(var item in catalog.Passports)
            if(Text(item,"mapId")==candidate["mapId"]!.ToString() && Text(item,"floor")==candidate["floor"]!.ToString() && Text(item,"sourceEntryId")==evidence["sourceEntryId"]!.ToString()) return item;
        return null;
    }
    private void ValidateContext(JsonObject candidate)
    {
        string mid=candidate["mapId"]!.ToString(),floor=candidate["floor"]!.ToString();
        if(!catalog.MapProfiles.TryGetValue(mid,out var profile) || !catalog.Floors.Contains(floor) || candidate["profileId"] is { } p && p.ToString()!=profile ||
            candidate["season"] is { } season && season.ToString()!=catalog.Season || candidate["mode"] is { } mode && mode.ToString()!=catalog.Mode)
            throw new ArgumentException("Candidate belongs to a different map/floor/mode/provider context.",nameof(candidate));
    }
}
