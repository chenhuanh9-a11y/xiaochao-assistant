"""Bounded real-service measurements for imported map resources.

No client window, input listener, capture provider, or game is created. Import
and load run through the production Qt service/provider APIs against a distinct
marked RuntimePaths root for each sample. This script never removes its store.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import time
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = Path("E:/codex project/load-speed-1-evidence")
TMP_ROOT = EVIDENCE_ROOT / "tmp"
STORE_MARKER = ".qt-lab-owner"
STORE_OWNER = "qt-quick-isolated-v1"
STORE_META = ".resource-benchmark-store.json"
CONTEXTS = (("s0", "nightmare"), ("s0", "difficult"),
            ("s1", "nightmare"), ("s1", "difficult"))
CONTEXT_LABELS = {
    ("s0", "nightmare"): "S0 噩梦",
    ("s0", "difficult"): "S0 困难",
    ("s1", "nightmare"): "S1 噩梦",
    ("s1", "difficult"): "S1 困难",
}


def _prepare_private_temp() -> None:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = str(TMP_ROOT)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYTHONPYCACHEPREFIX"] = str(TMP_ROOT / "pycache")
    sys.dont_write_bytecode = True
    import tempfile
    tempfile.tempdir = str(TMP_ROOT)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: object, *, source: Path | None = None,
               store: Path | None = None, output: Path | None = None) -> str:
    text = str(value)
    replacements = [(str(EVIDENCE_ROOT), "<evidence>"),
                    (str(Path.home()), "<user-home>")]
    if source is not None:
        replacements.append((str(source), "<source>"))
    if store is not None:
        replacements.append((str(store), "<store>"))
    if output is not None:
        replacements.append((str(output), "<output>"))
    for original, replacement in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        text = text.replace(original, replacement).replace(original.replace("/", "\\"), replacement)
    return text


def _require_within(path: Path, root: Path, label: str) -> Path:
    root = root.resolve()
    candidate = path.absolute()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{label} must stay inside {root}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{label} must stay inside {root}")
    return resolved


def _context_archive_paths(source: Path) -> list[dict]:
    base_dir = source / "xingtu-1.1.8-controls-plan-20261114-r2" / "地图资源" / "结构包"
    route_root = (source / "xingtu-author-routes-repin2-c16d45f4-20261114"
                  / "作者路线包")
    base_names = {
        ("s0", "nightmare"): "S0噩梦.xingtu-base",
        ("s0", "difficult"): "S0困难.xingtu-base",
        ("s1", "nightmare"): "S1噩梦.xingtu-base",
        ("s1", "difficult"): "S1困难.xingtu-base",
    }
    season_dirs = {"s0": "s0厄运之女", "s1": "s1磷光症"}
    mode_dirs = {"nightmare": "噩梦", "difficult": "困难"}
    rows = []
    for season, mode in CONTEXTS:
        context = f"{season}:{mode}"
        base = base_dir / base_names[(season, mode)]
        if not base.is_file():
            raise FileNotFoundError(f"Missing base package for {context}: {base.name}")
        rows.append(dict(context=context, season=season, mode=mode, kind="base", path=base))
        route_dir = route_root / season_dirs[season] / mode_dirs[mode]
        matches = sorted(route_dir.glob("*爱吃醋*作者路线.xingtu-route"))
        if len(matches) != 1:
            raise FileNotFoundError(
                f"Expected one 爱吃醋 route for {context}; found {len(matches)}")
        rows.append(dict(context=context, season=season, mode=mode, kind="route", path=matches[0]))
    return rows


def _hash_file(path: Path) -> tuple[int, str]:
    with path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    return size, digest


def _manifest(path: Path, manifest_name: str) -> tuple[dict, str]:
    with zipfile.ZipFile(path, "r") as archive:
        raw = archive.read(manifest_name)
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid manifest in {path.name}")
    return value, hashlib.sha256(raw).hexdigest()


def _input_summary(source: Path, manifest_name: str) -> tuple[list[dict], dict]:
    started = time.perf_counter_ns()
    entries, manifest_rows = [], []
    for spec in _context_archive_paths(source):
        path = spec["path"]
        size, digest = _hash_file(path)
        manifest, manifest_sha256 = _manifest(path, manifest_name)
        component_count = len(manifest.get("components", ()))
        row = dict(context=spec["context"], season=spec["season"], mode=spec["mode"],
                   kind=spec["kind"], label=CONTEXT_LABELS[(spec["season"], spec["mode"])],
                   file=path.relative_to(source).as_posix(), bytes=size, sha256=digest,
                   packageId=manifest.get("packageId"), resourceVersion=manifest.get("resourceVersion"),
                   manifestSha256=manifest_sha256,
                   manifest=manifest)
        entries.append(dict(spec, bytes=size, sha256=digest, manifest=manifest,
                            manifest_sha256=manifest_sha256))
        manifest_rows.append(row)
    summary = dict(schema="resource-benchmark-inputs-v1", generatedAtUtc=_utc_now(),
                   sourcePackages=("xingtu-1.1.8-controls-plan-20261114-r2",
                                   "xingtu-author-routes-repin2-c16d45f4-20261114"),
                   selection="四模式各一基础包与爱吃醋作者路线包；未改写原始交付文件",
                   inputs=manifest_rows,
                   totalInputBytes=sum(row["bytes"] for row in manifest_rows),
                   preparationElapsedMs=(time.perf_counter_ns() - started) / 1_000_000)
    return entries, summary


def _bind_repo_imports(repo_root: Path) -> dict[str, str]:
    search_paths = [repo_root / part for part in (
        "tools", "tools/maps", "tests", "experiments/qt_quick", "app")]
    missing = [str(path) for path in search_paths if not path.is_dir()]
    if missing:
        raise ValueError("Source checkout is missing required import roots")
    sys.path[:0] = [str(path) for path in search_paths if str(path) not in sys.path]
    required_modules = (
        "imported_resource_packages", "imported_resource_archive", "resource_snapshot_io",
        "star_map_import", "resource_transaction", "client_resources",
        "client_imported_resources", "client_imported_activation",
        "client_imported_activation_plan", "client_imported_providers",
        "client_original_providers", "imported_original_base",
        "imported_controls_profile", "imported_s1_controls_profile",
    )
    provenance = {}
    for name in required_modules:
        module = importlib.import_module(name)
        module_file = Path(module.__file__).resolve(strict=True)
        if not module_file.is_relative_to(repo_root):
            raise RuntimeError(f"Module {name} resolved outside the selected source checkout")
        provenance[name] = module_file.relative_to(repo_root).as_posix()
    return provenance


class FunctionTimings:
    """Time selected real functions without replacing their behavior."""
    def __init__(self):
        self.active_sample = None
        self.rows = []
        self.lock = threading.Lock()
        self.local = threading.local()

    def wrap(self, module, name):
        original = getattr(module, name)
        qualified = f"{getattr(module, '__name__', type(module).__name__)}.{name}"

        def measured(*args, **kwargs):
            sample = self.active_sample
            if sample is None:
                return original(*args, **kwargs)
            depth = getattr(self.local, "depth", 0)
            started = time.perf_counter_ns()
            self.local.depth = depth + 1
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = time.perf_counter_ns() - started
                self.local.depth = depth
                row = dict(sample=sample, function=qualified,
                           elapsedMs=elapsed / 1_000_000, nestedDepth=depth,
                           thread=threading.current_thread().name)
                with self.lock:
                    self.rows.append(row)
        setattr(module, name, measured)

    def for_sample(self, sample):
        with self.lock:
            return [dict(row) for row in self.rows if row["sample"] == sample]


def _install_function_timings(timings: FunctionTimings) -> None:
    import imported_resource_archive
    import imported_resource_inventory
    import client_imported_admission
    import client_imported_activation_plan
    import client_imported_activation
    import client_imported_providers

    for module, names in (
        (imported_resource_archive, ("inspect_archive", "save_archive")),
        (imported_resource_inventory, ("list_saved_versions",)),
        (client_imported_admission, ("admit_import", "available_base_contexts")),
        (client_imported_activation_plan, ("plan_context_activations",)),
        (client_imported_activation_plan, ("build_native_engine",)),
        (client_imported_activation, ("activate_contexts", "activate_displays", "build_native_engine")),
        (client_imported_providers, ("build_native_engine",)),
    ):
        for name in names:
            if hasattr(module, name):
                timings.wrap(module, name)


def _machine_info(source: Path, store: Path) -> dict:
    dependencies = {}
    for package in ("PySide6", "numpy", "opencv-python", "opencv-python-headless",
                    "Pillow", "psutil"):
        try:
            dependencies[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            dependencies[package] = None
    try:
        import psutil
        memory_bytes = int(psutil.virtual_memory().total)
        cpu_percent = psutil.cpu_percent(interval=0.25)
        process_count = len(psutil.pids())
    except Exception:
        memory_bytes, cpu_percent, process_count = None, None, None
    input_disk = shutil.disk_usage(source)
    store_disk = shutil.disk_usage(store.parent if store.exists() else store.parent)
    return dict(os=platform.platform(), architecture=platform.machine(), cpu=platform.processor() or None,
                logicalCpuCount=os.cpu_count(), physicalMemoryBytes=memory_bytes,
                dependencies=dependencies,
                inputVolume=dict(drive=Path(source.anchor).drive or None,
                                 totalBytes=input_disk.total, freeBytes=input_disk.free),
                storeVolume=dict(drive=Path(store.anchor).drive or None,
                                 totalBytes=store_disk.total, freeBytes=store_disk.free),
                ambientLoad=dict(systemCpuPercentBestEffort=cpu_percent, processCount=process_count,
                                 otherWorkloadsIsolated=False,
                                 note="同机器其他在途任务未隔离、未停止；CPU/进程数为采样时快照。"))


def _source_commit(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={repo_root}", "-C", str(repo_root),
             "rev-parse", "HEAD"], check=True, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except Exception:
        return None


def _write_json(path: Path, value: dict, *, initial: bool = False) -> None:
    if initial and path.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {path.name}")
    temporary = path.with_name(path.name + ".partial")
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _pump_until(app, predicate, timeout_seconds: float, label: str) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            app.processEvents()
            return
        time.sleep(0.002)
    raise TimeoutError(f"Timed out waiting for {label} after {timeout_seconds:.1f}s")


def _service_contexts(service) -> list[str]:
    return sorted(str(value) for value in service.availableContexts)


def _verify_inventory(service, package_count: int = 8) -> dict:
    rows = list(service.packageRows)
    contexts = _service_contexts(service)
    expected = sorted(f"{season}:{mode}" for season, mode in CONTEXTS)
    if not service.inventoryLoaded:
        raise AssertionError("resource inventory is not ready")
    if len(rows) != package_count:
        raise AssertionError(f"expected {package_count} saved packages; found {len(rows)}")
    if contexts != expected:
        raise AssertionError(f"expected available contexts {expected}; found {contexts}")
    if service.busy:
        raise AssertionError("resource service remained busy after terminal signal")
    return dict(packageRows=len(rows), availableContexts=contexts,
                inventoryLoaded=bool(service.inventoryLoaded), busy=bool(service.busy))


def _verify_consumers(resolver, factories, season: str, mode: str) -> dict:
    key = f"{season}:{mode}"
    snapshot = resolver.snapshot()
    resolved = snapshot.resolve_context(season, mode)
    if resolved.base is None or resolved.controls is None:
        raise AssertionError(f"{key} actual base or controls component is unavailable")
    report = factories.capabilities(season)[mode]
    engine = factories._engines.get(key)
    if engine is None or not report.get("prepared") or not report.get("dataReady"):
        raise AssertionError(f"{key} factory did not prepare actual resources: {report}")
    if getattr(engine, "qt_native_base", None) is None or getattr(engine, "qt_controls_profile", None) is None:
        raise AssertionError(f"{key} original base/controls decoders were not attached")
    inventory = getattr(engine, "qt_resource_inventory", {})
    if int(inventory.get("typedLayers", 0)) < 1:
        raise AssertionError(f"{key} did not decode any typed base resource")
    optional = getattr(engine, "qt_optional_displays", None)
    bindings = getattr(optional, "bindings", {}) if optional is not None else {}
    if not bindings:
        raise AssertionError(f"{key} author display consumer has no usable layer bindings")
    profile, map_id, floor = next(iter(bindings))
    binding = optional.binding(profile, map_id, floor)
    if not binding or not binding.get("usable"):
        raise AssertionError(f"{key} author display binding failed currentness validation")
    pixels = optional.pixels(binding["path"], "aligned-overlay")
    if pixels is None or not getattr(pixels, "size", 0):
        raise AssertionError(f"{key} author display consumer returned no pixels")
    return dict(context=key, basePackageId=resolved.base.component_ref.package_id,
                baseVersion=resolved.base.component_ref.resource_version,
                controlsPackageId=resolved.controls.component_ref.package_id,
                controlsVersion=resolved.controls.component_ref.resource_version,
                typedLayers=int(inventory["typedLayers"]), dataReady=bool(report["dataReady"]),
                rankReady=bool(report.get("rankReady")), liveReady=bool(report.get("liveReady")),
                authorProfile=str(profile), authorMap=str(map_id), authorFloor=str(floor),
                authorPixelShape=list(pixels.shape), authorPixelBytes=int(pixels.nbytes),
                authorLayerUsable=True)


def _setup_store(store: Path, scenario: str, transport: str) -> dict:
    if scenario == "import":
        if store.exists():
            raise FileExistsError(f"Import sample store already exists: {store.name}")
        store.mkdir(parents=True)
        (store / STORE_MARKER).write_text(STORE_OWNER, encoding="ascii")
        metadata = dict(schema="resource-benchmark-store-v1", transport=transport,
                        createdAtUtc=_utc_now())
        _write_json(store / STORE_META, metadata, initial=True)
        return metadata
    marker = store / STORE_MARKER
    if not store.is_dir() or not marker.is_file() or marker.read_text(encoding="ascii") != STORE_OWNER:
        raise ValueError("Load scenario requires a previously benchmarked marked store")
    metadata_path = store / STORE_META
    if not metadata_path.is_file():
        raise ValueError("Load store is missing its benchmark metadata")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("transport") != transport:
        raise ValueError("Load transport must match the import transport that created the store")
    return metadata


def _run(args) -> int:
    _prepare_private_temp()
    evidence = EVIDENCE_ROOT.resolve()
    TMP_ROOT.resolve()
    repo_root = Path(args.repo).expanduser().resolve(strict=True)
    if not (repo_root / "experiments/qt_quick/client_imported_resources.py").is_file():
        raise ValueError("--repo must be a Xingtu source checkout")
    source = Path(args.source).expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError("--source must be the delivery root containing the two package folders")
    store = _require_within(Path(args.store), TMP_ROOT, "--store")
    output = _require_within(Path(args.output), evidence, "--output")
    inputs_path = output.with_name(output.stem + ".inputs.json")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output.name}")
    if inputs_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing input summary: {inputs_path.name}")

    module_provenance = _bind_repo_imports(repo_root)
    import imported_resource_packages

    started_utc = _utc_now()
    inputs, input_summary = _input_summary(source, imported_resource_packages.MANIFEST_NAME)
    _write_json(inputs_path, input_summary, initial=True)
    store_metadata = _setup_store(store, args.scenario, args.transport)
    machine = _machine_info(source, store)
    result = dict(schema="imported-resource-service-benchmark-v1", status="running",
                  scenario=args.scenario, transport=args.transport, startedAtUtc=started_utc,
                  finishedAtUtc=None, sourceCommit=_source_commit(repo_root),
                  moduleProvenance=module_provenance,
                  sourceInputsFile=inputs_path.name, storeId=store.name,
                  storeTransport=store_metadata.get("transport"), machine=machine,
                  processBoundaries=dict(newProcessPerInvocation=True, windowsCreated=False,
                      inputHooksStarted=False, gameStarted=False, captureStarted=False,
                      liveRuntimeCreated=True, sessionServiceCreated=True,
                      sessionIdleLeaseRequired=True),
                  timingDefinition={}, stageEvents=[], operations=[], loadPasses=[],
                  startupElapsedMs=None, measuredElapsedMs=None, failure=None)
    _write_json(output, result, initial=True)

    app = service = session = runtime = factories = None
    start_setup = time.perf_counter_ns()
    stage_events = result["stageEvents"]
    current_sample = [None]
    completions, service_errors = [], []
    function_timings = FunctionTimings()
    run_start_ns = time.perf_counter_ns()
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtCore import QCoreApplication, QUrl
        from client_paths import RuntimePaths
        from imported_resource_catalog import ResourceResolver
        from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
        from client_live_runtime import LiveRuntime
        from client_session import SessionService
        from client_imported_resources import ImportedResourceService

        import client_imported_resources
        app = QCoreApplication.instance() or QCoreApplication([])
        paths = RuntimePaths(store, False)
        resolver = ResourceResolver(paths.imported_resource_root,
                                    supported_capabilities=SUPPORTED_CAPABILITIES)
        factories = build_imported_engine_factories(resolver, paths)
        runtime = LiveRuntime(engine_factories=factories, evidence_kind="unverified")
        session = SessionService(runtime)
        idle_lease = session.acquireResourceLease()
        if not idle_lease:
            raise RuntimeError("LiveRuntime did not grant an idle resource lease")
        session.releaseResourceLease(idle_lease)
        service = ImportedResourceService(resolver, paths=paths, engine_factories=factories)
        service.setLeaseProvider(session)
        function_timings.wrap(service, "_read_inventory")
        _install_function_timings(function_timings)

        def record_stage():
            sample = current_sample[0]
            stage = str(service.operationStage)
            row = dict(sample=sample, atElapsedMs=(time.perf_counter_ns() - run_start_ns) / 1_000_000,
                       stage=stage, busy=bool(service.busy), inventoryLoaded=bool(service.inventoryLoaded),
                       availableContextCount=len(service.availableContexts))
            if not stage_events or tuple(stage_events[-1].items()) != tuple(row.items()):
                stage_events.append(row)

        service.changed.connect(record_stage)
        service.importCompleted.connect(lambda title, message: completions.append(
            dict(title=str(title), message=_safe_text(message, source=source, store=store, output=output),
                 atElapsedMs=(time.perf_counter_ns() - run_start_ns) / 1_000_000,
                 sample=current_sample[0])))
        service.errorReported.connect(lambda error: service_errors.append(
            dict(sample=current_sample[0], errorCode=str(error.get("errorCode", "resource-error")),
                 action=str(error.get("action", "")), exceptionType=str(error.get("exceptionType", "")),
                 atElapsedMs=(time.perf_counter_ns() - run_start_ns) / 1_000_000)))
        result["startupElapsedMs"] = (time.perf_counter_ns() - start_setup) / 1_000_000
        result["timingDefinition"] = dict(
            clock="time.perf_counter_ns; wall elapsed, no CPU-time conversion",
            importOperation="Accepted ImportedResourceService.importArchive/importVerifiedArchive call to matching importCompleted, inventoryLoaded=true, busy=false, activationPlanOpen=false",
            loadOperation="service.listPackages acceptance to full inventory-ready state; then four factory.prepare_context calls and actual typed-base/control plus author-pixel consumer checks, repeated in the same process",
            functionTimings="Selected original functions are wrapped only to measure elapsed time; their implementations and return values are unchanged. Nested function times overlap and must not be summed.",
            stageTimings="service.changed snapshots record operationStage transitions and state; they are point observations, not inferred full phase durations.",
            cacheBoundary="Each invocation is a new Python process. A load invocation reuses only the exact marked store created by its paired import invocation; no store, user cache, or OS file cache is cleared. Cold means process-local service/factory caches, not a cold physical-disk cache. Input SHA/manifest collection before timing can also warm source archive reads.",
            ambientLoads="Other same-machine work is not isolated or stopped; best-effort CPU and process counts are recorded.")

        if args.scenario == "import":
            batch_start = time.perf_counter_ns()
            for item in inputs:
                relative = item["path"].relative_to(source).as_posix()
                sample = f"{item['context']}:{item['kind']}:{item['path'].name}"
                current_sample[0] = sample
                function_timings.active_sample = sample
                call_start = time.perf_counter_ns()
                before_completion = len(completions)
                before_error = len(service_errors)
                failure = None
                try:
                    url = QUrl.fromLocalFile(str(item["path"])).toString()
                    if args.transport == "verified":
                        expected = dict(packageId=item["manifest"]["packageId"],
                                        bytes=item["bytes"], sha256=item["sha256"])
                        accepted = service.importVerifiedArchive(
                            url, expected, int(service.operationGeneration))
                        entrypoint = "ImportedResourceService.importVerifiedArchive"
                    else:
                        accepted = service.importArchive(url)
                        entrypoint = "ImportedResourceService.importArchive"
                    if not accepted:
                        raise RuntimeError(f"{entrypoint} did not accept {item['path'].name}")
                    _pump_until(app, lambda: (len(completions) > before_completion
                            or len(service_errors) > before_error) and not service.busy,
                        args.timeout_seconds, f"{relative} import/automatic activation")
                    if len(completions) > before_completion:
                        terminal = completions[-1]
                        if terminal.get("sample") != sample:
                            raise AssertionError("importCompleted was not associated with the active sample")
                        if service.busy or service.activationPlanOpen or not service.inventoryLoaded:
                            raise AssertionError("import completed before service activation/inventory settled")
                    else:
                        failure = service_errors[-1]
                except Exception as error:
                    failure = dict(errorType=type(error).__name__,
                                   message=_safe_text(error, source=source, store=store, output=output))
                finally:
                    elapsed = (time.perf_counter_ns() - call_start) / 1_000_000
                    item_result = dict(sample=sample, context=item["context"], kind=item["kind"],
                        file=relative, bytes=item["bytes"], sha256=item["sha256"],
                        manifestPackageId=item["manifest"].get("packageId"),
                        entrypoint=("ImportedResourceService.importVerifiedArchive" if args.transport == "verified"
                                    else "ImportedResourceService.importArchive"),
                        operationStart="immediately before service method invocation",
                        operationEnd="terminal importCompleted plus inventoryLoaded=true/busy=false/activationPlanOpen=false, or first service error",
                        elapsedMs=elapsed, completion=(completions[-1] if len(completions) > before_completion else None),
                        serviceErrors=service_errors[before_error:],
                        stageEvents=[row for row in stage_events if row.get("sample") == sample],
                        functionTimings=function_timings.for_sample(sample), failure=failure)
                    result["operations"].append(item_result)
                    result["measuredElapsedMs"] = (time.perf_counter_ns() - batch_start) / 1_000_000
                    result["inventoryAfterOperation"] = dict(
                        packageRows=len(service.packageRows), availableContexts=_service_contexts(service),
                        inventoryLoaded=bool(service.inventoryLoaded), busy=bool(service.busy))
                    _write_json(output, result)
                current_sample[0] = None
                function_timings.active_sample = None
            try:
                result["finalInventory"] = _verify_inventory(service)
            except Exception as error:
                result["failure"] = dict(errorType=type(error).__name__,
                    message=_safe_text(error, source=source, store=store, output=output))
            result["measuredElapsedMs"] = (time.perf_counter_ns() - batch_start) / 1_000_000
            result["batchEndpoint"] = "first importArchive/importVerifiedArchive invocation to final inventory assertion"
        else:
            list_start = time.perf_counter_ns()
            load_start = list_start
            current_sample[0] = "load:listPackages"
            function_timings.active_sample = current_sample[0]
            service.listPackages()
            _pump_until(app, lambda: bool(service.inventoryLoaded) and not service.busy,
                        args.timeout_seconds, "new-process listPackages inventory")
            list_ms = (time.perf_counter_ns() - list_start) / 1_000_000
            inventory = _verify_inventory(service)
            if service_errors:
                raise AssertionError(f"listPackages reported an error: {service_errors[-1]}")
            result["listPackages"] = dict(elapsedMs=list_ms, endpoint="listPackages invocation to inventoryLoaded=true and busy=false",
                                          inventory=inventory,
                                          stageEvents=[row for row in stage_events if row.get("sample") == "load:listPackages"],
                                          functionTimings=function_timings.for_sample("load:listPackages"))
            current_sample[0] = None
            function_timings.active_sample = None
            for pass_name in ("cold", "same-process-repeat"):
                for season, mode in CONTEXTS:
                    key = f"{season}:{mode}"
                    sample = f"load:{pass_name}:{key}"
                    current_sample[0] = sample
                    function_timings.active_sample = sample
                    context_start = time.perf_counter_ns()
                    preparation_start = time.perf_counter_ns()
                    capabilities = factories.prepare_context(mode, season=season)
                    prepare_ms = (time.perf_counter_ns() - preparation_start) / 1_000_000
                    capability = dict(capabilities[mode])
                    consumer_start = time.perf_counter_ns()
                    consumer = _verify_consumers(resolver, factories, season, mode)
                    consumer_ms = (time.perf_counter_ns() - consumer_start) / 1_000_000
                    row = dict(passName=pass_name, context=key,
                        elapsedMs=(time.perf_counter_ns() - context_start) / 1_000_000,
                        prepareContextMs=prepare_ms, actualConsumerCheckMs=consumer_ms,
                        capability=dict(available=bool(capability.get("available")),
                                        dataReady=bool(capability.get("dataReady")),
                                        rankReady=bool(capability.get("rankReady")),
                                        liveReady=bool(capability.get("liveReady")),
                                        prepared=bool(capability.get("prepared"))),
                        actualConsumers=consumer,
                        stageEvents=[event for event in stage_events if event.get("sample") == sample],
                        functionTimings=function_timings.for_sample(sample))
                    result["loadPasses"].append(row)
                    current_sample[0] = None
                    function_timings.active_sample = None
                    result["measuredElapsedMs"] = (time.perf_counter_ns() - load_start) / 1_000_000
                    result["inventoryBeforeLoad"] = inventory
                    _write_json(output, result)
            result["measuredElapsedMs"] = (time.perf_counter_ns() - load_start) / 1_000_000
            cold_rows = [row for row in result["loadPasses"] if row["passName"] == "cold"]
            warm_rows = [row for row in result["loadPasses"] if row["passName"] == "same-process-repeat"]
            result["loadTimingSummary"] = dict(
                listPackagesMs=list_ms,
                coldPrepareContextTotalMs=sum(row["prepareContextMs"] for row in cold_rows),
                coldContextEndToEndTotalMs=sum(row["elapsedMs"] for row in cold_rows),
                warmPrepareContextTotalMs=sum(row["prepareContextMs"] for row in warm_rows),
                warmContextEndToEndTotalMs=sum(row["elapsedMs"] for row in warm_rows),
                actualConsumerChecksTotalMs=sum(row["actualConsumerCheckMs"]
                                                for row in result["loadPasses"]),
                measuredOverallMs=result["measuredElapsedMs"])
            result["batchEndpoint"] = "listPackages invocation to two sequential prepare_context and author-consumer passes for all four contexts"

        if result["failure"] is None:
            if args.scenario == "import":
                failed_operations = [row for row in result["operations"] if row["failure"]]
                if failed_operations or len(result["operations"]) != 8:
                    result["failure"] = dict(errorType="ImportSamplesFailed",
                        message=f"{len(failed_operations)} of {len(result['operations'])} import samples failed")
            elif len(result["loadPasses"]) != 8:
                result["failure"] = dict(errorType="LoadSamplesIncomplete",
                    message=f"Expected 8 load passes; measured {len(result['loadPasses'])}")
    except Exception as error:
        result["failure"] = dict(errorType=type(error).__name__,
            message=_safe_text(error, source=source, store=store, output=output))
        result["serviceErrors"] = service_errors
    finally:
        current_sample[0] = None
        function_timings.active_sample = None
        try:
            if service is not None:
                service.shutdown()
                service.pool.waitForDone(max(1000, int(args.shutdown_timeout_seconds * 1000)))
                if app is not None:
                    app.processEvents()
        except Exception as error:
            result.setdefault("cleanupErrors", []).append(dict(service=_safe_text(error)))
        try:
            if session is not None:
                session.shutdown()
                session._pool.waitForDone(max(1000, int(args.shutdown_timeout_seconds * 1000)))
        except Exception as error:
            result.setdefault("cleanupErrors", []).append(dict(session=_safe_text(error)))
        try:
            if runtime is not None:
                runtime.shutdown()
            if factories is not None:
                factories.invalidate()
        except Exception as error:
            result.setdefault("cleanupErrors", []).append(dict(runtime=_safe_text(error)))
        result["stageEvents"] = stage_events
        result["finishedAtUtc"] = _utc_now()
        result["totalProcessElapsedMs"] = (time.perf_counter_ns() - run_start_ns) / 1_000_000
        result["status"] = "passed" if result.get("failure") is None else "failed"
        result["serviceErrors"] = service_errors
        _write_json(output, result)
    return 0 if result["status"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Read-only E:/codex/deliveries root")
    parser.add_argument("--repo", default=str(REPO_ROOT), help="Source checkout to measure (defaults to this tool's checkout)")
    parser.add_argument("--store", required=True, help="New isolated store for import; existing marked store for load")
    parser.add_argument("--output", required=True, help="New JSON result path inside load-speed-1-evidence")
    parser.add_argument("--scenario", choices=("import", "load"), required=True)
    parser.add_argument("--transport", choices=("manual", "verified"), required=True)
    parser.add_argument("--timeout-seconds", type=float, default=900.0,
                        help="Per service operation timeout (default: 900)")
    parser.add_argument("--shutdown-timeout-seconds", type=float, default=60.0,
                        help="Graceful worker drain timeout (default: 60)")
    args = parser.parse_args()
    if not (0 < args.timeout_seconds <= 3600):
        parser.error("--timeout-seconds must be in (0, 3600]")
    if not (0 < args.shutdown_timeout_seconds <= 600):
        parser.error("--shutdown-timeout-seconds must be in (0, 600]")
    try:
        return _run(args)
    except Exception as error:
        print(json.dumps(dict(status="failed-before-measurement", errorType=type(error).__name__,
                              message=_safe_text(error)), ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
