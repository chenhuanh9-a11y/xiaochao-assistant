using System.Text.Json;
using StarMap.Core.Input;

namespace StarMap.App;

internal sealed partial class RuntimePage
{
    JsonElement? dragBoundary;
    MapDragMotion? pointerDragMotion;
    GlobalInputPointerEvent? pointerDragPress;

    void CancelPointerDrag()
    {
        if(pointerDragPress is { } press)runtime?.CancelGameMapDrag(press.GestureId);
        pointerDragPress=null;pointerDragMotion=null;
    }

    // Called only for the strict ordered pointer lane, after floor/X precedence on down.
    internal void HandleGamePointer(GlobalInputPointerEvent pointer)
    {
        if(pointer.Phase=="cancel")
        {if(pointerDragPress?.GestureId==pointer.GestureId)CancelPointerDrag();return;}
        if(closed || configuring || loadingResources || session is null || runtime is null)return;
        if(pointer.Phase=="down")
        {
            CancelPointerDrag();
            if(pointer.PressContextSnapshot?.DownIntentSnapshot is not StarMap.Runtime.GameFloorClickContext context)return;
            var g=context.Geometry;
            var motion=new MapDragMotion(pointer.X,pointer.Y,g.Left,g.Top,g.Width,g.Height,dragBoundary);
            if(!motion.Active || !runtime.BeginGameMapDrag(pointer).Success)return;
            pointerDragPress=pointer;pointerDragMotion=motion;return;
        }
        if(pointerDragPress is not { } press || pointerDragMotion is null || press.GestureId!=pointer.GestureId
            || press.Generation!=pointer.Generation || press.PointerGeneration!=pointer.PointerGeneration)return;
        var step=pointerDragMotion.Consume(pointer.Phase,pointer.X,pointer.Y);
        if(step is null)return;
        var prepared=runtime.PrepareGameMapDragMotion(pointer,step,session.Read().Sequence);
        if(!prepared.Success){CancelPointerDrag();return;}
        if(prepared.FirstMotion)
        {
            CancelDelay();mapRead=null;
            session.CancelPendingForPresentation("map-drag-started");
        }
        var applied=runtime.ApplyGameMapDragMotion(pointer,step);
        if(!applied.Success){CancelPointerDrag();return;}
        if(pointer.Phase=="up")
        {
            pointerDragPress=null;pointerDragMotion=null;
            if(applied.ProbeRequired)Dispatch("probe_map_after_drag",new {gestureId=pointer.GestureId,pointerSequence=pointer.Sequence});
        }
        session.RefreshRuntime();Render();
    }
}
