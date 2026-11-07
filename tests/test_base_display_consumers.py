"""Current Qt author-status consumer and shared base-cache byte budget."""
from pathlib import Path
from types import SimpleNamespace
import unittest
import test_base_map_display as base_fixture
from client_route_preferences import _DisplaySelection

class BaseDisplayConsumerTests(unittest.TestCase):
    def setUp(self):
        self.f = base_fixture.BaseMapDisplayTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.mid = 'nightmare-layout-104026'

    def test_declared_unusable_is_not_missing(self):
        # Qt's actual selection adapter delegates to the shared author-status rule.
        selection = _DisplaySelection.__new__(_DisplaySelection)
        selection.preferred_route_profile_id = 'missing-author'
        selection.author_route_catalog = SimpleNamespace(variants=lambda *args, **kw: ())
        selection.optional_author_displays = None
        self.assertEqual(selection._base_author_status(self.mid, '1'), 'missing')
        selection.author_route_catalog.variants = lambda *args, **kw: (SimpleNamespace(layers={'1': Path('absent.png')}),)
        self.assertEqual(selection._base_author_status(self.mid, '1'), 'available')
        selection.optional_author_displays = SimpleNamespace(binding=lambda *args: dict(usable=False))
        self.assertEqual(selection._base_author_status(self.mid, '1'), 'registration-failed')

    def test_provider_byte_limit_counts_retained_reference_and_pixels(self):
        from base_map_display import BaseMapDisplay
        size=self.f.mask.nbytes*5
        provider=BaseMapDisplay(max_bytes=size)
        first=provider.resolve(self.f.library,self.mid,'1',self.f.ref)
        self.assertIsNotNone(first.layer)
        other=SimpleNamespace(shape=self.f.ref.shape,clean_mask=self.f.mask.copy(),path=self.f.ref.path)
        self.assertIsNotNone(provider.resolve(self.f.library,self.mid,'1',other).layer)
        self.assertEqual(len(provider._cache),1)
        self.assertIsNone(provider.peek(self.f.library,self.mid,'1',self.f.ref).layer)
        small=BaseMapDisplay(max_bytes=size-1)
        self.assertEqual(small.resolve(self.f.library,self.mid,'1',self.f.ref).reason,'base-map-cache-byte-limit')
        self.assertEqual(len(small._cache),0)
