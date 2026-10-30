import QtQuick
import QtQuick.Controls.Basic

Window {
    id: toast
    objectName: "clientToastWindow"
    visible: false
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
    property string notificationTitle: ""
    property string notificationMessage: ""
    property string notificationKind: "info"
    property int eventRevision: 0
    property var availableArea: ({x: 0, y: 0, width: 1, height: 1})
    readonly property real inset: Math.min(14, Math.max(0, (width - 1) / 4))
    width: Math.max(1, Math.min(480, Math.max(180, Math.max(titleMetrics.width, messageMetrics.width) + 58), availableArea.width - 32))
    height: Math.max(1, Math.min(Math.max(20, textBlock.implicitHeight) + 20, availableArea.height - 32))
    x: Math.round(availableArea.x + Math.max(0, (availableArea.width - width) / 2))
    y: Math.round(availableArea.y + Math.max(0, Math.min(16, availableArea.height - height)))

    function dismiss() {
        expiry.stop()
        fade.stop()
        visible = false
        opacity = 1
    }
    function present(title, message, kind) {
        var heading = String(title || ""), detail = String(message || "")
        if (!heading.trim() && !detail.trim()) return
        // The coordinator provides a real QScreen.availableGeometry work area,
        // choosing the target screen or primary screen; never a virtual desktop.
        var area = clientCoordinator.notificationGeometry
        if (!area || !(Number(area.width) > 0) || !(Number(area.height) > 0)
                || !isFinite(Number(area.x)) || !isFinite(Number(area.y))) {
            dismiss()
            return
        }
        expiry.stop()
        fade.stop()
        availableArea = {x: Number(area.x), y: Number(area.y), width: Number(area.width), height: Number(area.height)}
        notificationTitle = heading
        notificationMessage = detail
        notificationKind = String(kind || "info")
        eventRevision += 1
        opacity = 1
        visible = true
        expiry.restart()
    }
    onClosing: function(event) { dismiss(); event.accepted = true }
    onVisibleChanged: if (!visible) { expiry.stop(); fade.stop(); opacity = 1 }

    Connections {
        target: clientCoordinator
        function onNotificationRequested(title, message, kind) { toast.present(title, message, kind) }
    }
    Timer { id: expiry; objectName: "toastExpiry"; interval: 3200; repeat: false; onTriggered: fade.restart() }
    NumberAnimation { id: fade; objectName: "toastFade"; target: toast; property: "opacity"; from: 1; to: 0; duration: 280; onFinished: toast.dismiss() }
    TextMetrics { id: titleMetrics; text: toast.notificationTitle; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; font.bold: true }
    TextMetrics { id: messageMetrics; text: toast.notificationMessage; font.family: "Microsoft YaHei UI"; font.pixelSize: 13 }

    Rectangle {
        anchors.fill: parent; radius: 8; color: "#ffffff"; border.color: "#d6e0ef"; clip: true
        Rectangle {
            id: statusIcon
            visible: toast.width >= 120
            x: toast.inset; y: (parent.height - height) / 2; width: 20; height: 20; radius: 10
            color: toast.notificationKind === "error" ? "#b8364a" : "#17775a"
            Label { anchors.centerIn: parent; text: toast.notificationKind === "error" ? "!" : "✓"; color: "white"; font.bold: true; font.pixelSize: 14 }
        }
        Column {
            id: textBlock
            x: toast.inset + (statusIcon.visible ? 30 : 0)
            y: Math.max(0, (parent.height - height) / 2)
            width: Math.max(1, parent.width - x - toast.inset)
            spacing: titleLabel.visible && messageLabel.visible ? 3 : 0
            Label { id: titleLabel; objectName: "toastTitle"; width: parent.width; visible: text !== ""; text: toast.notificationTitle; color: "#142b4b"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; font.bold: true; wrapMode: Text.WordWrap }
            Label { id: messageLabel; objectName: "toastMessage"; width: parent.width; visible: text !== ""; text: toast.notificationMessage; color: "#142b4b"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; wrapMode: Text.WordWrap }
        }
    }
}
