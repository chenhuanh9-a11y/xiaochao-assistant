"""Settings/controller and resource-page consumers without Tk construction."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

APP=Path(__file__).parents[1]/'app'

def subject(filename,names,namespace):
    tree=ast.parse((APP/filename).read_text('utf-8-sig'))
    methods=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in names]
    assert len(methods)==len(names)
    wrapper=ast.ClassDef(name='Subject',bases=[],keywords=[],body=methods,decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper],type_ignores=[])),filename,'exec'),namespace)
    return namespace['Subject']


class ControllerTests(unittest.TestCase):
    def setUp(self):
        names={'route_selection_snapshot','select_route_profile','_apply_route_profile_setting','_shown_route_profile'}
        ns=dict(gallery_of=lambda p:p[7:] if p and p.startswith('custom:') else None,
                DEFAULT_ROUTE_PROFILE_ID='wang-qingxin',ROUTE_PROFILE_IDS_BY_LABEL={})
        cls=subject('identityv_daily_assistant.py',names,ns)
        cls.route_profile_id=property(lambda s:'shared' if s.missing else ('shared' if s.preferred_route_profile_id.startswith('custom:') else s.preferred_route_profile_id),
                                      lambda s,v:setattr(s,'preferred_route_profile_id',v))
        s=self.s=cls();s.missing=False;s.preferred_route_profile_id='suju';s.game_mode='nightmare'
        s.mode_route_profiles={'difficult':'zhan10-speed'}
        s._mode_route_options=lambda:(('suju','滋酥居'),('wang-qingxin','王清心'),('nightingale','爱吃醋'),('custom:one','自定义·一'))
        s.author_route_catalog=None;s._shared_structure_mode_enabled=lambda:True
        s.route_profile_var=Mock(get=lambda:'滋酥居');s._route_profile_label=lambda p:p
        s._sync_route_settings_ui=Mock();s._save_settings=Mock(return_value=True)
        s._refresh_shared_route_pixels=Mock(return_value=True);s._refresh_visible_sheet_after_route_setting=Mock()
        s._set_status=Mock();s._log=Mock();s._hide_spatial_route=Mock()
        s._current_display_descriptor=Mock(return_value=object())
        s._committed_display_feedback=lambda *_:SimpleNamespace(status='结构底图' if s.missing else '作者路线')
        s.gate=SimpleNamespace(confirmed_id='nightmare-03');s.current_floor='2'
        s._invalidate_alignment_work=s._alignment_v2_reset_round=s._clear_round_layer_group=Mock(side_effect=AssertionError('No identity reset'))

    def test_switch_persists_mode_preference_and_only_repaints(self):
        result=self.s.select_route_profile('wang-qingxin')
        self.assertTrue(result['success'])
        self.assertEqual(self.s.preferred_route_profile_id,'wang-qingxin')
        self.assertEqual(self.s.mode_route_profiles,{'difficult':'zhan10-speed','nightmare':'wang-qingxin'})
        self.s._refresh_shared_route_pixels.assert_called_once_with('suju')
        self.s._invalidate_alignment_work.assert_not_called()

    def test_missing_layer_preserves_selected_author_and_explains_fallback(self):
        self.s.missing=True
        result=self.s.select_route_profile('nightingale')
        self.assertTrue(result['success']);self.assertTrue(result['fallback'])
        self.assertEqual(result['selected'],'nightingale');self.assertEqual(result['effective'],'shared')
        self.assertIn('结构底图',result['message'])

    def test_custom_gallery_remains_selected_as_shared_display_basis(self):
        result=self.s.select_route_profile('custom:one')
        self.assertTrue(result['success']);self.assertEqual(result['selected'],'custom:one')
        self.assertEqual(result['effective'],'shared');self.assertFalse(result['fallback'])

    def test_missing_package_is_rejected_without_writing(self):
        result=self.s.select_route_profile('unknown-author')
        self.assertFalse(result['success']);self.s._save_settings.assert_not_called()

    def test_failed_save_rolls_back_preference_and_mode_cache(self):
        self.s._save_settings.return_value=False
        result=self.s.select_route_profile('wang-qingxin')
        self.assertFalse(result['success']);self.assertEqual(self.s.preferred_route_profile_id,'suju')
        self.assertEqual(self.s.mode_route_profiles,{'difficult':'zhan10-speed'})
        self.s._refresh_shared_route_pixels.assert_not_called()

    def test_closed_or_unauthorized_display_waits_without_reidentification(self):
        self.s._refresh_shared_route_pixels.return_value=False
        self.assertTrue(self.s.select_route_profile('wang-qingxin')['success'])
        self.s._hide_spatial_route.assert_called_once()
        self.s._invalidate_alignment_work.assert_not_called()


class BrowserTests(unittest.TestCase):
    def setUp(self):
        cls=subject('map_resource_browser.py',{'sync_route_selection','_select_runtime_route'},dict(WARNING='warning',MUTED='muted'))
        self.s=cls();self.s._runtime_route_signature=None
        self.snapshot=dict(mode='nightmare',options=(('suju','滋酥居'),('nightingale','爱吃醋')),
                           selected='suju',effective='suju',message='提示')
        self.s.assistant=SimpleNamespace(route_selection_snapshot=Mock(side_effect=lambda:self.snapshot),
                                        select_route_profile=Mock(return_value={'success':True}))
        self.s.runtime_route_box=Mock();self.s.runtime_route_var=Mock();self.s.runtime_route_feedback=Mock()

    def test_resource_page_calls_actual_controller_with_stable_id(self):
        self.s.sync_route_selection();self.s.runtime_route_box.current.return_value=1
        self.s._select_runtime_route()
        self.s.assistant.select_route_profile.assert_called_once_with('nightingale')

    def test_periodic_sync_does_not_reconfigure_unchanged_dropdown(self):
        self.s.sync_route_selection();self.s.sync_route_selection()
        self.s.runtime_route_box.configure.assert_called_once()

    def test_mode_change_rebuilds_available_choices(self):
        self.s.sync_route_selection()
        self.snapshot=dict(mode='difficult',options=(('zhan10-speed','展十'),),selected='zhan10-speed')
        self.s.sync_route_selection()
        self.assertEqual(self.s._runtime_route_keys,('zhan10-speed',))

    def test_unavailable_controller_and_failure_have_explicit_feedback(self):
        self.s.sync_route_selection();self.s.runtime_route_box.current.return_value=1
        self.s.assistant.select_route_profile.return_value=dict(success=False,message='无法保存')
        self.s._select_runtime_route()
        self.s.runtime_route_feedback.configure.assert_called_with(text='无法保存',fg='warning')

if __name__=='__main__': unittest.main()
