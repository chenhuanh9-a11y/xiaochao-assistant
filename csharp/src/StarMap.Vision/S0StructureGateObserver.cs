using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision;

/// <summary>Candidate-independent S0 glyph evidence from the declared original exit observer.
/// Observed raster support is not a calibrated native marker, scale or map identity.</summary>
public sealed class S0StructureGateObserver : IDisposable
{
    private readonly SideDoorObservation observer;
    public string TemplateSha256 { get; }
    public string ProfileSha256 { get; }

    public S0StructureGateObserver(string declaredIconPath)
    {
        // Preserve the existing anchor template/profile identity protocol. The
        // fingerprint and detector consume one byte snapshot, not two path reads.
        byte[] bytes=File.ReadAllBytes(declaredIconPath);
        observer=new(bytes);
        TemplateSha256=Convert.ToHexStringLower(SHA256.HashData(bytes));
        ProfileSha256=Convert.ToHexStringLower(SHA256.HashData(JsonSerializer.SerializeToUtf8Bytes(new{
            algorithm="original-s0-exit-observation",minimumIconScore=SideDoorObservation.ReliableIconScore,
            region="current-visible-structure-bounds",support="full-glyph-raster",multipleGlyphs="unknown"})));
    }

    public StructureGateAnchor Detect(Mat frame,CancellationToken cancellation=default,Func<bool>? obsolete=null)
    {
        Check();var result=observer.Observe(frame,cancellation:cancellation);Check();
        var inside=new List<(Point2d Center,int Width,int Height,double Score)>();
        foreach(var row in result.Observations.OfType<JsonObject>())
        {
            var icon=row["icon"]!.AsObject();var center=icon["center"]!.AsArray();var size=icon["size"]!.AsArray();
            double x=center[0]!.GetValue<double>(),y=center[1]!.GetValue<double>();
            int width=size[0]!.GetValue<int>(),height=size[1]!.GetValue<int>();double score=icon["score"]!.GetValue<double>();
            if(result.Bounds is { } bounds && double.IsFinite(x)&&double.IsFinite(y)&&double.IsFinite(score)
                && width>0&&height>0&&x>=bounds.X&&x<bounds.Right&&y>=bounds.Y&&y<bounds.Bottom)
                inside.Add((new(x,y),width,height,score));
        }
        var glyphs=inside.Where(p=>p.Score>=SideDoorObservation.ReliableIconScore).OrderByDescending(p=>p.Score)
            .ThenBy(p=>p.Center.X).ThenBy(p=>p.Center.Y).ToArray();
        var diagnostic=new JsonObject{["source"]="declared-s0-exit-icon",["glyph_presence_verified"]=glyphs.Length>0,
            ["reliable_glyph_count"]=glyphs.Length,["observations"]=result.Observations.DeepClone(),
            ["raster_size_is_map_zoom"]=false,["coordinates_authorized"]=false,
            ["uncertainty_kind"]="observed-raster-support-not-native-marker-calibration"};
        if(glyphs.Length!=1)return new(){Status="unknown",Reason=glyphs.Length==0?"no-reliable-s0-glyph":"multiple-reliable-s0-glyphs",
            TemplateSha256=TemplateSha256,ProfileSha256=ProfileSha256,Diagnostics=diagnostic,Frame=frame};
        var glyph=glyphs[0];double halfX=glyph.Width/2d,halfY=glyph.Height/2d;
        double second=inside.Where(p=>p.Center!=glyph.Center).Select(p=>p.Score).DefaultIfEmpty(0).Max();
        return new(){Status="located",Reason="s0-glyph-observed",CenterPx=glyph.Center,
            LocalizationUncertaintyPx=Math.Max(halfX,halfY),
            CenterSupportBoundsPx=[glyph.Center.X-halfX,glyph.Center.Y-halfY,glyph.Center.X+halfX,glyph.Center.Y+halfY],
            GlyphRasterSizePx=[glyph.Width,glyph.Height],RasterSupportRangePx=[glyph.Width,glyph.Width],
            Score=glyph.Score,DistinctSecondScore=second,ScoreMargin=glyph.Score-second,
            TemplateSha256=TemplateSha256,ProfileSha256=ProfileSha256,Diagnostics=diagnostic,Frame=frame};

        void Check(){cancellation.ThrowIfCancellationRequested();if(obsolete?.Invoke()==true)throw new OperationCanceledException("obsolete-s0-glyph");}
    }
    public void Dispose()=>observer.Dispose();
}
