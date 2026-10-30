pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Window {
    id: panel
    objectName: "clientControlsWindow"
    title: "星图 · 游戏控制"
    visible: false
    width: 344
    height: collapsed ? 62 : Math.min(760, modules.implicitHeight + 126)
    x: preferences.compactPosition ? preferences.compactPosition[0] : 24
    y: preferences.compactPosition ? preferences.compactPosition[1] : 24
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
    property bool collapsed: false
    readonly property var preferences: settingsService.values
    readonly property var layout: preferences.gameControlLayout || ({order: [], enabled: [], display: {}})
    readonly property var snapshot: session.snapshot
    readonly property var activeModules: layout.order.filter(function(key) { return layout.enabled.indexOf(key) >= 0 })
    function available(action) {
        return !!snapshot.commands && !!snapshot.commands[action] && snapshot.commands[action].available
               && !snapshot.resourceLocked && (!session.busy || action === "end_round")
    }
    function commit(key) { settingsService.saveFields([key]) }
    function savePosition() {
        settingsService.setValue("compactPosition", [Math.round(x), Math.round(y)])
        commit("compactPosition")
    }

    component SectionTitle: Label {
        color: "#183a73"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; font.bold: true
        Layout.topMargin: 6; Layout.bottomMargin: 2
    }
    component OpacityRow: RowLayout {
        id: row
        required property string settingKey
        required property string title
        property bool edited: false
        Layout.fillWidth: true
        Label { text: row.title; color: "#354f75"; font.pixelSize: 12; Layout.preferredWidth: 72 }
        AppSlider {
            Layout.fillWidth: true
            from: 0; to: 100; stepSize: 1
            value: Math.round(panel.preferences[row.settingKey] * 100)
            Accessible.name: row.title
            onMoved: { row.edited = true; settingsService.setValue(row.settingKey, value / 100) }
            onPressedChanged: if (!pressed && row.edited) { panel.commit(row.settingKey); row.edited = false }
            Keys.onReleased: if (row.edited) { panel.commit(row.settingKey); row.edited = false }
        }
        Label { text: Math.round(panel.preferences[row.settingKey] * 100) + "%"; color: "#354f75"; font.pixelSize: 12; Layout.preferredWidth: 36; horizontalAlignment: Text.AlignRight }
    }

    Rectangle {
        anchors.fill: parent; anchors.margins: 4
        radius: 12; color: Qt.rgba(.95, .97, 1, panel.preferences.interfaceOpacity)
        border.color: "#cbd9ed"
        Rectangle {
            id: header
            height: 52; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            color: "transparent"
            Label { x: 14; anchors.verticalCenter: parent.verticalCenter; text: "星图 · 游戏控制"; color: "#183a73"; font.family: "Microsoft YaHei UI"; font.pixelSize: 14; font.bold: true }
            MouseArea {
                anchors.fill: parent; anchors.rightMargin: 118
                property point previousPoint
                onPressed: function(mouse) { previousPoint = mapToGlobal(mouse.x, mouse.y) }
                onPositionChanged: function(mouse) {
                    if (!pressed) return
                    var point = mapToGlobal(mouse.x, mouse.y)
                    panel.x += point.x - previousPoint.x; panel.y += point.y - previousPoint.y
                    previousPoint = point
                }
                onReleased: panel.savePosition()
            }
            Row {
                anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter; spacing: 4
                AppButton { text: panel.collapsed ? "展开" : "收起"; implicitWidth: 50; implicitHeight: 32; padding: 6; onClicked: windows.toggleCollapsed("controls") }
                AppButton { text: "隐藏"; implicitWidth: 50; implicitHeight: 32; padding: 6; onClicked: windows.hide("controls") }
            }
        }
        ScrollView {
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: header.bottom; anchors.bottom: status.top
            anchors.margins: 12; anchors.topMargin: 0
            visible: !panel.collapsed
            clip: true; contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                id: modules
                width: parent.width
                spacing: 10
                Repeater {
                    model: panel.activeModules
                    delegate: Loader {
                        required property string modelData
                        Layout.fillWidth: true
                        sourceComponent: modelData === "display" ? displayModule : modelData === "map" ? mapModule : modelData === "recognition" ? recognitionModule : roundModule
                    }
                }
                Label { visible: panel.activeModules.length === 0; text: "控制模块已隐藏，可在设置中开启。"; wrapMode: Text.WordWrap; color: "#526785"; font.pixelSize: 12; Layout.fillWidth: true }
            }
        }
        Label {
            id: status
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 12
            visible: !panel.collapsed
            height: 38; maximumLineCount: 2; elide: Text.ElideRight; wrapMode: Text.Wrap
            text: session.message; color: "#526785"; font.family: "Microsoft YaHei UI"; font.pixelSize: 12
        }
    }

    Component {
        id: displayModule
        ColumnLayout {
            spacing: 6
            SectionTitle { text: "显示" }
            RowLayout {
                AppButton { text: panel.snapshot.userVisible === true ? "隐藏贴合层" : panel.snapshot.userVisible === false ? "显示贴合层" : "贴合层"; enabled: panel.available("toggle_sheet_visibility"); Layout.fillWidth: true; onClicked: session.dispatch("toggle_sheet_visibility") }
                AppButton { text: "参考地图"; Layout.fillWidth: true; onClicked: clientCoordinator.showReference() }
            }
            OpacityRow { visible: !!panel.layout.display.alignedOpacity; settingKey: "sheetOpacity"; title: "贴合层" }
            OpacityRow { visible: !!panel.layout.display.referenceOpacity; settingKey: "referenceOpacity"; title: "参考图" }
            AppButton { visible: !!panel.layout.display.referencePosition; text: "编辑参考图位置"; Layout.fillWidth: true; onClicked: clientCoordinator.beginReferenceEdit() }
        }
    }
    Component {
        id: mapModule
        ColumnLayout {
            spacing: 6
            SectionTitle { text: "模式与地图" }
            RowLayout {
                AppButton { text: "噩梦"; selected: session.currentMode === "nightmare"; enabled: panel.available("set_game_mode"); Layout.fillWidth: true; onClicked: clientCoordinator.setMode("nightmare") }
                AppButton { text: "困难"; selected: session.currentMode === "difficult"; enabled: panel.available("set_game_mode"); Layout.fillWidth: true; onClicked: clientCoordinator.setMode("difficult") }
            }
            RowLayout {
                AppButton { text: "选择地图"; Layout.fillWidth: true; enabled: panel.available("_start_map_reselection"); onClicked: session.dispatch("_start_map_reselection") }
                AppButton { text: "浏览图层"; Layout.fillWidth: true; onClicked: clientCoordinator.navigate("gallery") }
            }
            RowLayout {
                Repeater {
                    model: ["B1", "1", "2"]
                    delegate: AppButton {
                        required property string modelData
                        text: modelData === "B1" ? "B1" : "F" + modelData
                        selected: String(panel.snapshot.floor) === modelData
                        enabled: panel.available("set_floor")
                        Layout.fillWidth: true
                        onClicked: session.dispatchWithPayload("set_floor", {floor: modelData})
                    }
                }
            }
        }
    }
    Component {
        id: recognitionModule
        ColumnLayout {
            spacing: 6
            SectionTitle { text: "候选与识别" }
            Label { text: panel.snapshot.candidateTitle || panel.snapshot.mapId || "尚未识别地图"; color: "#354f75"; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
            RowLayout {
                AppButton { text: "上一候选"; enabled: panel.available("previous_candidate"); Layout.fillWidth: true; onClicked: session.dispatch("previous_candidate") }
                AppButton { text: "下一候选"; enabled: panel.available("next_candidate"); Layout.fillWidth: true; onClicked: session.dispatch("next_candidate") }
            }
            RowLayout {
                AppButton { text: "确认地图"; enabled: panel.available("confirm_route"); Layout.fillWidth: true; onClicked: session.dispatch("confirm_route") }
                AppButton { text: "重新识别"; enabled: panel.available("capture_now"); Layout.fillWidth: true; onClicked: session.dispatch("capture_now") }
            }
        }
    }
    Component {
        id: roundModule
        ColumnLayout {
            spacing: 6
            SectionTitle { text: "本局" }
            RowLayout {
                AppButton { text: panel.snapshot.roundPreparing ? "准备中" : "开始本局"; primary: true; enabled: panel.available("start_round"); Layout.fillWidth: true; onClicked: clientCoordinator.startRound() }
                AppButton { text: "结束本局"; enabled: panel.available("end_round"); Layout.fillWidth: true; onClicked: session.dispatch("end_round") }
            }
        }
    }
}
