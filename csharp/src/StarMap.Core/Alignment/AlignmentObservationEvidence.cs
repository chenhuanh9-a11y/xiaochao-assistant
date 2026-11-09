using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace StarMap.Core.Alignment;

public static class AlignmentObservationEvidence
{
    public static IReadOnlyDictionary<string,object?> CanonicalResidualEvidence(IReadOnlyDictionary<string,object?> residual,string basis,
        object? coordinatePackId,bool profileBridgeVerified=true)
    {
        var result=AlignmentData.CopyMap(residual);
        bool pack=AlignmentData.Truth(coordinatePackId),verified=AlignmentData.True(result,"verified");
        result["validationBasis"]="shared-canonical-geometry-current-frame";result["validationSensorBasis"]=basis;
        result["coordinatePackId"]=AlignmentData.Copy(coordinatePackId);result["poseVerifiedAgainstCanonicalGeometry"]=verified&&pack;
        result["profileRenderDerivedFromCanonical"]=pack&&profileBridgeVerified;
        result["poseVerifiedAgainstSharedStructure"]=AlignmentData.Truth(AlignmentData.Get(result,"verified"))&&basis=="game-rendered-shared-structure";
        result["poseVerifiedAgainstRenderedProfile"]=false;result["routeProfileIndependent"]=true;
        return AlignmentData.ReadOnly(result);
    }
    public static bool RenderedProfileResidualIsVerified(IReadOnlyDictionary<string,object?>? residual)
    {
        if(residual is null)return false;
        object? Get(string key)=>AlignmentData.Get(residual,key);
        bool True(string key)=>AlignmentData.True(residual,key);
        string basis=AlignmentData.String(Get("validationBasis"));
        bool legacy=True("verified")&&True("poseVerifiedAgainstRenderedProfile")&&basis=="actual-rendered-profile-structural-mask";
        bool canonical=True("verified")&&True("poseVerifiedAgainstCanonicalGeometry")&&True("profileRenderDerivedFromCanonical")
            &&basis=="shared-canonical-geometry-current-frame"&&AlignmentData.Truth(Get("coordinatePackId"));
        bool source=false;
        if(True("verified")&&True("poseVerifiedAgainstSourceStructure")&&basis=="source-structure-heldout-current-frame")
        {
            try
            {
                double inliers=PythonInt(AlignmentData.Truth(Get("heldOutInliers"))?Get("heldOutInliers"):0);
                double cells=PythonInt(AlignmentData.Truth(Get("heldOutCellCount"))?Get("heldOutCellCount"):0);
                double median=residual.ContainsKey("proposedTransformMedianError")?PythonFloat(Get("proposedTransformMedianError")):double.PositiveInfinity;
                double p90=residual.ContainsKey("proposedTransformP90Error")?PythonFloat(Get("proposedTransformP90Error")):double.PositiveInfinity;
                source=True("sourceCoordinatesVerified")&&True("sourceFloorIsProducerLabelOnly")&&AlignmentData.String(Get("sourceCoordinateSpace"))=="producer-source-png-pixels"
                    &&True("sourcePngIsDisplayImage")&&AlignmentData.Truth(Get("sourceGroup"))&&(AlignmentData.String(Get("gameFloor")) is "1" or "2" or "B1")
                    &&inliers>=5&&cells>=4&&double.IsFinite(median)&&double.IsFinite(p90)&&median<=4&&p90<=8;
            }
            catch(Exception e)when(e is FormatException or ArgumentException or InvalidCastException){source=false;}
        }
        return legacy||canonical||source;
    }
    private static double PythonInt(object? v)
    {
        if(v is string text)
        {
            string s=PythonNumberText(text);
            if(!Regex.IsMatch(s,"^[+-]?[0-9]+$"))throw new FormatException();
            return double.Parse(s,NumberStyles.AllowLeadingSign,CultureInfo.InvariantCulture);
        }
        double n=PythonFloat(v);
        // Python int(NaN) raises ValueError (caught by the source predicate),
        // whereas int(infinity) raises OverflowError and remains exceptional.
        if(double.IsNaN(n))throw new FormatException("cannot convert NaN to integer");
        if(!double.IsFinite(n))throw new OverflowException("nonfinite integer conversion");
        return Math.Truncate(n);
    }
    private static double PythonFloat(object? v)
    {
        if(v is null)throw new ArgumentException("float of null");
        if(v is bool b)return b?1:0;
        if(v is not string text)return Convert.ToDouble(v,CultureInfo.InvariantCulture);
        string s=PythonNumberText(text);
        bool negative=s.StartsWith('-');
        string unsigned=s.StartsWith('+')||negative?s[1..]:s;
        if(unsigned.Equals("inf",StringComparison.OrdinalIgnoreCase)||unsigned.Equals("infinity",StringComparison.OrdinalIgnoreCase))
            return negative?double.NegativeInfinity:double.PositiveInfinity;
        if(unsigned.Equals("nan",StringComparison.OrdinalIgnoreCase))return double.NaN;
        // Convert.ToDouble(string) implicitly accepts thousands separators;
        // Python float does not. Float excludes AllowThousands explicitly.
        if(!Regex.IsMatch(s,@"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"))throw new FormatException();
        return double.Parse(s,NumberStyles.Float,CultureInfo.InvariantCulture);
    }
    private static string PythonNumberText(string value)
    {
        var normalized=new StringBuilder(value.Length);
        foreach(var rune in value.Trim().EnumerateRunes())
        {
            int digit=Rune.GetUnicodeCategory(rune)==UnicodeCategory.DecimalDigitNumber
                ?(int)Rune.GetNumericValue(rune):-1;
            if(digit>=0)normalized.Append((char)('0'+digit));else normalized.Append(rune.ToString());
        }
        string text=normalized.ToString();
        for(int i=0;i<text.Length;i++)if(text[i]=='_'&&(i==0||i+1==text.Length
            ||text[i-1] is <'0' or >'9'||text[i+1] is <'0' or >'9'))throw new FormatException("invalid numeric underscore");
        return text.Replace("_","",StringComparison.Ordinal);
    }
}
