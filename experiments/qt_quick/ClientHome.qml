// Geometry and component grouping: shipped Tk 3438365b (38490F4F).
// The requested blue/white palette is kept across the migrated surfaces.
import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    readonly property bool wide: width >= 720
    readonly property var hostWindow: page.Window.window
    property var pendingUpdate: ({})
    function showPendingUpdate() {
        if (!pendingUpdate.version || !hostWindow || !hostWindow.visible
                || hostWindow.visibility === Window.Minimized) return
        updateAnnouncement.releaseData = pendingUpdate
        pendingUpdate = ({})
        updateAnnouncement.open()
    }
    Connections {
        target: infoService
        function onStartupAnnouncement(release) {
            page.pendingUpdate = release
            page.showPendingUpdate()
        }
    }
    Connections {
        target: page.hostWindow
        function onVisibleChanged() { page.showPendingUpdate() }
        function onVisibilityChanged() { page.showPendingUpdate() }
    }
    function canRun(action) {
        return session.snapshot.commands && session.snapshot.commands[action]
            && session.snapshot.commands[action].available && !session.busy && !resourceService.busy
    }
    readonly property string roundAction: session.snapshot.roundActive ? "end_round"
        : session.snapshot.roundPreparing ? "toggle_round" : "start_round"
    ScrollView {
        id: scroll
        anchors.fill: parent; clip: true; contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        GridLayout {
            width: scroll.availableWidth
            columns: page.wide ? 2 : 1
            columnSpacing: page.wide ? Math.round(page.width * .034 / 8) * 8 : 0
            rowSpacing: 24
            ColumnLayout {
                Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; spacing: 12
                Rectangle {
                    id: hero
                    objectName: "homeHero"
                    Layout.fillWidth: true; implicitHeight: Math.max(279, heroContents.implicitHeight + 48)
                    radius: 12; color: "#e9effd"
                    ColumnLayout {
                        id: heroContents
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.leftMargin: 28; anchors.rightMargin: 28; anchors.topMargin: 24; spacing: 10
                        Label { text: "YOUR ROUTE, YOUR WAY"; color: "#526785"; font.pixelSize: 11; font.bold: true }
                        Label { objectName: "homeHeroTitle"; text: "地图与路线，\n随手查看。"; color: "#142035"; font.pixelSize: page.width >= 1200 ? 34 : hero.width >= 560 ? 30 : 26; font.bold: true; lineHeight: 1.15 }
                        Label { text: "先打开游戏控件，再回到游戏按地图键查看小抄。"; color: "#5c677d"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        RowLayout {
                            Layout.topMargin: 10; spacing: 10
                            AppButton { text: "打开游戏控件"; icon.source: "icons/panel.png"; icon.color: "transparent"; primary: false; implicitHeight: 44; onClicked: windows.show("controls") }
                            AppButton { text: "返回游戏"; implicitHeight: 44; enabled: clientCoordinator.boundWindow !== ""; onClicked: clientCoordinator.returnToGame() }
                        }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: roundContents.implicitHeight + 40
                    radius: 12; color: "white"
                    ColumnLayout {
                        id: roundContents
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 20; spacing: 12
                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: "本局"; color: "#17365e"; font.pixelSize: 19; font.bold: true }
                            AppButton { text: "噩梦"; implicitWidth: 56; implicitHeight: 32; selected: session.currentMode === "nightmare"; enabled: !session.busy && !resourceService.busy && !settingsService.busy; onClicked: clientCoordinator.setMode("nightmare") }
                            AppButton { text: "困难"; implicitWidth: 56; implicitHeight: 32; selected: session.currentMode === "difficult"; enabled: !session.busy && !resourceService.busy && !settingsService.busy; onClicked: clientCoordinator.setMode("difficult") }
                            Item { Layout.fillWidth: true }
                            AppButton { text: "识别详情"; implicitHeight: 32; onClicked: clientCoordinator.navigate("recognition") }
                        }
                        RowLayout {
                            objectName: "homeRoundStats"
                            Layout.fillWidth: true; spacing: 10
                            Repeater {
                                model: [{label: "状态", value: session.snapshot.controlStatusText || (session.snapshot.roundActive ? "本局进行中" : session.snapshot.roundPreparing ? "已准备" : "等待开始")},
                                        {label: "地图", value: session.snapshot.mapName || session.snapshot.mapId || "尚未确认"},
                                        {label: "楼层", value: session.snapshot.observedFloor ? (session.snapshot.observedFloor === "B1" ? "B1" : "F" + session.snapshot.observedFloor) : "尚未确认"}]
                                delegate: Rectangle {
                                    required property var modelData
                                    Layout.fillWidth: true; Layout.preferredWidth: 1; implicitHeight: 68
                                    radius: 12; color: "#f5f7fb"
                                    ColumnLayout {
                                        anchors.fill: parent; anchors.margins: 12; spacing: 3
                                        Label { text: modelData.label; font.pixelSize: 11; color: "#5c677d" }
                                        Label { text: modelData.value; font.pixelSize: 14; font.bold: true; color: "#142035"; Layout.fillWidth: true; elide: Text.ElideRight }
                                    }
                                }
                            }
                        }
                        Label { text: clientCoordinator.runtimeStatus; color: "#567096"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton { objectName: "clientStartRound"; text: session.snapshot.roundActionText || (session.snapshot.roundActive ? "结束本局" : session.snapshot.roundPreparing ? "取消准备" : "开始本局"); primary: true; enabled: page.canRun(page.roundAction); onClicked: page.roundAction === "start_round" ? clientCoordinator.startRound() : session.dispatch(page.roundAction) }
                            AppButton { text: "游戏连接设置"; onClicked: connectionDialog.open() }
                            Item { Layout.fillWidth: true }
                        }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: previewContents.implicitHeight + 40
                    radius: 12; color: "white"
                    ColumnLayout {
                        id: previewContents
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 20; spacing: 16
                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: "地图资源"; font.pixelSize: 19; font.bold: true; color: "#17365e" }
                            Item { Layout.fillWidth: true }
                            AppButton { text: "查看全部"; implicitHeight: 30; onClicked: clientCoordinator.navigate("gallery") }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 12
                            Repeater {
                                model: infoService.homePreviews
                                delegate: Item {
                                    required property var modelData
                                    Layout.fillWidth: true; Layout.preferredWidth: 100; implicitHeight: 146
                                    Rectangle {
                                        anchors.left: parent.left; anchors.right: parent.right; height: 116; radius: 9; color: "#f0f5fc"
                                        Image { anchors.fill: parent; anchors.margins: 9; objectName: "homeMapPreview"; source: modelData.imageUrl; asynchronous: true; fillMode: Image.PreserveAspectFit; sourceSize.width: 320; sourceSize.height: 220 }
                                    }
                                    Label { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; text: modelData.title + " · " + (modelData.floor === "B1" ? "B1" : "F" + modelData.floor) + (modelData.structure ? " · 结构图" : ""); color: "#567096"; font.pixelSize: 11; elide: Text.ElideRight }
                                    MouseArea { objectName: "homePreviewOpen"; anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: { if (parent.modelData.key) infoService.openHomePreview(parent.modelData.key); else clientCoordinator.navigate("gallery") } }
                                }
                            }
                        }
                        Label { visible: infoService.homePreviews.length === 0; text: "导入本地图包后，可在这里快速查看已安装地图。"; color: "#567096"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: !page.wide
                Layout.preferredWidth: page.wide ? Math.max(250, Math.round(page.width * .261 / 8) * 8) : -1
                Layout.maximumWidth: page.wide ? Math.max(250, Math.round(page.width * .261 / 8) * 8) : Infinity
                Layout.alignment: Qt.AlignTop; spacing: 12
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: shortcutContents.implicitHeight + 40; radius: 12; color: "white"
                    ColumnLayout {
                        id: shortcutContents
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 20; spacing: 8
                        Label { text: "快捷键概览"; color: "#17365e"; font.pixelSize: 18; font.bold: true }
                        Label { text: "当前已保存的快捷键"; color: "#567096"; font.pixelSize: 12 }
                        Repeater {
                            model: [{label:"游戏开图", key:settingsService.persistedValues.hotkey},
                                    {label:"显示 / 隐藏控件", key:settingsService.persistedValues.actionHotkeys[0]},
                                    {label:"开始 / 结束本局", key:settingsService.persistedValues.actionHotkeys[8]},
                                    {label:"小抄显隐", key:settingsService.persistedValues.actionHotkeys[6]}]
                            delegate: ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true; spacing: 8
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label { text: modelData.label; color: "#17365e"; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    Rectangle {
                                        implicitWidth: Math.max(42, hotkey.implicitWidth + 18); implicitHeight: 34
                                        radius: 7; color: "#f3f7ff"; border.color: "#c8dafb"
                                        Label { id: hotkey; anchors.centerIn: parent; text: modelData.key || "未设置"; color: "#245de7"; font.bold: true; font.pixelSize: 13 }
                                    }
                                }
                            }
                        }
                        AppButton { text: "修改快捷键"; implicitHeight: 34; onClicked: clientCoordinator.navigate("settings") }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: helpContents.implicitHeight + 40; radius: 12; color: "white"
                    ColumnLayout {
                        id: helpContents
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 20; spacing: 12
                        Repeater {
                            model: [{title:"使用帮助", note:"地图键、显隐与常见问题", icon:"help"},
                                    {title:"排查与反馈", note:"查看运行信息，导出排查文件", icon:"note"}]
                            delegate: ToolButton {
                                required property var modelData
                                Layout.fillWidth: true; implicitHeight: 56
                                onClicked: clientCoordinator.navigate("help")
                                contentItem: RowLayout {
                                    spacing: 12
                                    Image { source: "icons/" + modelData.icon + ".png"; sourceSize.width: 60; sourceSize.height: 60; Layout.preferredWidth: 30; Layout.preferredHeight: 30 }
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 3
                                        Label { text: modelData.title; font.bold: true; color: "#17365e"; font.pixelSize: 13 }
                                        Label { text: modelData.note; color: "#567096"; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    }
                                }
                                background: Rectangle { radius: 8; color: parent.hovered ? "#f1f5fc" : "transparent" }
                            }
                        }
                        AppButton { text: "查看识别回放"; implicitHeight: 32; onClicked: clientCoordinator.navigate("replay") }
                    }
                }
            }
        }
    }
    Dialog {
        id: updateAnnouncement
        objectName: "startupUpdateAnnouncement"
        property var releaseData: ({})
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(520, parent ? parent.width - 40 : 520)
        modal: true; focus: false; title: "发现新版本 v" + (releaseData.version || "")
        closePolicy: Popup.CloseOnEscape
        onOpened: infoService.markRead()
        background: Rectangle { radius: 12; color: "white"; border.color: "#d5e2f5" }
        contentItem: ColumnLayout {
            spacing: 14
            Label { text: "当前 " + infoService.version + " · " + (updateAnnouncement.releaseData.published_date || ""); color: "#567096"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label { text: "更新公告"; color: "#17365e"; font.bold: true }
            ScrollView {
                Layout.fillWidth: true; Layout.preferredHeight: 190; clip: true; contentWidth: availableWidth
                Label { width: parent.width; text: updateAnnouncement.releaseData.notes || ""; wrapMode: Text.Wrap; textFormat: Text.PlainText; color: "#354f75" }
            }
            Flow {
                Layout.fillWidth: true; spacing: 8
                AppButton { text: "夸克网盘下载"; primary: true; visible: !!(updateAnnouncement.releaseData.pages && updateAnnouncement.releaseData.pages.quark); enabled: !infoService.checking; onClicked: { infoService.openDownloadPage("quark"); updateAnnouncement.close() } }
                AppButton { text: "UC 网盘下载"; visible: !!(updateAnnouncement.releaseData.pages && updateAnnouncement.releaseData.pages.uc); enabled: !infoService.checking; onClicked: { infoService.openDownloadPage("uc"); updateAnnouncement.close() } }
                AppButton { objectName: "startupUpdateSkip"; text: "跳过此版本"; enabled: !infoService.checking; onClicked: { infoService.skipVersion(); updateAnnouncement.close() } }
                AppButton { objectName: "startupUpdateLater"; text: "稍后提醒"; onClicked: updateAnnouncement.close() }
            }
        }
    }
    Dialog {
        id: connectionDialog
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(520, parent ? parent.width - 40 : 520)
        modal: true; title: "连接游戏窗口"; standardButtons: Dialog.Close
        background: Rectangle { radius: 16; color: "white"; border.color: "#d5e2f5" }
        contentItem: ColumnLayout {
            spacing: 12
            Label { text: "会自动连接唯一的第五人格窗口；有多个窗口或自动连接失败时，可在此选择。"; color: "#567096"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            ComboBox { id: gameWindow; objectName: "gameWindowPicker"; Layout.fillWidth: true; model: clientCoordinator.gameWindows; textRole: "title"; valueRole: "hwnd"; displayText: currentIndex < 0 ? "选择游戏窗口" : currentText }
            RowLayout {
                AppButton { text: "查找游戏"; onClicked: clientCoordinator.refreshGameWindows() }
                AppButton { text: "连接窗口"; enabled: gameWindow.currentIndex >= 0 && !session.busy; onClicked: clientCoordinator.bindGameWindow(gameWindow.currentValue) }
                Item { Layout.fillWidth: true }
            }
            CheckBox { text: "启用游戏内操作快捷键"; checked: clientCoordinator.inputEnabled; onClicked: clientCoordinator.setInputEnabled(checked) }
            Label { text: clientCoordinator.message; color: "#567096"; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 12 }
        }
    }
}
