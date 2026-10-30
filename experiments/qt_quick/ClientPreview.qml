import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Window {
    id: preview
    objectName: "clientPreviewWindow"
    palette: ClientPalette {}
    readonly property var previewData: previewService.previewData
    property real zoom: 1.0
    title: (previewData.title || "地图预览") + (previewData.floor ? " · " + floorLabel(previewData.floor) : "")
    width: 900; height: 720; minimumWidth: 400; minimumHeight: 300
    visible: false
    color: "#edf4ff"
    flags: Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint
    modality: Qt.NonModal
    onClosing: function(event) { event.accepted = true }

    function floorLabel(value) { return value === "B1" ? "B1 地下室" : value + " 楼" }
    function changeZoom(factor, reset) {
        var cx = (viewport.contentX + viewport.width / 2) / Math.max(1, viewport.contentWidth)
        var cy = (viewport.contentY + viewport.height / 2) / Math.max(1, viewport.contentHeight)
        zoom = reset ? 1 : Math.max(0.5, Math.min(4, zoom * factor))
        Qt.callLater(function() {
            viewport.contentX = Math.max(0, Math.min(viewport.contentWidth - viewport.width, cx * viewport.contentWidth - viewport.width / 2))
            viewport.contentY = Math.max(0, Math.min(viewport.contentHeight - viewport.height, cy * viewport.contentHeight - viewport.height / 2))
        })
    }
    Shortcut { sequence: "Escape"; context: Qt.WindowShortcut; enabled: preview.visible; onActivated: preview.close() }
    Shortcut { sequence: "+"; context: Qt.WindowShortcut; enabled: preview.visible; onActivated: preview.changeZoom(1.2, false) }
    Shortcut { sequence: "-"; context: Qt.WindowShortcut; enabled: preview.visible; onActivated: preview.changeZoom(1 / 1.2, false) }
    Shortcut { sequence: "0"; context: Qt.WindowShortcut; enabled: preview.visible; onActivated: preview.changeZoom(1, true) }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 12; spacing: 8
        Label { Layout.fillWidth: true; text: "完整图像 · 滚轮或 + / − 缩放，0 适应窗口"; color: "#526785"; font.pixelSize: 12; wrapMode: Text.WordWrap }
        RowLayout {
            Layout.fillWidth: true; spacing: 6
            AppButton { objectName: "previewZoomOut"; text: "缩小"; enabled: preview.zoom > 0.5; onClicked: preview.changeZoom(1 / 1.2, false) }
            AppButton { objectName: "previewZoomIn"; text: "放大"; enabled: preview.zoom < 4; onClicked: preview.changeZoom(1.2, false) }
            AppButton { objectName: "previewFit"; text: "适应窗口"; onClicked: preview.changeZoom(1, true) }
            Label { text: Math.round(preview.zoom * 100) + "%"; color: "#526785"; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "previewCloseButton"; text: "关闭预览"; onClicked: preview.close() }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true; color: "#e8eff9"; radius: 8
            Flickable {
                id: viewport; objectName: "previewViewport"
                anchors.fill: parent; clip: true
                contentWidth: Math.max(width, mapImage.width + 24)
                contentHeight: Math.max(height, mapImage.height + 24)
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.horizontal: ScrollBar {}
                ScrollBar.vertical: ScrollBar {}
                Image {
                    id: mapImage; objectName: "previewMapImage"
                    readonly property real fit: implicitWidth > 0 && implicitHeight > 0 ? Math.min((viewport.width - 24) / implicitWidth, (viewport.height - 24) / implicitHeight) : 1
                    width: implicitWidth * fit * preview.zoom; height: implicitHeight * fit * preview.zoom
                    x: (viewport.contentWidth - width) / 2; y: (viewport.contentHeight - height) / 2
                    source: preview.visible ? (preview.previewData.imageUrl || "") : ""
                    asynchronous: true; cache: true; fillMode: Image.PreserveAspectFit
                    onSourceChanged: preview.changeZoom(1, true)
                }
                WheelHandler {
                    target: null
                    onWheel: function(event) { if (event.angleDelta.y) { preview.changeZoom(event.angleDelta.y > 0 ? 1.2 : 1 / 1.2, false); event.accepted = true } }
                }
            }
            Label {
                anchors.centerIn: parent; width: parent.width - 24
                visible: mapImage.status !== Image.Ready
                text: !preview.previewData.imageUrl ? "请选择可预览的地图图层。" : mapImage.status === Image.Error ? "图片读取失败，请重新选择图层。" : "正在加载图片…"
                color: "#526785"; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
            }
        }
    }
}
