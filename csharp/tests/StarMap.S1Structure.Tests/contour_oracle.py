"""Independent original-native 4.12 polygon expectations, including retracing synthetic contours."""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    if cv2.__version__!='4.12.0':raise RuntimeError('Use original native OpenCV4.12')
    cases=[]
    def case(name,points,epsilon=2):
        contour=np.asarray(points,np.int32).reshape(-1,1,2)
        expected=cv2.approxPolyDP(contour,epsilon,True).reshape(-1,2).tolist()
        cases.append(dict(name=name,contour=contour.reshape(-1,2).tolist(),epsilon=float(epsilon),expected=expected))
    case('rectangle',[[0,0],[40,0],[40,30],[0,30]])
    case('retracing-branch',[[0,0],[20,0],[20,20],[30,20],[20,20],[20,40],[0,40]])
    case('returning-spike',[[0,0],[50,0],[50,50],[30,50],[29,32],[28,49],[0,50]])
    case('single-point',[[3,5]])
    case('repeated-point',[[3,5],[3,5],[3,5]])
    rng=np.random.default_rng(307412)
    for index in range(96):
        points=np.cumsum(rng.integers(-8,9,size=(64,2)),axis=0)
        case(f'closed-retracing-walk-{index:03}',points,epsilon=(1,2,3,5)[index%4])
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(dict(opencv=cv2.__version__,cases=cases),indent=2),encoding='utf-8')
    print(json.dumps(dict(cases=len(cases),output=str(a.output))))


if __name__=='__main__':main()
