using OpenCvSharp;

namespace StarMap.Vision;

/// <summary>
/// Closed integer-contour Douglas-Peucker semantics used by the original OpenCV 4.12 solver.
/// OpenCV 4.13 changed the recursive distance from an infinite line to a segment;
/// retaining that native call changes measured corners even for identical input masks.
/// </summary>
public static class StructureContourApproximation
{
    // Algorithm source: https://github.com/opencv/opencv/blob/4.12.0/modules/imgproc/src/approx.cpp
    // OpenCV's Apache-2.0 distribution license is retained in ThirdParty/OpenCV-LICENSE.txt.
    // The source file's additional Intel license is retained verbatim below.
    //
    //                        Intel License Agreement
    //                For Open Source Computer Vision Library
    //
    // Copyright (C) 2000, Intel Corporation, all rights reserved.
    // Third party copyrights are property of their respective owners.
    // Redistribution and use in source and binary forms, with or without modification,
    // are permitted provided that the following conditions are met:
    //
    //   * Redistribution's of source code must retain the above copyright notice,
    //     this list of conditions and the following disclaimer.
    //
    //   * Redistribution's in binary form must reproduce the above copyright notice,
    //     this list of conditions and the following disclaimer in the documentation
    //     and/or other materials provided with the distribution.
    //
    //   * The name of Intel Corporation may not be used to endorse or promote products
    //     derived from this software without specific prior written permission.
    //
    // This software is provided by the copyright holders and contributors "as is" and
    // any express or implied warranties, including, but not limited to, the implied
    // warranties of merchantability and fitness for a particular purpose are disclaimed.
    // In no event shall the Intel Corporation or contributors be liable for any direct,
    // indirect, incidental, special, exemplary, or consequential damages
    // (including, but not limited to, procurement of substitute goods or services;
    // loss of use, data, or profits; or business interruption) however caused
    // and on any theory of liability, whether in contract, strict liability,
    // or tort (including negligence or otherwise) arising in any way out of
    // the use of this software, even if advised of the possibility of such damage.

    public static Point[] ApproximateClosed(IReadOnlyList<Point> contour, double epsilon)
    {
        if (epsilon < 0 || !(epsilon < 1e30)) throw new ArgumentOutOfRangeException(nameof(epsilon));
        int count = contour.Count;
        if (count == 0) return [];
        double squaredEpsilon = epsilon * epsilon;
        int position = 0, rightStart = 0, rightEnd, newCount = 0;
        Point start = new(-1000000,-1000000), point = default;
        bool within = false;
        var stack = new Stack<(int Start,int End)>();
        var output = new Point[count];
        Point ReadSource(ref int index) { var result = contour[index]; if (++index >= count) index = 0; return result; }
        for (int iteration = 0; iteration < 3; iteration++)
        {
            double maximum = 0;
            position = (position+rightStart)%count; start = ReadSource(ref position);
            for (int j = 1; j < count; j++)
            {
                point = ReadSource(ref position);
                double dx = point.X-start.X, dy = point.Y-start.Y, distance = dx*dx+dy*dy;
                if (distance > maximum) { maximum = distance; rightStart = j; }
            }
            within = maximum <= squaredEpsilon;
        }
        if (!within)
        {
            int sliceStart = position % count;
            rightEnd = sliceStart; rightStart = (rightStart+sliceStart)%count;
            stack.Push((rightStart,rightEnd)); stack.Push((sliceStart,rightStart));
        }
        else output[newCount++] = start;
        while (stack.Count > 0)
        {
            var slice = stack.Pop(); var end = contour[slice.End]; position = slice.Start; start = ReadSource(ref position);
            if (position != slice.End)
            {
                double dx = end.X-start.X, dy = end.Y-start.Y, maximum = 0;
                if (dx == 0 && dy == 0) throw new ArgumentException("Contour slice endpoints coincide.",nameof(contour));
                while (position != slice.End)
                {
                    point = ReadSource(ref position);
                    double distance = Math.Abs((point.Y-start.Y)*dx-(point.X-start.X)*dy);
                    if (distance > maximum) { maximum = distance; rightStart = (position+count-1)%count; }
                }
                within = maximum*maximum <= squaredEpsilon*(dx*dx+dy*dy);
            }
            else { within = true; start = contour[slice.Start]; }
            if (within) output[newCount++] = start;
            else { stack.Push((rightStart,slice.End)); stack.Push((slice.Start,rightStart)); }
        }
        count = newCount; position = count-1;
        Point ReadOutput(ref int index) { var result = output[index]; if (++index >= count) index = 0; return result; }
        start = ReadOutput(ref position); int writePosition = position; point = ReadOutput(ref position);
        for (int i = 0; i < count && newCount > 2; i++)
        {
            var end = ReadOutput(ref position);
            double dx = end.X-start.X, dy = end.Y-start.Y;
            double distance = Math.Abs((point.X-start.X)*dy-(point.Y-start.Y)*dx);
            double inner = (double)(point.X-start.X)*(end.X-point.X)+(double)(point.Y-start.Y)*(end.Y-point.Y);
            if (distance*distance <= .5*squaredEpsilon*(dx*dx+dy*dy) && dx != 0 && dy != 0 && inner >= 0)
            {
                newCount--; output[writePosition] = start = end; if (++writePosition >= count) writePosition = 0;
                point = ReadOutput(ref position); i++; continue;
            }
            output[writePosition] = start = point; if (++writePosition >= count) writePosition = 0; point = end;
        }
        return output.Take(newCount).ToArray();
    }
}
