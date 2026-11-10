"""Small read-only JSONL process for the first hybrid gallery slice.

The worker owns one browse snapshot at a time. It deliberately has no Qt,
runtime, game-window, settings, or live-recognition dependencies.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import redirect_stdout
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import stat
import sys
from typing import BinaryIO


MAX_REQUEST_LINE_BYTES = 64 * 1024
MAX_SOURCE_PIXELS = 32_000_000
MAX_PREVIEW_EDGE = 1600
MAX_PREVIEW_PNG_BYTES = 12 * 1024 * 1024


@dataclass(frozen=True)
class _PreviewBinding:
    path: Path
    stamp: tuple
    available: bool


@dataclass(frozen=True)
class _CatalogSnapshot:
    catalog_id: int
    season: str
    mode: str
    rows: dict[str, _PreviewBinding]


class _ResourceChanged(Exception):
    pass


class _BrowseWorker:
    def __init__(self, resource_root: Path, installation_root: Path,
                 *, browse_loader, read_lock, readiness_check,
                 seasons, modes_for_season, context_label, image_module):
        self.resource_root = resource_root
        self.installation_root = installation_root
        self._load_browse_context = browse_loader
        self._resource_read_lock = read_lock
        self._assert_resources_ready = readiness_check
        self._seasons = seasons
        self._modes_for_season = modes_for_season
        self._context_label = context_label
        self._image = image_module
        self._catalog_sequence = 0
        self._snapshot: _CatalogSnapshot | None = None

    def handle(self, request):
        request_id = request.get("id") if isinstance(request, dict) else None
        if type(request_id) is not int:
            return self._failure(None, "invalid-request", "request id must be an integer"), False
        if not isinstance(request, dict):
            return self._failure(request_id, "invalid-request", "request must be an object"), False
        operation = request.get("operation")
        if operation == "hello":
            return {"id": request_id, "ok": True, "result": self._hello()}, False
        if operation == "catalog":
            return self._catalog(request_id, request), False
        if operation == "preview":
            return self._preview(request_id, request), False
        if operation == "shutdown":
            return {"id": request_id, "ok": True, "result": {}}, True
        return self._failure(request_id, "unknown-operation", "unknown operation"), False

    @staticmethod
    def _failure(request_id, code, message):
        return {"id": request_id, "ok": False,
                "error": {"code": str(code), "message": str(message)}}

    def _hello(self):
        contexts = []
        for season in self._seasons:
            for mode in self._modes_for_season(season):
                contexts.append({"season": season, "mode": mode,
                                 "label": self._context_label(season, mode)})
        return {"contexts": contexts, "python": platform.python_version()}

    def _catalog(self, request_id, request):
        # Every catalog attempt invalidates the previous snapshot, including
        # invalid requests and failed loads. IDs never get reused in-process.
        self._catalog_sequence += 1
        catalog_id = self._catalog_sequence
        self._snapshot = None
        season, mode = request.get("season"), request.get("mode")
        if (not isinstance(season, str) or season not in self._seasons
                or not isinstance(mode, str) or mode not in self._modes_for_season(season)):
            return self._failure(request_id, "invalid-context", "unknown season or mode")
        try:
            with self._resource_read_lock(self.installation_root, wait=True):
                self._assert_resources_ready(self.installation_root)
                context = self._load_browse_context(self.resource_root, mode, season=season)
                if context.error:
                    return self._failure(request_id, "resource-error", context.error)
                profiles = []
                rows = []
                bindings = {}
                floor_metadata = getattr(context, "floor_metadata", None) or {}
                from map_resource_model import floor_label

                for profile in context.profiles:
                    profile_id = str(profile.profile_id)
                    profiles.append({"id": profile_id, "name": str(profile.name)})
                    for item in profile.maps:
                        for variant in item.variants:
                            for floor_value, path_value in variant.layers.items():
                                floor = str(floor_value)
                                path = Path(path_value)
                                key = f"{season}/{mode}/{profile_id}/{item.id}/{variant.id}/{floor}"
                                if key in bindings:
                                    return self._failure(request_id, "resource-error",
                                                         "duplicate gallery row key")
                                stamp = context.path_stamps.get(path)
                                metadata = floor_metadata.get(floor, {})
                                label = metadata.get("label") if hasattr(metadata, "get") else None
                                rows.append({
                                    "key": key,
                                    "title": str(item.name),
                                    "mapId": str(item.id),
                                    "profileId": profile_id,
                                    "variant": str(variant.name),
                                    "floor": floor,
                                    "floorLabel": str(label) if label else floor_label(floor),
                                    "available": stamp is not None,
                                })
                                bindings[key] = _PreviewBinding(path, tuple(stamp or ()), stamp is not None)
                self._snapshot = _CatalogSnapshot(catalog_id, season, mode, bindings)
        except Exception as error:
            return self._failure(request_id, "resource-error",
                                 f"resource browsing failed: {type(error).__name__}: {error}")
        return {"id": request_id, "ok": True, "result": {
            "catalogId": catalog_id,
            "season": season,
            "mode": mode,
            "profiles": profiles,
            "rows": rows,
            "warnings": [str(warning) for warning in context.warnings],
        }}

    def _preview(self, request_id, request):
        catalog_id = request.get("catalogId")
        key = request.get("key")
        if type(catalog_id) is not int or not isinstance(key, str):
            return self._failure(request_id, "invalid-request", "preview requires integer catalogId and string key")
        snapshot = self._snapshot
        if snapshot is None:
            return self._failure(request_id, "no-catalog", "load a catalog before requesting a preview")
        if catalog_id != snapshot.catalog_id:
            return self._failure(request_id, "stale-catalog", "catalog snapshot is no longer current")
        binding = snapshot.rows.get(key)
        if binding is None:
            return self._failure(request_id, "invalid-key", "row key is not part of the current catalog")
        if not binding.available:
            return self._failure(request_id, "preview-unavailable", "preview resource was missing at catalog load")
        try:
            with self._resource_read_lock(self.installation_root, wait=True):
                self._assert_resources_ready(self.installation_root)
                data = self._read_bound_resource(binding)
            png, width, height = self._make_preview(data)
        except _ResourceChanged:
            return self._failure(request_id, "resource-changed", "preview resource changed after catalog load")
        except _PreviewTooLarge as error:
            return self._failure(request_id, error.code, error.message)
        except Exception as error:
            return self._failure(request_id, "preview-error",
                                 f"preview could not be produced: {type(error).__name__}")
        return {"id": request_id, "ok": True, "result": {
            "catalogId": snapshot.catalog_id,
            "key": key,
            "width": width,
            "height": height,
            "pngBase64": base64.b64encode(png).decode("ascii"),
        }}

    def _read_bound_resource(self, binding: _PreviewBinding) -> bytes:
        expected = binding.stamp
        path = binding.path
        try:
            resolved = path.resolve(strict=True)
            if resolved != path or not resolved.is_relative_to(self.resource_root):
                raise _ResourceChanged()
            with resolved.open("rb") as stream:
                before = os.fstat(stream.fileno())
                if (not stat.S_ISREG(before.st_mode)
                        or (before.st_mtime_ns, before.st_size) != tuple(expected[:2])):
                    raise _ResourceChanged()
                data = stream.read()
                after = os.fstat(stream.fileno())
            current = path.stat()
        except _ResourceChanged:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _ResourceChanged() from None
        stat_pair = (after.st_mtime_ns, after.st_size)
        if (not stat.S_ISREG(after.st_mode) or stat_pair != tuple(expected[:2])
                or (before.st_mtime_ns, before.st_size) != stat_pair
                or (current.st_mtime_ns, current.st_size) != stat_pair
                or len(data) != after.st_size):
            raise _ResourceChanged()
        if len(expected) >= 3 and sha256(data).hexdigest() != expected[2]:
            raise _ResourceChanged()
        return data

    def _make_preview(self, source: bytes):
        image = self._image
        with image.open(BytesIO(source)) as decoded:
            width, height = decoded.size
            if (width <= 0 or height <= 0 or width * height > MAX_SOURCE_PIXELS):
                raise _PreviewTooLarge("image-too-large", "image exceeds the 32 million pixel limit")
            decoded.load()
            rgba = decoded.convert("RGBA")
        try:
            rgba.thumbnail((MAX_PREVIEW_EDGE, MAX_PREVIEW_EDGE), image.Resampling.LANCZOS)
            output = BytesIO()
            rgba.save(output, format="PNG")
            png = output.getvalue()
        finally:
            rgba.close()
        if len(png) > MAX_PREVIEW_PNG_BYTES:
            raise _PreviewTooLarge("preview-too-large", "encoded preview exceeds the 12 MiB limit")
        with image.open(BytesIO(png)) as check:
            check.load()
            result_width, result_height = check.size
        return png, result_width, result_height


class _PreviewTooLarge(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def _emit(output, response):
    output.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
    output.flush()


def _error(request_id, code, message):
    return {"id": request_id, "ok": False,
            "error": {"code": code, "message": message}}


def _serve(worker, input_stream: BinaryIO, output):
    while True:
        raw = input_stream.readline(MAX_REQUEST_LINE_BYTES + 1)
        if not raw:
            return 0
        if len(raw) > MAX_REQUEST_LINE_BYTES:
            if not raw.endswith(b"\n"):
                while raw and not raw.endswith(b"\n"):
                    raw = input_stream.readline(MAX_REQUEST_LINE_BYTES + 1)
            _emit(output, _error(None, "request-too-large", "request line exceeds 64 KiB"))
            continue
        if raw.endswith(b"\n"):
            raw = raw[:-1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _emit(output, _error(None, "invalid-json", "request line is not valid UTF-8 JSON"))
            continue
        response, shutdown = worker.handle(request)
        _emit(output, response)
        if shutdown:
            return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only hybrid gallery worker")
    parser.add_argument("--resource-root", required=True, help="absolute read-only resource root")
    parser.add_argument("--installation-root", help="absolute resource transaction lock root")
    args = parser.parse_args(argv)
    resource_root = Path(args.resource_root)
    installation_root = Path(args.installation_root) if args.installation_root else (
        resource_root.parent if resource_root.name.casefold() == "_internal" else resource_root)
    if not resource_root.is_absolute() or not installation_root.is_absolute():
        parser.error("--resource-root and --installation-root must be absolute paths")
    try:
        resource_root = resource_root.resolve(strict=True)
        installation_root = installation_root.resolve(strict=True)
    except OSError as error:
        parser.error(f"resource roots must exist: {type(error).__name__}")
    if not resource_root.is_dir() or not installation_root.is_dir():
        parser.error("resource roots must be directories")
    if resource_root not in (installation_root, installation_root / "_internal"):
        parser.error("resource root must be the installation root or its _internal directory")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict", newline="\n")
    protocol_output = sys.stdout
    with redirect_stdout(sys.stderr):
        app_root = Path(__file__).resolve().parents[2]
        app_directory = str(app_root / "app")
        if app_directory not in sys.path:
            sys.path.insert(0, app_directory)
        from PIL import Image
        from map_resource_context import load_browse_context
        from resource_transaction import assert_resources_ready, resource_read_lock
        from season_context import SEASONS, SeasonContext, modes_for_season

        worker = _BrowseWorker(resource_root, installation_root,
            browse_loader=load_browse_context,
            read_lock=resource_read_lock,
            readiness_check=assert_resources_ready,
            seasons=SEASONS,
            modes_for_season=modes_for_season,
            context_label=lambda season, mode: SeasonContext(season, mode).label,
            image_module=Image)
        return _serve(worker, sys.stdin.buffer, protocol_output)


if __name__ == "__main__":
    raise SystemExit(main())
