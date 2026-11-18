"""Read-only views over installed versioned recognition resource packages.

This validates manifest identity, namespace and selected file reads. Full disk
inventory validation is explicit at import/activation preflight; neither path
establishes component semantic usability by an algorithm.
"""
from __future__ import annotations

from copy import deepcopy
from contextvars import ContextVar
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


_read_operations = ContextVar('imported_resource_read_operations', default=())


MANIFEST_NAME = 'manifest.json'
MANIFEST_LIMIT = 4 * 1024 * 1024
SCHEMA = 'xingtu-resource-package'
FORMAT_VERSION = 1
CONTROL_BANK_FORMAT_VERSION = 2
SUPPORTED_FORMAT_VERSIONS = frozenset({FORMAT_VERSION, CONTROL_BANK_FORMAT_VERSION})
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
    'clientCompatibility', 'components', 'files', 'provenance', 'displayName', 'bankId', 'generatorVersion',
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
        self._manifest_cache = None
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

    def _load(self):
        operation = self._read_operation()
        if operation is not None:
            operation._assert_owners()
            return operation._loaded
        return self._load_actual()

    def _load_actual(self):
        self._assert_current()
        package_root = self._package_root()
        # Read and verify the actual file on every access. Only decoding the
        # same already validated declarations is reusable; replacement, byte
        # changes and a superseded selection must never hit this cache.
        raw, stamp, digest = self._read_manifest_bytes(package_root)
        ref = self.component_ref
        cached = self._manifest_cache
        if cached is None or cached[0] != ref:
            try:
                manifest = files._json(raw)
            except (TypeError, ValueError) as error:
                raise files.ImportRejected('resource-package-invalid-manifest') from error
            components, inventory = self._validate_manifest(manifest, ref)
            cached = (ref, manifest, components, inventory)
            self._manifest_cache = cached
        _, manifest, components, inventory = cached
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
                or manifest['formatVersion'] not in SUPPORTED_FORMAT_VERSIONS):
            _reject('unsupported-manifest-format')
        bank_fields = {'bankId', 'generatorVersion'}
        if manifest['formatVersion'] == CONTROL_BANK_FORMAT_VERSION:
            if (not bank_fields <= set(manifest)
                    or not isinstance(manifest['bankId'], str)
                    or not re.fullmatch('[a-f0-9]{32}', manifest['bankId'])
                    or not isinstance(manifest['generatorVersion'], str)
                    or not re.fullmatch('[a-z0-9][a-z0-9.-]{0,63}', manifest['generatorVersion'])):
                _reject('invalid-bank-identity')
        elif bank_fields & set(manifest):
            _reject('invalid-bank-identity')
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
        operation = self._read_operation()
        if operation is not None:
            return operation.root
        _, data_root, _, _, _, _, _ = self._load()
        component_root = self._component_root(data_root, self.component_ref)
        self._assert_current()
        return component_root

    @staticmethod
    def _component_root(data_root, ref):
        component_root = files._target(data_root, ref.component_id)
        if not component_root.is_dir():
            _reject('component-root-unavailable')
        return component_root

    @property
    def manifest(self):
        return deepcopy(self._load()[2])

    @property
    def component(self):
        components = self._load()[3]
        return deepcopy(components[self.component_ref.component_id])

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
        operation = self._read_operation()
        if operation is not None:
            return operation.path(relative)
        package_root, _, _, _, inventory, _, _ = self._load()
        target = self._selected_path(package_root, inventory, self.component_ref, relative)
        self._assert_current()
        return target

    @staticmethod
    def _selected_path(package_root, inventory, ref, relative):
        try:
            normalized = files._relative(relative)
        except (TypeError, ValueError) as error:
            raise files.ImportRejected('resource-package-invalid-file-path') from error
        if normalized != relative:
            _reject('invalid-file-path')
        key = (ref.component_id, relative)
        if key not in inventory:
            _reject('file-not-declared')
        target = files._target(package_root,
                               'data/' + ref.component_id + '/' + relative)
        try:
            info = target.lstat()
        except OSError as error:
            raise files.ImportRejected('resource-package-file-unavailable') from error
        if not stat.S_ISREG(info.st_mode):
            _reject('non-regular-package-file')
        return target

    def admission_read_scope(self):
        """Create a private construction scope; it cannot authorize live reads."""
        return _AdmissionReadScope(self)

    def _read_operation(self):
        return next((operation for operation in reversed(_read_operations.get())
                     if operation.owner_view is self), None)

    def read_scope(self, cancelled=None):
        """Reuse one actual read proof, including ordinary live consumers.

        Only this operation's consumed payloads are retained and checked again
        before return. Models keep their actual PackageView after the scope.
        """
        return _PackageReadOperation(self, cancelled)

    def file_ref(self, relative):
        self.path(relative)
        return FileRef(self.component_ref, relative)

    def read(self, relative):
        return self.read_many((relative,))[0]

    def read_many(self, relatives, *, expected_root=None, cancelled=None, validate_payloads=None,
                  on_payload=None):
        """Read selected payloads under one current component/manifest scope.

        Each payload retains its stable-file and declared byte checks. Nothing
        is returned until the manifest identity and selection are checked again.
        Payload errors retain their type and name their resource_file_ref so
        consumers can distinguish required inputs from optional preparations.
        Content preflight runs inside this proof; an owning source may also
        pin its component root without reloading the manifest afterwards.
        on_payload may stage each actual payload locally; the final proof has
        not run yet. Its own exceptions are not attributed to a resource file.
        """
        operation = self._read_operation()
        if operation is not None:
            return operation.read_many(relatives, expected_root=expected_root, cancelled=cancelled,
                validate_payloads=validate_payloads, on_payload=on_payload)
        files._check_cancelled(cancelled)
        expected_root = Path(expected_root) if expected_root is not None else None
        ref, resource_root = self.component_ref, self.resource_root
        package_root, data_root, _, _, inventory, stamp, digest = self._load()
        if expected_root is not None and self._component_root(data_root, ref) != expected_root:
            _reject('component-root-changed')
        result = []
        for relative in relatives:
            file_ref = FileRef(ref, relative)
            try:
                raw = self._read_payload(package_root, inventory, ref.component_id, relative)
            except (OSError, ValueError) as error:
                error.resource_file_ref = file_ref
                raise
            result.append(raw)
            if on_payload is not None:
                on_payload(file_ref, raw)
        if validate_payloads is not None:
            validate_payloads(tuple(result))
        # Compare both contents and stable-file identity after all payloads.
        _, current_stamp, current_digest = self._read_manifest_bytes(package_root)
        if current_digest != digest or current_stamp['identity'] != stamp['identity']:
            _reject('manifest-replaced')
        if expected_root is not None and (self.resource_root != resource_root
                or self._component_root(data_root, ref) != expected_root):
            _reject('component-root-changed')
        self._assert_current()
        files._check_cancelled(cancelled)
        if self.component_ref != ref:
            _reject('component-reference-changed')
        return tuple(result)

    @staticmethod
    def _read_payload(package_root, inventory, component_id, relative):
        row = inventory.get((component_id, relative))
        if row is None:
            _reject('file-not-declared')
        target = files._target(package_root,
                               'data/' + component_id + '/' + relative)
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
        return raw


