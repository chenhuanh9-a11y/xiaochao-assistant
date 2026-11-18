"""Real directory publication and Windows handle ownership; no map/GUI claims."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from resource_transaction import (ResourceTransactionError,
                                  rename_owned_directory_no_replace)


def identity(path):
    info = path.lstat()
    return [info.st_dev, info.st_ino]


class OwnedDirectoryPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        self.target = self.root / 'target'
        self.source.mkdir()
        (self.source / 'payload').write_bytes(b'owned payload')
        self.expected = identity(self.source)

    def publish(self, before_move=lambda: None):
        return rename_owned_directory_no_replace(
            self.source, self.target, self.expected, before_move=before_move)

    def test_move_preserves_directory_and_payload_identities(self):
        payload_identity = identity(self.source / 'payload')
        callbacks = []
        def verify():
            callbacks.append(identity(self.source))
            self.assertEqual((self.source / 'payload').read_bytes(), b'owned payload')
        self.assertIsNone(self.publish(verify))
        self.assertEqual(callbacks, [self.expected])
        self.assertFalse(self.source.exists())
        self.assertEqual(identity(self.target), self.expected)
        self.assertEqual(identity(self.target / 'payload'), payload_identity)

    def test_target_created_during_callback_is_not_replaced(self):
        target_identity = []
        def conflict():
            self.target.mkdir()
            (self.target / 'foreign').write_bytes(b'foreign payload')
            target_identity.extend(identity(self.target))
        with self.assertRaises(FileExistsError):
            self.publish(conflict)
        self.assertEqual(identity(self.source), self.expected)
        self.assertEqual((self.source / 'payload').read_bytes(), b'owned payload')
        self.assertEqual(identity(self.target), target_identity)
        self.assertEqual((self.target / 'foreign').read_bytes(), b'foreign payload')
        released = self.root / 'released-after-target-conflict'
        self.source.rename(released)
        self.assertEqual(identity(released), self.expected)

    def test_replaced_source_is_rejected_before_callback(self):
        detached = self.root / 'original'
        self.source.rename(detached)
        self.source.mkdir()
        (self.source / 'foreign').write_bytes(b'foreign payload')
        foreign_identity = identity(self.source)
        callbacks = []
        with self.assertRaisesRegex(ResourceTransactionError, 'resource-directory-source-changed'):
            self.publish(lambda: callbacks.append(True))
        self.assertEqual(callbacks, [])
        self.assertEqual(identity(self.source), foreign_identity)
        self.assertEqual(identity(detached), self.expected)
        self.assertFalse(self.target.exists())

    def test_callback_failure_releases_directory_handle(self):
        class VerificationFailed(Exception):
            pass
        def fail():
            raise VerificationFailed('actual tree verification failed')
        with self.assertRaisesRegex(VerificationFailed, 'actual tree verification failed'):
            self.publish(fail)
        detached = self.root / 'after-failure'
        self.source.rename(detached)
        self.assertEqual(identity(detached), self.expected)
        self.assertFalse(self.target.exists())

    def test_missing_destination_parent_is_not_created(self):
        self.target = self.root / 'missing-parent' / 'target'
        with self.assertRaisesRegex(ResourceTransactionError, 'resource-directory-target-parent-unavailable'):
            self.publish()
        self.assertFalse(self.target.parent.exists())
        self.assertEqual(identity(self.source), self.expected)

    def test_replaced_destination_parent_is_not_used(self):
        parent = self.root / 'destination-parent'
        parent.mkdir()
        self.target = parent / 'target'
        detached = self.root / 'original-destination-parent'
        original_identity = identity(parent)
        def replace():
            parent.rename(detached)
            parent.mkdir()
            (parent / 'foreign').write_bytes(b'foreign parent')
        with self.assertRaisesRegex(ResourceTransactionError, 'resource-directory-target-parent-changed'):
            self.publish(replace)
        self.assertEqual(identity(self.source), self.expected)
        self.assertEqual(identity(detached), original_identity)
        self.assertEqual((parent / 'foreign').read_bytes(), b'foreign parent')
        self.assertFalse(self.target.exists())

    def test_directory_link_is_rejected_without_moving_either_tree(self):
        link = self.root / 'source-link'
        if os.name == 'nt':
            command = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(self.source)],
                                     capture_output=True, text=True, errors='replace', timeout=10)
            self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
        else:
            link.symlink_to(self.source, target_is_directory=True)
        original_source = self.source
        self.source = link
        with self.assertRaisesRegex(ResourceTransactionError, 'resource-linked-target'):
            self.publish()
        self.assertEqual(identity(original_source), self.expected)
        self.assertTrue(link.exists())
        self.assertFalse(self.target.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows same-handle directory ownership')
    def test_bound_handle_blocks_source_replacement_during_callback(self):
        detached = self.root / 'attempted-replacement'
        blocked = []
        def replace():
            try:
                self.source.rename(detached)
            except OSError:
                blocked.append(True)
            else:
                self.source.mkdir()
                (self.source / 'foreign').write_bytes(b'foreign payload')
        try:
            self.publish(replace)
        except ResourceTransactionError as error:
            self.assertEqual(error.code, 'resource-directory-source-changed')
            self.assertFalse(self.target.exists())
            self.assertEqual(identity(detached), self.expected)
            self.assertEqual((self.source / 'foreign').read_bytes(), b'foreign payload')
        else:
            self.assertEqual(blocked, [True])
            self.assertFalse(detached.exists())
            self.assertEqual(identity(self.target), self.expected)
            self.assertEqual((self.target / 'payload').read_bytes(), b'owned payload')


if __name__ == '__main__':
    unittest.main()
