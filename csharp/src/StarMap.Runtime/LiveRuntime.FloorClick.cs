using System.Text.Json;
using StarMap.Core.Floors;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed record GameFloorClickContext(string RuntimeInstance,long Epoch,SessionSelection Selection,string Round,
    string Resource,long Cycle,CaptureStamp Stamp,CapturedClientGeometry Geometry,long ObservationOrdinal,double ObservationCreatedAtSeconds)
    :IGlobalInputPointerIntentContext;
public sealed record GameFloorClickResult(bool Success,string Reason,string? ClickedFloorId=null,long IntentRevision=0,bool CloseControlHit=false);
public sealed record GameFloorIntent(string FloorId,long Cycle,double ExpiresAt,long Revision);

public sealed partial class LiveRuntime
{
    readonly string pointerInstance=Guid.NewGuid().ToString("N");
    readonly GlobalInputPointerContextSlot pointerSlot=new();
    readonly Dictionary<string,long> pointerRetiredThrough=new(StringComparer.Ordinal);
    PointerObservation? pointerObservation;
    long pointerObservationOrdinal,pointerIntentRevision;
    GameFloorIntent? gameFloorIntent;
    (long Start,long End)? floorClickEpochSpan;
    PointerScope? publishedPointerScope;
    string pointerScopeId="";
    string floorPolicyReason="";
    bool floorClickRequired;
    sealed record PointerObservation(CapturedClientFrame Frame,JsonElement Read,long Ordinal,double CreatedAt,MapCloseControlObservation? CloseControl);
    sealed record PointerScope(long Epoch,SessionSelection Selection,string Round,string Resource,long Cycle,
        string WindowIdentity,long CaptureGeneration,CapturedClientGeometry Geometry);
    public GlobalInputPointerContextSlot PointerContextSlot=>pointerSlot;
    double PointerNowSeconds=>_options.TimeProvider.GetTimestamp()/(double)_options.TimeProvider.TimestampFrequency;

    void RememberPointerObservation(CapturedClientFrame frame,FloorObservationResult read)
        =>pointerObservation=new(frame,JsonSerializer.SerializeToElement(read.FloorRead),++pointerObservationOrdinal,PointerNowSeconds,read.CloseControl);
    void ClearFloorClickIntent(bool preservePolicy=false)
    {gameFloorIntent=null;floorClickEpochSpan=null;if(!preservePolicy)floorPolicyReason="";floorClickRequired=false;}
    void ClearPointerObservation(){ClearFloorClickIntent();pointerObservation=null;pointerSlot.Publish(null);}
    bool PointerRetired(SessionRuntimeRequest request)=>request.Action is not ("end_round" or "stop_round" or "reset_round" or "cancel" or "close_map")
        && !string.IsNullOrEmpty(request.Context.Ticket.SessionId)
        && pointerRetiredThrough.TryGetValue(request.Context.Ticket.SessionId,out long retired) && request.Context.Ticket.Sequence<=retired;

    GameFloorClickContext? CurrentPointerContext()
    {
        if(_closed || !_roundActive || !_mapOpen || _roundId is null || _resources is null
            || pointerObservation is not {Frame.Context: { } captureContext} observation)return null;
        // The last accepted raw observation owns input evidence independently
        // of the numeric frame, which confirmation retires until fresh capture.
        return new(pointerInstance,_epoch,_selection,_roundId,_resources.Id,_mapCycle,observation.Frame.Stamp,captureContext.Geometry,observation.Ordinal,observation.CreatedAt);
    }
    void PublishPointerObservation()
    {
        var context=CurrentPointerContext();
        if(context is null){pointerSlot.Publish(null);return;}
        long boundary=floorClickEpochSpan is { } span && span.End==_epoch?span.Start:_epoch;
        var scope=new PointerScope(boundary,context.Selection,context.Round,context.Resource,context.Cycle,
            context.Stamp.WindowIdentity,context.Stamp.Generation,context.Geometry);
        if(scope!=publishedPointerScope){publishedPointerScope=scope;pointerScopeId=Guid.NewGuid().ToString("N");}
        var previous=pointerSlot.Read();
        if(previous?.DownIntentSnapshot is GameFloorClickContext prior && prior==context && previous.DownIntentScopeId==pointerScopeId)return;
        pointerSlot.Publish(new(Guid.NewGuid().ToString("N"),context.ObservationCreatedAtSeconds,pointerScopeId,context));
    }

