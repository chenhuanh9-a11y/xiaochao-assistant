"""Render shipped QML with synthetic providers, then label the resulting pixels.

No client startup, engine, native hooks, game/window discovery, user settings,
author images, network or clipboard access. The offscreen QPA is mandatory.
Raw captures and their source hashes are kept separately from packaged PNGs.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["QSG_RHI_BACKEND"] = "software"
os.environ["QT_SCALE_FACTOR"] = "1"

from PySide6.QtCore import QObject, Property, Signal, Slot, QEventLoop, QMetaObject, QTimer, QUrl, Qt, QRectF, QPointF, qVersion
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem, QQuickWindow

ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / "experiments" / "qt_quick"
OUTPUT = QML / "help-images"


class Provider(QObject):
    changed = Signal()
    startupAnnouncement = Signal("QVariantMap")
    confirmationRequested = Signal(str, str)

    @Slot()
    def refresh(self):
        pass

    @Slot()
    def cancelShortcutCapture(self):
        pass

    @Slot(str, result=str)
    def detailText(self, _key):
        return ("演示记录 · 非用户日志\n阶段：读取资源\n"
                "原因：示例资源尚未安装\n建议：先查看当前模式资源状态，再导入 ZIP。\n"
                "反馈时请补充发生时间、模式、动作和等待时间。")


def provider(values):
    """Expose fixture data as actual QObject properties to the unmodified QML."""
    properties = {}
    for key, value in values.items():
        properties[key] = Property("QVariant", lambda self, v=value: v, constant=True)
    return type("SyntheticProvider", (Provider,), properties)()


def fixture(phase="idle", next_flows=False, images_root=OUTPUT):
    preferences = dict(
        hotkey="G", actionHotkeys=["F9", "F6", "F7", "F8", "", "", "F11", "", "F10", ""],
        sheetVisibilityEnabled=True, referenceSheetVisible=True, sheetOpacity=.55,
        referenceOpacity=.7, interfaceOpacity=.95, sheetScale=1., gameMode="nightmare",
        routeProfileId="", modeRouteProfiles={}, zhan10CoffinVariantId="",
        orbVisible=True, reduceMotion=False, uiSoundEnabled=False,
        gameControlLayout=dict(schemaVersion=2,
            order=["display", "mode", "manual_map", "recognition", "round"],
            enabled=["display", "round"],
            display=dict(alignedOpacity=True, referenceOpacity=True, referencePosition=True)))
    active = phase == "active"
    preparing = phase == "prepared"
    commands = {key: dict(available=True) for key in
        ("start_round", "end_round", "toggle_round", "set_game_mode", "select_group_member")}
    state = dict(roundActive=active, roundPreparing=preparing,
        roundActionText="结束本局" if active else "取消准备" if preparing else "开始本局",
        commands=commands, mapOpen=active, groupId="demo-only" if active else "",
        groupMemberIds=["demo-a", "demo-b"] if active else [],
        groupMemberRows=[dict(mapId="demo-a", name="示例地图 A"), dict(mapId="demo-b", name="示例地图 B")],
        currentGroupMemberId="demo-a" if active else "", identityPhase="GROUP_LOCKED" if active else "IDLE",
        provisionalMapId="demo-a" if active else "", groupGeneration=1, cycleId=1,
        mapName="示例地图 A" if active else "", mapId="demo-a" if active else "",
        observedFloor="1" if active else "", floor="1" if active else "",
        overlayVisible=active, referenceVisible=active, displayAuthorized=active,
        candidateRows=[], availableFloors=[], controlStatusText="本局进行中" if active else "已准备" if preparing else "等待开始")
    version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    topics = json.loads((QML / "help-topics.json").read_text(encoding="utf-8"))
    for row in topics:
        for step in row["steps"]:
            step["bindings"] = ""
    return {
        "settingsService": provider(dict(values=preferences, persistedValues=deepcopy(preferences),
            busy=False, dirty=False, controlLayoutDirty=False, shortcutRecording=False,
            shortcutCandidateReady=False, shortcutCandidate="", shortcutTarget="", shortcutMessage="",
            coffinVariantOptions=[], coffinVariantContext=dict(mode="nightmare", profileId=""),
            coffinVariantMessage="", message="示例键位以当前设置为准")),
        "session": provider(dict(snapshot=state, busy=False, currentMode="nightmare", appliedMode="nightmare")),
        "clientCoordinator": provider(dict(referenceEditing=False, boundWindow="", gameWindows=[],
            inputEnabled=False, message="演示状态，未连接游戏", runtimeStatus=
            "已准备：请切回游戏并打开地图，等待实际开始。" if preparing else "界面演示，未连接或操作游戏。")),
        "resourceService": provider(dict(busy=False, canCancel=False, pendingKind="", recoveryRequired=False,
            recoveryTransactions=[], recoveryExecutionAllowed=False, recoveryUsesHelper=False,
            cleanupResidues=[], cleanupPending=False,
            packageRows=[dict(packageId="demo-only", title="示例图包（合成状态）", status="installed", fileCount=4, canRemove=True)] if next_flows else [],
            message="示例图包已导入；请核对当前资源列表。" if next_flows else "演示状态：选包后的导入进度与结果在此显示。")),
        "infoService": provider(dict(version="界面演示 · " + str(version.get("displayVersion", "当前源码")),
            checking=False, updatePhase="idle", includeFailureEvidence=False, operation="",
            message="诊断导出前，可先展开最近问题查看原因。", hasError=False,
            diagnosticsReceipt={}, release={}, hasUnread=False, skippedVersion="", history=[],
            historyMessage="", homePreviews=[], helpTopics=topics,
            helpImages={key: QUrl.fromLocalFile(str(images_root / file)).toString() for key, file in
                        (("quick-start", "home.png"), ("shortcuts-visibility", "settings.png"),
                         ("resources-opacity", "resources.png"), ("check-feedback", "help.png"))},
            supportCodes=dict(wechat="", alipay=""),
            facts=dict(displayEnvironment="离屏 Qt · 合成演示状态", runtimeStatus="未连接游戏"))),
        "problemService": provider(dict(writeError="", recent=[dict(id="demo-problem", localTime="示例时间",
            phaseLabel="资源", title="示例资源尚未安装", count=1,
            summary="这是帮助图的演示记录；请在实际使用时查看本次运行的原因。")])),
        "manualSelection": provider(dict(snapshot=dict(visible=True, canOpen=True, canSetMode=True,
            mode="auto", gameMode="nightmare", stage="maps", groupName="演示路线分组", canBack=True,
            canChoose=True, revision=1, page=0, pageCount=1, reason="", keyLabels={},
            message="演示状态：先选路线分组，再选择地图；选图入口快捷键默认未绑定。",
            options=[dict(id="demo-" + letter, name="示例地图 " + letter, kind="map", thumbnailUrl="") for letter in "ABCD"]))),
        "plazaService": provider(dict(snapshot=dict(loggedIn=False, busy=False, canCancel=False,
            canSearch=True, loaded=True, selectedRouteId="", stage="ready",
            message="演示内容：游客可浏览，登录后可下载至图库。",
            rows=[dict(versionId="demo-only", title="示例路线（演示内容）", description="合成说明，没有加载线上路线或作者图。",
                       author="演示作者", mapCount=4, downloadCount=0, updatedDate="示例日期")]))),
        "windows": provider({}), "onboardingService": provider({}),
    }


def pump(milliseconds=150):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def visual_item(root, name):
    item = root.contentItem() if isinstance(root, QQuickWindow) else root
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = visual_item(child, name)
        if found is not None:
            return found
    return None


def render(filename, evidence, name, *, phase="idle", width=1104, height=700, properties=None, setup=None, check=None, crop=None, next_flows=False, images_root=OUTPUT):
    engine = QQmlEngine()
    providers = fixture(phase, next_flows, images_root)
    for key, value in providers.items():
        engine.rootContext().setContextProperty(key, value)
    warnings = []
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(QML / filename)))
    if component.isError():
        raise RuntimeError("\n".join(row.toString() for row in component.errors()))
    root = component.create()
    if root is None:
        raise RuntimeError("Failed to instantiate " + filename)
    if isinstance(root, QQuickWindow):
        window = root
        root.setProperty("expandedHeightLimit", 900)
    else:
        window = QQuickWindow()
        window.resize(width, height)
        window.setColor(QColor("#f1f5fb"))
        root.setParentItem(window.contentItem())
        root.setWidth(width)
        root.setHeight(height)
    window.setFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
    for key, value in (properties or {}).items():
        root.setProperty(key, value)
    pump(1)
    if setup:
        setup(root)
    window.show()
    pump(220)
    if check:
        check(root)
        pump(80)
    image = window.grabWindow()
    if image.isNull():
        raise RuntimeError("Qt returned an empty offscreen image: " + filename)
    raw_path = evidence / (name + ".png")
    if not image.save(str(raw_path)):
        raise RuntimeError("Cannot save " + str(raw_path))
    result = image
    crop_rect = None
    if crop:
        item = root.findChild(QQuickItem, crop)
        if item is None:
            raise RuntimeError("Missing actual QML item " + crop)
        item = item.parentItem().parentItem()
        point = item.mapToScene(QPointF(0, 0))
        crop_rect = [round(point.x()), round(point.y()), round(item.width()), round(item.height())]
        result = image.copy(*crop_rect)
    record = dict(qml=filename, raw=raw_path.name, width=image.width(), height=image.height(),
                  crop=crop_rect, phase=phase, warnings=warnings,
                  sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest())
    window.hide()
    root.setParent(None)
    root.deleteLater()
    if window is not root:
        window.deleteLater()
    pump(1)
    engine.deleteLater()
    pump(1)
    if warnings:
        raise RuntimeError("QML warnings in " + filename + ":\n" + "\n".join(warnings))
    return result, record


class Card:
    def __init__(self, title, subtitle, height):
        self.image = QImage(1200, height, QImage.Format_ARGB32)
        self.image.fill(QColor("#f1f5fb"))
        self.painter = QPainter(self.image)
        self.painter.setRenderHint(QPainter.Antialiasing)
        self.text(40, 28, 1120, 56, title, 32, "#142b4b", True)
        self.text(40, 84, 1120, 58, subtitle, 19)
        self.text(40, height - 48, 1120, 35, "实际 Qt 界面 · 合成演示状态 · 示例键位以当前设置为准", 16, "#526785")

    def text(self, x, y, w, h, value, size=21, color="#354f75", bold=False):
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(size)
        font.setBold(bold)
        self.painter.setFont(font)
        self.painter.setPen(QColor(color))
        self.painter.drawText(QRectF(x, y, w, h), Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, value)

    def label(self, number, text, x, y, w=1100):
        self.painter.setPen(Qt.NoPen)
        self.painter.setBrush(QColor("#2f66eb"))
        self.painter.drawEllipse(QRectF(x, y + 5, 30, 30))
        self.text(x + 9, y, 26, 40, str(number), 18, "white", True)
        self.text(x + 44, y, w - 44, 42, text, 22, "#17365e", True)

    def shot(self, shot, x, y, max_width, max_height):
        scale = min(max_width / shot.width(), max_height / shot.height())
        target = QRectF(x, y, shot.width() * scale, shot.height() * scale)
        self.painter.drawImage(target, shot)
        self.painter.setPen(QPen(QColor("#ccd8eb"), 1))
        self.painter.setBrush(Qt.NoBrush)
        self.painter.drawRoundedRect(target, 4, 4)

    def save(self, path):
        self.painter.end()
        if not self.image.save(str(path)):
            raise RuntimeError("Cannot save " + str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--evidence", type=Path, default=ROOT / "outputs" / "qt-help-render")
    parser.add_argument("--font", type=Path, action="append", help="Installed font file (repeatable); Windows defaults to Microsoft YaHei regular/bold.")
    parser.add_argument("--include-next-flows", action="store_true", help="After integration is verified, include actual manual-picker and plaza QML plus the installed-package fixture.")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.evidence = args.evidence.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    args.evidence.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication.instance() or QGuiApplication([])
    # Windows offscreen QPA does not enumerate system fonts. Register installed
    # fonts explicitly; never ship/copy them or accept tofu as a valid capture.
    fonts = args.font or [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
                          for name in ("msyh.ttc", "msyhbd.ttc")]
    for path in fonts:
        if not path.is_file() or QFontDatabase.addApplicationFont(str(path)) < 0:
            raise RuntimeError("Cannot load required installed font: " + str(path))
    if "Microsoft YaHei UI" not in QFontDatabase.families():
        raise RuntimeError("Microsoft YaHei UI is required by the actual QML; supply its installed font with --font")
    sys.path.insert(0, str(QML))
    from client_theme import apply_light_theme
    apply_light_theme(app)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    records = []
    used_qml = ["ClientHome.qml", "ClientControls.qml", "ClientSettings.qml", "ClientResources.qml",
                "ClientHelp.qml", "ClientProblemsPanel.qml", "ClientPalette.qml", "AppButton.qml",
                "AppSlider.qml", "ClientChestAutoTap.qml"]
    if args.include_next_flows:
        used_qml += ["ClientManualSelection.qml", "ClientManualWindow.qml", "ClientPlaza.qml"]
    source_files = [QML / name for name in used_qml] + sorted((QML / "icons").glob("*.png")) + [QML / "help-topics.json", QML / "client_theme.py", ROOT / "version.json", Path(__file__)]
    def source_hashes():
        return {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    initial_sources = source_hashes()

    def capture(filename, name, **kwargs):
        image, record = render(filename, args.evidence, name, next_flows=args.include_next_flows, images_root=args.output, **kwargs)
        records.append(record)
        return image

    idle = capture("ClientHome.qml", "home-idle", crop="homeRoundStats")
    prepared = capture("ClientHome.qml", "home-prepared", phase="prepared", crop="homeRoundStats")
    active = capture("ClientControls.qml", "controls-group", phase="active")
    display = capture("ClientControls.qml", "controls-display")
    orb = capture("ClientControls.qml", "controls-collapsed", properties={"collapsed": True})
    settings = capture("ClientSettings.qml", "settings", height=740, properties={"advancedDisplay": True})
    resources = capture("ClientResources.qml", "resources", height=300 if args.include_next_flows else 400)
    manual = capture("ClientManualWindow.qml", "manual-picker") if args.include_next_flows else None
    plaza = capture("ClientPlaza.qml", "plaza", height=320) if args.include_next_flows else None

    def expand_problem(root):
        panel = root.findChild(QObject, "recentProblemsPanel")
        panel.setProperty("expanded", True)
        panel.setProperty("openedId", "demo-problem")

    help_image = capture("ClientHelp.qml", "help", height=800, setup=expand_problem)
    if source_hashes() != initial_sources:
        raise RuntimeError("QML/source files changed during rendering; rerun after integration stabilizes")
    card = Card("快速开始", "准备 → 打开游戏地图 → 实际开始；按当前状态操作。", 1700 if args.include_next_flows else 1100)
    card.label(1, "点“开始本局”", 40, 150, 740)
    card.shot(idle, 40, 202, 740, 260)
    card.label(2, "“已准备”仍需打开游戏地图", 40, 490, 740)
    card.shot(prepared, 40, 542, 740, 260)
    card.label(3, "实际开始后可结束", 820, 150, 340)
    card.shot(active, 820, 202, 340, 665)
    card.text(40, 850, 740, 80, "准备不等于开始；“取消准备”只取消等待。\n地图和楼层默认自动识别。", 23)
    card.text(40, 940, 1120, 70, "“选择本组地图”只列出当前相似组成员；图中的 A/B 为演示，\n并非生产相似组名单，也不是全图库手动选图。", 20)
    if manual is not None:
        card.label(4, "独立入口：“选择本局地图”", 40, 1030)
        card.shot(manual, 40, 1080, 660, 560)
        card.text(750, 1090, 410, 230, "先选路线分组，再选择地图。\n\n自动识别和手动选图是两种选图方式；指定地图后，楼层识别与贴合继续自动进行。", 23)
        card.text(750, 1370, 410, 170, "手动选图入口快捷键默认未绑定，可在设置中录入。\n\n截图中的地图名称均为演示，不使用作者图。", 21)
    card.save(args.output / "home.png")

    card = Card("快捷键与显隐", "录入 → 确认到草稿 → 保存更改；示例 G/F9/F10/F11 以当前设置为准。", 1320)
    card.label(1, "快捷键和显示设置", 40, 145)
    card.shot(settings, 40, 198, 1120, 740)
    card.label(2, "贴合、定点独立开关", 40, 955, 650)
    card.shot(display, 40, 1010, 330, 240)
    card.text(390, 1012, 400, 150, "“收起”只折叠控件。\n更多菜单中的“隐藏控件”\n或当前控件快捷键隐藏整个控件。", 21)
    card.shot(orb, 830, 1015, 80, 80)
    card.text(930, 1012, 230, 100, "点击“展开”\n恢复面板", 22)
    card.text(390, 1160, 770, 80, "“编辑位置”后拖动定点地图，点“完成”保存；\n贴合显隐不会同步切换定点地图。", 21)
    card.save(args.output / "settings.png")

    card = Card("资源与透明度", "选择本地 ZIP 后查看校验、导入进度与结果，再核对资源列表。", 1340 if args.include_next_flows else 1000)
    card.label(1, "本地 ZIP：导入、更新与卸载" if args.include_next_flows else "导入 ZIP：普通新增自动导入，必要替换先确认", 40, 145)
    card.shot(resources, 40, 200, 1120, 300 if args.include_next_flows else 400)
    if plaza is not None:
        card.label(2, "路线广场：游客浏览，登录后下载到图库", 40, 530)
        card.shot(plaza, 40, 585, 1120, 320)
    lower = 940 if args.include_next_flows else 630
    card.label(3 if args.include_next_flows else 2, "贴合与定点的不透明度分别调整", 40, lower)
    card.shot(display, 40, lower + 58, 340, 245)
    card.text(410, lower + 60, 750, 120, "0% 完全透明，100% 完全不透明。\n看不见时，检查对应显隐开关、当前地图状态与不透明度。", 24)
    card.text(410, lower + 195, 750, 100, "下载副本可使用、复制编辑或删除；内置图库不可删除。" if args.include_next_flows else "重复、取消或失败按结果提示处理。\n此图展示无已安装作者图的演示状态。", 21)
    card.save(args.output / "resources.png")

    card = Card("检查与反馈", "先看最近问题，记录发生步骤，再按需要导出诊断。", 1100)
    card.label(1, "展开记录，选中技术详情文字后复制", 40, 145)
    card.shot(help_image, 40, 200, 1120, 740)
    card.text(40, 952, 1120, 95, "导出诊断 ZIP 后核对文件路径；附带失败画面须主动勾选。\n只使用已保存的失败画面，不截取新画面；分享前先检查内容。", 22)
    card.save(args.output / "help.png")

    # Verify the real help card's lazy-image consumer as well as the source PNGs.
    # Emitting a signal on an offscreen Qt object never sends a native input.
    for topic in ("quick-start", "shortcuts-visibility", "resources-opacity", "check-feedback"):
        def open_image(root, key=topic):
            toggle = visual_item(root, "helpImageToggle-" + key)
            if toggle is None or not QMetaObject.invokeMethod(toggle, "clicked"):
                raise RuntimeError("Missing help image toggle " + key)

        def check_image(root, key=topic):
            image = visual_item(root, "helpOperationImage-" + key)
            if image is None or image.property("paintedWidth") <= 0 or image.property("paintedHeight") <= 0:
                raise RuntimeError("Help card did not load its PNG: " + key)
            expression = QQmlExpression(QQmlEngine.contextForObject(image), image, "status === 1")
            result = expression.evaluate()
            ready = result[0] if isinstance(result, tuple) else result
            if expression.hasError() or ready is not True:
                raise RuntimeError("Help card Image is not Ready: " + key)
            scroller = root.findChild(QObject, "helpPageScroll").property("contentItem")
            scroller.setProperty("contentY", max(0., image.mapToScene(QPointF(0, 0)).y() - 100))

        capture("ClientHelp.qml", "consumer-" + topic, height=900,
                properties={"openedTopic": topic}, setup=open_image, check=check_image)

    if source_hashes() != initial_sources:
        raise RuntimeError("QML/source files changed during rendering; rerun after integration stabilizes")

    manifest = dict(schema="qt-help-offscreen-render-v1", qt=qVersion(),
        platform=app.platformName(), backend="software", synthetic=True,
        includedNextFlows=args.include_next_flows,
        fonts={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in fonts},
        scope="Actual repository QML pixels; fixture providers only. No game, native input, author images or user data. Not an end-to-end game/import test.",
        captures=records,
        sources=initial_sources,
        images={p.name: dict(bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                for p in sorted(args.output.glob("*.png"))})
    (args.evidence / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(dict(output=str(args.output), evidence=str(args.evidence), captures=len(records), qmlWarnings=sum(len(r["warnings"]) for r in records)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
