"""Explicit reviewed front families stored with their actual recognition base.

Membership is data, not a similarity result. Missing bindings keep the whole
family unavailable; they must not turn known ambiguity into unique identity.
"""
from pathlib import Path
from types import MappingProxyType

from confirmed_front_groups import ConfirmedFrontGroups
from imported_resource_packages import FileRef
from season_context import SeasonContext
import star_map_import as files


SCHEMA = 'xingtu-reviewed-front-groups-v1'
INTERFACE = 'reviewedFrontGroups'
RESOURCE_PATH = 'references/reviewed-front-groups.json'


def _reject(reason):
    code = 'imported-reviewed-groups-' + reason
    raise files.ImportRejected(code, code=code)


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        _reject('text-invalid')


def validate_front_group_members(row):
    """Shared bounded declarations, before the producer expands FileRefs."""
    _text(row.get('groupId'))
    members = row.get('memberIds')
    if (row.get('floor') != '1' or row.get('entranceKind') != 'front'
            or not isinstance(members, list) or not 2 <= len(members) <= 768
            or any(not isinstance(mid, str) or not mid or len(mid) > 128 for mid in members)
            or len(set(members)) != len(members)):
        _reject('members-invalid')
    for mid in members:
        if files._relative(mid) != mid or '/' in mid:
            _reject('member-id-invalid')
    return tuple(members)


class ImportedFrontGroups(ConfirmedFrontGroups):
    """The existing group protocol, with component-bound data and reads."""
    def __init__(self, base, path):
        self._base, self._path = base, path
        self.context = SeasonContext(season=base.season, mode=base.mode)
        self.mode, self.map_ids = base.mode, frozenset(base.pack.by_id)
        self._view = base.reader.view
        self.file_ref = FileRef(base.component_ref, path)
        if Path(path).suffix.casefold() != '.json':
            _reject('reference-type-invalid')
        self._view.path(path)
        payload = base.reader.json(path)
        fields = {'schema', 'season', 'mode', 'groups'}
        if (not isinstance(payload, dict) or set(payload) not in (fields, fields | {'reviewSource'})
                or payload['schema'] != SCHEMA or payload['season'] != base.season
                or payload['mode'] != base.mode or not isinstance(payload['groups'], list)
                or len(payload['groups']) > 768):
            _reject('declarations-invalid')
        self.review_source = payload.get('reviewSource', 'not-declared')
        _text(self.review_source)  # Descriptive provenance, never an authority flag.
        # This consumer has only the established S0 F1/front scope. No table
        # can borrow it for S1, negative floors or semantic side entrances.
        if base.season != 's0':
            _reject('context-unsupported')
        groups, by_id, bindings, gaps, seen_members = [], {}, {}, {}, set()
        versions = base.versions
        for row in payload['groups']:
            if (not isinstance(row, dict) or set(row) != {'groupId', 'floor', 'entranceKind', 'memberIds', 'bindings'}
                    or row['floor'] != '1' or row['entranceKind'] != 'front'):
                _reject('scope-invalid')
            group_id, members = row['groupId'], row['memberIds']
            members = validate_front_group_members(row)
            if group_id in by_id or seen_members.intersection(members):
                _reject('members-invalid')
            seen_members.update(members)
            if not isinstance(row['bindings'], list) or len(row['bindings']) > len(members):
                _reject('bindings-invalid')
            declared = {}
            for binding in row['bindings']:
                if (not isinstance(binding, dict)
                        or set(binding) != {'mapId', 'referenceResource', 'structureVersionKey'}
                        or binding['mapId'] not in members or binding['mapId'] in declared):
                    _reject('binding-invalid')
                resource = FileRef(base.component_ref, binding['referenceResource'])
                if Path(resource.path).suffix.casefold() != '.npz':
                    _reject('binding-type-invalid')
                self._view.path(resource.path)  # Declared missing files remain damage.
                keys = [key for key in versions if key[1] == binding['mapId'] and key[3] == '1']
                if len(keys) != 1:
                    _reject('binding-not-typed')
                version = versions[keys[0]]
                if (self._view.path(resource.path).resolve() != version['path']
                        or binding['structureVersionKey'] != version['expected']['structureVersionKey']):
                    _reject('structure-binding-mismatch')
                declared[binding['mapId']] = (resource, version)
            missing = tuple('member-not-in-base:' + mid if mid not in self.map_ids else
                            'member-typed-binding-unavailable:' + mid
                            for mid in members if mid not in self.map_ids or mid not in declared)
            group = MappingProxyType(dict(groupId=group_id, mode=self.mode, entranceKind='front',
                                           floor='1', memberIds=tuple(members)))
            groups.append(group)
            by_id[group_id], bindings[group_id] = group, MappingProxyType(declared)
            if missing:
                gaps[group_id] = missing
        self.groups = tuple(groups)
        self._by_id, self._bindings = MappingProxyType(by_id), MappingProxyType(bindings)
        self.group_gaps = MappingProxyType(gaps)
        self.gaps = ('reviewed-front-group-bindings-unavailable',) if gaps else ()
        # Reuse existing component/FileRef identity as an opaque resource key;
        # no new membership digest or program-frozen member list is introduced.
        ref = base.component_ref
        self.index_digest = f'{ref.package_id}/{ref.resource_version}/{ref.component_id}@{ref.manifest_sha256}:{path}'
        self.assert_current()

    def assert_current(self):
        self._base.assert_current()
        self._base.reader(self._view.path(self._path))

    def group_for(self, map_id, *, floor, entrance_kind):
        self.assert_current()
        return super().group_for(map_id, floor=floor, entrance_kind=entrance_kind)

    def validate(self, group, provider):
        self.assert_current()
        if group not in self.groups:
            _reject('group-not-declared')
        group_id = group['groupId']
        if group_id in self.group_gaps:
            raise ValueError('reviewed-front-group-unavailable:' + self.group_gaps[group_id][0])
        if provider is None:
            raise ValueError('reviewed-front-group-provider-unavailable')
        for mid in group['memberIds']:
            resource, version = self._bindings[group_id][mid]
            reference = provider('shared-structure', mid, '1')
            if reference is None:
                raise ValueError('reviewed-front-group-reference-unavailable:' + mid + '/1')
            self._base.reader(reference.path)  # Warm provider cache still checks content.
            metadata = reference.metadata
            if (Path(reference.path).resolve() != self._view.path(resource.path).resolve()
                    or metadata.get('binding') != version['binding']
                    or metadata.get('canvas') != version['canvas']
                    or tuple(reference.shape) != tuple(version['canvas']['shapeHW'])
                    or any(metadata.get(name) != value for name, value in version['expected'].items())):
                raise ValueError('reviewed-front-group-reference-mismatch:' + mid + '/1')
        self.assert_current()

    def validate_available(self, provider):
        for group in self.groups:
            if group['groupId'] not in self.group_gaps:
                self.validate(group, provider)
        self.assert_current()
