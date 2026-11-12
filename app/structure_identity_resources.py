"""Whole-pool texture-footprint inputs from an admitted native base bundle.

This is static resource work, not frame work. It reads saved preparations and
never calls prepare, extracts pixels, fits a pose or upgrades old capabilities.
Geometry completeness describes the declared footprint catalogue only; source
equivalence, current observations, independent B and identity remain unqualified.
Semantic known/unknown channels are neither required nor manufactured for this
separate feature class. Legacy working drafts remain explicit competitors/gaps.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from types import SimpleNamespace

from imported_native_base import NativeBaseBundle
from imported_resource_packages import ComponentRef, FileRef
from structure_identity_index import (DirectoryEntry, MapFloorKey,
                                      StructureIdentityIndex, anchors_from_arrays)
import star_map_import as files


FEATURE_CLASS = "texture-footprint"
IDENTITY_ISSUES = (
    "footprint-source-observation-equivalence-unverified",
    "source-independent-observation-qualification-unverified",
    "independent-heldout-b-unverified",
    "shared-current-frame-residual-and-identity-qualification-required",
)


@dataclass(frozen=True)
class FootprintSourceInfo:
    key: MapFloorKey
    reference_resource: FileRef | None
    prepared_resource: FileRef | None
    source_kind: str | None
    source_class: str | None
    source_version: str | None
    structure_version_key: str | None
    base_snapshot: str | None
    known_boundary_available: bool
    unknown_mask_available: bool
    original_authority: tuple
    qualification_issues: tuple[str, ...]
    geometry_searchable: bool


@dataclass(frozen=True)
class _ResourceRead:
    reference_resource: FileRef
    binding: tuple
    # The loader's last identity element is id(reference), a cache-owner ID.
    # Retain its actual root/FileRefs, not that ephemeral owner or mask atlas.
    prepared_identity: tuple | None
    uses_prepared_source: bool


def footprint_anchors(prepared):
    """Stable prepared-row IDs; exact physical centres shared across rows.

    No coordinate quantization or proximity merge is used. IDs are scoped by
    the actual reference/prepared resource binding in each DirectoryEntry.
    """
    centres, centre_ids = {}, []
    for chain in prepared.chains:
        point = tuple(float(value) for value in chain[1])
        centre_ids.append(centres.setdefault(point, len(centres)))
    return anchors_from_arrays(prepared.chains, prepared.rays,
        feature_ids=range(len(prepared.chains)), center_ids=centre_ids)


def _binding_text(reference_resource, prepared_resource, metadata):
    # Serialize existing identities; introduce no digest or new identity scheme.
    return json.dumps(dict(reference=asdict(reference_resource),
        prepared=asdict(prepared_resource) if prepared_resource is not None else None,
        binding=metadata.get("binding"),
        **{name: metadata.get(name) for name in
           ("structureVersionKey", "sourceVersion", "baseSnapshot", "canvas")}),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class StructureIdentityResources:
    """Immutable geometry plus explicit qualification and resource lifetime.

    assert_current checks the owning component/catalog. check_current additionally
    re-reads the actual declarations and payloads through their original readers,
    as a resource-operation boundary. It is intentionally not hidden in index
    retrieval or a per-frame full-catalogue read. Final pose consumers must keep
    their existing current-resource checks. No full mask/distance atlas is held.
    """

    index: StructureIdentityIndex
    component: ComponentRef
    sources: tuple[FootprintSourceInfo, ...]
    original_rank_ready: bool
    identity_issues: tuple[str, ...] = IDENTITY_ISSUES
    feature_class: str = field(default=FEATURE_CLASS, init=False)
    authorizesIdentity: bool = field(default=False, init=False)
    lock: bool = field(default=False, init=False)
    _bundle: object = field(default=None, repr=False, compare=False)
    _source: object = field(default=None, repr=False, compare=False)
    _reader: object = field(default=None, repr=False, compare=False)
    _root: Path | None = field(default=None, repr=False, compare=False)
    _declarations: tuple[Path, ...] = field(default=(), repr=False, compare=False)
    _reads: tuple[_ResourceRead, ...] = field(default=(), repr=False, compare=False)

    @property
    def geometry_complete(self):
        return not self.index.directory_gaps

    def assert_current(self):
        self._bundle.assert_current()
        if (self._bundle.component_ref != self.component
                or self._bundle.fast_structure_source is not self._source
                or self._bundle.reader is not self._reader):
            raise ValueError("structure-identity-resource-owner-changed")
        self._source.assert_current()

    def check_current(self, *, cancelled=None):
        self.assert_current()
        required, lookups, prepared_reads = list(self._declarations), [], []
        for read in self._reads:
            files._check_cancelled(cancelled)
            path = self._root / read.reference_resource.path
            if not read.uses_prepared_source:
                required.append(path)
                continue
            # ImportedFastStructureSource.read consumes only this typed lookup
            # header, and still verifies its real index/reference/prepared bytes.
            # A fresh lookup avoids pinning all decoded source masks in memory.
            lookups.append(SimpleNamespace(path=path, metadata=dict(binding=dict(read.binding))))
            prepared_reads.append(read)
        payloads = self._source.read_many(lookups, required_paths=tuple(dict.fromkeys(required)),
                                          cancelled=cancelled)
        for read, payload in zip(prepared_reads, payloads):
            identity = payload[1][:3] if payload is not None else None
            if identity != read.prepared_identity:
                raise ValueError("structure-identity-prepared-resource-changed")
        files._check_cancelled(cancelled)
        self.assert_current()


def build_structure_identity_resources(bundle, provider, *, floor=None, cancelled=None):
    """Read the complete declared map pool, optionally restricted to one floor.

    The existing bundle/producer owns typed admission and preparation derivation.
    This adapter consumes their saved rows without treating a footprint as a wall
    or treating a complete geometry index as formal identity qualification.
    """
    if not isinstance(bundle, NativeBaseBundle) or not callable(provider):
        raise TypeError("structure-identity-native-bundle-and-provider-required")
    view, root, component, reader = bundle.read_binding()
    pack, source = bundle.pack, bundle.fast_structure_source
    floors = tuple(str(value) for value in pack.floors)
    if floor is not None:
        floor = str(floor)
        if floor not in floors:
            raise ValueError("structure-identity-floor-not-declared")
        floors = (floor,)
    mode = f"{bundle.season}:{bundle.mode}"
    # Build domain before considering provider/prepared availability. In a
    # same-floor query every declared map still competes, including absent layers.
    domain = tuple(MapFloorKey(mode, item.id, fl, "variant-01")
                   for item in pack.maps for fl in floors)
    # Season/mode/runtimeInterfaces came from this owning declaration, not
    # from the manifest alone. Recheck its actual bytes with the other inputs.
    entrypoint = FileRef(component, view.component["entrypoint"])
    declarations = (view.path(entrypoint.path), view.path(bundle.interfaces["identityPack"]),
                    bundle.resources.index_path)
    batch = None
    batch_provider = (callable(getattr(provider, "batch_bindings", None))
        and callable(getattr(provider, "read_with_resource", None))
        and getattr(provider, "resources", None) is bundle.resources
        and getattr(provider, "versions", None) is bundle.versions)
    provider_state = tuple(getattr(provider, name, None) for name in
        ("root", "resources", "versions", "_legacy_reference_loader", "_check_current"))
    if batch_provider:
        requests = tuple((pack.identity_profiles[item.id], item.id, fl)
            for item in pack.maps for fl in floors
            if pack.identity_profiles.get(item.id) and item.layers.get(fl) is not None)
        batch = source.prepare_batch(provider.batch_bindings(requests),
                                     required_paths=declarations, cancelled=cancelled)
    if batch is None:
        reader.read_many(declarations, expected_root=root, cancelled=cancelled)
    entries, infos, reads = [], [], []
    for item in pack.maps:
        for fl in floors:
            files._check_cancelled(cancelled)
            bundle.assert_current()
            key = MapFloorKey(mode, item.id, fl, "variant-01")
            profile = pack.identity_profiles.get(item.id)
            reference = None
            reason = None
            if not profile:
                reason = "identity-profile-unavailable"
            elif item.layers.get(fl) is None:
                reason = "declared-native-layer-unavailable"
            else:
                reference = (provider.read_with_resource(profile, item.id, fl,
                    read_resource=batch.read_resource) if batch is not None
                    else provider(profile, item.id, fl))
                if reference is None:
                    reason = "typed-footprint-reference-unavailable"
            metadata = reference.metadata if reference is not None else {}
            expected_binding = dict(profile=profile, mapId=item.id, floor=fl, variant="variant-01")
            ref_file = (FileRef(component, reader.relative_path(reference.path))
                        if reference is not None else None)
            prepared_file, anchors = None, None
            native = (reference is not None and metadata.get("sourceKind") == "game-rendered-shared-structure"
                      and (metadata.get("roles") or {}).get("clean") == "shared_mask"
                      and (metadata.get("roles") or {}).get("validation") == "shared_mask")
            if reference is not None:
                if metadata.get("binding") != expected_binding:
                    reason = "typed-footprint-binding-mismatch"
                elif not native:
                    reason = "source-is-not-native-texture-footprint"
                elif metadata.get("authorPixelsUsedForStructure") is True:
                    reason = "author-pixels-not-footprint-source"
                if reason is not None:
                    reads.append(_ResourceRead(ref_file, (), None, False))
                else:
                    declaration = source.entries.get((profile, item.id, "variant-01", fl))
                    if declaration is not None:
                        declared_reference, prepared_file = declaration
                        if declared_reference != ref_file:
                            raise ValueError("structure-identity-prepared-reference-mismatch")
                    if batch is not None and not callable(getattr(batch, "acquire_prepared_use", None)):
                        payload = batch.read(reference)
                        prepared = None
                        if payload is not None:
                            try:
                                # Keep all six original fields/CRC checks, then
                                # release masks/distance after the small anchors.
                                prepared = source.decode(payload[0], reference)
                            except (OSError, ValueError):
                                pass  # Optional semantic damage is still a gap.
                    else:
                        loader = batch if batch is not None else source
                        use = loader.acquire_prepared_use(reference, cancelled=cancelled)
                        if use.reference is not reference or use.source is not loader:
                            raise ValueError("structure-identity-preparation-owner-changed")
                        payload = loader.read(reference, cancelled=cancelled)
                        if use.prepared is not None and (payload is None or payload[1] != use.identity):
                            raise ValueError("structure-identity-preparation-changed")
                        prepared = use.prepared
                    identity = payload[1][:3] if payload is not None else None
                    reads.append(_ResourceRead(ref_file, tuple(expected_binding.items()), identity, True))
                    if prepared is None:
                        reason = "saved-footprint-preparation-unavailable"
                    else:
                        try:
                            anchors = footprint_anchors(prepared)
                        except ValueError:
                            reason = "saved-footprint-anchor-features-invalid"
                    del prepared
            issues = ["footprint-source-equivalence-not-independently-qualified"]
            geometry = metadata.get("sourceGeometry") or {}
            if geometry.get("rendererVerified") is False:
                issues.append("renderer-footprint-equivalence-unverified")
            if geometry.get("skippedRoomFloors"):
                issues.append("renderer-skipped-room-floors-unreviewed")
            if reason:
                issues.append(reason)
            searchable = reason is None
            entries.append(DirectoryEntry(key,
                _binding_text(ref_file, prepared_file, metadata) if ref_file is not None else None,
                anchors, frozenset(anchor.feature_id for anchor in anchors) if anchors is not None else None,
                searchable, searchable, reason))
            infos.append(FootprintSourceInfo(key, ref_file, prepared_file,
                metadata.get("sourceKind"), metadata.get("sourceClass"), metadata.get("sourceVersion"),
                metadata.get("structureVersionKey"), metadata.get("baseSnapshot"),
                reference is not None and reference.known_boundary is not None,
                reference is not None and reference.unknown_mask is not None,
                tuple((name, metadata[name]) for name in
                      ("authorizesIdentity", "authorizesMapIdentity", "authorizesStructureValidation", "staticAcceptance")
                      if name in metadata), tuple(issues), searchable))
    result = StructureIdentityResources(StructureIdentityIndex(domain, entries, directory_complete=True),
        component, tuple(infos), bundle.rank_ready, _bundle=bundle, _source=source,
        _reader=reader, _root=root, _declarations=declarations, _reads=tuple(reads))
    # The next proof reads the full compressed pool afresh. Do not retain the
    # first operation's full compressed bytes while that second batch is live.
    if batch is not None:
        batch.release()
    batch = loader = use = None
    result.check_current(cancelled=cancelled)
    if batch_provider and any(getattr(provider, name, None) is not value for name, value in zip(
            ("root", "resources", "versions", "_legacy_reference_loader", "_check_current"), provider_state)):
        raise ValueError("structure-identity-resource-owner-changed")
    return result
