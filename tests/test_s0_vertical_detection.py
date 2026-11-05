"""Synthetic pixels for legacy compatibility and the new signed S0 column.

Drawn HUDs are controlled positive/negative inputs, not real game screenshots.
"""
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from floor_control_region import locate_floor_row, read_floor_in_region
import test_client_floor_region as legacy


def column(order, selected, *, size=(1920,1080)):
    frame=np.full((1080,1920,3),33,np.uint8)
    for i,floor in enumerate(order):
        x,y=1620,360+i*60
        if floor==selected:frame[y-32:y+32,x-30:x+30]=110
        color=(15,)*3 if floor==selected else (225,)*3
        offset=4 if floor<0 else 0
        if abs(floor)==1:
            cv2.rectangle(frame,(x-2+offset,y-10),(x+1+offset,y+9),color,-1)
        else:
            cv2.polylines(frame,[np.array([(x-6,y-10),(x+6,y-10),(x+6,y-2),
                                          (x-6,y+4),(x-6,y+9),(x+6,y+9)])],False,color,2,cv2.LINE_8)
        if floor<0:cv2.rectangle(frame,(x-13,y-1),(x-6,y),color,-1)
    return cv2.resize(frame,size,interpolation=cv2.INTER_NEAREST)


class DetectionPixels(unittest.TestCase):
    def test_legacy_near_square_tiles_keep_auto_and_region_compatibility(self):
        for pitch in (44,48,52,64):
            for mode,floors in [('difficult',(1,2)),('nightmare',(-1,1,2))]:
                for floor in floors:
                    with self.subTest(pitch=pitch,mode=mode,floor=floor),patch.object(legacy,'TILE',pitch):
                        frame=legacy.hud(floors,floor)
                        self.assertEqual(locate_floor_row(frame,mode)['selectedFloor'],floor)
                        self.assertEqual(read_floor_in_region(frame,legacy.REGION,mode)['selectedFloor'],floor)

    def test_signed_vertical_pixels_read_each_mode_and_floor(self):
        for size in [(1920,1080),(1600,900),(2560,1440)]:
            for mode,order in [('difficult',(2,1)),('nightmare',(2,1,-1))]:
                for floor in order:
                    with self.subTest(size=size,mode=mode,floor=floor):
                        frame=column(order,floor,size=size)
                        read=locate_floor_row(frame,mode)
                        self.assertEqual(read['selectedFloor'],floor,read)
                        self.assertEqual(read['controlAxis'],'vertical')
                        self.assertEqual(read['observedOrder'],list(order))
                        roi=(.79,.25,.11,.27)
                        self.assertEqual(read_floor_in_region(frame,roi,mode)['selectedFloor'],floor)

    def test_mode_conflict_negative_s1_labels_and_missing_glyph_are_rejected(self):
        for mode,frame in [('difficult',column((2,1,-1),1)),('nightmare',column((2,1),1)),
                           ('difficult',column((-1,-2),-1)),('nightmare',column((-1,-2,-3),-1))]:
            with self.subTest(mode=mode):self.assertIsNone(locate_floor_row(frame,mode)['selectedFloor'])
        frame=column((2,1,-1),1);frame[465:495,1600:1640]=33
        self.assertIsNone(locate_floor_row(frame,'nightmare')['selectedFloor'])

    def test_automatic_search_rejects_two_complete_layouts(self):
        frame=legacy.hud((-1,1,2),1)
        frame[250:550,1500:1750]=column((2,1,-1),1)[250:550,1500:1750]
        self.assertEqual(locate_floor_row(frame,'nightmare')['reason'],'layouts-ambiguous')


if __name__=='__main__':unittest.main()
