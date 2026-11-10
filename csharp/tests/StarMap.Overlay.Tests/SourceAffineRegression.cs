using System.IO;
using System.Diagnostics;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using StarMap.App.Platform;

internal static class SourceAffineRegression
{
    internal static int CompareQt(string oracle, string output)
    {
        RenderOptions.ProcessRenderMode = RenderMode.SoftwareOnly;
        var comparisons = new List<object>();
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllBytes(oracle));
            using var surface = new WpfAlignedOverlaySurface();
            foreach (var row in document.RootElement.GetProperty("cases").EnumerateArray())
            {
                string id = row.GetProperty("id").GetString()!;
                byte[] qt = File.ReadAllBytes(row.GetProperty("qtPremultipliedBgraPath").GetString()!);
                int qw = row.GetProperty("qtRawWidth").GetInt32(), qh = row.GetProperty("qtRawHeight").GetInt32();
                if (qt.Length != checked(qw*qh*4)) throw new InvalidDataException("Qt byte length disagrees with target.");
                if (row.TryGetProperty("expectedRefusal", out var refusal) && refusal.GetBoolean())
                {
                    if (row.GetProperty("actualQmlValid").GetBoolean() || Enumerable.Range(0,qw*qh).Any(p=>qt[p*4+3]!=0))
                        throw new InvalidDataException("Original recorded-grant refusal did not remain transparent.");
                    comparisons.Add(new{id,comparison="original-recorded-grant-refusal",comparedPixels=false}); continue;
                }
                int width = row.GetProperty("sourceWidth").GetInt32(), height = row.GetProperty("sourceHeight").GetInt32(), stride = row.GetProperty("sourceStride").GetInt32();
                byte[] input = File.ReadAllBytes(row.GetProperty("sourceBgraPath").GetString()!);
                if (input.Length != checked(stride*height)) throw new InvalidDataException("Original source byte length disagrees with canvas.");
                var source = BitmapSource.Create(width,height,96,96,PixelFormats.Bgra32,null,input,stride);source.Freeze();
                var matrix = row.GetProperty("matrix").EnumerateArray().Select(r=>r.EnumerateArray().Select(v=>v.GetDouble()).ToArray()).ToArray();
                var h = new WpfOverlayHomography(matrix[0][0],matrix[0][1],matrix[0][2],matrix[1][0],matrix[1][1],matrix[1][2],matrix[2][0],matrix[2][1],matrix[2][2]);
                int cw=row.GetProperty("clientPhysicalWidth").GetInt32(),ch=row.GetProperty("clientPhysicalHeight").GetInt32();
                double dpi=row.GetProperty("deviceScale").GetDouble()*96,opacity=row.GetProperty("sheetOpacity").GetDouble();
                var data=row.GetProperty("displayInput");
                var client=new WpfOverlayClientGeometry((int)data.GetProperty("originX").GetDouble(),(int)data.GetProperty("originY").GetDouble(),cw,ch,(uint)dpi);
                var ticket=surface.Reserve(new object());
                long allocated=GC.GetAllocatedBytesForCurrentThread();var watch=Stopwatch.StartNew();
                var prepared=WpfAlignedOverlaySurface.PrepareAffine(ticket,source,h,client,opacity);
                double sourcePrepareMs=watch.Elapsed.TotalMilliseconds;long sourcePrepareBytes=GC.GetAllocatedBytesForCurrentThread()-allocated;
                var visual=new WpfAlignedOverlayVisual();visual.Apply(prepared,dpi,dpi);var rendered=Render(visual,cw,ch,dpi);byte[] wpf=Read(rendered);
                var raster=new WpfOverlayBgra32(width,height,stride,input);
                allocated=GC.GetAllocatedBytesForCurrentThread();watch.Restart();
                var cpu=WpfAlignedOverlaySurface.Prepare(ticket,raster,h,client,opacity);
                double cpuPrepareMs=watch.Elapsed.TotalMilliseconds;long cpuPrepareBytes=GC.GetAllocatedBytesForCurrentThread()-allocated;
                var cpuVisual=new WpfAlignedOverlayVisual();cpuVisual.Apply(cpu,dpi,dpi);byte[] old=Read(Render(cpuVisual,cw,ch,dpi));
                int cpuMaximum=old.Zip(wpf,(a,b)=>Math.Abs(a-b)).Max();
                int cpuAlphaSupportDifference=Enumerable.Range(0,cw*ch).Count(p=>(old[p*4+3]>0)!=(wpf[p*4+3]>0));
                var rect=row.GetProperty("comparisonCropPhysicalRect").EnumerateArray().Select(x=>x.GetInt32()).ToArray();
                if(rect[0]!=0||rect[1]!=0||rect[2]!=cw||rect[3]!=ch||qw<cw||qh<ch)throw new InvalidDataException("Unexpected Qt client crop.");
                int maximum=0,changed=0,alphaSupportDifference=0;long sum=0;
                for(int y=0;y<ch;y++)for(int x=0;x<cw;x++)
                {
                    int q=(y*qw+x)*4,w=(y*cw+x)*4;
                    if((qt[q+3]>0)!=(wpf[w+3]>0))alphaSupportDifference++;
                    for(int c=0;c<4;c++){int d=Math.Abs(qt[q+c]-wpf[w+c]);maximum=Math.Max(maximum,d);sum+=d;if(d!=0)changed++;}
                }
                var image=(Image)((Canvas)visual.Children[0]).Children[0];var mapping=image.TransformToAncestor(visual);
                Point[] corners=[new(0,0),new(width,0),new(0,height),new(width,height)];
                var qtCorners=row.GetProperty("qtCornersLogical").EnumerateArray().Select(p=>p.EnumerateArray().Select(v=>v.GetDouble()).ToArray()).ToArray();
                double cornerError=0;for(int i=0;i<4;i++){var p=mapping.Transform(corners[i]);cornerError=Math.Max(cornerError,Math.Max(Math.Abs(p.X-qtCorners[i][0]),Math.Abs(p.Y-qtCorners[i][1])));}
                if(cornerError>.02)throw new InvalidDataException("Actual WPF and original Qt geometry disagree.");
                Save(rendered,Path.Combine(output,id+"-wpf.png"));File.WriteAllBytes(Path.Combine(output,id+"-wpf.pbgra"),wpf);
                comparisons.Add(new{id,comparison="actual-Qt-software-vs-WPF-software",comparedPixels=true,cw,ch,dpi,maximum,
                    changedChannels=changed,meanAbsoluteChannelError=sum/(double)(cw*ch*4),alphaSupportDifference,cornerError,
                    cpuMaximum,cpuAlphaSupportDifference,sourcePrepareMs,cpuPrepareMs,sourcePrepareBytes,cpuPrepareBytes,
                    syntheticDisplayGrant=row.TryGetProperty("syntheticDisplayGrant",out var grant)&&grant.GetBoolean()});
                if(id is "identity-opaque" or "integer-translation" or "negative-clip")
                    if(maximum!=0)throw new InvalidDataException("Independent exact opaque control differs: "+id);
            }
            File.WriteAllText(Path.Combine(output,"result.json"),JsonSerializer.Serialize(new{exit=0,comparisons,
                scope="Actual original Qt software-vs-actual WPF software characterization. Recorded refusal controls retained; display-only synthetic grants are not Runtime/live/window authorization. General interpolation differences are measurements, not equivalence passes."},new JsonSerializerOptions{WriteIndented=true}));return 0;
        }
        catch(Exception error){File.WriteAllText(Path.Combine(output,"result.json"),JsonSerializer.Serialize(new{exit=1,comparisons,error=error.ToString()},new JsonSerializerOptions{WriteIndented=true}));return 1;}
    }
    internal static int Run(string output)
    {
        RenderOptions.ProcessRenderMode = RenderMode.SoftwareOnly;
        byte[] bytes = [0,0,255,255, 0,255,0,128, 255,0,0,255, 30,40,50,0, 255,255,255,255, 10,20,200,64];
        var source = BitmapSource.Create(3,2,96,96,PixelFormats.Bgra32,null,bytes,12); source.Freeze();
        var raster = new WpfOverlayBgra32(3,2,12,bytes);
        var cases = new[] {
            ("identity", WpfOverlayHomography.Identity, true),
            ("integer-clipped", new WpfOverlayHomography(1,0,-1,0,1,1,0,0,1), true),
            ("rotation90", new WpfOverlayHomography(0,-1,3,1,0,0,0,0,1), true),
            ("scale2",new WpfOverlayHomography(2,0,0,0,2,0,0,0,1),false),
            ("real-scale",new WpfOverlayHomography(.6624533675312996,0,1.3,0,.6624533675312996,.4,0,0,1),false),
            ("shear",new WpfOverlayHomography(1,.2,-.2,.1,1,.3,0,0,1),false)
        };
        var evidence = new List<object>(); bool coordinateFailure = false;
        try
        {
            using var surface = new WpfAlignedOverlaySurface();
            foreach(uint dpi in new uint[]{96,120,144,192})
            foreach(var (name,h,exact) in cases)
            {
                var client = new WpfOverlayClientGeometry(-1920,-300,6,5,dpi); var ticket = surface.Reserve(new object());
                var cpu = WpfAlignedOverlaySurface.Prepare(ticket,raster,h,client,.75);
                var affine = WpfAlignedOverlaySurface.PrepareAffine(ticket,source,h,client,.75);
                if(!ReferenceEquals(source,affine.Image) || affine.ClientPixels is not null) throw new InvalidDataException("Affine source was copied or projected.");
                var oldVisual = new WpfAlignedOverlayVisual(); oldVisual.Apply(cpu,dpi,dpi);
                var newVisual = new WpfAlignedOverlayVisual(); newVisual.Apply(affine,dpi,dpi);
                var before=Render(oldVisual,6,5,dpi); var after=Render(newVisual,6,5,dpi);
                var a=Read(before);var b=Read(after);int max=a.Zip(b,(x,y)=>Math.Abs(x-y)).Max();int changed=a.Zip(b,(x,y)=>x!=y).Count(x=>x);
                Save(before,Path.Combine(output,$"{name}-{dpi}-cpu.png"));Save(after,Path.Combine(output,$"{name}-{dpi}-source.png"));
                evidence.Add(new{name,dpi,expectedExact=exact,maxChannelDifference=max,changedChannels=changed,
                    differingPixels=Enumerable.Range(0,30).Where(p=>!a.AsSpan(p*4,4).SequenceEqual(b.AsSpan(p*4,4)))
                        .Select(p=>new{x=p%6,y=p/6,cpu=a.Skip(p*4).Take(4).ToArray(),source=b.Skip(p*4).Take(4).ToArray()}).ToArray()});
                coordinateFailure |= exact && max!=0;
            }
            if(coordinateFailure) throw new InvalidDataException("One or more exact coordinate/alpha cases differ; see all comparisons.");
            var own=surface.Reserve(new object());var first=WpfAlignedOverlaySurface.PrepareAffine(own,source,WpfOverlayHomography.Identity,new(0,0,6,5,96));
            var persistent=new WpfAlignedOverlayVisual();persistent.Apply(first,96,96);
            var image=(Image)((Canvas)persistent.Children[0]).Children[0];var transform=image.RenderTransform;
            surface.Withdraw();var next=surface.Reserve(new object());
            var moved=WpfAlignedOverlaySurface.PrepareAffine(next,source,new(1,0,2,0,1,1,0,0,1),new(0,0,6,5,96),.5);
            if(!ReferenceEquals(first.Image,moved.Image) || surface.TryApply(own,first) || surface.NativeHandle!=0)throw new InvalidDataException("Pixels revived an old ticket.");
            persistent.Apply(moved,96,96);var shown=Read(Render(persistent,6,5,96));int red=(1*6+2)*4;
            if(!ReferenceEquals(image,((Canvas)persistent.Children[0]).Children[0]) || !ReferenceEquals(source,image.Source)
                || !ReferenceEquals(transform,image.RenderTransform) || Math.Abs(shown[red+2]-128)>1 || Math.Abs(shown[red+3]-128)>1
                || shown[3]!=0)throw new InvalidDataException("Position update did not reuse the actual Image/source/transform or paint new coordinates.");
            void Reject(Func<WpfAlignedOverlayPreparedFrame> prepare)
            {try{prepare();}catch(Exception error)when(error is ArgumentException or InvalidDataException or OperationCanceledException){return;}throw new InvalidDataException("Invalid affine frame was accepted.");}
            var client96=new WpfOverlayClientGeometry(0,0,6,5,96);
            Reject(()=>WpfAlignedOverlaySurface.PrepareAffine(next,source,new(1,0,0,0,1,0,.1,0,1),client96));
            Reject(()=>WpfAlignedOverlaySurface.PrepareAffine(next,source,default,client96));
            Reject(()=>WpfAlignedOverlaySurface.PrepareAffine(next,source,new(double.NaN,0,0,0,1,0,0,0,1),client96));
            Reject(()=>WpfAlignedOverlaySurface.PrepareAffine(next,source,WpfOverlayHomography.Identity,client96,double.NaN));
            Reject(()=>WpfAlignedOverlaySurface.PrepareAffine(own,source,WpfOverlayHomography.Identity,client96));
            var mutable=BitmapSource.Create(3,2,96,96,PixelFormats.Bgra32,null,bytes,12);
            Reject(()=>WpfAlignedOverlaySurface.PrepareAffine(next,mutable,WpfOverlayHomography.Identity,client96));
            using var cancelled=new CancellationTokenSource();cancelled.Cancel();
            Reject(()=>WpfAlignedOverlaySurface.PrepareAffine(next,source,WpfOverlayHomography.Identity,client96,cancellationToken:cancelled.Token));
            File.WriteAllText(Path.Combine(output,"result.json"),JsonSerializer.Serialize(new{exit=0,comparisons=evidence,
                persistentImageSourceTransformReused=true,invalidAndExpiredFramesRejected=7,
                scope="Actual WPF software renderer, source-vs-CPU characterization; non-exact cases are measured differences, not equivalence passes. No Show/native/game/GPU/latency claim."},new JsonSerializerOptions{WriteIndented=true}));return 0;
        }
        catch(Exception error)
        {
            File.WriteAllText(Path.Combine(output,"result.json"),JsonSerializer.Serialize(new{exit=1,comparisons=evidence,error=error.ToString()},new JsonSerializerOptions{WriteIndented=true}));return 1;
        }
    }
    static BitmapSource Render(FrameworkElement visual,int width,int height,double dpi)
    {visual.Measure(new(width*96/dpi,height*96/dpi));visual.Arrange(new Rect(0,0,width*96/dpi,height*96/dpi));visual.UpdateLayout();var bitmap=new RenderTargetBitmap(width,height,dpi,dpi,PixelFormats.Pbgra32);bitmap.Render(visual);bitmap.Freeze();return bitmap;}
    static byte[] Read(BitmapSource image){var bytes=new byte[image.PixelWidth*image.PixelHeight*4];image.CopyPixels(bytes,image.PixelWidth*4,0);return bytes;}
    static void Save(BitmapSource image,string path){var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(image));using var stream=File.Create(path);encoder.Save(stream);}
}
