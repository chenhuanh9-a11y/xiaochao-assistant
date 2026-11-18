import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ApplicationWindow {
    palette: ClientPalette {}
    id: boot
    objectName: "qtClientBoot"
    width: 520; height: 280; visible: true; title: productionProduct ? "星图" : "星图 · Qt 私测"
    color: "#f3f6fc"; font.family: "Microsoft YaHei UI"
    property string statusText: "正在准备星图…"
    readonly property bool failed: statusText.indexOf("准备失败") === 0
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 28; spacing: 14
        RowLayout {
            spacing: 12
            Image {
                source: "icons/xingtu-round.png"
                Layout.preferredWidth: 44; Layout.preferredHeight: 44
                sourceSize.width: 88; sourceSize.height: 88
            }
            ColumnLayout {
                spacing: 2
                Label { text: "星图"; font.pixelSize: 22; font.bold: true; color: "#183a73" }
                Label { text: boot.failed ? "这一步没有完成" : "马上就好"; color: "#526785"; font.pixelSize: 12 }
            }
        }
        Label {
            text: boot.statusText; Layout.fillWidth: true; wrapMode: Text.WordWrap
            color: boot.failed ? "#b8364a" : "#354f75"
        }
        ProgressBar {
            Layout.fillWidth: true
            indeterminate: !boot.failed
            visible: !boot.failed
            Accessible.name: boot.statusText
        }
        Label {
            visible: boot.failed
            text: "可以关掉这个窗口后重新打开。已经安装的内容不会被这次准备改掉。"
            wrapMode: Text.WordWrap; Layout.fillWidth: true; color: "#526785"
        }
        Item { Layout.fillHeight: true }
        Label {
            text: "只准备星图自己的数据，不会改你已经安装的游戏。"
            wrapMode: Text.WordWrap; Layout.fillWidth: true; color: "#526785"; font.pixelSize: 12
        }
    }
}
