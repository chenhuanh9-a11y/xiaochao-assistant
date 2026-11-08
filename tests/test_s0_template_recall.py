"""Independent synthetic recall/false-read fixtures; no user screenshot data."""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import floor_control_region as detector
from s0_vertical_controls import observe_s0_map, validated_control_rects
from test_client_floor_region import ORDERS, column
from test_s0_map_geometry_proof import s0_bright


def narrow_column(mode, selected, *, gray=145):
    """A 65-pixel '1' on a 74x98 button, drawn without production templates."""
    order = dict(ORDERS)[mode]
    frame = s0_bright(mode)
    frame[:700,1570:1750] = 33
    x = 1680
    for index, floor in enumerate(order):
        y = 300+index*92
        if floor == selected:
            frame[y-49:y+49,x-37:x+37] = 110
        color = 15 if floor == selected else gray
        offset = 4 if floor<0 else 0
        if abs(floor)==1:
            # Connected narrow numeral: a full-height three-pixel stroke,
            # minus one corner pixel. It need not occupy 1.5% of its button.
            frame[y-11:y+11,x-1+offset:x+2+offset] = color
            frame[y-11,x-1+offset] = 110 if floor==selected else 33
        else:
            cv2.polylines(frame,[np.array([(x-6,y-11),(x+6,y-11),
                (x+6,y-2),(x-6,y+4),(x-6,y+10),(x+6,y+10)])],False,(color,)*3,2)
        if floor<0:
            frame[y-1:y+1,x-14:x-5] = color
    return frame


