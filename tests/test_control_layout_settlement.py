"""Production geometry contracts without native window or game interaction."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'app/identityv_daily_assistant.py'
TREE=ast.parse(SOURCE.read_text(encoding='utf-8'))
CLASS=next(n for n in TREE.body if isinstance(n,ast.ClassDef) and n.name=='DailyAssistant')
def method(name,**namespace):
    node=next(n for n in CLASS.body if isinstance(n,ast.FunctionDef) and n.name==name)
    node.decorator_list=[]
    scope=dict(math=math,tk=SimpleNamespace(Misc=object),tk_widget_exists=lambda v:v is not None,
        tk_widget_is_viewable=lambda w:False,**namespace)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),scope)
    return scope[name]

class SettlementTests(unittest.TestCase):
    def test_measured_expanded_size_is_preserved_in_dip_and_snapshot_is_read_only(self):
        events=[]
        view=SimpleNamespace(set_display_scale=lambda s:events.append(('scale',s)),
            set_available_height=lambda h:events.append(('cap',h)),
            expanded_size=lambda:(events.append(('measure',)) or (528,375)))
        context=SimpleNamespace(scale=1.5,monitor=SimpleNamespace(work_area_px=SimpleNamespace(height=1200)))
        owner=SimpleNamespace(game_control_view=view,assistant_shell=object(),
            _ui_context_for_window_snapshot=lambda w:context,control_collapsed=False,sheet_move_mode=False)
        self.assertEqual(method('_quick_panel_dimensions')(owner),(352,250))
        self.assertEqual([x[0] for x in events],['scale','cap','measure'])
        events.clear()
        self.assertEqual(method('_quick_panel_dimensions')(owner),(352,250))
        self.assertEqual([x[0] for x in events],['cap','measure'])
        owner.control_collapsed=True
        events.clear()
        self.assertEqual(method('_quick_panel_dimensions')(owner),(64,64))
        self.assertNotIn(('measure',),events)

    def test_final_geometry_and_alpha_settle_before_native_rounding_and_noactivate(self):
        events=[]
        shell=SimpleNamespace(attributes=lambda *a:events.append(('attributes',)+a),
            geometry=lambda g:events.append(('geometry',g)),
            update_idletasks=lambda:events.append(('settle',)),
            deiconify=lambda:events.append(('show',)),lift=lambda:events.append(('lift',)))
        owner=SimpleNamespace(assistant_shell=shell,assistant_shell_mode='quick',control_collapsed=False,
            game_control_view=SimpleNamespace(BALL_TRANSPARENT_COLOR='#010203'),
            _sync_game_control_view=lambda:events.append(('render',)),
            _ui_action_begin=lambda *a,**k:1,_hide_orb_status_pill=lambda:None,
            _shell_target_geometry=lambda m:(352,250,100,120),
            _set_assistant_surface=lambda m:events.append(('surface',m)),
            assistant_shell_frame=SimpleNamespace(place_configure=lambda **k:None),
            _control_panel_key_is_down=lambda:False,_hide_shell_connector=lambda:None,
            _clear_window_rounding_region=lambda w:None,interface_opacity=1.0,
            _ui_context_for_window_snapshot=lambda w:SimpleNamespace(scale=1.0),
            _apply_window_rounding=lambda w,**k:events.append(('round',k['sync_layout'])),
            _set_noactivate=lambda w,b,**k:events.append(('noactivate',k['sync_layout'])),
            _show_shell_connector=lambda t:None,_ui_action_finish=lambda *a,**k:None,
            _play_ui_sound=lambda *a:None)
        method('_show_assistant_shell',set_rounded_scale=lambda *a:events.append(('scale',)))(owner,'quick')
        keys=[e[0] for e in events]
        self.assertLess(keys.index('geometry'),keys.index('settle'))
        self.assertLess(max(i for i,k in enumerate(keys) if k=='attributes'),keys.index('settle'))
        self.assertLess(keys.index('settle'),keys.index('round'))
        self.assertLess(keys.index('round'),keys.index('noactivate'))
        self.assertLess(keys.index('noactivate'),keys.index('show'))
        self.assertEqual(events[keys.index('round')],('round',False))
        self.assertEqual(events[keys.index('noactivate')],('noactivate',False))

    def test_hidden_legacy_navigation_is_not_restyled_and_visible_task_is_fresh(self):
        events=[]
        def chrome(name):
            return SimpleNamespace(pack=lambda **kw:events.append((name,'pack')),
                pack_forget=lambda:events.append((name,'forget')))
        button=SimpleNamespace(configure=lambda **kw:events.append(('navstyle',kw)))
        owner=SimpleNamespace(assistant_shell_mode='quick',
            _ui_action_begin=lambda *a,**kw:None,
            shell_header=chrome('header'),shell_navigation=chrome('navigation'),
            assistant_shell_body=object(),quick_surface=chrome('quick'),
            details_surface=chrome('recognition'),settings_surface=chrome('settings'),
            shell_nav_buttons={'quick':button},_sync_recognition_ui=lambda:events.append(('sync',)))
        theme=SimpleNamespace(NAV_SELECTED='selected',TINT='tint',TEXT='text',MUTED='muted')
        fn=method('_set_assistant_surface',floating_theme=theme)
        fn(owner,'quick')
        self.assertIn(('navigation','forget'),events)
        self.assertFalse(any(row[0]=='navstyle' for row in events))
        events.clear();fn(owner,'settings')
        style=next(row[1] for row in events if row[0]=='navstyle')
        self.assertEqual(style['bg'],'tint')
        self.assertEqual(style['fg'],'muted')
        self.assertEqual(style['font'][-1],'normal')
        self.assertIn(('navigation','pack'),events)

    def test_default_noactivate_keeps_settlement_and_requeries_current_wrapper(self):
        events=[]
        user=SimpleNamespace(GetAncestor=lambda child,kind:(events.append(('ancestor',child)) or 789),
            GetWindowLongW=lambda *a:0,SetWindowLongW=lambda *a:events.append(('style',a[0])),
            SetWindowPos=lambda *a:events.append(('position',a[0])))
        window=SimpleNamespace(update_idletasks=lambda:events.append(('settle',)),winfo_id=lambda:123)
        native=SimpleNamespace(windll=SimpleNamespace(user32=user))
        fn=method('_set_noactivate',ctypes=native)
        fn(window,True)
        self.assertEqual(events[0],('settle',))
        self.assertEqual(events[1],('ancestor',123))
        self.assertIn(('style',789),events)
        events.clear();fn(window,True,sync_layout=False)
        self.assertNotIn(('settle',),events)
        self.assertEqual(events[0],('ancestor',123))

if __name__=='__main__':unittest.main()
