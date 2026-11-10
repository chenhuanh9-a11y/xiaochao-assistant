namespace StarMap.Core.Input;

/// <summary>Marker for a business owner's immutable, pixel-free press context.</summary>
public interface IGlobalInputPointerIntentContext { }

public sealed record GlobalInputPointerContextSnapshot(string ContextId,double ObservationCreatedAtSeconds,
    string? DownIntentScopeId=null,IGlobalInputPointerIntentContext? DownIntentSnapshot=null);

/// <summary>
/// One immutable published value. The runtime is the sole publisher; native
/// code only reads this slot and never invokes a business or capture callback.
/// Null means no observation, including when a target has legacy static fields.
/// </summary>
public sealed class GlobalInputPointerContextSlot
{
    private GlobalInputPointerContextSnapshot? current;
    public GlobalInputPointerContextSnapshot? Read()=>Volatile.Read(ref current);
    public void Publish(GlobalInputPointerContextSnapshot? value)=>Volatile.Write(ref current,value);
}