class _AdmissionReadScope:
    """Reuse declarations only while an unpublished native model is built.

    Every payload still enters the original stable-file/integrity reader. A
    successful construction rereads every consumed FileRef before one final
    actual manifest/root/catalog proof. No filesystem verdict survives exit.
    """
    def __init__(self, view):
        self.owner_view = view
        self._active = False
        self._owners = []
        self._consumed = {}

    def _view_state(self):
        view = self.owner_view
        return id(view), view.resource_root, view.component_ref, id(view._assert_current)

    def __enter__(self):
        self._state = self._view_state()
        loaded = self.owner_view._load_actual()
        self._loaded = loaded
        (self._package_root, self._data_root, _, self._components,
         self._inventory, self._stamp, self._digest) = loaded
        self._ref = self._state[2]
        # Component semantics precede the first root access in the original
        # decoder. Do not turn a later missing root into an earlier error.
        self._root = None
        self._active = True
        try:
            self._assert_owners()
        except BaseException:
            self._active = False
            raise
        return self

    def _assert_owners(self):
        if not self._active:
            _reject('read-scope-closed')
        state = self._view_state()
        if state[2] != self._ref:
            _reject('component-reference-changed')
        if state != self._state or any(read_state() != initial for read_state, initial in self._owners):
            _reject('component-root-changed')

    def register_owner(self, read_state):
        self._assert_owners()
        if any(owner is read_state for owner, _ in self._owners):
            return
        self._owners.append((read_state, read_state()))

    def record_consumed(self, file_ref):
        """Include a successful ordinary live read in the final byte proof."""
        self._assert_owners()
        if file_ref.component != self._ref:
            _reject('component-reference-changed')
        self._consumed[file_ref] = None

    @property
    def component_ref(self):
        self._assert_owners()
        return self._ref

    @property
    def root(self):
        self._assert_owners()
        if self._root is None:
            self._root = self.owner_view._component_root(self._data_root, self._ref)
        return self._root

    @property
    def component(self):
        self._assert_owners()
        return deepcopy(self._components[self._ref.component_id])

    @property
    def inventory(self):
        self._assert_owners()
        return {path: dict(row) for (owner, path), row in self._inventory.items()
                if owner == self._ref.component_id}

    def path(self, relative):
        self._assert_owners()
        return self.owner_view._selected_path(self._package_root, self._inventory, self._ref, relative)

    def read(self, relative):
        return self.read_many((relative,))[0]

    def _read_payload(self, file_ref):
        try:
            return self.owner_view._read_payload(
                self._package_root, self._inventory, file_ref.component.component_id, file_ref.path)
        except (OSError, ValueError) as error:
            error.resource_file_ref = file_ref
            raise

    def read_many(self, relatives, *, expected_root=None, cancelled=None, validate_payloads=None,
                  on_payload=None):
        self._assert_owners()
        files._check_cancelled(cancelled)
        if expected_root is not None and Path(expected_root) != self.root:
            _reject('component-root-changed')
        result = []
        for relative in relatives:
            file_ref = FileRef(self._ref, relative)
            raw = self._read_payload(file_ref)
            self._consumed[file_ref] = None
            result.append(raw)
            if on_payload is not None:
                on_payload(file_ref, raw)
        if validate_payloads is not None:
            validate_payloads(tuple(result))
        self._assert_owners()
        files._check_cancelled(cancelled)
        return tuple(result)

    def _finish(self):
        self._assert_owners()
        for file_ref in self._consumed:
            # Final proof must enter the real reader, including for a live
            # operation which reused the first immutable bytes internally.
            _AdmissionReadScope._read_payload(self, file_ref)
        self._assert_owners()
        loaded = self.owner_view._load_actual()
        package_root, data_root, _, _, _, stamp, digest = loaded
        if stamp['identity'] != self._stamp['identity'] or digest != self._digest:
            _reject('manifest-replaced')
        if (package_root != self._package_root
                or self.owner_view._component_root(data_root, self._ref) != self.root):
            _reject('component-root-changed')
        self._assert_owners()

    def verify(self):
        """Force the real consumed-file and manifest proof before publication."""
        self._finish()

    def __exit__(self, kind, error, traceback):
        try:
            # Preserve the decoder's original first error and classification.
            if kind is None:
                self._finish()
        finally:
            self._active = False
            self._owners.clear()
            self._consumed.clear()
        return False


