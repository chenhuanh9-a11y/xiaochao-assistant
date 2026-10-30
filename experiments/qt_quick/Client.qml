// Operate: persistent Qt pages, actual shared services, separate passive game surfaces.
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: root
    objectName: "qtClientMain"
    width: 1000; height: 720; minimumWidth: 650; minimumHeight: 470
    visible: true; title: productionProduct ? "星图" : "星图 · Qt 私测"
    color: "#edf4ff"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13
    property string page: "home"
    property real replayAlpha: 0.55
    property var pages: ["home", "gallery", "resources", "settings", "replay", "help", "recognition", "custom", "downloads"]
    readonly property bool compactRail: width < 800 || height < 660
    readonly property int workspaceMargin: width >= 1200 ? 48 : 28
    function section(name) { return name === "resources" || name === "downloads" ? "gallery" : name === "recognition" ? "home" : name === "replay" ? "help" : name }
    function heading(name) { return ({home:"主页面",gallery:"地图资源",resources:"图包管理",downloads:"在线图包",settings:"设置",replay:"识别回放",help:"帮助与公告",recognition:"地图识别",custom:"自定义路线"})[name] || "星图" }
    readonly property var routeOptions: {
        var options = routeChoices.profiles.slice()
        var custom = customGalleryService.snapshot
        if (routeChoices.mode === "nightmare" && custom.available)
            for (var i = 0; i < custom.galleries.length; i++)
                options.push({id: "custom:" + custom.galleries[i].id, name: custom.galleries[i].name})
        return options
    }
    Connections { target: clientCoordinator; function onPageRequested(name) { root.page = name } }
    function updateInputGuard() {
        var item = activeFocusItem
        var textEntry = item && (item.hasOwnProperty("inputMethodComposing") || item.hasOwnProperty("selectByMouse"))
        clientCoordinator.setTextEntryActive(active && !!textEntry)
    }
    onActiveFocusItemChanged: updateInputGuard()
    onActiveChanged: updateInputGuard()
    Shortcut {
        sequence: settingsService.persistedValues.actionHotkeys[0] || ""
        context: Qt.ApplicationShortcut; autoRepeat: false
        enabled: !clientCoordinator.textEntryActive
        onActivated: clientCoordinator.toggleControlsKeyboard()
    }
    ClientOnboarding {}
    onClosing: function(event) {
        event.accepted = clientCoordinator.closeReady
        if (!event.accepted) clientCoordinator.requestClose()
    }
    RowLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            objectName: "clientNavigationRail"
            Layout.preferredWidth: root.compactRail ? 80 : 96; Layout.fillHeight: true; color: "white"
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 8; spacing: 4
                Item { Layout.fillWidth: true; Layout.preferredHeight: root.compactRail ? 54 : 90
                    Column { anchors.centerIn: parent; spacing: 7
                        Image { anchors.horizontalCenter: parent.horizontalCenter; width: 26; height: 26; source: "icons/note.png"; sourceSize.width: 52; sourceSize.height: 52 }
                        Label { anchors.horizontalCenter: parent.horizontalCenter; visible: !root.compactRail; text: "星图"; color: "#17365e"; font.bold: true; font.pixelSize: 13 }
                    }
                }
                Repeater {
                    model: [{key:"home",label:"主页面",icon:"home"},{key:"gallery",label:"地图资源",icon:"map"},
                            {key:"custom",label:"自定义",icon:"note"},{key:"settings",label:"设置",icon:"settings"},{key:"help",label:"帮助",icon:"help"}]
                    ToolButton {
                        id: navigation
                        required property var modelData
                        objectName: "nav_"+modelData.key
                        Layout.fillWidth: true; Layout.preferredHeight: root.compactRail ? 48 : 68
                        Accessible.name: modelData.label
                        ToolTip.visible: hovered && root.compactRail; ToolTip.text: modelData.label
                        onClicked: root.page = modelData.key
                        contentItem: Column {
                            spacing: 5
                            Image { anchors.horizontalCenter: parent.horizontalCenter; width: 23; height: 23; source: "icons/"+navigation.modelData.icon+".png"; sourceSize.width: 46; sourceSize.height: 46; opacity: root.section(root.page) === navigation.modelData.key ? 1 : .65 }
                            Label { anchors.horizontalCenter: parent.horizontalCenter; visible: !root.compactRail; text: navigation.modelData.label; color: root.section(root.page) === navigation.modelData.key ? "#245de7" : "#4c6282"; font.pixelSize: 12 }
                        }
                        background: Rectangle { radius: 11; color: root.section(root.page) === navigation.modelData.key ? "#dceaff" : navigation.hovered ? "#f0f5fd" : "transparent"; border.width: navigation.activeFocus ? 1 : 0; border.color: "#2f66eb" }
                    }
                }
                Item { Layout.fillHeight: true }
                Rectangle { Layout.fillWidth: true; height: 1; color: "#d5e2f5"; Layout.leftMargin: 4; Layout.rightMargin: 4 }
                ToolButton { Layout.fillWidth: true; Layout.preferredHeight: root.compactRail ? 42 : 64; Accessible.name: "退出"; onClicked: clientCoordinator.requestClose()
                    contentItem: Column { spacing: 5
                        Image { anchors.horizontalCenter: parent.horizontalCenter; width: 22; height: 22; source: "icons/exit.png"; opacity: .65 }
                        Label { anchors.horizontalCenter: parent.horizontalCenter; visible: !root.compactRail; text: "退出"; color: "#4c6282"; font.pixelSize: 12 }
                    }
                }
            }
        }
        ColumnLayout {
        Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
        RowLayout {
            Layout.fillWidth: true; Layout.preferredHeight: 60
            Layout.leftMargin: root.workspaceMargin; Layout.rightMargin: root.workspaceMargin
            AppButton { visible: root.page === "resources" || root.page === "downloads" || root.page === "recognition" || root.page === "replay"; text: "返回"; implicitWidth: 62; onClicked: root.page = root.section(root.page) }
            Label { text: root.heading(root.page); color: "#17365e"; font.pixelSize: 14; font.bold: true }
            Item { Layout.fillWidth: true }
            Label { visible: root.width >= 800; text: clientCoordinator.boundWindow ? "已连接游戏" : "等待连接游戏"; color: "#5b7294"; font.pixelSize: 12 }
            AppButton { text: "最小化到任务栏"; onClicked: root.showMinimized() }
        }
        StackLayout {
            objectName: "clientPages"
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.leftMargin: root.workspaceMargin; Layout.rightMargin: root.workspaceMargin; Layout.bottomMargin: 24
            currentIndex: root.pages.indexOf(root.page)
            ClientHome {}
            ClientGallery {}
            ClientResources {}
            ClientSettings { profileOptions: root.routeOptions; profilesMode: routeChoices.mode }
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
            ClientRecognition {}
            ClientCustomGallery {}
            ClientOnlinePackages {}
        }
        }
    }
    FileDialog { id: replayPicker; title: "选择识别回放"; nameFilters: ["星图回放 (replay.json)"]; onAccepted: clientCoordinator.loadReplay(selectedFile.toString()) }
    Popup {
        anchors.centerIn: parent; width: 310; height: 110; modal: true; closePolicy: Popup.NoAutoClose
        visible: clientCoordinator.closing && !clientCoordinator.closeReady
        background: Rectangle { color: "white"; radius: 12 }
        Label { anchors.centerIn: parent; width: parent.width-32; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; text: "正在结束任务并保存已接受的操作…"; color: "#183a73" }
    }
}
