import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ApplicationWindow {
    id: boot
    objectName: "qtClientBoot"
    width: 520; height: 260; visible: true; title: productionProduct ? "星图" : "星图 · Qt 私测"
    color: "#f3f6fc"; font.family: "Microsoft YaHei UI"
    property string statusText: "正在准备独立测试数据…"
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 32; spacing: 16
        Label { text: "星图"; font.pixelSize: 28; font.bold: true; color: "#183a73" }
        Label { text: boot.statusText; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#354f75" }
        Item { Layout.fillHeight: true }
        Label { text: "首次启动准备本客户端的数据，不修改现有安装"; wrapMode: Text.WordWrap; Layout.fillWidth: true; color: "#526785" }
    }
}
