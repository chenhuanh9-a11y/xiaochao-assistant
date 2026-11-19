pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "plazaPreviewDialog"
    readonly property var state: routePlazaService.snapshot
    readonly property var preview: routePlazaService.previewState
    readonly property bool hasImage: !!preview.image
    readonly property bool loading: state.operation === "preview"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(760, parent ? parent.width - 32 : 760)
    height: Math.min(hasImage ? 630 : 340, parent ? parent.height - 32 : 630)
    modal: true
    focus: true
    visible: !!preview.key
    title: preview.title || "路线预览"
    onClosed: routePlazaService.closePreview()
    onOpened: if (preview.local) routePlazaService.loadPreview(numbers.checked)
    standardButtons: Dialog.Close
    header: Label {
        text: root.title; textFormat: Text.PlainText; elide: Text.ElideRight
        padding: 12; font.bold: true; font.pixelSize: 18; color: "#142b4b"
    }

    contentItem: ColumnLayout {
        spacing: 8
        Label {
            Layout.fillWidth: true
            text: (root.preview.scope || "") + " · 作者 " + (root.preview.author || "")
                  + " · " + (root.preview.mapCount || 0) + " 张图"
            textFormat: Text.PlainText; wrapMode: Text.Wrap; color: "#526785"
        }
        ScrollView {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(description.implicitHeight, 68)
            visible: !!root.preview.description
            contentWidth: availableWidth; clip: true
            Label {
                id: description
                width: parent.width
                text: root.preview.description || ""
                textFormat: Text.PlainText; wrapMode: Text.Wrap; color: "#354f75"
            }
        }
        RowLayout {
            Layout.fillWidth: true
            ComboBox {
                objectName: "plazaPreviewMap"
                visible: root.hasImage
                Layout.fillWidth: true
                model: root.preview.maps || []; textRole: "name"; valueRole: "id"
                currentIndex: indexOfValue(root.preview.map || "")
                enabled: !root.state.busy
                Accessible.name: "预览地图"
                onActivated: routePlazaService.selectPreview(currentValue || "", "", numbers.checked)
            }
            ComboBox {
                objectName: "plazaPreviewFloor"
                visible: root.hasImage
                model: root.preview.floors || []; textRole: "name"; valueRole: "id"
                currentIndex: indexOfValue(root.preview.floor || "")
                enabled: !root.state.busy
                Accessible.name: "预览楼层"
                onActivated: routePlazaService.selectPreview(root.preview.map || "", currentValue || "", numbers.checked)
            }
            RoutePointNumbersSwitch {
                id: numbers
                onCheckedChanged: if (root.visible && root.hasImage && !root.state.busy)
                    routePlazaService.selectPreview(root.preview.map, root.preview.floor, checked)
            }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 120
            radius: 10; color: "#edf2fa"; clip: true
            Image {
                objectName: "plazaPreviewImage"
                anchors.fill: parent; anchors.margins: 10
                source: root.preview.image || ""
                fillMode: Image.PreserveAspectFit; asynchronous: true; cache: false
            }
            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(parent.width - 28, 380)
                visible: !root.hasImage
                spacing: 8
                Label {
                    Layout.fillWidth: true
                    text: root.loading ? "正在读取路线和地图…"
                          : root.preview.local ? "使用本机已保存的路线。"
                          : "加载地图需要登录，服务端可能计为一次下载。"
                    horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; color: "#526785"
                }
                AppButton {
                    objectName: "plazaLoadPreview"
                    Layout.alignment: Qt.AlignHCenter
                    text: root.preview.local || root.state.username ? "加载地图" : "登录"
                    visible: !root.loading; enabled: !root.state.busy
                    primary: true
                    onClicked: routePlazaService.loadPreview(numbers.checked)
                }
            }
        }
        Label {
            Layout.fillWidth: true
            visible: !!text
            text: root.preview.message || root.preview.notice || ""
            textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: root.preview.error ? "#b91c1c" : "#526785"
        }
    }
}
