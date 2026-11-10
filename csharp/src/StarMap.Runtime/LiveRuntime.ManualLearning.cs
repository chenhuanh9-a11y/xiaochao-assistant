using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private ManualLearningStatus? _manualLearning;
    private (BoundResources Owner,IManualConfirmationMemory Store)? _confirmationMemory;
    private sealed record ConfirmationLabelOwner(long Epoch,BoundResources Bound,SessionSelection Selection,
        string Round,string Map,string? Floor,CaptureEvidenceKind EvidenceKind);

    private ManualLearningStatus PersistConfirmationLabel(ConfirmationLabelOwner owner,CancellationToken token)
    {
        if(owner.Selection.Season!="s0")return new("skipped","season-manual-learning-unregistered");
        if(owner.EvidenceKind!=CaptureEvidenceKind.LiveClient)return new("skipped","non-live-evidence");
        CapturedClientFrame captured;
        lock(_sync)
        {
            if(token.IsCancellationRequested || _closed || owner.Epoch!=_epoch || !ReferenceEquals(owner.Bound,_resources)
                || owner.Selection!=_selection || owner.Round!=_roundId || !_roundActive || !_mapOpen
                || owner.Map!=_mapId || owner.Floor!=_floor || _frame is not {Context.EvidenceKind:CaptureEvidenceKind.LiveClient} frame)
                return new("skipped","confirmation-context-obsolete");
            if(!_capture.IsCurrent(frame.Stamp))return new("skipped","capture-context-changed");
            // Acceptance point: immutable current pixels and context now belong
            // to this one label. Disk work drains outside _sync even after close.
            captured=frame;
        }
        bool saved=false;string stage="save";
        try
        {
            var memory=_confirmationMemory is { } existing && ReferenceEquals(existing.Owner,owner.Bound)?existing.Store
                :_options.ConfirmationMemoryFactory?.Invoke(owner.Bound.Passports);
            if(memory is null)return new("skipped","memory-store-unavailable");
            _confirmationMemory=(owner.Bound,memory);
            var geometry=captured.Context!.Geometry;
            using var pixels=ToBgr(captured);
            var row=memory.RecordConfirmation(owner.Map,owner.Floor!,pixels,
                [geometry.Left,geometry.Top,checked(geometry.Left+geometry.Width),checked(geometry.Top+geometry.Height)]);
            saved=true;stage="reload";memory.Reload();
            return WithContext(new("saved",Saved:true,Reloaded:true,HasSample:row["samplePath"] is not null));
        }
        catch(Exception error)
        {
            if(error is ManualConfirmationPostSaveException committed){saved=true;stage=committed.Stage;}
            string message=error.GetType().Name+":"+error.Message;
            return WithContext(new("error",Saved:saved,Error:message[..Math.Min(256,message.Length)],FailureStage:stage));
        }
        ManualLearningStatus WithContext(ManualLearningStatus value)=>value with {Season=owner.Selection.Season,Mode=owner.Selection.Mode,
            RoundId=owner.Round,MapId=owner.Map,Floor=owner.Floor};
    }
}