class _PackageReadOperation(_AdmissionReadScope):
    """The existing bounded read proof, applied to one live operation.

    No filesystem verdict survives exit. Nested consumers share this proof;
    every newly consumed file still uses the original reader, and final checks
    reject changes while the immutable bytes were being consumed.
    """
    def __init__(self, view, cancelled):
        super().__init__(view)
        self._cancelled = (cancelled.is_set if hasattr(cancelled, 'is_set') else cancelled)
        self._parent = None
        self._discarded = False
        self._payloads, self._paths = {}, {}

    def __enter__(self):
        files._check_cancelled(self._cancelled)
        self._parent = self.owner_view._read_operation()
        if self._parent is not None:
            self._parent._assert_owners()
            return self._parent
        super().__enter__()
        self._token = _read_operations.set((*_read_operations.get(), self))
        return self

    def _assert_owners(self):
        files._check_cancelled(self._cancelled)
        if self._discarded:
            _reject('optional-read-discarded')
        super()._assert_owners()

    def discard_optional(self):
        """A rejected author candidate cannot contaminate structural fallback."""
        if self._components[self._ref.component_id]['type'] != 'display':
            _reject('required-read-cannot-be-discarded')
        self._discarded = True

    def path(self, relative):
        self._assert_owners()
        if not isinstance(relative, str):
            return super().path(relative)
        if relative not in self._paths:
            self._paths[relative] = super().path(relative)
        return self._paths[relative]

    def _read_payload(self, file_ref):
        self._assert_owners()
        if file_ref not in self._payloads:
            self._payloads[file_ref] = super()._read_payload(file_ref)
        return self._payloads[file_ref]

    def __exit__(self, kind, error, traceback):
        if self._parent is not None:
            if kind is None:
                files._check_cancelled(self._cancelled)
                self._parent._assert_owners()
            return False
        try:
            if self._discarded:
                self._active = False
                self._owners.clear()
                self._consumed.clear()
                return False
            return super().__exit__(kind, error, traceback)
        finally:
            _read_operations.reset(self._token)
            self._payloads.clear()
            self._paths.clear()
