"""Fixed import diagnostic in an owned copy of the real frozen installation.

No scripts, arbitrary packages, installation-root overrides or user profiles are
accepted. This module does not launch anything or change the existing guard.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import zipfile

SCHEMA = 'xingtu-owned-import-audit-v1'
PACKAGES = {
    'difficult-zhan10': (24875493, 'e6412509db94c987834d8434851c93333dedabdd7918398a6a927d64261045aa'),
    'nightmare-suju': (31090470, 'f28d5365f72a66e24ec82a85c5b7f3109687399c7020ca9a1a8edc889bc5e63f'),
    'nightmare-nightingale': (25900349, '68876553f298dcb6154b926244400b4b1c00354bd2f346d56a501c5e76856922'),
    'nightmare-wang-qingxin': (37033947, '764e875a34566ad0b96121e52dd7a059cee5780b3aada11c1c821962e8523ebf'),
    'nightmare-lianghapi': (12536380, '510573c566b0e942d4658af536f6661399d207e6e69c6c071c04bb7c4517cf2e'),
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def no_links(path, *, stop_at=None):
    """Reject reparse components, optionally stopping at an owned root inclusive.

    Default pre-guard identity validation still checks every parent. Bounded
    post-guard calls validate lexical ancestry before any filesystem request.
    """
    path = Path(path)
    parts = (path, *path.parents)
    if stop_at is not None:
        boundary = Path(stop_at)
        if (not path.is_absolute() or not boundary.is_absolute()
                or '..' in path.parts or '..' in boundary.parts or boundary not in parts):
            raise ValueError('import-audit-invalid-link-boundary')
        parts = parts[:parts.index(boundary) + 1]
    for part in parts:
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('import-audit-reparse-point')


def read_json(path):
    no_links(path)
    if Path(path).stat().st_size > 128 * 1024:
        raise ValueError('import-audit-metadata-too-large')
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_owned_run(run_root, nonce, executable):
    """Validate data only. Does not claim or execute a run; usable by unit tests."""
    root = Path(run_root)
    if (not root.is_absolute() or str(root).startswith(('\\\\', '//'))
            or not re.fullmatch(r'xingtu-import-audit-[0-9a-f]{32}', root.name)
            or not re.fullmatch(r'[0-9a-f]{32}', nonce)):
        raise ValueError('import-audit-invalid-owned-root')
    no_links(root)
    if root != root.resolve():
        raise ValueError('import-audit-noncanonical-root')
    installation = root / 'installation'
    no_links(installation)
    exe = Path(executable)
    no_links(exe)
    if exe.parent != installation or exe.name != '第五人格小抄助手.exe':
        raise ValueError('import-audit-executable-not-owned-copy')
    owner = read_json(root / 'ownership.json')
    if (owner.get('schema') != SCHEMA or owner.get('nonce') != nonce
            or owner.get('runRoot') != str(root) or owner.get('installation') != str(installation)
            or owner.get('executableSha256') != sha256(exe)
            or not re.fullmatch(r'[0-9A-F]{8}', str(owner.get('buildFingerprint', '')))
            or not isinstance(owner.get('createdAtUTC'), str)):
        raise ValueError('import-audit-ownership-mismatch')
    marker = root / 'owner.marker'
    no_links(marker)
    if marker.read_text(encoding='ascii') != nonce:
        raise ValueError('import-audit-marker-mismatch')
    release = read_json(installation / 'release-report.json')
    if (release.get('buildFingerprint') != owner['buildFingerprint']
            or str(release.get('executableSha256', '')).lower() != owner['executableSha256']
            or release.get('sourceDirty') is not False):
        raise ValueError('import-audit-release-identity-mismatch')
    manifest = read_json(root / 'inputs.json')
    if not isinstance(manifest, list) or len(manifest) != len(PACKAGES):
        raise ValueError('import-audit-input-set')
    inputs, seen = [], set()
    for row in manifest:
        if not isinstance(row, dict) or set(row) != {'packageId', 'path', 'bytes', 'sha256'}:
            raise ValueError('import-audit-input-fields')
        package = row['packageId']
        if package not in PACKAGES or package in seen:
            raise ValueError('import-audit-input-set')
        seen.add(package)
        path = root / 'inputs' / (package + '.zip')
        if row['path'] != 'inputs/' + package + '.zip':
            raise ValueError('import-audit-input-path')
        no_links(path)
        size, digest = PACKAGES[package]
        if (type(row['bytes']) is not int or row['bytes'] != size or row['sha256'] != digest
                or path.stat().st_size != size or sha256(path) != digest):
            raise ValueError('import-audit-input-integrity')
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo('map-package.json')
            if info.file_size > 2 * 1024 * 1024:
                raise ValueError('import-audit-input-manifest-size')
            if json.loads(archive.read(info)).get('packageId') != package:
                raise ValueError('import-audit-input-package-id')
        inputs.append(dict(row, absolutePath=str(path)))
    for name in ('import-started.json', 'ui-audit.json', 'import-scenario.json'):
        if (root / name).exists():
            raise ValueError('import-audit-already-started')
    return dict(output=root, installation=installation, ownership=owner, inputs=inputs)


def prepare_import_audit(argv):
    if len(argv) != 4 or argv[1] != '--ui-import-audit':
        raise SystemExit('Usage: application.exe --ui-import-audit OWNED_RUN_ROOT NONCE')
    if not getattr(sys, 'frozen', False) or not getattr(sys, '_MEIPASS', None):
        raise SystemExit('--ui-import-audit requires the actual packaged executable')
    try:
        data = validate_owned_run(argv[2], argv[3], sys.executable)
        meipass = Path(sys._MEIPASS)
        no_links(meipass)
        if meipass != data['installation'] / '_internal':
            raise ValueError('import-audit-meipass-not-owned-copy')
        with (data['output'] / 'import-started.json').open('x', encoding='utf-8') as stream:
            json.dump(dict(schema=SCHEMA, nonce=argv[3], pid=os.getpid(), executable=sys.executable), stream)
        return data
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise SystemExit(f'Import audit refused: {error}') from error


def _inventory(root, boundary):
    root = Path(root)
    boundary = Path(boundary)
    if (not root.is_absolute() or not boundary.is_absolute()
            or '..' in root.parts or '..' in boundary.parts
            or (root != boundary and boundary not in root.parents)):
        raise ValueError('import-audit-invalid-inventory-boundary')
    no_links(boundary, stop_at=boundary)
    if not root.exists():
        return {}
    no_links(root, stop_at=boundary)
    result = {}
    for path in sorted(root.rglob('*')):
        no_links(path, stop_at=boundary)
        if path.is_file():
            result[str(path.relative_to(root))] = sha256(path)
    return result


def run_import_scenario(assistant, ui, output, pump, stage, inputs):
    """Invoke real widgets/workers in the frozen constructor's isolated copy.

    The single I/O seam is askopenfilename, returning exact validated ZIP paths.
    Native file-dialog interaction is deliberately not claimed.
    """
    import map_resource_browser
    from PIL import Image
    from star_map_import import verified_installed_package

    output = Path(output)
    installation = Path(sys.executable).parent
    if installation != output / 'installation':
        raise RuntimeError('Import scenario must run in the owned native PE copy')
    report = dict(status='running', cases=[], failures=[],
        seams=['filedialog.askopenfilename returns exact fixed approved ZIP path; native dialog not clicked'],
        executable=sys.executable, installation=str(installation), rootOverride=False)
    report['explicitBrowseTransitions'] = []
    report['widgetActions'] = []
    report_path = output / 'import-scenario.json'

    def save():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def snapshot():
        return assistant.map_package_import_snapshot()

    def require(value, reason):
        if not value:
            raise RuntimeError(reason)

    def wait(label, getter, done, timeout=35):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = getter()
            if state.get('stage') == 'error':
                raise RuntimeError(label + ': ' + json.dumps(state, ensure_ascii=False))
            if done(state):
                return state
            pump(20)
        raise TimeoutError(label + ': ' + json.dumps(getter(), ensure_ascii=False))

    def preference():
        return dict(mode=assistant.game_mode,
            route=getattr(assistant, 'route_profile_id', None),
            preferred=getattr(assistant, 'preferred_route_profile_id', None),
            modeRoutes=dict(getattr(assistant, 'mode_route_profiles', {})))

    # All declared base dependencies are protected, rather than only 82 assumed
    # filenames. Check present custom libraries too, including the isolated root.
    dependencies = set()
    for item in inputs:
        with zipfile.ZipFile(item['absolutePath']) as archive:
            metadata = json.loads(archive.read('map-package.json'))
            dependencies.update(row['path'] for row in metadata['requiresFiles'])
    protected = {name: sha256(installation / name) for name in sorted(dependencies)}
    custom_roots = [output / 'custom', installation / 'assets' / 'custom-maps']
    # An owned fixture makes preservation meaningful even for an empty fresh
    # profile. It is not a user route and is never copied from a user directory.
    custom_roots[0].mkdir(exist_ok=True)
    with (custom_roots[0] / 'audit-preservation-marker.bin').open('xb') as marker:
        marker.write(b'owned diagnostic fixture: preserve during author import\n')
    custom_before = {str(path): _inventory(path, output) for path in custom_roots}
    report.update(protectedCoreCount=len(protected), protectedCustomRoots=list(custom_before))
    browser = ui.map_browser
    original_dialog = map_resource_browser.filedialog.askopenfilename
    initial_mode = assistant.game_mode
    initial_browse_mode = browser.browse_mode
    initial_preference = preference()

    def invoke(widget, label):
        require(str(widget.cget('state')) != 'disabled', label + ': widget disabled')
        report['widgetActions'].append(dict(action=label, widget=str(widget),
            widgetClass=widget.winfo_class(), text=str(widget.cget('text')),
            manager=widget.winfo_manager()))
        stage(label, widget.invoke, 50)

    def browse_ready():
        context = browser._browse_context
        return dict(stage='error' if context is not None and context.error else
            'ready' if context is not None and context.mode == browser.browse_mode
            and browser._browse_work is None and browser._browse_pending is None else 'checking',
            mode=browser.browse_mode, message=context.error if context is not None else '')

    def choose_package(package, label):
        wait(label + '_context', browse_ready, lambda value: value['stage'] == 'ready')
        require(package in browser._row_data, 'Imported package missing from new page installed rows')
        require(package in browser._card_records, 'Imported package has no actual author card')
        frame = browser._card_records[package]['frame']
        # The current page creates this real button in _make_author_card but
        # does not retain its handle. Locate that exact child instead of
        # invoking the hidden inherited gallery or calling _select_package.
        buttons = [child for child in frame.winfo_children()
                   if hasattr(child, 'invoke') and str(child.cget('text')) == '查看图包']
        require(len(buttons) == 1, 'Actual author-card 查看图包 button not unique')
        invoke(buttons[0], label + '_view_package_button')
        require(browser._selected_package == package and browser._detail_open,
                'Author card did not select the requested package')
        selected = browser._row_data[package]
        profile = selected.get('profile')
        require(profile is not None and profile.package_id == package,
                'Selected new-page profile belongs to another package')
        require(tuple(browser._map_items) == tuple(profile.maps),
                'New-page map selector does not use selected package profile')
        return selected

    def combobox_event(widget, index, label):
        report['widgetActions'].append(dict(action=label, widget=str(widget),
            widgetClass=widget.winfo_class(), event='<<ComboboxSelected>>', index=index,
            manager=widget.winfo_manager()))
        def select():
            widget.current(index)
            widget.event_generate('<<ComboboxSelected>>')
        stage(label, select, 80)

    def import_one(item, label):
        row = dict(packageId=item['packageId'], phase=label, pathKind='owned-input-copy')
        report['cases'].append(row)
        save()
        before = preference()
        calls = []
        def choose(**kwargs):
            calls.append(dict(title=kwargs.get('title'), parentKind=type(kwargs.get('parent')).__name__))
            return item['absolutePath']
        map_resource_browser.filedialog.askopenfilename = choose
        try:
            browser.sync_map_package_import()
            invoke(browser.import_button, label + '_choose')
        finally:
            map_resource_browser.filedialog.askopenfilename = original_dialog
        require(len(calls) == 1, 'Real import button did not request exactly one file selection')
        row['dialogCalls'] = calls
        row['inspection'] = wait(label + '_inspect', snapshot,
            lambda value: value.get('stage') == 'ready')
        browser.sync_map_package_import()
        require(row['inspection'].get('liveAvailable') is True, 'Expected fixed package to use live import')
        require(row['inspection'].get('canImport') is True, 'Inspected package cannot import')
        invoke(browser.import_confirm_button, label + '_confirm')
        row['completion'] = wait(label + '_install', snapshot,
            lambda value: value.get('stage') == 'complete')
        receipt = verified_installed_package(installation, item['packageId'], item['sha256'])
        row['receipt'] = receipt
        require(receipt.get('status') == 'installed', 'Installed receipt/hash validation failed')
        require(preference() == before, 'Import silently changed runtime mode or author preference')
        # The new page has independent browse context and per-package selectors.
        # Its inherited source_box/_cards are hidden compatibility internals.
        browser.refresh()
        wait(label + '_browse', browse_ready, lambda value: value['stage'] == 'ready')
        package_images = []
        with zipfile.ZipFile(item['absolutePath']) as archive:
            metadata = json.loads(archive.read('map-package.json'))
            package_images = [installation / file['path'] for file in metadata['files']
                if Path(file['path']).suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}]
        require(package_images, 'Fixed package has no preview images')
        with Image.open(package_images[0]) as preview:
            preview.load()
            row['decodedPreview'] = dict(size=list(preview.size), mode=preview.mode)
        image_names = {str(path) for path in package_images}
        expected_mode = 'difficult' if item['packageId'] == 'difficult-zhan10' else 'nightmare'
        require(browser.browse_mode == expected_mode, 'Explicit browse mode was not established')
        invoke(browser.tab_buttons['installed'], label + '_installed_tab')
        selected = choose_package(item['packageId'], label)
        row['selectedProfile'] = selected['profile'].profile_id
        row['packageRowKey'] = selected['key']
        from map_resource_model import choose_resource
        choice = None
        for map_index, model_map in enumerate(browser._map_items):
            floor_keys = tuple(browser._browse_context.pack.by_id[model_map.id].layers)
            for floor_index, floor in enumerate(floor_keys):
                binding = choose_resource(model_map, floor, floor)
                if binding and str(binding[2]) in image_names:
                    choice = map_index, floor_index, str(binding[2])
                    break
            if choice:
                break
        require(choice is not None, 'Selected package has no preview binding to its own installed files')
        map_index, floor_index, chosen_path = choice
        combobox_event(browser.map_box, map_index, label + '_map_selector')
        combobox_event(browser.floor_box, floor_index, label + '_floor_selector')
        def pixels_ready():
            widget = browser.detail_image
            request = getattr(widget, '_image_request', None)
            displayed = getattr(widget, '_displayed_image_request', None)
            valid = (request is not None and displayed == request and request[1][0] == chosen_path
                     and widget.cget('image') and getattr(widget, '_map_photo', None) is not None)
            return dict(stage='ready' if valid else 'checking',
                        text=str(widget.cget('text')), selectedPackage=browser._selected_package)
        wait(label + '_detail_pixels', pixels_ready, lambda value: value['stage'] == 'ready')
        require(browser._selected_package == item['packageId'], 'Detail package changed during preview')
        row['actualDetailPreview'] = dict(profile=selected['profile'].profile_id,
            pathKind='owned-installed-author-image', widget=str(browser.detail_image),
            mapWidget=str(browser.map_box), floorWidget=str(browser.floor_box),
            tkImage=str(browser.detail_image.cget('image')), mapIndex=map_index, floorIndex=floor_index)
        require(preference() == before, 'Browse preview changed runtime mode or author selection')
        save()

    try:
        stage('import_resource_page', ui._nav_buttons['maps'].invoke, 100)
        for index, item in enumerate(inputs):
            prefix = str(index) + '_' + item['packageId']
            target_mode = 'difficult' if item['packageId'] == 'difficult-zhan10' else 'nightmare'
            if browser.browse_mode != target_mode:
                before_switch = preference()
                old_browse_mode = browser.browse_mode
                invoke(browser.resource_mode_buttons[target_mode], prefix + '_explicit_mode_' + target_mode)
                wait(prefix + '_browse_mode_ready', browse_ready, lambda value: value['stage'] == 'ready')
                require(browser.browse_mode == target_mode, 'Browse button failed to select target browse mode')
                require(preference() == before_switch, 'Browse-mode button changed runtime mode or author choice')
                report['explicitBrowseTransitions'].append(dict(before=old_browse_mode, after=browser.browse_mode,
                    runtimeBefore=before_switch, runtimeAfter=preference(), reason='explicit resource browse-mode button'))
            import_one(item, prefix + '_first')
            import_one(item, prefix + '_repeat')
            require(browser._selected_package == item['packageId'], 'Management selection is not imported package')
            # New page manage_button directly calls _manage_selected, which
            # inspects the selected package and captures browse management context.
            invoke(browser.manage_button, prefix + '_manage_selected_package')
            ready = wait(prefix + '_removal_ready', assistant.managed_author_packages_snapshot,
                lambda value: isinstance(value.get('ready'), dict) and not value.get('busy'))
            require(ready['ready'].get('packageId') == item['packageId'], 'Removal selected wrong package')
            browser.sync_managed_packages()
            require(browser._managed_open and browser._management_context == browser._current_management_context(),
                    'New-page management confirmation context is stale')
            invoke(browser.managed_confirm, prefix + '_confirm_backup_remove')
            final = wait(prefix + '_removed', assistant.managed_author_packages_snapshot,
                lambda value: value.get('stage') == 'ready' and not value.get('busy')
                and any(row.get('packageId') == item['packageId'] and row.get('status') == 'not-installed'
                        for row in value.get('packages', [])))
            backups = list((installation / 'work' / 'map-package-removals').glob(item['packageId'] + '-*'))
            require(backups and any((path / 'installed-receipt.json').is_file() for path in backups),
                    'Removal completed without receipt-owned recoverable backup')
            report['cases'].append(dict(packageId=item['packageId'], phase='backup-remove', snapshot=final,
                                        backupCount=len(backups)))
            import_one(item, prefix + '_reimport')
        if browser.browse_mode != initial_browse_mode:
            before_switch = preference()
            old_browse_mode = browser.browse_mode
            invoke(browser.resource_mode_buttons[initial_browse_mode], 'restore_initial_browse_mode')
            wait('restore_initial_browse_ready', browse_ready, lambda value: value['stage'] == 'ready')
            require(preference() == before_switch, 'Restore browse-mode button changed runtime state')
            report['explicitBrowseTransitions'].append(dict(before=old_browse_mode, after=browser.browse_mode,
                runtimeBefore=before_switch, runtimeAfter=preference(), reason='restore initial browse-mode button'))
        final_preference = preference()
        require(final_preference == initial_preference and assistant.game_mode == initial_mode,
                'Scenario changed runtime mode or author preferences')
        report['finalPreference'] = final_preference
        require({name: sha256(installation / name) for name in protected} == protected,
                'Import changed protected base/structure dependency bytes')
        require({str(path): _inventory(path, output) for path in custom_roots} == custom_before,
                'Import changed protected custom library')
        report['status'] = 'completed'
        report['protectedCoreUnchanged'] = report['protectedCustomUnchanged'] = True
    except Exception as error:
        report['status'] = 'failed'
        report['failures'].append(dict(type=type(error).__name__, message=str(error),
            importSnapshot=snapshot(), managedSnapshot=assistant.managed_author_packages_snapshot(),
            preference=preference()))
        save()
        raise
    finally:
        map_resource_browser.filedialog.askopenfilename = original_dialog
        save()
    return report
