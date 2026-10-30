pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Window {
    id: panel
    palette: ClientPalette {}
    objectName: "clientControlsWindow"
    title: "星图 · 游戏控制"
    visible: false
    width: effectiveCollapsed ? 64 : 352
    height: effectiveCollapsed ? 64 : Math.min(expandedHeightLimit, Math.max(243, naturalExpandedHeight))
    property var snapController: null
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
    property bool collapsed: false
    property int expandedHeightLimit: 640
    readonly property bool editing: clientCoordinator.referenceEditing === true
    readonly property bool effectiveCollapsed: collapsed && !editing
    readonly property real naturalExpandedHeight: header.height + 24 + modules.implicitHeight + (editing ? 76 : 0)
    readonly property var preferences: settingsService.values
    readonly property var layout: preferences.gameControlLayout || ({order: [], enabled: [], display: {}})
    readonly property var snapshot: session.snapshot
    readonly property string appliedMode: session.appliedMode || session.currentMode
    readonly property var activeModules: layout.order.filter(function(key) { return layout.enabled.indexOf(key) >= 0 })
    readonly property var actionOrder: ["toggle-panel", "candidate-prev", "candidate-next", "confirm-round", "cycle-floor", "end-round", "toggle-sheet-visibility", "start-round", "toggle-round"]
    function available(action) {
        return !!snapshot.commands && !!snapshot.commands[action] && snapshot.commands[action].available
               && !snapshot.resourceLocked && (!session.busy || action === "end_round")
    }
    function modeAvailable() {
        return !!snapshot.commands && !!snapshot.commands.set_game_mode && snapshot.commands.set_game_mode.available
               && !snapshot.roundActive && !snapshot.roundPreparing && !snapshot.resourceLocked
               && !snapshot.contextPending && !settingsService.busy
               && (!session.busy || snapshot.pendingAction === "refresh_map")
    }
    function provisionalGroup() {
        return snapshot.identityPhase === "GROUP_LOCKED" || (snapshot.groupLocked
            && snapshot.identityPhase !== "MEMBER_VERIFIED" && snapshot.identityPhase !== "MANUAL_MEMBER_CONFIRMED")
    }
    function mapText() {
        if (snapshot.identityPhase === "MANUAL_MEMBER_CONFIRMED") {
            var id = snapshot.currentGroupMemberId || snapshot.manualGroupMemberId || snapshot.mapId || ""
            var rows = snapshot.groupMemberRows || []
            var row = rows.find(function(value) { return value.mapId === id })
            return row && row.name ? row.name : snapshot.mapName || id
        }
        if (provisionalGroup()) return snapshot.provisionalMapId ? "暂用 " + snapshot.provisionalMapId : "相似组 " + (snapshot.groupId || "")
        return snapshot.mapName || snapshot.mapId || ""
    }
    function statusText() {
        if (snapshot.contextPending) return "正在切换模式或路线"
        if (session.busy && snapshot.pendingAction === "start_round") return "正在准备本局"
        if (snapshot.roundPreparing) return "已准备 · 打开游戏地图后开始"
        if (snapshot.identityPhase === "MANUAL_MEMBER_CONFIRMED") return "本局已手动选择 · " + (snapshot.displayAuthorized ? outputStatus() : "位置待核对")
        if (provisionalGroup()) return snapshot.displayAuthorized ? "相似组已锁定 · " + outputStatus() : "相似组已锁定 · 位置待核对"
        if (snapshot.identityPhase === "MEMBER_VERIFIED") return snapshot.displayAuthorized ? "地图已核对 · " + outputStatus() : "地图已核对 · 位置待核对"
        if (!clientCoordinator.boundWindow) {
            return snapshot.roundActive || snapshot.mapOpen || snapshot.mapId || snapshot.observedFloor ? "游戏连接待核对" : "等待游戏"
        }
        if (!snapshot.roundActive) return "游戏已连接 · 等待开始本局"
        if (snapshot.mapOpen) return snapshot.displayAuthorized ? "地图已打开 · " + outputStatus() : "地图已打开 · 正在核对"
        return "本局进行中"
    }
    function outputStatus() {
        return preferences.sheetVisibilityEnabled === false ? "贴合已隐藏" + (shortcut("toggle-sheet-visibility") ? " · " + shortcut("toggle-sheet-visibility") + "显示" : "") : "贴合已就绪"
    }
    function shortcut(action) {
        var index = actionOrder.indexOf(action)
        return index >= 0 && preferences.actionHotkeys ? preferences.actionHotkeys[index] || "" : ""
    }
    function commit(key) { settingsService.saveFields([key]) }
    function savePosition() {
        settingsService.setValue("compactPosition", [Math.round(x), Math.round(y)])
        commit("compactPosition")
    }
    function floorText() {
        if (snapshot.controlFloorText) return snapshot.controlFloorText
        var floor = snapshot.observedFloor
        return floor === "B1" ? "观测楼层 B1" : floor === "1" || floor === "2" ? "观测楼层 F" + floor : "楼层待核对"
    }
    function visibilityText(value, yes, no, unknown) { return value === true ? yes : value === false ? no : unknown }

    component ControlButton: AppButton {
        property string hint: ""
        implicitHeight: 34; font.pixelSize: 13; font.bold: true; padding: 6
        ToolTip.visible: hovered && hint.length > 0
        ToolTip.text: hint
        ToolTip.delay: 450
    }
    component KeyHint: Label {
        color: "#5c677d"; font.family: "Microsoft YaHei UI"; font.pixelSize: 11
        wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter
        visible: text.length > 0; Layout.fillWidth: true
    }
    component PanelDrag: MouseArea {
        property bool expandOnClick: false
        property point previousPoint
        property point pressPoint
        property real travel: 0
        property bool dragging: false
        onPressed: function(mouse) {
            previousPoint = mapToGlobal(mouse.x, mouse.y)
            pressPoint = previousPoint; travel = 0; dragging = false
        }
        onPositionChanged: function(mouse) {
            if (!pressed) return
            var point = mapToGlobal(mouse.x, mouse.y)
            travel = Math.max(travel, Math.abs(point.x - pressPoint.x), Math.abs(point.y - pressPoint.y))
            if (travel > 3 && !dragging) { dragging = true; if (panel.snapController) panel.snapController.beginDrag() }
            panel.x += point.x - previousPoint.x; panel.y += point.y - previousPoint.y
            previousPoint = point
        }
        onReleased: {
            if (expandOnClick && travel <= 3) windows.toggleCollapsed("controls")
            else if (travel > 3) { if (panel.snapController) panel.snapController.releaseDrag(); panel.savePosition() }
        }
    }
    component OpacityControl: ColumnLayout {
        id: cell
        required property string settingKey
        required property string title
        property bool edited: false
        Layout.fillWidth: true; spacing: 0
        RowLayout {
            Layout.fillWidth: true
            Label { text: cell.title; color: "#5c677d"; font.family: "Microsoft YaHei UI"; font.pixelSize: 12; Layout.fillWidth: true }
            Label { text: Math.round(panel.preferences[cell.settingKey] * 100) + "%"; color: "#142035"; font.family: "Microsoft YaHei UI"; font.bold: true; font.pixelSize: 12 }
        }
        AppSlider {
            Layout.fillWidth: true; implicitHeight: 32
            from: 0; to: 100; stepSize: 1
            value: Math.round(panel.preferences[cell.settingKey] * 100)
            Accessible.name: cell.title
            onMoved: { cell.edited = true; settingsService.setValue(cell.settingKey, value / 100) }
            onPressedChanged: if (!pressed && cell.edited) { panel.commit(cell.settingKey); cell.edited = false }
            Keys.onReleased: if (cell.edited) { panel.commit(cell.settingKey); cell.edited = false }
        }
    }

    Rectangle {
        objectName: "controlExpandedSurface"
        anchors.fill: parent; visible: !panel.effectiveCollapsed
        radius: 12; color: Qt.rgba(1, 1, 1, panel.preferences.interfaceOpacity)
        Item {
            id: header
            anchors.top: parent.top; anchors.topMargin: 8
            anchors.left: parent.left; anchors.right: parent.right; anchors.leftMargin: 12; anchors.rightMargin: 12
            height: 76
            ColumnLayout {
                anchors.left: parent.left; anchors.right: tools.left; anchors.rightMargin: 8; anchors.top: parent.top
                spacing: 3
                Label { objectName: "controlStatusText"; text: panel.statusText(); color: "#5c677d"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
                Label { objectName: "controlMapFloorText"; text: panel.mapText() + (panel.mapText() ? " · " : "") + panel.floorText(); color: "#5c677d"; font.family: "Microsoft YaHei UI"; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
            }
            Label {
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.bottomMargin: 8
                text: (panel.preferences.sheetVisibilityEnabled === false ? "贴合已隐藏" + (panel.shortcut("toggle-sheet-visibility") ? "/" + panel.shortcut("toggle-sheet-visibility") : "") : panel.visibilityText(panel.snapshot.overlayVisible, "贴合已显示", "贴合暂不可见", "贴合状态待核对")) + " · " + panel.visibilityText(panel.snapshot.referenceVisible, "定点已显示", "定点暂不可见", "定点状态待核对")
                color: "#5c677d"; font.family: "Microsoft YaHei UI"; font.pixelSize: 11; elide: Text.ElideRight
            }
            PanelDrag { anchors.fill: parent; anchors.rightMargin: 88 }
            Row {
                id: tools
                anchors.top: parent.top; anchors.right: parent.right; spacing: 4
                ControlButton { objectName: "controlCollapseButton"; text: "收起"; implicitWidth: 40; implicitHeight: 30; enabled: !panel.editing; onClicked: windows.toggleCollapsed("controls") }
                ControlButton {
                    id: moreButton
                    objectName: "controlMoreButton"
                    implicitWidth: 28; implicitHeight: 30
                    Accessible.name: "更多控制操作"
                    contentItem: Canvas {
                        implicitWidth: 16; implicitHeight: 16
                        onPaint: {
                            var context = getContext("2d"); context.clearRect(0, 0, width, height); context.fillStyle = "#1f50cc"
                            for (var i = 0; i < 3; ++i) { context.beginPath(); context.arc(width / 2 - 4 + i * 4, height / 2, 1, 0, Math.PI * 2); context.fill() }
                        }
                    }
                    onClicked: moreMenu.open()
                    Menu {
                        id: moreMenu
                        y: moreButton.height
                        MenuItem { text: "主页"; onTriggered: clientCoordinator.navigate("home") }
                        MenuItem { text: "自定义控件"; onTriggered: clientCoordinator.navigate("settings") }
                        MenuItem { text: "隐藏控件"; onTriggered: windows.hide("controls") }
                        MenuSeparator {}
                        MenuItem { text: "选择地图"; enabled: panel.available("reselect_map"); onTriggered: clientCoordinator.requestManualMapSelection() }
                        MenuItem { text: "重新识别"; enabled: panel.available("capture_now"); onTriggered: session.dispatch("capture_now") }
                        MenuItem { text: "候选与识别"; onTriggered: clientCoordinator.navigate("recognition") }
                    }
                }
            }
        }
        ScrollView {
            id: scroll
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: header.bottom; anchors.bottom: editBar.top
            anchors.leftMargin: 12; anchors.rightMargin: 12; anchors.bottomMargin: 8
            clip: true; contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                id: modules
                width: scroll.availableWidth; spacing: 4
                Repeater {
                    model: panel.activeModules
                    delegate: Loader {
                        required property string modelData
                        Layout.fillWidth: true
                        sourceComponent: modelData === "display" ? displayModule : modelData === "mode" ? modeModule : modelData === "manual_map" || modelData === "map" ? manualMapModule : modelData === "recognition" ? recognitionModule : roundModule
                    }
                }
            }
        }
        ColumnLayout {
            id: editBar
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.leftMargin: 12; anchors.rightMargin: 12; anchors.bottomMargin: panel.editing ? 8 : 0
            height: panel.editing ? 64 : 0; visible: panel.editing; spacing: 3
            Label { text: "正在调整定点位置"; color: "#2f66eb"; font.family: "Microsoft YaHei UI"; font.pixelSize: 12; Layout.fillWidth: true }
            RowLayout {
                Layout.fillWidth: true; spacing: 6
                ControlButton { text: "完成"; Layout.fillWidth: true; onClicked: clientCoordinator.finishReferenceEdit() }
                ControlButton { text: "取消"; Layout.fillWidth: true; onClicked: clientCoordinator.cancelReferenceEdit() }
            }
        }
    }

    Rectangle {
        objectName: "controlCollapsedOrb"
        anchors.fill: parent; visible: panel.effectiveCollapsed
        radius: 32; color: orbDrag.pressed ? "#dce6fc" : "#eaf1ff"
        border.color: "#c0d0e9"; border.width: 1
        Image { width: 30; height: 30; x: 17; y: 8; source: "icons/panel.png"; sourceSize.width: 60; sourceSize.height: 60 }
        Label { anchors.horizontalCenter: parent.horizontalCenter; y: 40; text: "展开"; color: "#2f66eb"; font.family: "Microsoft YaHei UI"; font.pixelSize: 11 }
        PanelDrag { id: orbDrag; anchors.fill: parent; expandOnClick: true; cursorShape: Qt.PointingHandCursor }
    }

    Component {
        id: displayModule
        ColumnLayout {
            spacing: 4
            RowLayout {
                Layout.fillWidth: true; Layout.topMargin: 6; spacing: 6
                ColumnLayout {
                    Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 2
                    ControlButton { objectName: "controlAlignedVisibility"; text: panel.preferences.sheetVisibilityEnabled ? "隐藏贴合" : "显示贴合"; icon.source: "icons/route.png"; selected: panel.preferences.sheetVisibilityEnabled === true; enabled: !settingsService.busy; Layout.fillWidth: true; hint: "切换贴合图显示，识别与地图状态继续保留"; onClicked: clientCoordinator.toggleAlignedVisibility() }
                    KeyHint { text: panel.shortcut("toggle-sheet-visibility") }
                }
                ColumnLayout {
                    Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 2
                    ControlButton { objectName: "controlReferenceVisibility"; text: panel.preferences.referenceSheetVisible ? "隐藏定点" : "显示定点"; icon.source: "icons/map.png"; selected: panel.preferences.referenceSheetVisible === true; enabled: !settingsService.busy; Layout.fillWidth: true; hint: "切换定点地图显示，保留已选择的地图"; onClicked: clientCoordinator.toggleReferenceVisibility() }
                    ControlButton { visible: !!panel.layout.display.referencePosition && !panel.editing; objectName: "controlEditReferencePosition"; text: "编辑位置"; hint: "拖动定点地图，完成后保存位置"; implicitHeight: 30; Layout.fillWidth: true; onClicked: clientCoordinator.beginReferenceEdit() }
                }
            }
            OpacityControl { visible: !!panel.layout.display.alignedOpacity; settingKey: "sheetOpacity"; title: "贴合层不透明度" }
            OpacityControl { visible: !!panel.layout.display.referenceOpacity; settingKey: "referenceOpacity"; title: "定点不透明度" }
        }
    }
    Component {
        id: modeModule
        ColumnLayout {
            spacing: 4
            RowLayout {
                Layout.fillWidth: true; Layout.topMargin: 8; spacing: 4
                ControlButton { objectName: "controlModeNightmare"; text: panel.appliedMode === "nightmare" ? "噩梦 · 已选" : "噩梦"; primary: panel.appliedMode === "nightmare"; selected: panel.appliedMode === "nightmare"; enabled: panel.modeAvailable(); Layout.fillWidth: true; onClicked: clientCoordinator.setMode("nightmare") }
                ControlButton { objectName: "controlModeDifficult"; text: panel.appliedMode === "difficult" ? "困难 · 已选" : "困难"; primary: panel.appliedMode === "difficult"; selected: panel.appliedMode === "difficult"; enabled: panel.modeAvailable(); Layout.fillWidth: true; onClicked: clientCoordinator.setMode("difficult") }
            }
        }
    }
    Component {
        id: manualMapModule
        ColumnLayout {
            spacing: 4
            ControlButton { objectName: "controlReselectMap"; text: "选择地图"; icon.source: "icons/map.png"; Layout.fillWidth: true; enabled: panel.available("reselect_map"); hint: panel.available("reselect_map") ? "手动选择待确认地图，不改变作者路线" : "正在准备地图资源"; onClicked: clientCoordinator.requestManualMapSelection() }
            KeyHint { visible: !panel.available("reselect_map"); text: panel.snapshot.resourceLocked ? "等待资源操作完成" : "正在准备地图资源" }
            RowLayout {
                visible: (panel.snapshot.availableFloors || []).length > 0
                Layout.fillWidth: true; spacing: 4
                Repeater {
                    model: panel.snapshot.availableFloors || []
                    delegate: ControlButton {
                        required property string modelData
                        text: modelData === "B1" ? "B1" : "F" + modelData
                        selected: String(panel.snapshot.manualFloor || panel.snapshot.floor) === modelData
                        enabled: panel.available("set_floor"); implicitWidth: 50; Layout.fillWidth: true
                        onClicked: session.dispatchWithPayload("set_floor", {floor: modelData})
                    }
                }
                ControlButton { text: "切换"; implicitWidth: 54; enabled: panel.available("cycle_floor"); onClicked: session.dispatch("cycle_floor") }
            }
        }
    }
    Component {
        id: recognitionModule
        ColumnLayout {
            spacing: 4
            ComboBox {
                objectName: "controlCandidateSelection"
                Layout.fillWidth: true; Layout.topMargin: 6
                model: panel.snapshot.candidateRows || []; textRole: "name"
                currentIndex: typeof panel.snapshot.selectedCandidateIndex === "number" ? panel.snapshot.selectedCandidateIndex : -1
                enabled: panel.available("select_candidate") && count > 0
                displayText: count > 0 && currentIndex >= 0 ? "候选：" + currentText : "尚无候选"
                font.family: "Microsoft YaHei UI"; font.pixelSize: 13
                Accessible.name: "选择识别候选地图"
                onActivated: function(index) { session.dispatchWithPayload("select_candidate", {index: index}) }
            }
            ControlButton { text: "上一候选"; enabled: panel.available("previous_candidate"); Layout.fillWidth: true; onClicked: session.dispatch("previous_candidate") }
            KeyHint { text: panel.shortcut("candidate-prev") }
            ControlButton { text: "下一候选"; enabled: panel.available("next_candidate"); Layout.fillWidth: true; onClicked: session.dispatch("next_candidate") }
            KeyHint { text: panel.shortcut("candidate-next") }
            ControlButton { text: "确认地图"; enabled: panel.available("confirm_route"); Layout.fillWidth: true; onClicked: session.dispatch("confirm_route") }
            KeyHint { text: panel.shortcut("confirm-round") }
            ControlButton { text: "重新识别"; enabled: panel.available("capture_now"); Layout.fillWidth: true; onClicked: session.dispatch("capture_now") }
            ControlButton { text: "查看候选列表"; Layout.fillWidth: true; onClicked: clientCoordinator.navigate("recognition") }
        }
    }
    Component {
        id: roundModule
        ColumnLayout {
            id: roundControls
            spacing: 2
            readonly property bool active: panel.snapshot.roundActive === true
            readonly property string action: active ? "end_round" : panel.snapshot.roundPreparing === true ? "toggle_round" : "start_round"
            ControlButton {
                objectName: "controlRoundAction"
                Layout.fillWidth: true; Layout.topMargin: 8; implicitHeight: 36
                text: panel.snapshot.roundActionText || (roundControls.active ? "结束本局" : panel.snapshot.roundPreparing === true ? "取消准备" : "开始本局")
                primary: !roundControls.active; enabled: panel.available(roundControls.action)
                onClicked: {
                    if (roundControls.action === "start_round") clientCoordinator.startRound()
                    else session.dispatch(roundControls.action)
                }
            }
            KeyHint { text: panel.shortcut("toggle-round") || panel.shortcut(roundControls.active ? "end-round" : "start-round") }
            KeyHint { visible: !panel.available(roundControls.action); text: session.busy ? "正在处理当前操作" : panel.snapshot.resourceLocked ? "等待资源操作完成" : "等待地图资源准备就绪" }
        }
    }
}