    /// <summary>UI-ordered metadata only. Caller owns listening/foreground admission; no capture or fit runs here.</summary>
    public GameFloorClickResult ObserveGameFloorClick(GlobalInputPointerEvent press,SessionSnapshot issuedThrough)
    {
        lock(_sync)
        {
            var snapshot=press.PressContextSnapshot;var current=CurrentPointerContext();
            if(snapshot?.DownIntentSnapshot is not GameFloorClickContext supplied || current is null)
                return new(false,"floor-click-context-unavailable");
            bool ownEpoch=floorClickEpochSpan is { } span && span.End==_epoch && supplied.Epoch>=span.Start && supplied.Epoch<=span.End;
            bool sameObservation=supplied.Stamp.FrameId==current.Stamp.FrameId && supplied.ObservationOrdinal==current.ObservationOrdinal
                && supplied.ObservationCreatedAtSeconds==current.ObservationCreatedAtSeconds;
            bool advanced=supplied.ObservationOrdinal>=0 && supplied.ObservationOrdinal<current.ObservationOrdinal
                && double.IsFinite(supplied.ObservationCreatedAtSeconds) && supplied.ObservationCreatedAtSeconds<=current.ObservationCreatedAtSeconds;
            if(press.Phase!="down" || press.ContextId!=snapshot.ContextId || snapshot.DownIntentScopeId!=pointerSlot.Read()?.DownIntentScopeId
                || supplied.RuntimeInstance!=current.RuntimeInstance || supplied.Epoch!=_epoch && !ownEpoch || supplied.Selection!=current.Selection
                || supplied.Round!=current.Round || supplied.Resource!=current.Resource || supplied.Cycle!=current.Cycle
                || supplied.Stamp.WindowIdentity!=current.Stamp.WindowIdentity || supplied.Stamp.Generation!=current.Stamp.Generation
                || supplied.Geometry!=current.Geometry || !(sameObservation || advanced))return new(false,"floor-click-context-stale");
            double age=PointerNowSeconds-press.CreatedAtSeconds;
            if(!double.IsFinite(age) || age<0 || age>2 || press.CreatedAtSeconds<supplied.ObservationCreatedAtSeconds)return new(false,"floor-click-expired");
            if(!_capture.IsBound || !_capture.IsObservationContextCurrent(pointerObservation!.Frame))return new(false,"capture-context-changed");
            var geometry=current.Geometry;
            if(issuedThrough.Closed || string.IsNullOrEmpty(issuedThrough.SessionId) || issuedThrough.LastIssuedSequence<0)return new(false,"floor-click-session-unavailable");
            double[] bounds=[geometry.Left,geometry.Top,(double)geometry.Left+geometry.Width,(double)geometry.Top+geometry.Height];
            int? clicked=GameFloorControl.Hit(press.X,press.Y,bounds,pointerObservation!.Read);
            if(clicked is null)return new(false,"not-floor-control",CloseControlHit:MapCloseControl.Hit(pointerObservation.CloseControl,press.X,press.Y,bounds));
            string floor=FloorContract.Find(_selection.Season,_selection.Mode)!.IdFor(clicked)!;
            pointerRetiredThrough[issuedThrough.SessionId]=Math.Max(pointerRetiredThrough.GetValueOrDefault(issuedThrough.SessionId),issuedThrough.LastIssuedSequence);
            long first=floorClickEpochSpan is { } existing && existing.End==_epoch?existing.Start:_epoch;
            _epoch++;_progressRequest=null;floorClickEpochSpan=(first,_epoch);_mapRecovery=null;
            bool targetChanged=_floor!=floor;
            floorClickRequired=_observedFloor!=floor;
            if(targetChanged){ResetSteady();_geometryGroup=null;}
            // An existing assistant hold follows the explicit game target,
            // keeping its original deadline. A game click creates no hold.
            if(_manualFloorStarted is { } hold && _options.TimeProvider.GetElapsedTime(hold)<TimeSpan.FromSeconds(2))
            {if(targetChanged)_manualFloor=null;}
            else{_manualFloor=null;_manualFloorStarted=null;}
            _floor=floor;_pose=null;RevokeDisplay("game-floor-control-intent");
            _structureSession?.RevokeCurrent();_structureTicket=null;_entryCandidates.Clear();
            gameFloorIntent=new(floor,_mapCycle,press.CreatedAtSeconds+2,++pointerIntentRevision);
            floorPolicyReason=targetChanged?"floor-state-applied":floorClickRequired?"already-current-floor":"current-floor-authorization-applied";
            _reason="game-floor-control-intent";Publish();
            return new(true,_reason,floor,gameFloorIntent.Revision);
        }
    }

    string? ResolveGameFloorIntent(FloorObservationResult read)
    {
        _observedFloor=read.AutomaticEligible?read.FloorId:null;
        if(gameFloorIntent is not { } intent)return null;
        if(intent.Cycle!=_mapCycle || PointerNowSeconds>intent.ExpiresAt)
        {gameFloorIntent=null;floorClickRequired=false;return null;}
        if(_observedFloor!=intent.FloorId)
        {floorPolicyReason="pending-click-mismatch";return intent.FloorId;}
        gameFloorIntent=null;floorPolicyReason=floorClickRequired?"current-floor-authorization-applied":"already-current-floor";floorClickRequired=false;
        return intent.FloorId;
    }
}