class TemplateRecall(unittest.TestCase):
    def assert_no_floor(self, frame, mode):
        observation, read = observe_s0_map(frame,mode)
        self.assertIsNone(read['selectedFloor'],(observation,read))
        self.assertFalse(read['controlGeometryVerified'])
        self.assertIsNone(validated_control_rects(read))

    def test_clipped_numeral_is_not_a_complete_label(self):
        from s0_floor_templates import classify_label
        for selected in (False, True):
            background, ink = (110, 15) if selected else (33, 225)
            patch = np.full((64, 64), background, np.uint8)
            patch[32:62, 28:32] = ink
            with self.subTest(selected=selected):
                self.assertEqual(classify_label(patch, selected=selected)['label'], 1)
                self.assertIsNone(classify_label(patch[:52], selected=selected))

    def test_soft_highlight_cannot_create_a_conflict_from_a_clipped_neighbor(self):
        frame = s0_bright('difficult', 2)
        frame[:700, 1570:1750] = 33
        # Independent soft-edged button pixels: dimming changes the measured
        # fill extent, but the complete visible labels still say 2 above 1.
        frame[296:425, 1571:1670] = 50
        frame[305:416, 1578:1663] = 80
        frame[307:402, 1584:1657] = 110
        cv2.putText(frame, '2', (1609, 368), cv2.FONT_HERSHEY_PLAIN,
                    2.5, (15,) * 3, 2)
        frame[451:481, 1618:1622] = 225
        observation, read = observe_s0_map(frame, 'difficult')
        self.assertTrue(observation['mapOpen'], (observation, read))
        self.assertEqual(read['selectedFloor'], 2, read)
        self.assertIsNotNone(validated_control_rects(read))

    def test_narrow_digits_use_whole_column_not_button_coverage(self):
        for mode, order in ORDERS:
            for selected in order:
                frame = narrow_column(mode,selected)
                observation, read = observe_s0_map(frame,mode)
                self.assertTrue(observation['mapOpen'],observation)
                self.assertEqual(read['selectedFloor'],selected,read)
                self.assertIsNotNone(validated_control_rects(read))
                self.assertTrue(all('score' in row for row in read['labelEvidence']))

    def test_neighbor_brightness_and_scale_retain_thin_numerals(self):
        for mode, order in ORDERS:
            for selected in order:
                for gray in (120,139,140,141,145,160):
                    for size in ((1280,800),(1920,1200),(2560,1600)):
                        with self.subTest(mode=mode,selected=selected,gray=gray,size=size):
                            frame=cv2.resize(narrow_column(mode,selected,gray=gray),size,
                                             interpolation=cv2.INTER_NEAREST)
                            read=detector.locate_vertical_floor_controls(frame,mode)
                            self.assertEqual(read['selectedFloor'],selected,read)

    def test_wrong_digit_missing_sign_and_noise_are_not_floor_evidence(self):
        for mode,_ in ORDERS:
            selected=-1 if mode=='nightmare' else 2
            for kind in ('missing','three','four','horizontal','noise','missing-sign'):
                if kind=='missing-sign' and mode!='nightmare':
                    continue
                frame=narrow_column(mode,selected)
                if kind=='missing-sign':
                    frame[482:486,1666:1675]=110
                else:
                    frame[377:407,1662:1698]=33
                    if kind in ('three','four'):
                        cv2.putText(frame,'3' if kind=='three' else '4',(1671,402),
                                    cv2.FONT_HERSHEY_PLAIN,1.7,(180,)*3,2)
                    elif kind=='horizontal':
                        frame[391:394,1668:1690]=180
                    elif kind=='noise':
                        rng=np.random.default_rng(31)
                        y=rng.integers(380,404,65);x=rng.integers(1665,1695,65)
                        frame[y,x]=180
                with self.subTest(mode=mode,kind=kind):
                    self.assert_no_floor(frame,mode)

    def test_five_non_game_ui_counterexamples_have_no_click_capability(self):
        for mode,_ in ORDERS:
            frames=[np.full((1200,1920,3),value,np.uint8)for value in (20,80,180)]
            frame=narrow_column(mode,1)
            frame[:,:1570]=33
            frame[:,1750:]=33
            frame[700:]=33
            frames.append(frame)
            random=np.random.default_rng(7).integers(0,256,(1200,1920,3),dtype=np.uint8)
            frames.append(random)
            for frame in frames:
                self.assert_no_floor(frame,mode)

    def test_complete_map_survives_global_gain_in_both_modes(self):
        for mode, order in ORDERS:
            for selected in order:
                for gain in (.6,.75,1.,1.25):
                    frame=cv2.convertScaleAbs(s0_bright(mode,selected),alpha=gain)
                    observation,read=observe_s0_map(frame,mode)
                    self.assertTrue(observation['mapOpen'],(mode,selected,gain,observation))
                    self.assertEqual(read['selectedFloor'],selected)

    def test_brightening_cannot_hide_a_conflicting_dim_column(self):
        for mode,order in ORDERS:
            frame=s0_bright(mode,1)
            dim=cv2.convertScaleAbs(column(order,2),alpha=.4)
            frame[:700,1450:1550]=dim[:700,1570:1670]
            self.assert_no_floor(frame,mode)

    def test_nonmatching_second_proposal_does_not_veto_a_valid_column(self):
        for mode,order in ORDERS:
            frame=s0_bright(mode,1)
            fake=column(order,2)
            frame[:700,1450:1550]=fake[:700,1570:1670]
            frame[343:377,1483:1518]=110
            cv2.putText(frame,'3',(1491,370),cv2.FONT_HERSHEY_PLAIN,1.7,(15,)*3,2)
            observation,read=observe_s0_map(frame,mode)
            self.assertTrue(observation['mapOpen'],observation)
            self.assertEqual(read['selectedFloor'],1,read)

    def test_one_weak_template_may_use_other_rows_but_not_a_wrong_label(self):
        order=(2,1,-1)
        points=[[.5,.25],[.5,.4],[.5,.55]]
        gray=np.full((1000,1000),33,np.uint8)
        def evaluate(scores, wrong=False):
            matches=[dict(label=floor if not(wrong and floor==1)else 2,
                          scores={floor:score},digitAspect=.2)
                     for floor,score in zip(order,scores)]
            with patch('s0_floor_templates.classify_label',side_effect=matches):
                return detector._vertical_label_evidence(gray,points,[.46,.5,.08,.10],
                                                        order,-1,(0,0),(1000,1000))
        self.assertIsNotNone(evaluate((.92,.48,.91)))
        self.assertIsNone(evaluate((.55,.48,.91)))
        self.assertIsNone(evaluate((.92,.9,.91),wrong=True))

    def test_programmatic_bank_and_competing_digits(self):
        from s0_floor_templates import _bank,make_template,classify_label,LABELS
        bank,_=_bank()
        self.assertFalse(bank.flags.writeable)
        self.assertTrue(np.isfinite(bank).all())
        for label in LABELS:
            sample=make_template(label,.24 if abs(label)==1 else .55,2,0.)
            match=classify_label(sample.astype(np.uint8),selected=False)
            self.assertEqual(match['label'],label,match)

    def test_negative_competitors_cannot_be_supported_into_minus_one(self):
        y=round(480*1200/1080)
        for digit in (3,4,7,9):
            for selected in (2,1,-1):
                frame=s0_bright('nightmare',selected)
                color=(15,)*3 if selected==-1 else (225,)*3
                frame[y-17:y+18,1602:1641]=110 if selected==-1 else 33
                cv2.putText(frame,str(digit),(1620,y+11),cv2.FONT_HERSHEY_PLAIN,1.5,color,2)
                cv2.line(frame,(1607,y),(1614,y),color,2)
                with self.subTest(digit=digit,selected=selected):
                    self.assert_no_floor(frame,'nightmare')

    def test_dot_or_plus_prefix_is_not_a_minus(self):
        from s0_floor_templates import make_template,classify_label
        for kind in ('dot','plus'):
            image=make_template(1,.24,2,0.)
            if kind=='dot':
                cv2.circle(image,(37,32),4,255,-1)
            else:
                cv2.line(image,(33,32),(41,32),255,3)
                cv2.line(image,(37,28),(37,36),255,3)
            self.assertIsNone(classify_label(image.astype(np.uint8),selected=False))
            for selected in (2,1,-1):
                frame=s0_bright('nightmare',selected)
                y=round(480*1200/1080)
                color=(15,)*3 if selected==-1 else (225,)*3
                frame[y-6:y+7,1604:1618]=110 if selected==-1 else 33
                if kind=='dot':
                    cv2.circle(frame,(1610,y),2,color,-1)
                else:
                    cv2.line(frame,(1606,y),(1614,y),color,2)
                    cv2.line(frame,(1610,y-4),(1610,y+4),color,2)
                self.assert_no_floor(frame,'nightmare')

    def test_short_s0_ui_override_does_not_change_s1_default(self):
        from s1_map_ui_geometry import observe_s1_map_ui_geometry
        frame=np.zeros((576,1024,3),np.uint8)
        self.assertEqual(observe_s1_map_ui_geometry(frame)['reason'],'s1-map-ui-size-unsupported')
        self.assertNotEqual(observe_s1_map_ui_geometry(frame,minimum_height=540)['reason'],
                            's1-map-ui-size-unsupported')


if __name__=='__main__':
    unittest.main()
