import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location("entry_preprocess",Path(__file__).resolve().parents[1]/"tools/maps/build_entry_candidates.py")
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class PreprocessingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/"inputs"
        self.root.mkdir()
        self.item=dict(mapId="a",floor="B1",unit=10,canvas={"layout":"L"},turns=[
            dict(point=[0,0],rays=[[1,0],[0,1]],next=1),
            dict(point=[10,0],rays=[[-1,0],[0,1]],next=-1)],
            gates=[dict(center=[0,0],semanticRole="spawn-marker")])
        self.binding=dict(mapId="a",floor="B1",resourcePath="base.bin",structureVersionKey="L@v/B1",sourceVersion="v")
        (self.root/"base.bin").write_bytes(b"structure")
        self.write("entries.json",dict(mode="any-mode",profileId="p",entries=[self.item]))
        self.write("index.json",dict(entries=[self.binding]))
        self.config=dict(resourceRoot=str(self.root),sources=[dict(entries="entries.json",index="index.json")])
        self.write("config.json",self.config)
    def write(self,name,data):
        (self.root/name).write_text(json.dumps(data),encoding="utf8")
    def run_build(self):
        result=m.build(self.root/"config.json",self.root.parent/(self.root.name+"-out"))
        manifest=m.read(result["manifest"])
        return result,[m.read(self.root.parent/(self.root.name+"-out")/e["file"]) for e in manifest["entries"]]
    def test_incremental_and_resource_change(self):
        a,rows=self.run_build()
        b,_=self.run_build()
        self.assertEqual((a["generated"],b["reused"]),(1,1))
        (self.root/"base.bin").write_bytes(b"changed")
        c,new=self.run_build()
        self.assertEqual(c["generated"],1)
        self.assertNotEqual(rows[0]["fingerprint"],new[0]["fingerprint"])
    def test_marker_is_not_boundary_and_b1_preserved(self):
        _,rows=self.run_build();r=rows[0]
        self.assertEqual(r["identity"][-1],"B1")
        self.assertFalse(r["grantsIdentity"])
        self.assertIn("doorway-boundary-unavailable",r["semanticGaps"])
        self.assertEqual(r["neighborhoods"][0]["anchor"]["kind"],"source-marker")
    def test_declared_subset_does_not_claim_raw_closure(self):
        self.config["sources"][0]["dependencies"]=["base.bin"]
        self.write("config.json",self.config)
        _,rows=self.run_build()
        self.assertIn("raw-source-dependency-closure-unverified",rows[0]["semanticGaps"])
    def test_dependency_event_scope(self):
        import sys
        sys.path.insert(0,str(Path(m.__file__).parent))
        from plan_entry_candidate_changes import plan
        rows=[dict(identity=["mode","p",k,"B1"],sha256=k) for k in ("a","b")]
        manifest=dict(schema="entry-candidates/1",entries=rows)
        events=plan(manifest,manifest,[rows[0]["identity"]])["events"]
        self.assertEqual([e["action"] for e in events],["fallback","reuse"])
        self.assertTrue(all(not e["deleteOldResource"] for e in events))
    def test_missing_binding_retained(self):
        self.write("index.json",dict(entries=[]))
        _,rows=self.run_build()
        self.assertEqual(rows[0]["neighborhoods"],[])
        self.assertIn("missing-versioned-structure-binding",rows[0]["semanticGaps"])
    def test_identical_candidates_not_collapsed(self):
        second=dict(self.item,mapId="b")
        self.write("entries.json",dict(mode="any-mode",profileId="p",entries=[self.item,second]))
        self.write("index.json",dict(entries=[self.binding,dict(self.binding,mapId="b")]))
        _,rows=self.run_build()
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["neighborhoods"][0]["signature"],rows[1]["neighborhoods"][0]["signature"])
    def test_stale_anchor_rejected(self):
        self.write("doors.json",dict(mode="any-mode",profileId="p",entries=[dict(mapId="a",floor="B1",sourceVersion="wrong",structureVersionKey="L@v/B1")]))
        self.config["sources"][0]["anchors"]="doors.json"
        self.write("config.json",self.config)
        with self.assertRaisesRegex(ValueError,"stale doorway"):
            self.run_build()
    def test_corrupt_cache_rejected(self):
        result,_=self.run_build()
        manifest=m.read(result["manifest"])
        (self.root.parent/(self.root.name+"-out")/manifest["entries"][0]["file"]).write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError,"corrupt cached"):
            self.run_build()
    def test_path_escape_rejected(self):
        self.binding["resourcePath"]="../outside"
        self.write("index.json",dict(entries=[self.binding]))
        with self.assertRaisesRegex(ValueError,"escapes"):
            self.run_build()
    def test_cut_neighborhood_never_bridges_nodes(self):
        item=dict(self.item,turns=[dict(point=[0,0],rays=[],next=1),dict(point=[100,0],rays=[],next=2),dict(point=[0,1],rays=[],next=-1)])
        rows=m.neighborhoods(item,[dict(center=[0,0])],[2])
        self.assertEqual([n["next"] for n in rows[0]["nodes"]],[-1,-1])

if __name__=="__main__":
    unittest.main()
