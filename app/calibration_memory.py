from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE_REGISTRY = ROOT / "references" / "overlay-calibration-profiles.json"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def empty_registry() -> dict:
    return {
        "schemaVersion": 2,
        "policy": (
            "Profiles are append-only evidence. A profile may render only when it is a "
            "live-overlay profile with state=verified, runtimeEnabled=true, and an "
            "independent live acceptance marked passed."
        ),
        "profileCount": 0,
        "candidateCount": 0,
        "runtimeEligibleCount": 0,
        "profiles": [],
    }


def is_runtime_eligible(profile: dict) -> bool:
    """Central fail-closed promotion gate for the new multi-profile registry."""
    acceptance = profile.get("liveAcceptance") or {}
    return bool(
        profile.get("profileType") == "live-overlay"
        and profile.get("state") == "verified"
        and profile.get("runtimeEnabled") is True
        and acceptance.get("status") == "passed"
    )


def load_registry(path: Path = DEFAULT_PROFILE_REGISTRY) -> dict:
    if not path.exists():
        return empty_registry()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 2 or not isinstance(data.get("profiles"), list):
        raise ValueError(f"unsupported calibration profile registry: {path}")
    return data


def _with_counts(data: dict) -> dict:
    result = copy.deepcopy(data)
    profiles = result.get("profiles", [])
    result["profileCount"] = len(profiles)
    result["candidateCount"] = sum(p.get("state") == "candidate" for p in profiles)
    result["runtimeEligibleCount"] = sum(is_runtime_eligible(p) for p in profiles)
    return result


