import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Window {
    id: overlay
    objectName: "overlayWindow"
    title: "星图 · 悬浮预览"
    width: 350
    height: expanded ? 430 : 66
    x: 100; y: 100
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
    property bool expanded: true
    property real controlAlpha: 0.96
    property real mapAlpha: 0.86
    property bool replayMode: false
    readonly property bool reduceMotion: typeof settingsService !== "undefined" && settingsService.values.reduceMotion
    Behavior on height {
        objectName: "overlayHeightBehavior"
        enabled: !overlay.reduceMotion
        NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
    }
    Rectangle {
        anchors.fill: parent; anchors.margins: 5
        radius: 16; color: Qt.rgba(0.94, 0.97, 1, overlay.controlAlpha)
        clip: true
        Rectangle {
            x: 0; y: 0; width: parent.width; height: 56
            color: Qt.rgba(0.87, 0.92, 1, overlay.controlAlpha)
            Label { x: 18; anchors.verticalCenter: parent.verticalCenter; text: overlay.replayMode ? "星图 · 结果回放" : "星图 · 浏览预览"; color: "#183a73"; font.bold: true; font.pixelSize: 14 }
            MouseArea {
                width: parent.width - 100; height: parent.height
                property real startX: 0
                property real startY: 0
                onPressed: function(mouse) { startX = mouse.x; startY = mouse.y }
                onPositionChanged: function(mouse) {
                    if (pressed) { overlay.x += mouse.x - startX; overlay.y += mouse.y - startY }
                }
            }
            Row {
                anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter
                spacing: 4
                AppButton { objectName: "collapseButton"; text: overlay.expanded ? "收起" : "展开"; implicitWidth: 50; onClicked: overlay.expanded = !overlay.expanded }
                AppButton { text: "关闭"; implicitWidth: 50; onClicked: overlay.hide() }
            }
        }
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 17
            anchors.topMargin: 68
            visible: overlay.height > 100
            spacing: 8
            Label { text: overlay.replayMode ? (backend.replayData.floor || "") + "楼 · 历史位置" : backend.selectedTitle; color: "#183a73"; Layout.fillWidth: true; elide: Text.ElideRight }
            Item {
                Layout.fillWidth: true; Layout.fillHeight: true
                Image { objectName: "overlayPreview"; anchors.fill: parent; visible: !overlay.replayMode; source: backend.selectedImage; sourceSize: Qt.size(640, 640); asynchronous: true; fillMode: Image.PreserveAspectFit; opacity: overlay.mapAlpha }
                ReplayCanvas { objectName: "overlayReplayCanvas"; anchors.fill: parent; visible: overlay.replayMode; record: overlay.replayMode ? backend.replayData : ({}); routeAlpha: overlay.mapAlpha }
            }
            Label { text: overlay.replayMode ? backend.replayData.label || "未载入回放" : "未连接识别 · 仅显示所选图层"; color: "#526785"; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
            RowLayout {
                Label { text: "面板"; color: "#354f75"; font.pixelSize: 12 }
                AppSlider { objectName: "controlAlphaSlider"; Layout.fillWidth: true; from: 0.35; to: 1; value: overlay.controlAlpha; onMoved: overlay.controlAlpha = value }
                Label { text: "地图"; color: "#354f75"; font.pixelSize: 12 }
                AppSlider { objectName: "mapAlphaSlider"; Layout.fillWidth: true; from: 0.1; to: 1; value: overlay.mapAlpha; onMoved: overlay.mapAlpha = value }
            }
        }
    }
}
