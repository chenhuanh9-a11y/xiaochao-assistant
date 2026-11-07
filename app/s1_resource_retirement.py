"""One explicit source-owned projection of the published S1 byte inventory.

This removes only the retired normal mode. It does not bless new geometry,
change hashes, grant author ownership, or accept a package-declared migration.
Historical three-mode inventories stay readable for recovery.
"""
SOURCE_VERSION = 's0-1.0.7+S1-native-2.104.3099014-20261106'
TARGET_VERSION = SOURCE_VERSION + '-routes-only-v1'
RETIRED_PREFIX = '_internal/seasons/s1/difficulty2/'
ACTIVE_MODES = frozenset({'difficult', 'nightmare'})


def projected_release(version, rows):
    if version != SOURCE_VERSION:
        return None
    retained = frozenset(row for row in rows if not row[0].startswith(RETIRED_PREFIX))
    omitted = frozenset(rows) - retained
    if not omitted or not retained:
        raise ValueError('incomplete S1 normal retirement source')
    return TARGET_VERSION, retained


def retirement_inventory(source_version, target_version, source_rows, target_rows):
    """Exact whole-mode removal; unrelated rename/omission remains forbidden."""
    projection = projected_release(source_version, source_rows)
    if projection is None or target_version != projection[0] or frozenset(target_rows) != projection[1]:
        return frozenset()
    return frozenset(source_rows) - projection[1]
