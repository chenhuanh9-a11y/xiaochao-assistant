"""Offline, mode-neutral candidate neighborhoods. Never grants identity or pose."""
import argparse
import hashlib
import json
import math
from pathlib import Path

SCHEMA = "entry-candidates/1"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def within(root, relative):
    p = (root / relative).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError("resource path escapes root")
    return p


def neighborhoods(item, anchors, radii):
    unit = float(item["unit"])
    if not math.isfinite(unit) or unit <= 0:
        raise ValueError("invalid unit")
    turns = item["turns"]
    for t in turns:
        if len(t["point"]) != 2 or not all(math.isfinite(v) for v in t["point"]):
            raise ValueError("invalid contour point")
        if t["next"] < -1 or t["next"] >= len(turns):
            raise ValueError("invalid contour adjacency")
    result = []
    for anchor in anchors:
        center = anchor["center"]
        if len(center) != 2 or not all(math.isfinite(v) for v in center):
            raise ValueError("invalid anchor")
        for radius in radii:
            ids = [i for i,t in enumerate(turns) if max(abs(t["point"][a]-center[a]) for a in (0,1)) <= radius*unit]
            selected = set(ids)
            nodes = [{"sourceNode": i, "relativePoint": [(turns[i]["point"][a]-center[a])/unit for a in (0,1)],
                      "rays": turns[i]["rays"], "next": turns[i]["next"] if turns[i]["next"] in selected else -1} for i in ids]
            # Exact descriptors are only buckets. Neither equal nor different hashes prove live uniqueness.
            signature = digest([{k:v for k,v in n.items() if k not in ("sourceNode", "next")} for n in nodes])
            result.append(dict(anchor=anchor, radiusCells=radius, nodes=nodes, signature=signature))
    return result


def build(config_path, output):
    config_path, output = Path(config_path), Path(output)
    config = read(config_path)
    root = Path(config["resourceRoot"]).resolve()
    if output.resolve().is_relative_to(root) or root.is_relative_to(output.resolve()):
        raise ValueError("output must be separate from source resources")
    radii = config.get("radiiCells", [2,4,8])
    if not radii or any(not math.isfinite(r) or r <= 0 for r in radii):
        raise ValueError("radii must be finite positive numbers")
    compiler = file_hash(__file__)
    rows, seen, generated, reused = [], set(), 0, 0
    # Validate all inputs before creating the output tree.
    for source in config["sources"]:
        payload = read(within(root, source["entries"]))
        bindings = {}
        for b in read(within(root, source["index"]))["entries"]:
            key = (str(b["mapId"]),str(b["floor"]))
            if key in bindings:
                raise ValueError("duplicate source binding")
            bindings[key] = b
        doors = read(within(root, source["anchors"])) if source.get("anchors") else None
        if doors and (doors["mode"] != payload["mode"] or doors["profileId"] != payload["profileId"]):
            raise ValueError("anchor mode/profile mismatch")
        for item in payload["entries"]:
            key = (str(item["mapId"]),str(item["floor"]))
            identity = (payload["mode"],payload["profileId"],*key)
            if identity in seen:
                raise ValueError("duplicate candidate identity")
            seen.add(identity)
            binding = bindings.get(key)
            gaps, anchors = [], []
            if not binding:
                gaps.append("missing-versioned-structure-binding")
            resource_hash = None
            if binding:
                resource_hash = file_hash(within(root,binding["resourcePath"]))
                for field in ("structureVersionKey", "sourceVersion"):
                    if not binding.get(field) or (item.get(field) and item[field] != binding[field]):
                        raise ValueError("entry binding mismatch: " + field)
            for door in (doors or {}).get("entries",[]):
                if (str(door["mapId"]),str(door["floor"])) != key:
                    continue
                if not binding or any(door[f] != binding[f] for f in ("sourceVersion","structureVersionKey")):
                    raise ValueError("stale doorway binding")
                if str(door["layoutId"]) != str(item["canvas"]["layout"]):
                    raise ValueError("doorway layout mismatch")
                anchors.append(dict(kind="geometric-boundary", center=door["boundaryCenter"], evidence=door))
            for gate in item.get("gates",[]):
                if "center" in gate:
                    anchors.append(dict(kind="source-marker", center=gate["center"], evidence=gate))
            if not any(a["kind"] == "geometric-boundary" for a in anchors):
                gaps.append("doorway-boundary-unavailable")
            gaps.extend(["known-wall-classification-unavailable", "live-uniqueness-not-evaluated"])
            # Raw dependency closure is not reconstructed from an alpha mask or a cached renderer path.
            deps = {name:file_hash(within(root,name)) for name in source.get("dependencies",[])}
            if not deps:
                gaps.append("raw-source-dependency-closure-unverified")
            source_gaps = [g for g in payload.get("semanticGaps",[]) if isinstance(g,dict) and str(g.get("mapId")) == key[0] and str(g.get("floor",key[1])) == key[1]]
            content = dict(schema=SCHEMA, identity=identity, binding=binding, coordinateSpace=item.get("canvas"), sourceSemanticGaps=source_gaps,
                           structureSha256=resource_hash, dependencies=deps, compilerSha256=compiler,
                           inputEntrySha256=digest(item), radiiCells=radii, anchors=anchors)
            fingerprint = digest(content)
            candidate = dict(**content, fingerprint=fingerprint, semanticGaps=gaps,
                             fallback="full-reference-competition", grantsIdentity=False,
                             neighborhoods=neighborhoods(item,anchors,radii) if binding else [])
            rows.append(candidate)
    output.mkdir(parents=True,exist_ok=True)
    inventory = []
    for row in rows:
        path = output / (row["fingerprint"] + ".json")
        data = encoded(row)
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("corrupt cached candidate: " + path.name)
            reused += 1
        else:
            with path.open("xb") as f:
                f.write(data)
            generated += 1
        inventory.append(dict(identity=row["identity"],file=path.name,sha256=hashlib.sha256(data).hexdigest(),
                              neighborhoods=len(row["neighborhoods"]),semanticGaps=row["semanticGaps"]))
    manifest = dict(schema=SCHEMA, entries=inventory, runtimeIntegration="pending-owner-adapter",
                    identityPolicy="retain-all-candidates; no unique decision from local signature")
    manifest_path = output / ("manifest-"+digest(manifest)+".json")
    data = encoded(manifest)
    if manifest_path.exists():
        if manifest_path.read_bytes() != data:
            raise ValueError("corrupt manifest")
    else:
        with manifest_path.open("xb") as f:
            f.write(data)
    return dict(manifest=str(manifest_path),entries=len(rows),generated=generated,reused=reused,
                neighborhoods=sum(r["neighborhoods"] for r in inventory))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(build(args.config,args.output)))
