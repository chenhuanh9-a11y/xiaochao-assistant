pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Window {
    id: panel
    objectName: "clientControlsWindow"
    title: "星图 · 游戏控制"
    visible: false
    width: effectiveCollapsed ? 64 : 352
    height: effectiveCollapsed ? 64 : Math.min(expandedHeightLimit, Math.max(243, naturalExpandedHeight))
    x: preferences.compactPosition ? preferences.compactPosition[0] : 24
    y: preferences.compactPosition ? preferences.compactPosition[1] : 24
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
    readonly property var activeModules: layout.order.filter(function(key) { return layout.enabled.indexOf(key) >= 0 })
    readonly property var actionOrder: ["toggle-panel", "candidate-prev", "candidate-next", "confirm-round", "cycle-floor", "end-round", "toggle-sheet-visibility", "start-round", "toggle-round"]
    function available(action) {
        return !!snapshot.commands && !!snapshot.commands[action] && snapshot.commands[action].available
               && !snapshot.resourceLocked && (!session.busy || action === "end_round")
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
        implicitHeight: 34; font.pixelSize: 13; font.bold: true; padding: 6
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
        onPressed: function(mouse) {
            previousPoint = mapToGlobal(mouse.x, mouse.y)
            pressPoint = previousPoint; travel = 0
        }
        onPositionChanged: function(mouse) {
            if (!pressed) return
            var point = mapToGlobal(mouse.x, mouse.y)
            travel = Math.max(travel, Math.abs(point.x - pressPoint.x), Math.abs(point.y - pressPoint.y))
            panel.x += point.x - previousPoint.x; panel.y += point.y - previousPoint.y
            previousPoint = point
        }
        onReleased: {
            if (expandOnClick && travel <= 3) windows.toggleCollapsed("controls")
            else if (travel > 3) panel.savePosition()
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
                Label { text: panel.snapshot.controlStatusText || panel.snapshot.title || "等待游戏"; color: "#5c677d"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
                Label { text: (panel.snapshot.mapName || panel.snapshot.mapId || "") + (panel.snapshot.mapName || panel.snapshot.mapId ? " · " : "") + panel.floorText(); color: "#5c677d"; font.family: "Microsoft YaHei UI"; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
            }
            Label {
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.bottomMargin: 8
                text: panel.visibilityText(panel.snapshot.overlayVisible, "贴合已显示", "贴合暂不可见", "贴合状态待核对") + " · " + panel.visibilityText(panel.snapshot.referenceVisible, "定点已显示", "定点暂不可见", "定点状态待核对")
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
                        MenuItem { text: "选择地图"; enabled: panel.available("reselect_map"); onTriggered: session.dispatch("reselect_map") }
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
                        sourceComponent: modelData === "display" ? displayModule : modelData === "map" ? mapModule : modelData === "recognition" ? recognitionModule : roundModule
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
        Canvas {
            width: 26; height: 22; x: 19; y: 12
            onPaint: {
                var context = getContext("2d"); context.clearRect(0, 0, width, height)
                context.strokeStyle = "#2f66eb"; context.lineWidth = 1.6
                context.strokeRect(2, 2, 22, 15); context.beginPath(); context.moveTo(9, 2); context.lineTo(9, 17); context.stroke()
                context.beginPath(); context.moveTo(10, 19); context.lineTo(13, 22); context.lineTo(16, 19); context.stroke()
            }
        }
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
                    ControlButton { text: "贴合 " + panel.visibilityText(panel.snapshot.userVisible, "开", "关", "未知"); primary: panel.snapshot.userVisible === true; enabled: panel.available("toggle_sheet_visibility"); Layout.fillWidth: true; onClicked: session.dispatch("toggle_sheet_visibility") }
                    KeyHint { text: panel.shortcut("toggle-sheet-visibility") }
                }
                ColumnLayout {
                    Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 2
                    ControlButton { text: "定点地图"; primary: panel.preferences.referenceSheetVisible === true; Layout.fillWidth: true; onClicked: clientCoordinator.showReference() }
                    ControlButton { visible: !!panel.layout.display.referencePosition && !panel.editing; text: "编辑位置"; implicitHeight: 30; Layout.fillWidth: true; onClicked: clientCoordinator.beginReferenceEdit() }
                }
            }
            OpacityControl { visible: !!panel.layout.display.alignedOpacity; settingKey: "sheetOpacity"; title: "贴合层不透明度" }
            OpacityControl { visible: !!panel.layout.display.referenceOpacity; settingKey: "referenceOpacity"; title: "定点不透明度" }
        }
    }
    Component {
        id: mapModule
        ColumnLayout {
            spacing: 4
            RowLayout {
                Layout.fillWidth: true; Layout.topMargin: 8; spacing: 4
                ControlButton { text: session.currentMode === "nightmare" ? "噩梦 · 已选" : "噩梦"; primary: session.currentMode === "nightmare"; enabled: panel.available("set_game_mode"); Layout.fillWidth: true; onClicked: clientCoordinator.setMode("nightmare") }
                ControlButton { text: session.currentMode === "difficult" ? "困难 · 已选" : "困难"; primary: session.currentMode === "difficult"; enabled: panel.available("set_game_mode"); Layout.fillWidth: true; onClicked: clientCoordinator.setMode("difficult") }
            }
            ControlButton { text: "选择地图"; Layout.fillWidth: true; enabled: panel.available("reselect_map"); onClicked: session.dispatch("reselect_map") }
            RowLayout {
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
        }
    }
}
