using OpenCvSharp;
using System.Text.Json.Nodes;
using static StarMap.Vision.S1StructureObservations;

namespace StarMap.Vision;

public sealed record StructureFramePreparationStats(int IslandPasses, int ComponentCacheEntries, int ChainCacheEntries);

/// <summary>One operation's owned BGR frame and serial, candidate-independent lazy observations.
/// Has no identity, resource, pose or display authority. Do not share its mutable lazy caches between tasks.</summary>
public sealed class StructureFramePreparation : IDisposable
{
    private readonly Mat _frame;
    private IReadOnlyList<Island>? _islands;
    private readonly Dictionary<Island,Observation?> _components = new(ReferenceEqualityComparer.Instance);
    private bool _disposed;

    public StructureFramePreparation(Mat input)
    {
        ArgumentNullException.ThrowIfNull(input);
        if(input.Empty() || input.Type()!=MatType.CV_8UC3)throw new ArgumentException("s1-structure-frame-invalid",nameof(input));
        _frame=input.Clone();
    }
    internal Mat Frame { get { ObjectDisposedException.ThrowIf(_disposed,this);return _frame; } }
    internal Rect Domain => new(0,0,Frame.Cols,Frame.Rows);
    public StructureFramePreparationStats Statistics => new(_islands is null?0:1,_components.Count,
        _components.Values.Count(c=>c?.Chains is not null));

    internal IReadOnlyList<Island> Islands() => _islands??=S1StructureObservations.Islands(Frame,Domain);

    // Geometry.Current chooses the first maximum-area admissible raw component.
    // Islands preserves label order for ties. Index chain/32-island filters are
    // consumer policies and never remove anything from this complete list.
    internal Observation? Primary() => Islands().FirstOrDefault() is { } first?For(first):null;

    internal Observation? For(Island island)
    {
        _=Frame;
        if(!_components.TryGetValue(island,out var context))
        {context=Geometry.FromComponent(_frame,island.Bounds,(JsonObject)island.Component.DeepClone());_components.Add(island,context);}
        return context;
    }
    internal void Require(Mat frame,Rect domain)
    {
        if(!ReferenceEquals(frame,Frame) || domain!=Domain)throw new ArgumentException("structure-preparation-frame-mismatch");
    }
    public void Dispose()
    {
        if(_disposed)return;_disposed=true;
        foreach(var context in _components.Values)context?.Dispose();
        _components.Clear();_frame.Dispose();
    }
}
