import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Window {
    id: reference
    objectName: "clientReferenceWindow"
    title: "星图 · " + (referenceData.title || "参考地图")
    visible: false
    width: Math.round(520 * preferences.sheetScale)
    height: Math.round(width * (mapImage.implicitWidth > 0 ? mapImage.implicitHeight / mapImage.implicitWidth : 1)) + (editing ? 58 : 0)
    x: preferences.sheetPosition ? preferences.sheetPosition[0] : 420
    y: preferences.sheetPosition ? preferences.sheetPosition[1] : 100
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
    readonly property var preferences: settingsService.values
    readonly property var referenceData: clientCoordinator.referenceData
    readonly property bool editing: clientCoordinator.referenceEditing
    onClosing: function(event) { event.accepted = false; clientCoordinator.hideReference() }

    Item {
        anchors.fill: parent
        Image {
            id: mapImage
            objectName: "clientReferenceImage"
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.bottom: editingToolbar.top
            source: reference.referenceData.imageUrl || ""
            asynchronous: true
            fillMode: Image.PreserveAspectFit
            opacity: reference.preferences.referenceOpacity
        }
        MouseArea {
            anchors.fill: mapImage
            enabled: reference.editing
            acceptedButtons: Qt.LeftButton
            function report(phase, mouse) {
                var point = mapToGlobal(mouse.x, mouse.y)
                clientCoordinator.referencePointer(phase, point.x, point.y)
            }
            onPressed: function(mouse) { report("press", mouse) }
            onPositionChanged: function(mouse) { if (pressed) report("move", mouse) }
            onReleased: function(mouse) { report("release", mouse) }
            onCanceled: clientCoordinator.cancelReferenceEdit()
        }
        Rectangle {
            id: editingToolbar
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            height: reference.editing ? 58 : 0
            visible: reference.editing
            color: "#edf3fd"; radius: 8; border.color: "#cbd9ed"
            RowLayout {
                anchors.fill: parent; anchors.margins: 10; spacing: 6
                Label { text: "拖动 · " + (reference.referenceData.title || "参考图") + " " + (reference.referenceData.floor || ""); color: "#354f75"; font.family: "Microsoft YaHei UI"; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
                AppButton { text: "完成"; primary: true; implicitWidth: 58; onClicked: clientCoordinator.finishReferenceEdit() }
                AppButton { text: "取消"; implicitWidth: 58; onClicked: clientCoordinator.cancelReferenceEdit() }
            }
        }
        Label {
            anchors.centerIn: mapImage
            visible: reference.editing && mapImage.status !== Image.Ready
            text: mapImage.status === Image.Error ? "参考图读取失败，请重新选择图层。" : "正在读取参考图…"
            color: "#354f75"; font.pixelSize: 13; padding: 12
            background: Rectangle { color: "#edf3fd"; radius: 8 }
        }
    }
}
