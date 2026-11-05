"""Layout geometry contracts using synthetic metadata, never game input."""
import copy
import unittest
from s0_vertical_controls import bind_control_geometry, validated_control_rects
from floor_observation_policy import game_floor_control_hit


def synthetic_read(mode="nightmare", selected=1):
    order = [2,1] if mode == "difficult" else [2,1,-1]
    points = [[.85,.30+i*.1] for i in range(len(order))]
    return dict(selectedFloor=selected,controlAxis="vertical",observedOrder=order,
        centerPoints=points,tileBounds=[.83,points[order.index(selected)][1]-.045,.04,.09],
        labelEvidence=[dict(floor=f,negative=f<0,digitAspect=.2 if abs(f)==1 else .5) for f in order])


class VerticalGeometryContracts(unittest.TestCase):
    def valid(self, mode="nightmare", selected=1):
        return bind_control_geometry(synthetic_read(mode,selected),mode,(1280,720),map_open=True)

    def test_both_orders_and_selected_floors_map_to_physical_interiors(self):
        for mode,order in [("difficult",[2,1]),("nightmare",[2,1,-1])]:
            for selected in order:
                row=self.valid(mode,selected)
                self.assertIsNotNone(validated_control_rects(row))
                for floor,(x,y) in zip(order,row["centerPoints"]):
                    self.assertEqual(game_floor_control_hit((100+x*1280,200+y*720),(100,200,1380,920),row),floor)
                self.assertIsNone(game_floor_control_hit((100+.5*1280,230),(100,200,1380,920),row))

    def test_malformed_geometry_never_becomes_a_hit(self):
        mutations=[
            lambda r:r.update(season="s1"),lambda r:r.update(mode="difficult"),
            lambda r:r.update(mode=[]),lambda r:r.update(controlAxis="horizontal"),
            lambda r:r.update(mapOpen=False),lambda r:r.update(controlGeometryVerified=False),
            lambda r:r.update(observedOrder=[-1,1,2]),lambda r:r.update(observedOrder=[2,True,-1]),
            lambda r:r.update(selectedFloor=True),lambda r:r.update(selectedFloor="1"),
            lambda r:r.update(controlFrameSize=[1280,float("nan")]),
            lambda r:r["controlRects"].pop(),
            lambda r:r["controlRects"][0].update(floor=True),
            lambda r:r["controlRects"][0].update(rect=[0,0,float("nan"),.3]),
            lambda r:r["controlRects"][0].update(rect=[0,0,float("inf"),.3]),
            lambda r:r["controlRects"][0].update(rect=[-.1,0,.5,.3]),
            lambda r:r["controlRects"][1].update(rect=r["controlRects"][0]["rect"]),
            lambda r:r["controlRects"][1].update(rect=[.1,.4,.2,.5]),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                row=self.valid();mutation(row)
                self.assertIsNone(validated_control_rects(row))
                self.assertIsNone(game_floor_control_hit((1188,488),(100,200,1380,920),row))
        for bounds,point in [((100,200,1380,921.1),(1188,488)),
                             ((100,200,1380,920),(float("nan"),488)),
                             ((100,200,1380,920),(True,488))]:
            self.assertIsNone(game_floor_control_hit(point,bounds,self.valid()))

    def test_binding_requires_current_map_and_complete_signed_labels(self):
        for opened in [False,None,1]:
            row=bind_control_geometry(synthetic_read(),"nightmare",(1280,720),map_open=opened)
            self.assertFalse(row["controlGeometryVerified"]);self.assertIsNone(row["selectedFloor"])
        for mutate in [lambda r:r["labelEvidence"][-1].update(negative=False),
                       lambda r:r["centerPoints"].reverse(),
                       lambda r:r["labelEvidence"].pop(),
                       lambda r:r.update(season="s1"),
                       lambda r:r.update(mode="difficult"),
                       lambda r:r.update(selectedFloor=2),
                       lambda r:r.update(tileBounds=[.99,.2,.1,.1])]:
            r=synthetic_read();mutate(r)
            self.assertIsNone(bind_control_geometry(r,"nightmare",(1280,720),map_open=True)["selectedFloor"])


if __name__=="__main__":unittest.main()
