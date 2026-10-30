// Operate: persistent Qt pages, actual shared services, separate passive game surfaces.
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: root
    objectName: "qtClientMain"
    width: 1180; height: 790; minimumWidth: 940; minimumHeight: 660
    visible: true; title: "星图 · Qt 私测"
    color: "#f3f6fc"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13
    property string page: "home"
    property real replayAlpha: 0.55
    property var pages: ["home", "gallery", "resources", "settings", "replay", "help"]
    Connections { target: clientCoordinator; function onPageRequested(name) { root.page = name } }
    function updateInputGuard() {
        var item = activeFocusItem
        var textEntry = item && (item.hasOwnProperty("inputMethodComposing") || item.hasOwnProperty("selectByMouse"))
        clientCoordinator.setTextEntryActive(active && !!textEntry)
    }
    onActiveFocusItemChanged: updateInputGuard()
    onActiveChanged: updateInputGuard()
    Shortcut {
        sequence: settingsService.values.actionHotkeys[0] || ""
        context: Qt.ApplicationShortcut; autoRepeat: false
        enabled: !clientCoordinator.textEntryActive
        onActivated: clientCoordinator.toggleControls()
    }
    onClosing: function(event) { event.accepted = false; clientCoordinator.requestClose() }
    RowLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            Layout.preferredWidth: 184; Layout.fillHeight: true; color: "#eaf1fd"
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 20; spacing: 8
                Label { text: "星图"; font.pixelSize: 25; font.bold: true; color: "#183a73"; Layout.topMargin: 12 }
                Label { text: "Qt 私测"; color: "#526785"; Layout.bottomMargin: 30 }
                Repeater {
                    model: [{key:"home",label:"首页"},{key:"gallery",label:"地图资源"},{key:"resources",label:"图包管理"},
                            {key:"settings",label:"设置"},{key:"replay",label:"识别回放"},{key:"help",label:"帮助与公告"}]
                    AppButton { required property var modelData; objectName: "nav_"+modelData.key; Layout.fillWidth: true; text: modelData.label; selected: root.page===modelData.key; onClicked: root.page=modelData.key }
                }
                Item { Layout.fillHeight: true }
                AppButton { text: "悬浮控件"; Layout.fillWidth: true; onClicked: windows.show("controls") }
                Label { text: "独立测试数据"; color: "#354f75"; font.pixelSize: 12 }
                Label { text: infoService.version; color: "#526785"; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            }
        }
        StackLayout {
            objectName: "clientPages"
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.margins: 28
            currentIndex: root.pages.indexOf(root.page)
            ClientHome {}
            ClientGallery {}
            ClientResources {}
            ClientSettings { profileOptions: routeChoices.profiles; profilesMode: routeChoices.mode }
            ColumnLayout {
                spacing: 16
                RowLayout {
                    Label { text: "识别结果回放"; color: "#142b4b"; font.pixelSize: 26; font.bold: true }
                    Item { Layout.fillWidth: true }
                    AppButton { text: "选择回放"; enabled: !session.busy; onClicked: replayPicker.open() }
                    AppButton { text: "悬浮查看"; primary: true; enabled: backend.replayAvailable; onClicked: clientCoordinator.showAlignedReplay() }
                }
                Label { text: backend.replayMessage; color: "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Rectangle {
                    Layout.fillWidth: true; Layout.fillHeight: true; radius: 12; color: "#142035"
                    ReplayCanvas { id: replay; objectName: "clientReplayCanvas"; anchors.fill: parent; anchors.margins: 10; record: backend.replayData; routeAlpha: root.replayAlpha }
                    Label { anchors.centerIn: parent; visible: !backend.replayAvailable || !replay.imagesReady; color: "#eaf1fd"; text: backend.replayAvailable ? "正在读取回放…" : "选择一份已保存的结果" }
                }
                RowLayout {
                    Label { text: "路线透明度"; color: "#354f75" }
                    AppSlider { Layout.preferredWidth: 240; from: 0; to: 1; value: root.replayAlpha; onMoved: root.replayAlpha=value }
                    Item { Layout.fillWidth: true }
                    Label { text: "历史结果不授权当前游戏显示"; color: "#526785"; font.pixelSize: 12 }
                }
            }
            ClientHelp {}
        }
    }
    FileDialog { id: replayPicker; title: "选择隔离回放"; nameFilters: ["星图回放 (replay.json)"]; onAccepted: clientCoordinator.loadReplay(selectedFile.toString()) }
    Popup {
        anchors.centerIn: parent; width: 310; height: 110; modal: true; closePolicy: Popup.NoAutoClose
        visible: clientCoordinator.closing
        background: Rectangle { color: "white"; radius: 12 }
        Label { anchors.centerIn: parent; width: parent.width-32; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; text: "正在结束任务并保存已接受的操作…"; color: "#183a73" }
    }
}
