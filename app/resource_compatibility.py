"""Read-only compatibility diagnosis, never an import or ownership authority.

The original rejection code remains authoritative. Classification explains the
next action without weakening archive, coordinate, receipt or transaction gates.
No file is read, changed, adopted, downloaded or silently repaired here.
"""
from __future__ import annotations

import re
import unicodedata


def safe_dependency_path(value):
    """Bounded shipped-resource identity; never an arbitrary local file path."""
    if (not isinstance(value, str)
            or len(value.encode('utf-8', errors='replace')) > 240
            or any(char in '\\:<>\"|?*' or unicodedata.category(char).startswith('C') for char in value)
            or any(not part or part in {'.', '..'} or part.endswith((' ', '.')) for part in value.split('/'))):
        return False
    if value.startswith('_internal/references/'):
        return True
    return re.fullmatch(
        r'_internal/seasons/s1/(?:difficulty2|difficult|nightmare)/'
        r'(?:native-map-manifest\.json|(?:references|assets|recognition)/[^\n]+)', value) is not None


def diagnose_compatibility(code, details=None):
    """Return a stable state and action for a *verified* producer error code.

    These are explanation hints, not evidence that a replacement/download is
    trusted or available. The mutating operation must revalidate its own plan.
    """
    code = code if isinstance(code, str) else ''
    details = details if isinstance(details, dict) else {}
    state, action = 'unknown', 'inspect'
    if code == 'missing-or-incompatible-dependency':
        state = 'base-missing' if details.get('missing') is True else (
            'base-incompatible' if details.get('missing') is False else 'base-unavailable')
        action = 'find-matching-base'
    elif code in {'unsupported-client-version', 'unsupported-client-compatibility'}:
        state, action = 'client-unsupported', 'check-client-protocol'
    elif code in {'unsupported-package-season', 'package-season-mismatch'}:
        state, action = 'context-mismatch', 'select-matching-season'
    elif code == 'retired-mode':
        state, action = 'mode-retired', 'select-active-mode'
    elif code == 'author-v2-retired-mode':
        state, action = 'archive-mode-retired', 'obtain-supported-mode-package'
    elif code in {'existing-data-conflict', 'target-changed-during-import',
                  'installed-package-changed'}:
        state, action = 'local-content-changed', 'preserve-and-inspect'
    elif code in {'shared-package-dependency-conflict', 'shared-package-ownership-conflict',
                  'installed-package-dependency-conflict', 'installed-package-owned-conflict'}:
        state, action = 'package-conflict', 'inspect-dependent-packages'
    elif code == 'author-v2-shrinking-package-requires-remove':
        state, action = 'update-needs-retirement', 'inspect-package-update'
    elif code == 'dependency-contract-changed-reimport-unsupported':
        state, action = 'receipt-migration-required', 'inspect-package-update'
    elif code.startswith('author-v2-base-unavailable'):
        state, action = 'base-unavailable', 'find-matching-base'
    elif code.startswith(('author-v2-reference-', 'author-v2-native-', 'author-v2-invalid-display-',
                          's1-author-binding-', 's1-author-unapproved-')):
        state, action = 'display-binding-incompatible', 'find-matching-display-binding'
    elif code in {'invalid-installed-package-receipt', 'invalid-installed-package-dependencies',
                  'installed-package-receipt-rejected', 'author-v2-receipt-dependency-closure-mismatch'}:
        state, action = 'receipt-unverified', 'preserve-and-inspect'
    elif code in {'file-integrity-mismatch', 'file-size-or-case-mismatch', 'package-size-or-sha-mismatch'}:
        state, action = 'archive-damaged', 'obtain-complete-archive'
    return dict(state=state, suggestedAction=action, authorizesMutation=False)


def compatibility_message(code, details=None):
    """Concrete, non-destructive next steps shared by resource frontends."""
    state = diagnose_compatibility(code, details)['state']
    return {
        'context-mismatch': '图包所属赛季与当前选择不同，本次未导入。请选择对应赛季后重新检查。',
        'mode-retired': 'S1 普通模式已停用。历史资源和偏好仍保留，请选择困难或噩梦模式。',
        'archive-mode-retired': '这个图包仅含已停用的 S1 普通模式，本次未导入，已安装资源未改动。请获取包含困难或噩梦层的图包。',
        'client-unsupported': '当前软件不支持这个图包声明的客户端协议。本次未导入，请核对软件与图包的适用版本。',
        'package-conflict': '这个更新与另一个已安装图包的所有权或依赖冲突。本次未改动资源，请先核对相关图包。',
        'update-needs-retirement': '新版图包减少或改名了旧文件，当前更新不能安全接管这些文件。本次未导入，旧图包保留；请先核对移除范围。',
        'display-binding-incompatible': '作者图与当前地图的结构或坐标绑定不匹配。本次未导入，请获取匹配的显示绑定；不要修改哈希强行导入。',
        'receipt-unverified': '图包的安装记录无法完整核实，现有文件已保留。本次不会自动认领、覆盖或删除，请核对原始图包。',
    }.get(state)
