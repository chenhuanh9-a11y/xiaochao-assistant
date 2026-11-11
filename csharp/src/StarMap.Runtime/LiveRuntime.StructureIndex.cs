using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Selection;
using StarMap.Core.Session;
using StarMap.Core.Identity;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private StructureIndexFilter? _structureIndex;
    private BoundResources? _structureIndexResources;
    private SharedStructureFitPipeline? _structureFits;
    private BoundResources? _structureFitResources;
    private bool _structureIndexEntered;
    private string? _s1ProvisionalMapId;
    private CaptureBindingScope? _s1PreferenceBinding;
    private ClientFrameContext? _s1PreferenceClient;
    private string StructureStage => _selection.Season+"-structure";
    private bool IsStructureProvisional => _identitySource == StructureStage+"-provisional";
    private string? EffectiveMapId => _mapId ?? _s1ProvisionalMapId;
    private bool S0DiscoveryRequired => _mapId is null||IsStructureProvisional||_identitySource=="group-default";

    // One request owns at most its current probe's preparation. Replacing a
    // probe retires borrowed frame observations, never the round entry latch.
    private sealed class S0PreparedObservation : IDisposable
    {
        internal CapturedClientFrame? Capture {get;private set;}
        internal StructureFramePreparation? Preparation {get;private set;}
        internal StructureGateAnchor? Anchor {get;set;}
        internal bool Entered {get;set;}
        internal void Replace(CapturedClientFrame capture,Mat frame)
        {Clear();Capture=capture;Preparation=new(frame);}
        internal void Clear()
        {Preparation?.Dispose();Preparation=null;Capture=null;Anchor=null;Entered=false;}
        public void Dispose()=>Clear();
    }

    private void DisposeStructureIndex()
    {
        _structureIndex?.Dispose(); _structureIndex = null; _structureIndexResources = null;
        _structureFits?.Dispose(); _structureFits = null; _structureFitResources = null;
        _currentResidualHost?.Dispose(); _currentResidualHost = null; _currentResidualResources = null;
    }

    // The single runtime worker owns these immutable resource preparations.
    // No frame, scale estimate or alignment transform is cached here.
    private SharedStructureFitPipeline GetStructureFits(BoundResources bound)
    {
        if (!ReferenceEquals(_structureFitResources, bound) || _structureFits is null)
        {
            _structureFits?.Dispose(); _structureFits = new(bound.Catalog, bound.LoadReference); _structureFitResources = bound;
        }
        return _structureFits;
    }

    private StructureIndexFilter GetStructureIndex(BoundResources bound, long epoch, CancellationToken token, CapturedClientFrame captured)
    {
        if (ReferenceEquals(_structureIndexResources, bound) && _structureIndex is not null) return _structureIndex;
        StarMap.Core.Resources.StaticExitMarkerCatalog? markers=null;
        if(bound.Passports.Season=="s0")
        {
            try
            {
                using var file=JsonDocument.Parse(File.ReadAllBytes(Path.Combine(_options.StructureIndexAssetDirectory,"s0_static_exit_markers.json")));
                markers=new(file.RootElement);
            }
            catch(Exception error)when(error is IOException or UnauthorizedAccessException or JsonException)
            { /* Missing coordinates stay UNKNOWN. Glyph observation remains independent. */ }
        }
        var created = new StructureIndexFilter(bound.Catalog, bound.StructureCatalog!, _options.StructureIndexAssetDirectory,
            bound.Passports,markers,bound.LoadReference);
        try
        {
            lock (_sync)
            {
                RequireCurrent(epoch, token, captured, bound);
                DisposeStructureIndex(); _structureIndex = created; _structureIndexResources = bound; return created;
            }
        }
        catch { created.Dispose(); throw; }
    }

    private bool ObserveS0StructureEntry(long epoch,CancellationToken token,CapturedClientFrame captured,
        BoundResources bound,StructureFramePreparation prepared,out StructureGateAnchor? anchor)
    {
        anchor=null;
        lock(_sync)
        {
            RequireCurrent(epoch,token,captured,bound);
            // A selected unique/human identity fits only that identity. An
            // automatic family's displayed member remains non-unique.
            if(!S0DiscoveryRequired)return false;
        }
        try{anchor=GetStructureIndex(bound,epoch,token,captured).DetectAnchor(prepared,()=>Obsolete(epoch,token,captured,bound),token);}
        catch(Exception error)when(error is IOException or InvalidDataException or UnauthorizedAccessException or JsonException or ArgumentException)
        { /* After entry, unavailable evidence cannot switch back to ordinary discovery. */ }
        lock(_sync)
        {
            RequireCurrent(epoch,token,captured,bound);
            if(anchor?.GlyphPresenceVerified==true)_structureIndexEntered=true;
            if(!_structureIndexEntered)return false;
            if(_recognitionPath!="structure-index"){_recognitionPath="structure-index";Publish();}
            return true;
        }
    }

    private RuntimeOperationResult ScanStructure(SessionRuntimeRequest request, long epoch, CancellationToken token,
        CapturedClientFrame captured, BoundResources bound, string floor, Mat frame, JsonElement preparation,
        StructureFramePreparation? existingPreparation=null,StructureGateAnchor? existingAnchor=null)
    {
        StructureEvidenceSession session; ObservationTicket ticket; StructureCandidatePool pool; string floorSource;
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            session = _structureSession ??= new(bound.StructureCatalog!, _roundId!);
            pool = _structurePool ??= new(_roundId!,bound.StructureCatalog!.ResourceKey,bound.StructureCatalog.MapIds);
            ticket = session.BeginObservation(floor); _structureTicket = ticket;
            floorSource = _manualFloor is null ? "current-frame-verified" : "operator-declared";
        }
        JsonObject? indexReport = null;
        using var ownedPreparation=existingPreparation is null?new StructureFramePreparation(frame):null;
        var preparedFrame=existingPreparation??ownedPreparation!;
        string poolResult = "structure-coordinate-unknown";
        JsonObject gate = new() { ["status"] = "unavailable", ["reason"] = "structure-index-resources-unavailable" };
        try
        {
            var index = GetStructureIndex(bound, epoch, token, captured);
            var anchor = existingAnchor??index.DetectAnchor(preparedFrame, () => Obsolete(epoch, token, captured, bound),token); gate = anchor.ToJson();
            lock (_sync)
            {
                RequireCurrent(epoch, token, captured, bound);
                if (bound.Passports.Season=="s0"?anchor.GlyphPresenceVerified:anchor.Status == "located") _structureIndexEntered = true;
                _recognitionPath = _structureIndexEntered ? "structure-index" : "ordinary";
            }
            // An explicit floor override can guide fitting. Contradictory raw
            // floor evidence cannot eliminate maps from the structural ledger.
            if (_structureIndexEntered && _observedFloor == floor)
                indexReport = index.Evaluate(preparedFrame, pool.Remaining, floor, anchor, () => Obsolete(epoch, token, captured, bound));
        }
        catch (Exception error) when (error is IOException or InvalidDataException or UnauthorizedAccessException or JsonException or ArgumentException)
        { indexReport = new() { ["coordinateVerified"] = false, ["reason"] = "structure-index-resources-unavailable", ["detail"] = error.Message }; }
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            if (!ReferenceEquals(_structurePool, pool)) return Receipt(request, false, "structure-observation-obsolete");
            if (indexReport is not null) poolResult = ApplyStructureIndex(pool, bound, floor, indexReport);
            if (_s1ProvisionalMapId is { } provisional && !pool.Remaining.Contains(provisional))
            { _s1ProvisionalMapId = null; _pose = null; _identitySource = StructureStage+"-candidates";ResetSteady();_geometryGroup=null; RevokeDisplay("provisional-map-excluded"); }
            if(bound.Passports.Season=="s0"&&_identitySource=="group-default"&&HasRegisteredGroup
                &&_mapId is { } member&&!pool.Remaining.Contains(member))
            {
                var survivors=_nativeIdentity!.MemberIds.Where(pool.Remaining.Contains).ToArray();
                ClearGroupChoice(resetCycle:false);ResetSteady();_geometryGroup=null;RevokeDisplay("automatic-group-member-excluded");_alignment=null;
                if(survivors.Length>0){_nativeIdentity=_nativeIdentity.PreferDisplayMember(survivors);_mapId=_nativeIdentity.ProvisionalMemberId;}
                else{_nativeIdentity=null;_mapId=null;_identitySource=StructureStage+"-candidates";}
            }
        }
        var family=IndexRegisteredFamily(bound,pool);
        lock(_sync)
        {
            RequireCurrent(epoch,token,captured,bound);
            if(family is not null&&_mapId is null)
            {
                var context=bound.Identity!.Context(_roundId!,floor,null);
                _nativeIdentity=NativeSimilarityState.ForContext(context).WithRegisteredGroup(family,
                    _s1ProvisionalMapId is { } provisional?[provisional]:[]);
                LockIdentity(_nativeIdentity.ProvisionalMemberId!,"group-default");
            }
        }
        var preparedFits=new Dictionary<string,PreparedRuntimeFit>();
        bool retainedGroup=bound.Passports.Season=="s0"&&_identitySource=="group-default"&&HasRegisteredGroup;
        var scanner=bound.UsesS1StructureFit?GetStructureFits(bound).CreateScanner(bound.StructureCatalog!)
            :new StructureCandidateScanner(bound.StructureCatalog!,FitS0Candidate);
        JsonObject? latest = null;
        bool materialCommitted = false;
        string? preferred = retainedGroup?_mapId:_s1ProvisionalMapId is { } prior && pool.Remaining.Contains(prior)
            && _s1PreferenceBinding is not null && _s1PreferenceBinding == _capture.BindingScope
            && _s1PreferenceClient == captured.Context ? prior : null;
        var report = scanner.Scan(preparedFrame, new(floor, floorSource, CandidateOrder: session.PriorityOrder(floor),
            PlannedMapIds: retainedGroup?[_mapId!]:pool.Remaining, PreferredMapId: preferred,
            Cancelled: () => Obsolete(epoch, token, captured, bound), OnProgress: progress =>
            {
                lock (_sync)
                {
                    if (Obsolete(epoch, token, captured, bound) || _structureTicket != ticket) return;
                    if (session.Commit(progress, ticket)) { latest = progress; PublishStructure(progress); }
                }
            }, FirstMaterialCommitted: TryCurrentMaterial));
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            if (_structureTicket != ticket || !session.Commit(report, ticket))
                return FailCurrent(request, epoch, "structure-observation-obsolete");
            PublishStructure(report);
            return Receipt(request, materialCommitted || _candidates.Length > 0, _reason);
        }

        JsonObject FitS0Candidate(Mat input,string id,string level,Func<bool> cancelled,Rect bounds)
        {
            if(cancelled())return new(){["success"]=false,["reason"]="structure-work-obsolete"};
            var fitted=PrepareFit(epoch,token,captured,bound,id,level,input,independentObservation:!retainedGroup);
            if(fitted is null)return new(){["success"]=false,["reason"]="target-floor-resource-unavailable"};
            preparedFits[id]=fitted;return fitted.Result;
        }

        bool TryCurrentMaterial(string id)
        {
            if (latest?["poses"]?[id] is not JsonObject fit || !StructureEvidence.VerifiedPose(fit)) return false;
            var entry = bound.StructureCatalog!.ReferenceFor(bound.Catalog,id,floor);
            var reference = entry is null ? null : bound.LoadReference(entry).Value;
            if (entry is null || reference is null) return false;
            var matrix = fit["matrixSourceToClient"]!.Deserialize<double[][]>()!;
            preparedFits.TryGetValue(id,out var cachedFit);
            var display = cachedFit?.Display??PrepareDisplay(bound, entry, matrix, reference, _selection.ProfileId, token);
            if (display.Material.Layer is null) return false;
            lock (_sync)
            {
                RequireCurrent(epoch, token, captured, bound);
                if (_structureTicket != ticket || !ReferenceEquals(_structurePool, pool) || !pool.Remaining.Contains(id)) return false;
                if(!retainedGroup)
                {
                    if (_s1ProvisionalMapId != id) { RevokeDisplay("provisional-map-changed");ResetSteady();_geometryGroup=null; _alignment = null; }
                    _s1ProvisionalMapId = id; _identitySource = StructureStage+"-provisional";
                }
                _s1PreferenceBinding = _capture.BindingScope; _s1PreferenceClient = captured.Context;
            }
            var prepared = cachedFit??new PreparedRuntimeFit(entry, fit, matrix, display, null, null,
                new() { ["attempted"] = false, ["reason"] = "explicit-domain-independent-fit" });
            Fit(request, epoch, token, captured, bound, id, floor, frame, prepared);
            lock (_sync)
            {
                RequireCurrent(epoch, token, captured, bound);
                materialCommitted = _structureTicket == ticket && ReferenceEquals(_structurePool, pool)
                    &&(retainedGroup?_mapId==id:_s1ProvisionalMapId == id) && RenderCurrent();
                if (materialCommitted && pool.AcceptCurrentMaterial(id))
                { ClearGroupChoice();_nativeIdentity=null;_mapId = id; _s1ProvisionalMapId = null; _identitySource = "structure-index-locked"; }
                Publish(); return materialCommitted;
            }
        }

        void PublishStructure(JsonObject result)
        {
            // Ordinary fitting proposes current pixels; only verified index
            // contradictions change the round pool. Unknown fits cannot vote.
            var supported=result["supportedMapIds"]!.AsArray().Select(id=>id!.GetValue<string>()).ToHashSet(StringComparer.Ordinal);
            var shown=bound.UsesS1StructureFit?result["supportedMapIds"]!.AsArray().Select(id=>id!.GetValue<string>()).Where(pool.Remaining.Contains):pool.Remaining;
            _candidates = shown.Select(id => new RuntimeCandidate(id,bound.Passports.MapProfiles[id],0,false,supported.Contains(id))
                {Name=bound.Passports.Maps.FirstOrDefault(map=>map.MapId==id)?.Name}).ToArray();
            _selected = Array.FindIndex(_candidates, row => row.MapId == EffectiveMapId);
            if (_selected < 0 && _candidates.Length > 0) _selected = 0;
            bool current = RenderCurrent();
            if (!current && !IsStructureProvisional&&!retainedGroup) _identitySource = StructureStage+"-candidates";
            _reason = poolResult == "structure-coordinate-conflict-retained" ? poolResult
                : current ? pool.LockedMapId is not null ? StructureStage+"-index-locked" : _selection.Season+"-candidate-material-current"
                : StructureStage+"-candidates-unresolved";
            _diagnostics = JsonSerializer.SerializeToElement(new { stage = StructureStage, floor = preparation.GetProperty("floor"), report = result,
                index = indexReport, gate, poolResult, session = session.Snapshot(), preparation,
                sharedFramePreparation=preparedFrame.Statistics }, Json);
            Publish();
        }
    }

    private JsonObject? IndexRegisteredFamily(BoundResources bound,StructureCandidatePool pool)
    {
        if(bound.Passports.Season!="s0"||_mapId is not null||pool.Eliminated.Count==0||pool.Remaining.Count<2)return null;
        // This is a lookup of the reviewed registration, not a claim that the
        // current observation is F1/front. Positive cross-floor elimination
        // must leave exactly that family's complete membership.
        var lookup=bound.RegisteredFamilies.Resolve(pool.Remaining[0],"1","front",pool.Remaining);
        if(lookup["status"]?.ToString()!="group"||lookup["candidates"]?[0] is not JsonObject declared
            ||declared["memberIds"] is not JsonArray members
            ||!members.Select(node=>node!.GetValue<string>()).ToHashSet(StringComparer.Ordinal).SetEquals(pool.Remaining))return null;
        return declared;
    }

    private static string ApplyStructureIndex(StructureCandidatePool pool, BoundResources bound, string floor, JsonObject report)
    {
        if (report["mode"]?.ToString() != bound.StructureCatalog!.Mode || report["resourceKey"]?.ToString() != pool.ResourceId
            || report["floor"]?.ToString() != floor || report["evaluatedMapIds"] is not JsonArray evaluated
            || report["contradictedMapIds"] is not JsonArray bad || report["candidateChecks"] is not JsonArray checks)
            return "structure-coordinate-unknown";
        try
        {
            return pool.Observe(report["coordinateVerified"]?.GetValue<bool>() == true,
                evaluated.Select(id => id!.GetValue<string>()).ToArray(), bad.Select(id => id!.GetValue<string>()).ToArray(),
                checks.Select(row => new StructureIndexCheck(row!["mapId"]!.GetValue<string>(), row["explicitContradiction"]!.GetValue<bool>())).ToArray());
        }
        catch (Exception error) when (error is InvalidOperationException or NullReferenceException or FormatException)
        { return "structure-coordinate-unknown"; }
    }
}