def save_registry(data: dict, path: Path = DEFAULT_PROFILE_REGISTRY) -> None:
    """Atomically save a registry without modifying source screenshots or legacy truth."""
    checked = _with_counts(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(checked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def scene_fingerprint(scene: dict) -> str:
    return _digest(scene)


def _size_distance(query: dict | None, known: dict | None) -> float:
    if not query or not known:
        return 0.35
    try:
        qw, qh = float(query["width"]), float(query["height"])
        kw, kh = float(known["width"]), float(known["height"])
        if min(qw, qh, kw, kh) <= 0:
            return 2.0
        return abs(math.log(qw / kw)) + abs(math.log(qh / kh))
    except (KeyError, TypeError, ValueError):
        return 2.0


def _crop_distance(query: dict | None, known: dict | None, client: dict | None) -> float:
    if not query or not known:
        return 0.25
    width = float((client or {}).get("width") or max(query.get("width", 1), 1))
    height = float((client or {}).get("height") or max(query.get("height", 1), 1))
    terms = (
        abs(float(query.get("x", 0)) - float(known.get("x", 0))) / width,
        abs(float(query.get("y", 0)) - float(known.get("y", 0))) / height,
        abs(float(query.get("width", 0)) - float(known.get("width", 0))) / width,
        abs(float(query.get("height", 0)) - float(known.get("height", 0))) / height,
    )
    return sum(terms)


def _scene_match(query: dict, known: dict) -> tuple[bool, float, list[str]]:
    reasons: list[str] = []
    q_orientation = query.get("orientation")
    k_orientation = known.get("orientation")
    if q_orientation and k_orientation and q_orientation != k_orientation:
        return False, float("inf"), ["orientation-conflict"]

    route_family = query.get("routeFamily")
    route_families = known.get("routeFamilies") or []
    if route_family and route_families and route_family not in route_families:
        return False, float("inf"), ["route-family-conflict"]

    q_kind = query.get("coordinateSpace")
    k_kind = known.get("coordinateSpace")
    if q_kind and k_kind and q_kind != k_kind:
        return False, float("inf"), ["coordinate-space-conflict"]

    score = 0.0
    score += 3.0 * _size_distance(query.get("clientSize"), known.get("clientSize"))
    score += 1.2 * _size_distance(query.get("referenceSize"), known.get("referenceSize"))
    score += 2.0 * _crop_distance(
        query.get("cropRect"), known.get("cropRect"), query.get("clientSize")
    )

    q_scale, k_scale = query.get("uiScale"), known.get("uiScale")
    if q_scale is None or k_scale is None:
        score += 0.2
    else:
        try:
            score += 2.0 * abs(math.log(float(q_scale) / float(k_scale)))
        except (TypeError, ValueError, ZeroDivisionError):
            score += 2.0

    if scene_fingerprint(query) == scene_fingerprint(known):
        reasons.append("exact-scene-fingerprint")
        score = 0.0
    else:
        reasons.append("nearest-compatible-scene")
    return True, score, reasons


def retrieve_profiles(
    map_id: str,
    floor: int,
    scene: dict,
    *,
    registry_path: Path = DEFAULT_PROFILE_REGISTRY,
    profile_type: str | None = None,
    require_runtime_eligible: bool = False,
) -> list[dict]:
    """Return compatible profiles, closest first; never silently promotes a candidate."""
    matches = []
    for profile in load_registry(registry_path).get("profiles", []):
        if profile.get("mapId") != map_id or int(profile.get("floor", -1)) != int(floor):
            continue
        if profile_type and profile.get("profileType") != profile_type:
            continue
        if require_runtime_eligible and not is_runtime_eligible(profile):
            continue
        compatible, score, reasons = _scene_match(scene, profile.get("scene") or {})
        if not compatible:
            continue
        result = copy.deepcopy(profile)
        result["match"] = {
            "score": round(float(score), 8),
            "reasons": reasons,
            "runtimeEligible": is_runtime_eligible(profile),
        }
        matches.append(result)
    return sorted(matches, key=lambda item: (item["match"]["score"], item["profileId"]))


def _validate_manual_candidate(candidate: dict) -> None:
    required = ("mapId", "mapName", "floor", "scene", "sourceEvidence", "transformEvidence")
    missing = [key for key in required if candidate.get(key) in (None, "", {})]
    if missing:
        raise ValueError(f"candidate missing required fields: {', '.join(missing)}")
    if int(candidate["floor"]) not in (1, 2):
        raise ValueError("floor must be 1 or 2")
    source = candidate["sourceEvidence"]
    if not source.get("sourceRef") or not source.get("sourceSha256"):
        raise ValueError("sourceEvidence requires sourceRef and sourceSha256")
    transform = candidate["transformEvidence"]
    anchors = transform.get("anchorPairs") or []
    if not transform.get("matrix") and len(anchors) < 2:
        raise ValueError("transformEvidence requires a matrix or at least two anchor pairs")
    if transform.get("residualErrorPx") is None:
        raise ValueError("transformEvidence requires residualErrorPx")


def record_manual_candidate(
    candidate: dict,
    *,
    registry_path: Path = DEFAULT_PROFILE_REGISTRY,
) -> dict:
    """Append one manual correction as a disabled candidate; never overwrite a profile."""
    _validate_manual_candidate(candidate)
    registry = load_registry(registry_path)
    scene = copy.deepcopy(candidate["scene"])
    evidence_key = {
        "mapId": candidate["mapId"],
        "floor": int(candidate["floor"]),
        "scene": scene,
        "sourceEvidence": candidate["sourceEvidence"],
        "transformEvidence": candidate["transformEvidence"],
    }
    profile_id = (
        f"candidate-{candidate['mapId']}-f{int(candidate['floor'])}-"
        f"{scene_fingerprint(scene)[:10]}-{_digest(evidence_key)[:10]}"
    )
    existing = next(
        (p for p in registry["profiles"] if p.get("profileId") == profile_id), None
    )
    if existing is not None:
        return {"created": False, "profile": copy.deepcopy(existing)}

    profile = {
        "profileId": profile_id,
        "profileType": "live-overlay",
        "mapId": candidate["mapId"],
        "mapName": candidate["mapName"],
        "floor": int(candidate["floor"]),
        "routeVersions": candidate.get("routeVersions") or ["speed", "coffin"],
        "state": "candidate",
        "runtimeEnabled": False,
        "scene": scene,
        "sceneFingerprint": scene_fingerprint(scene),
        "sourceEvidence": copy.deepcopy(candidate["sourceEvidence"]),
        "transformEvidence": copy.deepcopy(candidate["transformEvidence"]),
        "humanConfirmation": copy.deepcopy(candidate.get("humanConfirmation") or {
            "status": "correction-recorded-not-promoted",
            "source": "manual-correction-tool",
        }),
        "regression": {"status": "not-run"},
        "liveAcceptance": {"status": "not-tested"},
        "createdAt": candidate.get("createdAt") or _utc_now(),
    }
    registry["profiles"].append(profile)
    save_registry(registry, registry_path)
    return {"created": True, "profile": copy.deepcopy(profile)}

