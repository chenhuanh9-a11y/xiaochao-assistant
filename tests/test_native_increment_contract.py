"""Writing must not begin when a native increment has ambiguous input bindings."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

TOOLS = Path(__file__).resolve().parents[1] / 'tools' / 'maps'
if not TOOLS.exists():  # Standalone handoff beside the not-yet-integrated tools.
    TOOLS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('native_increment', TOOLS/'build_native_increment.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class IncrementContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base = self.root/'base'; self.source = self.root/'source'
        self.source.mkdir(); (self.base/'references').mkdir(parents=True)
        self.put(self.base/'references/nightmare-map-pack.json', {'maps':[]})
        raw = {'room_data':[], 'door':{}}
        self.put(self.root/'literals.json', {'levelLayouts':{'data':{'100':raw}}})
        catalog = self.root/'catalog.json'
        self.put(catalog, {'gameConfigVersion':'v1','maps':{'100':{'roomDataRaw':[], 'normalizedDoors':[]}}})
        entries=[]
        for world, floor in enumerate(['B1','1','2'],1):
            png=self.root/f'{floor}.png'; metadata=self.root/f'{floor}.json'
            image=mod.np.zeros((60,60,4),mod.np.uint8); image[10:50,10:50,3]=255
            mod.cv2.imencode('.png',image)[1].tofile(png)
            self.put(metadata, {'levelId':'100','floor':world,'targetPixelsPerVoxel':60,'outTarget':str(png),
                'bounds':{'minX':0,'maxX':128,'minY':0,'maxY':128},
                'sourceCatalog':{'sourceFile':str(catalog),'sourceFileSha256':mod.sha(catalog)}})
            entries.append({'runtimeMapId':'nightmare-layout-100','sourceLayout':'100','floor':floor,
                'sourceWorldFloor':world,'nativeSourceMetadata':str(metadata),'nativeSourcePNG':str(png),
                'nativeCanvasWH':[60,60],'nativeSourcePNGSha256':mod.sha(png),'nativeSourceMetadataSha256':mod.sha(metadata)})
        self.plan={'sourceVersion':'v1','entries':entries}
        self.put(self.root/'plan.json',self.plan)
        self.put(self.root/'entries.json',{'entries':[{'mapId':'nightmare-layout-100','layout':'100','floor':'1','worldFloor':2}]})
        self.args=SimpleNamespace(base=self.base,source=self.source,plan=self.root/'plan.json',
            output=self.root/'output',entry_inventory=self.root/'entries.json',literals=self.root/'literals.json',entry_builder=TOOLS/'native_increment_entry_rows.py')

    def tearDown(self): self.tmp.cleanup()
    def put(self,path,value): path.write_text(json.dumps(value),encoding='utf8')
    def rejects(self):
        self.put(self.args.plan,self.plan)
        with self.assertRaises(ValueError): mod.validate_inputs(self.args)
        self.assertFalse(self.args.output.exists())
    def test_valid_plan_has_no_write(self):
        mod.validate_inputs(self.args); self.assertFalse(self.args.output.exists())
    def test_wrong_world_floor(self):
        self.plan['entries'][0]['sourceWorldFloor']=999; self.rejects()
    def test_wrong_source_layout(self):
        self.plan['entries'][0]['sourceLayout']='101'; self.rejects()
    def test_duplicate_floor(self):
        self.plan['entries'].append(self.plan['entries'][0].copy()); self.rejects()
    def test_path_map_id(self):
        self.plan['entries'][0]['runtimeMapId']='../escape'; self.rejects()
    def test_output_nested_in_input(self):
        self.args.output=self.base/'recursive'; self.rejects()
    def test_source_hash_mismatch(self):
        self.plan['entries'][0]['nativeSourcePNGSha256']='0'*64; self.rejects()
    def test_catalog_hash_mismatch(self):
        (self.root/'catalog.json').write_text('{}',encoding='utf8'); self.rejects()
    def test_unknown_or_empty_render_selection(self):
        node=shutil.which('node')
        if not node: self.skipTest('Node is required for the renderer CLI')
        run=self.root/'run'; (run/'catalog-candidate').mkdir(parents=True)
        self.put(run/'catalog-candidate/latest-catalog-candidate.json',{'maps':{'100':{}}})
        for selection in ['999999',',']:
            result=subprocess.run([node,str(TOOLS/'render_native_increment.cjs'),'--run',str(run),
                '--vendor',str(self.root/'vendor'),'--output',str(self.args.output),'--levels',selection],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('All selected levels',result.stderr)
            self.assertFalse(self.args.output.exists())

if __name__=='__main__': unittest.main()
