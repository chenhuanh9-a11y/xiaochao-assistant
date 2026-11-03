"""Qt opt-in is independent of the legacy Tk runtime-enabled bit.

Do not repair unreadable/foreign files implicitly. Unknown JSON fields survive
explicit saves; no migration writes are needed simply to launch the client.
"""
import json
import os
from pathlib import Path
import tempfile

from client_paths import _no_links
from chest_auto_tap import ChestAutoTap, SCHEMA, validate

INTENT_SCHEMA = 'xingtu-qt-chest-explicit-intent-v1'


def read_object(path, schema):
    try:
        _no_links(path)
    except ValueError as error:
        raise OSError("配置路径不能包含链接") from error
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (ValueError, UnicodeError) as error:
        raise OSError('配置损坏，保留原文件，请先修复配置') from error
    if not isinstance(raw, dict) or raw.get('schema') != schema:
        raise OSError('配置格式未知，保留原文件')
    return raw


def merge_save(path, schema, changes):
    raw = read_object(path, schema)
    raw.update(changes)
    raw['schema'] = schema
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(raw, stream, ensure_ascii=False, indent=2)
        try:
            _no_links(path)
        except ValueError as error:
            raise OSError("配置路径不能包含链接") from error
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class QtChestAutoTap(ChestAutoTap):
    """Keep legacy parameters compatible without discarding unknown fields."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, require_release=True, **kwargs)

    def _save(self, settings):
        raw = read_object(self.path, SCHEMA)
        if raw:
            try:
                validate({**self._defaults(), **raw}, {})
            except ValueError as error:
                raise OSError('原按键配置损坏，保留原文件，请先修复配置') from error
        merge_save(self.path, SCHEMA, settings)
