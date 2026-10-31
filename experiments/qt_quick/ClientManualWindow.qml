import QtQuick
import QtQuick.Controls.Basic

ApplicationWindow {
    objectName: "clientManualWindow"
    title: "选择本局地图 · 星图"
    width: 700; height: 590; minimumWidth: 620; minimumHeight: 520
    visible: false
    palette: ClientPalette {}
    font.family: "Microsoft YaHei UI"
    flags: Qt.Window | Qt.WindowStaysOnTopHint
    onClosing: function(event) { event.accepted = false; manualSelection.close() }
    ClientManualSelection { anchors.fill: parent; service: manualSelection }
}
