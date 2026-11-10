using StarMap.Core.Session;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private RuntimeOperationResult PrepareManualAlignment(SessionRuntimeRequest request,long epoch,CancellationToken token)
    {
        lock(_sync)
        {
            RequireCurrent(epoch,token);
            if(_selection.Season!="s1")return Receipt(request,false,"manual-session-not-required");
            if(_roundActive)return Receipt(request,true,"round-already-active");
            if(!_capture.IsBound)return Receipt(request,false,"capture-not-bound");
            return StartRound(request,epoch,token,manualAlignment:true);
        }
    }

    private ValueTask<RuntimeOperationResult> FitManualFrame(SessionRuntimeRequest request,long epoch,CancellationToken token)
    {
        lock(_sync)
        {
            RequireCurrent(epoch,token);
            if(!_roundActive || !_manualAlignmentSession || _mapId is null)
                return ValueTask.FromResult(Receipt(request,false,"manual-map-required"));
        }
        if(Flag(request.Payload,"s1OpenEdge"))return MapInput(request,epoch,token);
        return CaptureAndSolve(request,epoch,token);
    }
}
