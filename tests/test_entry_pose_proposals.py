"""Entry hints keep purpose limits and the actual reference coordinate basis."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

import numpy as np

from entry_pose_proposals import current_entry_pose_seeds, entry_pose_proposals_for_reference


class EntryPoseProposalTests(unittest.TestCase):
    def setUp(self):
        self.matrix = np.array([[.7, 0, 200], [0, .7, 150], [0, 0, 1.]])
        self.entry = dict(mapId='map-a', floor='2', profileId='entry-canvas',
            proposalOnly=True, authorizesPose=False, authorizesIdentity=False,
            poseVerified=False, identityVerified=False,
            verification=dict(success=True, matrixSourceToClient=self.matrix))
        self.row = dict(mapId='map-a', difficultEntry=self.entry)
        self.reference = SimpleNamespace(metadata=dict(binding=dict(
            profile='reference-canvas', mapId='map-a', floor='2')))
        self.basis = np.array([[2., 0, 30], [0, 2., -10], [0, 0, 1]])
        self.adapters = SimpleNamespace(matrix_profile_to_canonical=lambda *args: self.basis.copy())

    def test_verified_geometry_is_a_hint_without_upgrading_resource_purpose(self):
        seeds = current_entry_pose_seeds([self.row], '2')
        self.assertIs(seeds['map-a'], self.entry)
        self.assertIs(seeds['map-a']['authorizesPose'], False)
        self.assertIs(seeds['map-a']['identityVerified'], False)

    def test_failed_foreign_ambiguous_and_contradicted_geometry_is_not_a_hint(self):
        for change in (dict(mapId='foreign'), dict(floor='1'), dict(poseAmbiguous=True),
                dict(entryCrossCheck=dict(contradicted=True)), dict(verification=dict(success=False)),
                dict(verification=dict(success=True, matrixSourceToClient=np.zeros((2, 3)))),
                dict(verification=dict(success=True, matrixSourceToClient=np.full((3, 3), np.nan)))):
            with self.subTest(change=change):
                entry = dict(deepcopy(self.entry), **change)
                self.assertEqual(current_entry_pose_seeds([dict(self.row, difficultEntry=entry)], '2'), {})

    def test_matching_nonidentity_bridges_do_not_transform_the_entry_matrix_again(self):
        proposals = entry_pose_proposals_for_reference(self.entry, 'map-a', '2', self.reference, self.adapters)
        self.assertEqual(len(proposals), 1)
        np.testing.assert_array_equal(proposals[0], self.matrix)
        self.assertIsNot(proposals[0], self.matrix)
        proposals[0][0, 2] = -500
        self.assertEqual(self.matrix[0, 2], 200)

    def test_missing_or_different_bridge_and_foreign_reference_do_not_supply_a_matrix(self):
        for different in (None, np.eye(3), np.full((3, 3), np.nan)):
            with self.subTest(bridge=different):
                adapters = SimpleNamespace(matrix_profile_to_canonical=lambda profile, *args:
                    self.basis if profile == 'entry-canvas' else different)
                self.assertEqual(entry_pose_proposals_for_reference(
                    self.entry, 'map-a', '2', self.reference, adapters), ())
        for binding in (dict(profile='reference-canvas', mapId='foreign', floor='2'),
                        dict(profile='reference-canvas', mapId='map-a', floor='1')):
            reference = SimpleNamespace(metadata=dict(binding=binding))
            self.assertEqual(entry_pose_proposals_for_reference(
                self.entry, 'map-a', '2', reference, self.adapters), ())

    def test_display_only_reference_cannot_supply_a_structure_pose_proposal(self):
        reference = SimpleNamespace(metadata=dict(self.reference.metadata, authorizesStructureValidation=False))
        self.assertEqual(entry_pose_proposals_for_reference(
            self.entry, 'map-a', '2', reference, self.adapters), ())
