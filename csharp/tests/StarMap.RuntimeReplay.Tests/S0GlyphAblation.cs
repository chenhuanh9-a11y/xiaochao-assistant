using System.IO;
using System.Runtime.InteropServices;
using OpenCvSharp;

// Exact derived input used by the independent original Python Runtime oracle.
// Recorded source/geometry is checked before this explicit pixel replacement;
// default untouched-frame index tests never install this callback.
internal static class S0GlyphAblation
{
    internal static Action<byte[],int,int,int> Create(string replay,RecordedCapture capture)
    {
        string folder=Path.Combine(Path.GetDirectoryName(replay)!,"runtime-identity-entry-original-01","no-glyph-01","glyph-ablated-inputs");
        return (pixels,width,height,stride)=>
        {
            string id=capture.SelectedId??throw new InvalidDataException("Missing selected capture identifier.");
            using var bgr=Cv2.ImRead(Path.Combine(folder,id+".png"),ImreadModes.Color);
            if(bgr.Empty()||bgr.Cols!=width||bgr.Rows!=height)throw new InvalidDataException("Independent no-glyph input dimensions differ.");
            using var bgra=new Mat();Cv2.CvtColor(bgr,bgra,ColorConversionCodes.BGR2BGRA);
            for(int row=0;row<height;row++)Marshal.Copy(bgra.Ptr(row),pixels,row*stride,width*4);
        };
    }
}
