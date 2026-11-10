"""Read-only views over installed versioned recognition resource packages.

This validates manifest identity, namespace and selected file reads. Full disk
inventory validation is explicit at import/activation preflight; neither path
establishes component semantic usability by an algorithm.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType

import generic_author_package as generic
import resource_snapshot_io
import star_map_import as files


MANIFEST_NAME = 'manifest.json'
MANIFEST_LIMIT = 4 * 1024 * 1024
SCHEMA = 'xingtu-resource-package'
FORMAT_VERSION = 1
COMPONENT_TYPES = frozenset({
    'recognition-base', 'controls-profile', 'display',
    'recognition-hints', 'reviewed-group-registry',
})
CONTEXTS = frozenset({
    's0:difficult', 's0:nightmare', 's1:difficult', 's1:nightmare',
})
_SHA256 = re.compile(r'[a-f0-9]{64}\Z')
_COMPONENT_FIELDS = frozenset({
    'componentId', 'type', 'schemaVersion', 'contexts', 'entrypoint', 'dependencies',
})
_FILE_FIELDS = frozenset({'componentId', 'path', 'bytes', 'sha256'})
_MANIFEST_FIELDS = frozenset({
    'schema', 'formatVersion', 'packageId', 'resourceVersion',
    'clientCompatibility', 'components', 'files', 'provenance', 'displayName',
})


def _reject(reason):
    raise files.ImportRejected('resource-package-' + reason)


def _segment(value, label):
    try:
        normalized = files._relative(value)
    except (TypeError, ValueError) as error:
        raise files.ImportRejected('resource-package-invalid-' + label) from error
    if normalized != value or '/' in value:
        _reject('invalid-' + label)
    return value


def _digest(value, label='digest'):
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _reject('invalid-' + label)
    return value


@dataclass(frozen=True)
class ComponentRef:
    package_id: str
    resource_version: str
    component_id: str
    manifest_sha256: str

    def __post_init__(self):
        _segment(self.package_id, 'package-id')
        _segment(self.resource_version, 'resource-version')
        _segment(self.component_id, 'component-id')
        _digest(self.manifest_sha256, 'manifest-digest')


@dataclass(frozen=True)
class FileRef:
    component: ComponentRef
    path: str

    def __post_init__(self):
        if not isinstance(self.component, ComponentRef):
            _reject('invalid-file-reference')
        try:
            normalized = files._relative(self.path)
        except (TypeError, ValueError) as error:
            raise files.ImportRejected('resource-package-invalid-file-path') from error
        if normalized != self.path:
            _reject('invalid-file-path')


class PackageView:
    """A read-only view rooted at one declared component's actual data tree."""

    def __init__(self, resource_root, component_ref, *, assert_current=None):
        if not isinstance(component_ref, ComponentRef):
            _reject('invalid-component-reference')
        self.resource_root = Path(os.path.abspath(resource_root))
        self.component_ref = component_ref
        self._manifest_identity = None
        self._assert_current = assert_current or (lambda: None)

    def _package_root(self):
        root = self.resource_root
        files._no_links(root)
        if not root.is_dir():
            _reject('resource-root-unavailable')
        relative = ('packages/' + self.component_ref.package_id + '/'
                    + self.component_ref.resource_version)
        package_root = files._target(root, relative)
        if not package_root.is_dir():
            _reject('package-unavailable')
        return package_root

    def _read_manifest_bytes(self, package_root):
        manifest_path = files._target(package_root, MANIFEST_NAME)
        try:
            raw, stamp = resource_snapshot_io.read_stable_file(
                manifest_path, MANIFEST_LIMIT, no_links=files._no_links,
                reject=lambda reason: _reject('manifest-' + reason))
        except OSError as error:
            raise files.ImportRejected('resource-package-manifest-unavailable') from error
        digest = hashlib.sha256(raw).hexdigest()
        if digest != self.component_ref.manifest_sha256:
            _reject('manifest-changed')
        if self._manifest_identity is None:
            self._manifest_identity = stamp['identity']
        elif stamp['identity'] != self._manifest_identity:
            _reject('manifest-replaced')
        return raw, stamp, digest

    def _read_manifest(self, package_root):
        raw, stamp, digest = self._read_manifest_bytes(package_root)
        try:
            manifest = files._json(raw)
        except (TypeError, ValueError) as error:
            raise files.ImportRejected('resource-package-invalid-manifest') from error
        return manifest, stamp, digest

    def _load(self):
        self._assert_current()
        package_root = self._package_root()
        manifest, stamp, digest = self._read_manifest(package_root)
        ref = self.component_ref
        components, inventory = self._validate_manifest(manifest, ref)
        data_root = files._target(package_root, 'data')
        self._assert_current()
        return package_root, data_root, manifest, components, inventory, stamp, digest

    def validate_inventory(self):
        """Explicitly validate the entire immutable package tree before activation.

        Runtime reads intentionally verify their selected paths and bytes only;
        import/activation preflight owns this bounded full-inventory proof.
        """
        package_root, data_root, _, components, inventory, stamp, _ = self._load()
        self._validate_disk_tree(package_root, data_root, components, inventory)
        _, current_stamp, _ = self._read_manifest_bytes(package_root)
        if current_stamp['identity'] != stamp['identity']:
            _reject('manifest-replaced')
        self._assert_current()
        return True

    @staticmethod
    def _validate_manifest(manifest, ref):
        if (set(manifest) - _MANIFEST_FIELDS
                or not {'schema', 'formatVersion', 'packageId', 'resourceVersion',
                        'clientCompatibility', 'components', 'files'} <= set(manifest)):
            _reject('invalid-manifest-fields')
        if (manifest['schema'] != SCHEMA or type(manifest['formatVersion']) is not int
                or manifest['formatVersion'] != FORMAT_VERSION):
            _reject('unsupported-manifest-format')
        package_id = _segment(manifest['packageId'], 'package-id')
        version = _segment(manifest['resourceVersion'], 'resource-version')
        if package_id != ref.package_id or version != ref.resource_version:
            _reject('package-identity-mismatch')
        compatibility = manifest['clientCompatibility']
        if (not isinstance(compatibility, dict)
                or set(compatibility) != {'resourceProtocol', 'requiredCapabilities'}
                or type(compatibility['resourceProtocol']) is not int
                or compatibility['resourceProtocol'] != 1):
            _reject('invalid-client-compatibility')
        capabilities = compatibility['requiredCapabilities']
        if (not isinstance(capabilities, list)
                or any(not isinstance(capability, str) or not capability
                       for capability in capabilities)
                or len(capabilities) != len(set(capabilities))):
            _reject('invalid-client-compatibility')
        if 'provenance' in manifest and not isinstance(manifest['provenance'], dict):
            _reject('invalid-provenance')
        if 'displayName' in manifest and not generic._label(manifest['displayName']):
            _reject('invalid-display-name')

        rows = manifest['components']
        if not isinstance(rows, list) or not rows or len(rows) > files.MAX_FILES:
            _reject('invalid-components')
        components = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != _COMPONENT_FIELDS:
                _reject('invalid-component')
            component_id = _segment(row['componentId'], 'component-id')
            if component_id.casefold() in {key.casefold() for key in components}:
                _reject('duplicate-component-id')
            if not isinstance(row['type'], str) or row['type'] not in COMPONENT_TYPES:
                _reject('unknown-component-type')
            if type(row['schemaVersion']) is not int or row['schemaVersion'] != 1:
                _reject('unsupported-component-schema')
            contexts = row['contexts']
            context_keys = []
            if not isinstance(contexts, list) or not contexts:
                _reject('invalid-component-contexts')
            for context in contexts:
                if (not isinstance(context, dict) or set(context) != {'season', 'mode'}
                        or not isinstance(context['season'], str)
                        or not isinstance(context['mode'], str)):
                    _reject('invalid-component-contexts')
                key = f"{context['season']}:{context['mode']}"
                if key not in CONTEXTS or key in context_keys:
                    _reject('invalid-component-contexts')
                context_keys.append(key)
            try:
                entrypoint = files._relative(row['entrypoint'])
            except (TypeError, ValueError) as error:
                raise files.ImportRejected('resource-package-invalid-entrypoint') from error
            if entrypoint != row['entrypoint']:
                _reject('invalid-entrypoint')
            if not isinstance(row['dependencies'], dict):
                _reject('invalid-dependencies')
            components[component_id] = dict(row, entrypoint=entrypoint)

        if ref.component_id not in components:
            _reject('component-identity-mismatch')

        file_rows = manifest['files']
        if not isinstance(file_rows, list) or not file_rows or len(file_rows) > files.MAX_FILES:
            _reject('invalid-file-inventory')
        inventory = {}
        folded_paths = set()
        total_bytes = 0
        for row in file_rows:
            if not isinstance(row, dict) or set(row) != _FILE_FIELDS:
                _reject('invalid-file-row')
            component_id = row['componentId']
            if not isinstance(component_id, str) or component_id not in components:
                _reject('unknown-file-component')
            try:
                path = files._relative(row['path'])
            except (TypeError, ValueError) as error:
                raise files.ImportRejected('resource-package-invalid-file-path') from error
            if path != row['path']:
                _reject('invalid-file-path')
            folded = (component_id.casefold(), path.casefold())
            if folded in folded_paths:
                _reject('duplicate-file-path')
            folded_paths.add(folded)
            size = row['bytes']
            if type(size) is not int or size < 0 or size > files.MAX_FILE_BYTES:
                _reject('invalid-file-size')
            total_bytes += size
            if total_bytes > files.MAX_TOTAL_BYTES:
                _reject('total-size-limit')
            _digest(row['sha256'], 'file-digest')
            inventory[(component_id, path)] = dict(row)

        for component_id, component in components.items():
            if (component_id, component['entrypoint']) not in inventory:
                _reject('entrypoint-not-in-inventory')
            seen_aliases = set()
            for alias, dependency in component['dependencies'].items():
                _segment(alias, 'dependency-alias')
                folded_alias = alias.casefold()
                if folded_alias in seen_aliases:
                    _reject('duplicate-dependency-alias')
                seen_aliases.add(folded_alias)
                if not isinstance(dependency, dict):
                    _reject('invalid-dependency-reference')
                if set(dependency) == {'componentId'}:
                    dependency_id = _segment(dependency['componentId'], 'component-id')
                    if dependency_id not in components:
                        _reject('unknown-local-dependency')
                elif set(dependency) == {'packageId', 'resourceVersion', 'componentId', 'manifestSha256'}:
                    ComponentRef(
                        dependency['packageId'], dependency['resourceVersion'],
                        dependency['componentId'], dependency['manifestSha256'])
                else:
                    _reject('invalid-dependency-reference')
        return components, inventory

    @staticmethod
    def _validate_disk_tree(package_root, data_root, components, inventory):
        files._no_links(package_root)
        root_entries = {}
        try:
            with os.scandir(package_root) as entries:
                for entry in entries:
                    if entry.name in root_entries or len(root_entries) >= 2:
                        _reject('unexpected-package-entry')
                    root_entries[entry.name] = Path(entry.path)
        except OSError as error:
            raise files.ImportRejected('resource-package-package-unavailable') from error
        if set(root_entries) != {MANIFEST_NAME, 'data'}:
            _reject('unexpected-package-entry')
        manifest_info = root_entries[MANIFEST_NAME].lstat()
        data_info = root_entries['data'].lstat()
        if not stat.S_ISREG(manifest_info.st_mode) or not stat.S_ISDIR(data_info.st_mode):
            _reject('invalid-package-layout')

        declared_component_ids = set(components)
        actual_files = set()
        allowed_directories = set()
        for component_id, path in inventory:
            parts = (component_id, *path.split('/'))
            allowed_directories.update(tuple(parts[:index]) for index in range(1, len(parts)))

        def walk(directory, relative_parts):
            with os.scandir(directory) as children:
                for entry in children:
                    child = Path(entry.path)
                    files._no_links(child)
                    info = child.lstat()
                    relative = '/'.join((*relative_parts, entry.name))
                    try:
                        normalized = files._relative(relative)
                    except (TypeError, ValueError) as error:
                        raise files.ImportRejected('resource-package-unsafe-package-path') from error
                    if normalized != relative:
                        _reject('unsafe-package-path')
                    key_parts = (*relative_parts, entry.name)
                    if stat.S_ISDIR(info.st_mode):
                        if not relative_parts and entry.name not in declared_component_ids:
                            _reject('unknown-component-directory')
                        if key_parts not in allowed_directories:
                            _reject('undeclared-directory')
                        walk(child, key_parts)
                    elif stat.S_ISREG(info.st_mode):
                        component_id, separator, path = relative.partition('/')
                        if not separator or component_id not in declared_component_ids:
                            _reject('unknown-component-file')
                        key = (component_id, path)
                        row = inventory.get(key)
                        if row is None:
                            _reject('undeclared-file')
                        if info.st_size != row['bytes']:
                            _reject('file-size-mismatch')
                        try:
                            raw, _ = resource_snapshot_io.read_stable_file(
                                child, files.MAX_FILE_BYTES, no_links=files._no_links,
                                reject=lambda reason: _reject('file-' + reason))
                        except OSError as error:
                            raise files.ImportRejected('resource-package-file-unavailable') from error
                        if (len(raw) != row['bytes']
                                or hashlib.sha256(raw).hexdigest() != row['sha256']):
                            _reject('file-integrity-mismatch')
                        actual_files.add(key)
                    else:
                        _reject('non-regular-package-entry')

        walk(data_root, ())
        if actual_files != set(inventory):
            _reject('incomplete-file-inventory')

    @property
    def root(self):
        _, data_root, _, _, _, _, _ = self._load()
        component_root = files._target(data_root, self.component_ref.component_id)
        if not component_root.is_dir():
            _reject('component-root-unavailable')
        self._assert_current()
        return component_root

    @property
    def manifest(self):
        return self._load()[2]

    @property
    def component(self):
        components = self._load()[3]
        return components[self.component_ref.component_id]

    @property
    def inventory(self):
        inventory = self._load()[4]
        component_id = self.component_ref.component_id
        return {path: dict(row) for (owner, path), row in inventory.items()
                if owner == component_id}

    @property
    def dependencies(self):
        """Return declared dependency aliases as complete immutable references."""
        component = self.component
        resolved = {}
        for alias, dependency in component['dependencies'].items():
            if set(dependency) == {'componentId'}:
                resolved[alias] = ComponentRef(
                    self.component_ref.package_id,
                    self.component_ref.resource_version,
                    dependency['componentId'], self.component_ref.manifest_sha256)
            else:
                resolved[alias] = ComponentRef(
                    dependency['packageId'], dependency['resourceVersion'],
                    dependency['componentId'], dependency['manifestSha256'])
        return MappingProxyType(resolved)

    def path(self, relative):
        package_root, _, _, _, inventory, _, _ = self._load()
        try:
            normalized = files._relative(relative)
        except (TypeError, ValueError) as error:
            raise files.ImportRejected('resource-package-invalid-file-path') from error
        if normalized != relative:
            _reject('invalid-file-path')
        key = (self.component_ref.component_id, relative)
        if key not in inventory:
            _reject('file-not-declared')
        target = files._target(package_root,
                               'data/' + self.component_ref.component_id + '/' + relative)
        try:
            info = target.lstat()
        except OSError as error:
            raise files.ImportRejected('resource-package-file-unavailable') from error
        if not stat.S_ISREG(info.st_mode):
            _reject('non-regular-package-file')
        self._assert_current()
        return target

    def file_ref(self, relative):
        self.path(relative)
        return FileRef(self.component_ref, relative)

    def read(self, relative):
        package_root, _, _, _, inventory, stamp, digest = self._load()
        try:
            normalized = files._relative(relative)
        except (TypeError, ValueError) as error:
            raise files.ImportRejected('resource-package-invalid-file-path') from error
        if normalized != relative:
            _reject('invalid-file-path')
        row = inventory.get((self.component_ref.component_id, relative))
        if row is None:
            _reject('file-not-declared')
        target = files._target(package_root,
                               'data/' + self.component_ref.component_id + '/' + relative)
        try:
            info = target.lstat()
        except OSError as error:
            raise files.ImportRejected('resource-package-file-unavailable') from error
        if not stat.S_ISREG(info.st_mode):
            _reject('non-regular-package-file')
        try:
            raw, _ = resource_snapshot_io.read_stable_file(
                target, files.MAX_FILE_BYTES, no_links=files._no_links,
                reject=lambda reason: _reject('file-' + reason))
        except OSError as error:
            raise files.ImportRejected('resource-package-file-unavailable') from error
        if len(raw) != row['bytes'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
            _reject('file-integrity-mismatch')
        # Compare both contents and stable-file identity after the payload read.
        _, current_stamp, current_digest = self._read_manifest_bytes(package_root)
        if current_digest != digest or current_stamp['identity'] != stamp['identity']:
            _reject('manifest-replaced')
        self._assert_current()
        return raw
