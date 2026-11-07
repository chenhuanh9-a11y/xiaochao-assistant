"""Qt presentation metadata over validated-context stand-ins, not live GUI QA."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'app'))
from PySide6.QtCore import QCoreApplication
from map_resource_context import BrowseContext, ProfileSummary
import backend


class GenericAuthorBrowserTests(unittest.TestCase):
    def test_validated_generic_s1_profile_is_selectable_in_qt_gallery(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        profile = ProfileSummary('author-new', 'New author', 'author-new', 1, 1, 0, 0, 1, 1, None, ())
        context = BrowseContext('difficulty2', ROOT, None, None, None, (profile,), {}, season='s1')
        with patch.object(backend, 'runtime_paths', return_value=SimpleNamespace(installation_root=ROOT)), \
                patch.object(backend.Backend, '_submit', lambda self, read, loaded: loaded(context, None)), \
                patch.object(backend.Backend, '_apply_rows', return_value=None):
            owner = backend.Backend(ROOT)
            owner._season, owner._mode = 's1', 'difficulty2'
            owner.refresh()
            self.assertTrue(owner.profiles[0]['runtimeSelectable'])
            self.assertEqual(owner.profiles[0]['runtimeProfileId'], 'author-new')
            owner.pool.waitForDone()
