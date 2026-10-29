"""Actual UI consumer methods with inert widgets; no Tk window or user input."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


class Widget:
    def __init__(self, *args, **kwargs):
        self.values = kwargs
        self.packed = False
    def configure(self, **kwargs): self.values.update(kwargs)
    def pack(self, **kwargs): self.packed = True
    def pack_forget(self): self.packed = False
    def winfo_manager(self): return 'pack' if self.packed else ''
    def destroy(self): self.values['destroyed'] = True


def browser_methods():
    path = Path(__file__).resolve().parents[1] / 'app/map_resource_browser.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = {'sync_managed_packages', '_toggle_managed_packages',
             '_inspect_managed_removal', '_confirm_managed_removal'}
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MapResourceBrowser')
    cls.bases = []; cls.decorator_list = []
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    module = ast.Module(body=[cls], type_ignores=[])
    env = dict(tk=SimpleNamespace(Frame=Widget), BG='bg',
               _button=lambda parent, text, command: Widget(parent, text=text, command=command),
               _label=lambda *a, **kw: Widget(*a, **kw))
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), env)
    return env['MapResourceBrowser']


class ManagementUITests(unittest.TestCase):
    def setUp(self):
        self.state = dict(stage='ready', busy=False, packages=[])
        self.calls = []
        def snapshot(**kwargs):
            self.calls.append(('snapshot', kwargs))
            return self.state
        self.browser = browser_methods()()
        b = self.browser
        b.assistant = SimpleNamespace(managed_author_packages_snapshot=snapshot,
            inspect_author_package_removal=lambda package: self.calls.append(('inspect', package)),
            confirm_author_package_removal=lambda package, **kw: self.calls.append(('confirm', package, kw)))
        b._closed = False; b._managed_open = True; b._managed_signature = None
        b._managed_rows = {}; b._managed_ready = None
        for name in ['managed_surface','managed_message','managed_list','managed_confirm','filter_host','content']:
            setattr(b, name, Widget())

    def test_local_status_and_removal_stay_available_without_online_catalog(self):
        self.state['packages'] = [dict(packageId='nightmare-suju',title='滋酥居',status='installed',canRemove=True),
            dict(packageId='nightmare-wang-qingxin',title='王清心',status='unmanaged',canRemove=False)]
        self.browser.sync_managed_packages()
        rows=self.browser._managed_rows
        self.assertIn('已安装', rows['nightmare-suju']['label'].values['text'])
        self.assertEqual(rows['nightmare-suju']['button'].values['state'],'normal')
        self.assertIn('不能安全删除', rows['nightmare-wang-qingxin']['label'].values['text'])
        self.assertEqual(rows['nightmare-wang-qingxin']['button'].values['state'],'disabled')

    def test_delete_requires_separate_confirmation_bound_to_receipt(self):
        self.browser._inspect_managed_removal('nightmare-suju')
        self.assertIn(('inspect','nightmare-suju'),self.calls)
        self.assertFalse(any(c[0]=='confirm' for c in self.calls))
        self.browser._confirm_managed_removal()
        self.assertFalse(any(c[0]=='confirm' for c in self.calls))
        self.state['ready']=dict(packageId='nightmare-suju',title='滋酥居',fileCount=72,
                                 receiptSha256='a'*64,planSha256='b'*64)
        self.browser.sync_managed_packages()
        self.assertIn('备份',self.browser.managed_message.values['text'])
        self.browser._confirm_managed_removal()
        self.assertIn(('confirm','nightmare-suju',{'expected_receipt_sha256':'a'*64,
                                                  'expected_plan_sha256':'b'*64}),self.calls)

    def test_busy_rows_disabled_and_completed_state_clears_confirmation(self):
        self.state.update(busy=True,packages=[dict(packageId='nightmare-suju',status='installed',canRemove=True)],
                          ready=dict(packageId='nightmare-suju',receiptSha256='a'*64,planSha256='b'*64))
        self.browser.sync_managed_packages()
        self.assertEqual(self.browser.managed_confirm.values['state'],'disabled')
        self.assertEqual(self.browser._managed_rows['nightmare-suju']['button'].values['state'],'disabled')
        self.state.update(busy=False,ready=None,packages=[dict(packageId='nightmare-suju',status='not-installed',canRemove=False)])
        self.browser.sync_managed_packages()
        self.assertFalse(self.browser.managed_confirm.packed)
        self.assertIn('未安装',self.browser._managed_rows['nightmare-suju']['label'].values['text'])

    def test_modified_managed_resource_has_explicit_backup_recovery(self):
        self.state.update(packages=[dict(packageId='nightmare-suju', status='incomplete', canRemove=True)],
            ready=dict(packageId='nightmare-suju', title='滋酥居', fileCount=2,
                       modifiedFiles=['changed.png'], receiptSha256='a'*64,planSha256='b'*64))
        self.browser.sync_managed_packages()
        row = self.browser._managed_rows['nightmare-suju']
        self.assertIn('需修复', row['label'].values['text'])
        self.assertIn('备份', row['button'].values['text'])
        self.assertEqual(row['button'].values['state'], 'normal')
        self.assertIn('1 个文件有本地改动', self.browser.managed_message.values['text'])
        self.assertIn('原样备份', self.browser.managed_message.values['text'])
        self.assertIn('重新导入', self.browser.managed_message.values['text'])

    def test_open_refreshes_local_state_and_closed_ui_ignores_results(self):
        self.browser._managed_open=False
        self.browser._toggle_managed_packages()
        self.assertIn(('snapshot',{'refresh':True}),self.calls)
        self.calls.clear();self.browser._closed=True
        self.browser.sync_managed_packages()
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
