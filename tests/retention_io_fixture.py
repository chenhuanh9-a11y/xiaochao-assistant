"""Single-thread temporary-directory IO doubles; NOT Windows handle evidence."""
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest.mock import patch
import os
import diagnostic_storage as logs
import failure_sampling as images

@contextmanager
def image_io():
    @contextmanager
    def transaction(root, *, create=False, namespace='automatic'):
        directory = images._directory(root, namespace)
        if not directory.exists() and not create:
            yield 'storage-absent'; return
        with logs._pinned_root(directory):
            journal = directory / 'automatic-failures.jsonl'
            journal.touch(exist_ok=True)
            yield 'ready'
    def require(root, namespace):
        return images._directory(root, namespace)
    def publish(directory, payload, **kwargs):
        (directory / 'automatic-failures.jsonl').write_bytes(payload)
    def retire(root, row, *, namespace='automatic-v2'):
        path = images._image_path(root, row, namespace)
        try: fd = images._open_image_read(path)
        except FileNotFoundError: return 'missing'
        try: images._verified_image_fd(fd, row)
        finally: os.close(fd)
        path.unlink()
        return 'retired'
    with ExitStack() as stack:
        stack.enter_context(patch.object(images, '_known_image_roots', set()))
        stack.enter_context(patch.object(images, '_automatic_storage_transaction', transaction))
        stack.enter_context(patch.object(images, '_require_transaction', require))
        stack.enter_context(patch.object(images, 'replace_automatic_journal', publish))
        stack.enter_context(patch.object(images, 'retire_automatic_image', retire))
        yield

def retire_log(root, name, entry):
    assert logs._verified(root, name, entry)
    (root / name).unlink()
