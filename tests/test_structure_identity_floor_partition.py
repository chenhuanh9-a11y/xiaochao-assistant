"""Floor partitions preserve retrieval while reusing prepared descriptors."""
import unittest
from unittest.mock import patch

import numpy as np

import structure_identity_index as identity
from test_structure_identity_index import entry, polygon_anchors


class StructureIdentityFloorPartitionTests(unittest.TestCase):
    def test_partition_reuses_descriptors_and_matches_fresh_floor_index(self):
        rows = (entry('upper', floor='2'), entry('selected'), entry('missing', anchors=None))
        for kind in ('full-chain', 'corner-centre'):
            with self.subTest(kind=kind):
                whole = identity.StructureIdentityIndex([row.key for row in rows], rows,
                    directory_complete=True, constraint_kind=kind)
                expected = identity.StructureIdentityIndex([row.key for row in rows if row.key.floor == '1'],
                    [row for row in rows if row.key.floor == '1'],
                    directory_complete=True, constraint_kind=kind)
                with patch.object(identity, '_validate_centres', side_effect=AssertionError('rebuilt-centres')), \
                     patch.object(identity, '_lengths_2d', side_effect=AssertionError('rebuilt-descriptors')):
                    actual = whole.for_floor('1')
                self.assertEqual(actual.domain, expected.domain)
                self.assertEqual(actual.entries, expected.entries)
                self.assertEqual(actual.directory_gaps, expected.directory_gaps)
                self.assertEqual(actual._ratios, expected._ratios)
                np.testing.assert_array_equal(actual._direction_rays, expected._direction_rays)
                self.assertFalse(actual._direction_rays.flags.writeable)
                originals = {(id(posting.anchor), posting.arm_order): posting for posting in whole._postings
                             if whole.entries[posting.entry_index].key.floor == '1'}
                for posting in actual._postings:
                    original = originals[id(posting.anchor), posting.arm_order]
                    self.assertIs(posting.points, original.points)
                    self.assertIs(posting.rays, original.rays)
                query = identity.ConstellationA(polygon_anchors(), True, constraint_kind=kind)
                for options in ({}, {'max_work': 1}, {'cancelled': lambda: True}):
                    self.assertEqual(actual.retrieve(query, epsilon=.1, **options),
                                     expected.retrieve(query, epsilon=.1, **options))

    def test_partition_keeps_unknown_missing_and_outside_competitors(self):
        rows = (entry('upper-unavailable', floor='2', anchors=None),
                entry('selected'), entry('forbidden', search_allowed=False), entry('outside'))
        domain = tuple(row.key for row in rows[:-1]) + (identity.MapFloorKey('fixture', 'absent', '1'),)
        whole = identity.StructureIdentityIndex(domain, rows, directory_complete=False)
        for floor in ('1', '2', 'unavailable'):
            with self.subTest(floor=floor):
                expected = identity.StructureIdentityIndex([key for key in domain if key.floor == floor],
                    [row for row in rows if row.key.floor == floor], directory_complete=False)
                actual = whole.for_floor(floor)
                self.assertEqual(actual.domain, expected.domain)
                self.assertEqual(actual.entries, expected.entries)
                self.assertEqual(actual.directory_gaps, expected.directory_gaps)
                query = identity.ConstellationA(polygon_anchors(), True)
                self.assertEqual(actual.retrieve(query, epsilon=.1), expected.retrieve(query, epsilon=.1))
