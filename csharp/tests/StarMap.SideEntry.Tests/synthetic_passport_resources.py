"""New, explicitly synthetic typed geometry/passports; never changes a real resource root."""
import json
from pathlib import Path
import shutil

import cv2
import numpy as np


def create(root, real_icon, mode):
    """Write a complete one-map test inventory in production wire formats.

    This is a declared synthetic world, not inferred semantics for a real map.
    Source map/pose are used below to draw input pixels only; rank_entry gets
    the whole registered test inventory and no known map or pose selector.
    """
    from single_map_structure import contour_graph, visible_wall_segments
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    profile = "zhan10-speed" if mode == "nightmare" else "shared-structure"
    floor = "2" if mode == "nightmare" else "1"
    mid = "synthetic-" + mode + "-cross"
    entry_relative = "references/nightmare-entries" if mode == "nightmare" else "recognition/synthetic-entries"
    directory = root / entry_relative
    directory.mkdir(parents=True)
    refs = root / "references"
    refs.mkdir(exist_ok=True)
    icon_path = refs / "difficult-exit-icon.json"
    shutil.copy2(real_icon, icon_path)
    prefix = "nightmare" if mode == "nightmare" else "synthetic-difficult"
    index_relative = "references/" + prefix + "-shared-structure-index.json"
    pack_relative = "references/" + prefix + "-map-pack.json"
    display_relative = "references/" + prefix + "-structure-display.json"
    versions_relative = "references/" + prefix + "-structure-version-bindings.json"
    coordinates_relative = "references/" + prefix + "-canonical-coordinate-pack.json"
    product_relative = entry_relative + "/synthetic-cross.npz"
    version = dict(structureVersionKey="synthetic-cross/" + floor, sourceVersion="synthetic-fixture-v1", baseSnapshot="synthetic-fixture-v1")
    binding = dict(profile=profile, mapId=mid, floor=floor, variant="variant-01")
    canvas = dict(coordinateSpace="synthetic-cross-pixels", layout="synthetic-cross", floor=floor,
                  worldFloor=3 if floor == "2" else 2, width=1260, height=1200, shapeHW=[1200, 1260], axes="x-right,y-down")
    # A terminal hall with two genuinely connected rooms. Every semantic wall
    # and port below belongs to these newly drawn pixels, not a real map.
    mask = np.zeros((1200, 1260), np.uint8)
    polygon = np.array([[200, 450], [520, 450], [520, 250], [720, 250], [720, 450], [1040, 450],
                        [1040, 510], [720, 510], [720, 750], [520, 750], [520, 510], [200, 510]], np.int32)
    cv2.fillPoly(mask, [polygon], 1)
    nodes = contour_graph(mask)
    lines = visible_wall_segments(mask, nodes=nodes)
    turns = [dict(point=n["point"].tolist(), rays=n["rays"].tolist(), next=n["next"]) for n in nodes]
    center = np.array([1010., 480.]); unit = 60.
    # These semantic planes come from the newly authored room rectangle.
    # Its south edge is an open connection and is not declared as a wall.
    # Do not classify raster-fitted contour medians by exact integer equality:
    # concave one-pixel joins legitimately yield 519.5 and 720.5 medians.
    room_walls = [dict(axis=0, sign=1, normal=520., lo=250., hi=450.),
                  dict(axis=0, sign=-1, normal=720., lo=250., hi=450.),
                  dict(axis=1, sign=-1, normal=250., lo=520., hi=720.)]
    local_room = dict(instance="synthetic-upper-room", kind="room", material="synthetic-neutral-room", roomTypeId=90001, rotation=0,
        connections=["synthetic-exit", "synthetic-upper-room/south"], connectingPort="south", knownWallLines=room_walls,
        ports=[dict(name="south", points=((np.array([[600., 450.], [640., 450.]]) - center) / unit).tolist())])
    passport = dict(mapId=mid, floor=floor, region="synthetic-known-wall-world", sourceEntryId="synthetic-exit", family="side",
        directionKnown=True, outwardDirections=["E"], center=center.tolist(), unit=unit, sourceProduct="synthetic-cross.npz",
        knownWallLines=lines, localInstances=[local_room], optionalRooms=[])
    metadata = dict(sourceKind="game-rendered-shared-structure", binding=binding, canvas=canvas, roles=dict(clean="shared_mask", validation="shared_mask"),
        fixtureScope="Synthetic geometry in the typed renderer wire format; not extracted game data or independent validation evidence", **version)
    with (root / product_relative).open("xb") as target:
        np.savez_compressed(target, shared_mask=mask, metadata=np.asarray(json.dumps(metadata)))
    common = dict(schemaVersion=1, mode=mode, profileId=profile)
    def write(relative, value):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf8") as target:
            json.dump(value, target, ensure_ascii=False)
    write(entry_relative + "/entries.json", dict(common, entries=[]))
    write(entry_relative + "/side-entries.json", dict(common, semanticGaps=[], entries=[dict(mapId=mid, floor=floor, profileId=profile,
        region="synthetic-known-wall-world", unit=unit, turns=turns, gates=[], sourceProduct="synthetic-cross.npz", canvas=canvas)]))
    write(entry_relative + "/entry-passports.json", dict(common, entries=[passport], coverage=[dict(mapId=mid, floor=floor, complete=True,
        basis="Complete inventory of the newly synthesized one-exit test world only")]))
    write(pack_relative, dict(schemaVersion=1, mode=mode, packId="synthetic-passport-fixture", floors=[floor], maps=[dict(id=mid, name=mid,
        identityProfileId=profile, resourceBase="resource-root", layers={floor: product_relative}, sourceVersion=version["sourceVersion"], sourceLayout="synthetic-cross")]))
    write(index_relative, dict(schemaVersion=1, resourceKind="shared-structure", entries=[dict(binding, resourcePath=product_relative, **version)]))
    write(display_relative, dict(schemaVersion=1, resourceKind="typed-shared-structure-display", indexPath=index_relative,
        entries=[dict(mapId=mid, floor=floor, binding=binding, referenceResource=product_relative, canvas=canvas, **version)]))
    write(versions_relative, dict(schemaVersion=1, resourceKind="native-structure-version-bindings", entries=[dict(mapId=mid, floor=floor, **version)]))
    write(coordinates_relative, dict(maps={}))
    if mode == "difficult":
        write("difficult-native-manifest.json", dict(schemaVersion=1, mode=mode, runtimeInterfaces=dict(identityPack=pack_relative, identityEntries=entry_relative,
            typedIndex=index_relative, coordinatePack=coordinates_relative, neutralDisplay=display_relative, versionBindings=versions_relative,
            exitIcon="references/difficult-exit-icon.json")))
    write("synthetic-fixture.json", dict(scope="Synthetic typed structure and complete passport inventory; never production resource coverage",
        mode=mode, sourceProduct=product_relative, geometry="New cross-shaped hall and two connected rooms", copiedAsset="Current exit icon only",
        entryRoot=entry_relative, typedIndex=index_relative, mapId=mid, floor=floor, profile=profile))
    return dict(root=root, mode=mode, entryRoot=directory, index=root / index_relative, icon=icon_path, profile=profile, floor=floor,
                mapId=mid, mask=mask, passport=passport)


def locator(fixture):
    """Use original resource parsing, geometry, icon detection and all validators."""
    from difficult_side_entry_identity import MapLocalEntryIdentity
    from shared_structure_reference import SharedStructureResourceStore
    store = SharedStructureResourceStore(fixture["index"], fixture["root"])
    if store.error:
        raise ValueError(store.error)
    def provider(profile, mid, floor):
        return store.load(profile, mid, floor)
    return MapLocalEntryIdentity(fixture["entryRoot"], mode=fixture["mode"], profile_id=fixture["profile"],
        floors=(fixture["floor"],), exit_icon_path=fixture["icon"], structure_store=store, shared_reference_provider=provider,
        profile_for_map=lambda mid: fixture["profile"])
