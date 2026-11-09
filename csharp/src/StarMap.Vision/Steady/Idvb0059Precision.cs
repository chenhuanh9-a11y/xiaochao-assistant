using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision.Steady;

public static partial class Idvb0059Steady
{
    static double Bilinear(Prepared field,Point2d xy)
    {
        int w=field.Width,h=field.Height;
        if(!double.IsFinite(xy.X)||!double.IsFinite(xy.Y)||xy.X<0||xy.Y<0||xy.X>w-1||xy.Y>h-1)return double.PositiveInfinity;
        int x0=(int)xy.X,y0=(int)xy.Y,x1=Math.Min(x0+1,w-1),y1=Math.Min(y0+1,h-1);
        double fx=xy.X-x0,fy=xy.Y-y0;
        return (field.Distance[y0*w+x0]*(1-fx)+field.Distance[y0*w+x1]*fx)*(1-fy)
             +(field.Distance[y1*w+x0]*(1-fx)+field.Distance[y1*w+x1]*fx)*fy;
    }

    // Only tests may replace this call's monotonic clock. Production always supplies Now and retains the 10ms deadline.
    static JsonObject PrecisionRefine(CurrentObservation observation,Prepared prepared,double seedScale,Point2d seedOffset,
        bool lockScale,Func<bool>? cancelled,Func<double> clock)
    {
        double started=clock();
        var centerLocal=new Point2d(observation.Width/2.0,observation.Height/2.0);
        var center=Add(centerLocal,P(observation.Origin));var referenceCenter=Mul(Sub(center,seedOffset),1/seedScale);
        double scale=seedScale,radius=0;var shift=new Point2d(0,0);int rounds=0,probes=0;
        JsonObject? before=null,after=null;
        bool Expired()=>clock()-started>=10||Cancelled(cancelled);
        JsonObject Finish(string reason)=>new() { ["seedScale"]=seedScale,["scale"]=scale,
            ["offset"]=Vec(Add(Sub(center,Mul(referenceCenter,scale)),shift)),["before"]=before,["after"]=after,
            ["iterations"]=rounds,["probes"]=probes,["milliseconds"]=clock()-started,["termination"]=reason,
            ["calibrated"]=reason=="converged",["radiusPixels"]=radius,["scaleLocked"]=lockScale };
        var points=observation.Points.Where(p=>observation.Valid[p.Y*observation.Width+p.X]>=128).Select(P).ToArray();
        var parts=points.Select(p=>(p.X>=centerLocal.X?1:0)+2*(p.Y>=centerLocal.Y?1:0)).ToArray();
        var counts=new int[4];foreach(int p in parts)counts[p]++;
        var radii=points.Select(p=>Distance(p,centerLocal)).ToArray();radius=radii.Length>0?radii.Max():0;
        bool span=counts[0]+counts[2]>0&&counts[1]+counts[3]>0&&counts[0]+counts[1]>0&&counts[2]+counts[3]>0;
        int active=counts.Count(c=>c>=15);
        if(points.Length<35||active<2||!span||radius<80)return Finish("insufficient-support");
        var weights=parts.Select(p=>counts[p]>=15?1.0/(active*counts[p]):0).ToArray();
        double[] Distances(double s,Point2d delta)
        {
            var result=new double[points.Length];
            for(int i=0;i<points.Length;i++)
            {
                var xy=Add(referenceCenter,Mul(Sub(Sub(points[i],centerLocal),delta),1/s));
                result[i]=Math.Min(6,Bilinear(prepared,xy)*s);
            }
            return result;
        }
        double Loss(double[] d)
        {
            double sum=0;for(int i=0;i<d.Length;i++)sum+=(d[i]<=1.5?.5*d[i]*d[i]:1.5*(d[i]-.75))*weights[i];return sum;
        }
        double LossAt(double s,Point2d delta)=>Loss(Distances(s,delta));
        double P95(IEnumerable<double> values)
        { var sorted=values.Order().ToArray();return sorted.Length>0?sorted[(int)Math.Ceiling(sorted.Length*.95)-1]:-1; }
        JsonObject Residual(double s,Point2d delta)
        {
            var d=Distances(s,delta);
            return new JsonObject { ["loss"]=LossAt(s,delta),["nearP95"]=P95(d.Where((_,i)=>radii[i]<=radius*.35)),
                ["farP95"]=P95(d.Where((_,i)=>radii[i]>=radius*.70)),
                ["partitionP95"]=Doubles(Enumerable.Range(0,4).Select(p=>P95(d.Where((_,i)=>parts[i]==p)))) };
        }
        if(Expired())return Finish("budget");
        before=Residual(scale,shift);if(before["loss"]!.GetValue<double>()>=5)return Finish("out-of-reference");
        double best=before["loss"]!.GetValue<double>(),ds=seedScale*.005,dt=1;bool converged=false;
        for(rounds=0;rounds<12;rounds++)
        {
            if(Expired())return Finish("budget");
            if(dt<=.125&&(lockScale||ds/seedScale*radius<=.25)){converged=true;break;}
            var candidates=new List<(double Scale,Point2d Shift)>();
            foreach(int si in lockScale?new[] {0}:new[] {-1,0,1})
                for(int xi=-1;xi<=1;xi++)for(int yi=-1;yi<=1;yi++)
                {
                    double s=scale+si*ds;var delta=Add(shift,new(xi*dt,yi*dt));
                    if(si==0&&xi==0&&yi==0)continue;
                    if(!lockScale&&Math.Abs(s/seedScale-1)>.030000001)continue;
                    if(Math.Abs(delta.X)>4.0000001||Math.Abs(delta.Y)>4.0000001)continue;
                    candidates.Add((s,delta));
                }
            double value=double.PositiveInfinity;(double Scale,Point2d Shift) winner=default;
            foreach(var row in candidates){double loss=LossAt(row.Scale,row.Shift);if(loss<value){value=loss;winner=row;}}
            probes+=candidates.Count;
            if(value<best-1e-9){best=value;scale=winner.Scale;shift=winner.Shift;}
            else{ds/=2;dt/=2;}
        }
        if(Expired())return Finish("budget");if(!converged)return Finish("iterations");
        if((!lockScale&&Math.Abs(scale/seedScale-1)>=.029999)||Math.Abs(shift.X)>=3.999999||Math.Abs(shift.Y)>=3.999999)return Finish("boundary");
        if(!lockScale)
        {
            foreach(int sign in new[] {-1,1})
            {
                if(Expired())return Finish("budget");
                double competitor=double.PositiveInfinity;
                for(int xi=-2;xi<=2;xi++)for(int yi=-2;yi<=2;yi++)
                    competitor=Math.Min(competitor,LossAt(scale+sign*scale/radius,Add(shift,new(xi*.5,yi*.5))));
                probes+=25;if(competitor<=best+1e-4)return Finish("scale-unobservable");
            }
        }
        after=Residual(scale,shift);return Finish(Expired()?"budget":"converged");
    }
}
