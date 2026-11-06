// Geometry and component grouping: shipped Tk 3438365b (38490F4F).
// The requested blue/white palette is kept across the migrated surfaces.
import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    readonly property var privateExperiment: session.snapshot.privateExperiment || ({})
    readonly property string privateLockedMap: privateExperiment.privateLockMapId || ""
    readonly property bool privateIdentityRetained: session.snapshot.season === "s1" && session.snapshot.roundActive === true
        && privateExperiment.privateExperimental === true && privateExperiment.privateExperimentalLocked === true
        && privateLockedMap.length > 0
    readonly property bool privatePoseCurrent: session.snapshot.displayAuthorized === true
        && (session.snapshot.provisionalMapId || session.snapshot.mapId || "") === privateLockedMap
    readonly property string privateStatus: "实验锁定（未正式校准） · " + (privatePoseCurrent ? "当前画面已贴合" : "位置待核对")
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
    function floorLabel(floor, label) {
        if (label) return label
        if (session.appliedSeason === "s1" && /^-[1-4]$/.test(String(floor)))
            return "B" + Math.abs(Number(floor))
        return floor === "B1" ? "B1" : "F" + floor
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
                    radius: 16
                    // Brand hero: deep-to-bright blue, white type, a star chart on the right.
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0; color: "#1f4fd1" }
                        GradientStop { position: 1; color: "#4c86ff" }
                    }
                    // A faint star chart on the right, echoing the Tk hero: decoration only.
                    Canvas {
                        objectName: "homeHeroStars"
                        visible: hero.width >= 560
                        anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                        width: Math.min(260, hero.width * .38)
                        onWidthChanged: requestPaint(); onHeightChanged: requestPaint()
                        onPaint: {
                            var ctx = getContext("2d"); ctx.reset()
                            var w = width, h = height
                            var pts = [[.12,.70],[.32,.52],[.50,.60],[.70,.34],[.86,.22]]
                            ctx.strokeStyle = "rgba(255,255,255,0.55)"; ctx.lineWidth = 2
                            ctx.beginPath(); ctx.moveTo(pts[0][0]*w, pts[0][1]*h)
                            for (var i = 1; i < pts.length; ++i) ctx.lineTo(pts[i][0]*w, pts[i][1]*h)
                            ctx.stroke()
                            ctx.strokeStyle = "rgba(255,255,255,0.18)"; ctx.lineWidth = 1
                            ctx.beginPath(); ctx.arc(.55*w, .55*h, .42*Math.min(w, h), 0, Math.PI*2); ctx.stroke()
                            ctx.beginPath(); ctx.arc(.55*w, .55*h, .26*Math.min(w, h), 0, Math.PI*2); ctx.stroke()
                            for (var j = 0; j < pts.length - 1; ++j) {
                                ctx.beginPath(); ctx.arc(pts[j][0]*w, pts[j][1]*h, 5, 0, Math.PI*2)
                                ctx.fillStyle = "#3a6ff0"; ctx.fill()
                                ctx.strokeStyle = "white"; ctx.lineWidth = 2; ctx.stroke()
                            }
                            var sx = pts[4][0]*w, sy = pts[4][1]*h, r = 14
                            ctx.fillStyle = "#f2b544"; ctx.beginPath()
                            ctx.moveTo(sx, sy - r); ctx.quadraticCurveTo(sx, sy, sx + r, sy)
                            ctx.quadraticCurveTo(sx, sy, sx, sy + r); ctx.quadraticCurveTo(sx, sy, sx - r, sy)
                            ctx.quadraticCurveTo(sx, sy, sx, sy - r); ctx.fill()
                            var dots = [[.20,.20],[.42,.14],[.62,.82],[.82,.66],[.28,.88],[.92,.48]]
                            ctx.fillStyle = "rgba(255,255,255,0.6)"
                            for (var k = 0; k < dots.length; ++k) { ctx.beginPath(); ctx.arc(dots[k][0]*w, dots[k][1]*h, 1.6, 0, Math.PI*2); ctx.fill() }
                        }
                    }
                    ColumnLayout {
                        id: heroContents
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.leftMargin: 28; anchors.rightMargin: 28; anchors.topMargin: 24; spacing: 10
                        Label { text: "✦  YOUR ROUTE, YOUR WAY"; color: "#cddbff"; font.pixelSize: 11; font.bold: true; font.letterSpacing: 1 }
                        Label { objectName: "homeHeroTitle"; text: "地图与路线，\n随手查看。"; color: "white"; font.pixelSize: page.width >= 1200 ? 34 : hero.width >= 560 ? 30 : 26; font.bold: true; lineHeight: 1.15 }
                        Label { text: "先打开游戏控件，再回到游戏按地图键查看小抄。"; color: "#e3ebff"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        RowLayout {
                            Layout.topMargin: 10; spacing: 10
                            AppButton { objectName: "homeShowControls"; text: "打开游戏控件"; icon.source: "icons/panel.png"; icon.color: "transparent"; inverse: true; implicitHeight: 44; onClicked: clientCoordinator.showControls() }
                            AppButton { text: "返回游戏"; onColor: true; implicitHeight: 44; enabled: clientCoordinator.boundWindow !== ""; onClicked: clientCoordinator.returnToGame() }
                        }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: roundContents.implicitHeight + 40
                    radius: 16; color: "white"; border.width: 1; border.color: "#e3e9f3"
                    ColumnLayout {
                        id: roundContents
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 20; spacing: 12
                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: "本局"; color: "#17365e"; font.pixelSize: 19; font.bold: true }
                            SeasonSelector {
                                id: homeContextSelector; objectName: "homeGameContext"
                                seasons: settingsService.seasonOptions
                                season: session.appliedSeason; mode: session.appliedMode
                                allowCurrentContextRequest: !!settingsService.persistedValues
                                    && (settingsService.persistedValues.gameSeason !== session.appliedSeason
                                        || settingsService.persistedValues.gameMode !== session.appliedMode)
                                enabled: !session.busy && !resourceService.busy && !settingsService.busy && !session.snapshot.contextPending && !session.snapshot.resourceLocked && !session.snapshot.roundActive && !session.snapshot.roundPreparing
                                onContextRequested: function(season, mode) { clientCoordinator.requestGameContext(season, mode) }
                            }
                            Item { Layout.fillWidth: true }
                            AppButton { text: "识别详情"; implicitHeight: 32; onClicked: clientCoordinator.navigate("recognition") }
                        }
                        Label {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#567096"
                            visible: !session.busy && !settingsService.busy && !!settingsService.persistedValues
                                     && (settingsService.persistedValues.gameSeason !== session.appliedSeason
                                         || settingsService.persistedValues.gameMode !== session.appliedMode)
                            text: "默认赛季和难度已保存，本局仍显示已应用的环境。可重新选择默认项重试。"
                        }
                        RowLayout {
                            objectName: "homeRoundStats"
                            Layout.fillWidth: true; spacing: 10
                            Repeater {
                                model: [{label: "状态", value: page.privateIdentityRetained ? page.privateStatus : session.snapshot.controlStatusText || (session.snapshot.roundActive ? "本局进行中" : session.snapshot.roundPreparing ? "已准备" : "等待开始")},
                                        {label: "地图", value: page.privateIdentityRetained ? "本局 " + page.privateLockedMap : session.snapshot.mapName || session.snapshot.mapId || "尚未确认"},
                                        {label: "楼层", value: session.snapshot.observedFloor ? page.floorLabel(session.snapshot.observedFloor, "") : "尚未确认"}]
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
                    radius: 16; color: "white"; border.width: 1; border.color: "#e3e9f3"
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
                                    Label { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; text: modelData.title + " · " + page.floorLabel(modelData.floor, modelData.floorLabel || "") + (modelData.displayKind ? " · " + modelData.displayKind : modelData.structure ? " · 结构图" : ""); color: "#567096"; font.pixelSize: 11; elide: Text.ElideRight }
                                    MouseArea { objectName: "homePreviewOpen"; anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: { if (parent.modelData.key) infoService.openHomePreview(parent.modelData.key); else clientCoordinator.navigate("gallery") } }
                                }
                            }
                        }
                        Label { visible: infoService.homePreviews.length === 0; text: !homeContextSelector.registeredContext(session.appliedSeason, session.appliedMode) ? homeContextSelector.seasonLabel(session.appliedSeason) + " 资源待补齐" : "导入本地图包后，可在这里快速查看已安装地图。"; color: "#567096"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: !page.wide
                Layout.preferredWidth: page.wide ? Math.max(250, Math.round(page.width * .261 / 8) * 8) : -1
                Layout.maximumWidth: page.wide ? Math.max(250, Math.round(page.width * .261 / 8) * 8) : Infinity
                Layout.alignment: Qt.AlignTop; spacing: 12
                Rectangle {
                    objectName: "homeTutorialInvite"
                    visible: tutorialService.inviteVisible && !onboardingService.active
                    Layout.fillWidth: true; implicitHeight: inviteContents.implicitHeight + 36; radius: 12; color: "#13214a"
                    ColumnLayout {
                        id: inviteContents
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; spacing: 8
                        Label { text: "第一次用星图？"; color: "#f1f5fd"; font.pixelSize: 16; font.bold: true }
                        Label { text: "花几分钟，跟着图学会选路线、开图贴合。"; color: "#aebddf"; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        RowLayout {
                            spacing: 8
                            AppButton { objectName: "homeTutorialOpen"; text: "看看教程"; implicitHeight: 34; onClicked: { tutorialService.dismissInvite(); tutorialService.open("start") } }
                            AppButton { objectName: "homeTutorialDismiss"; text: "不用了"; implicitHeight: 34; onClicked: tutorialService.dismissInvite() }
                        }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: shortcutContents.implicitHeight + 40; radius: 16; color: "white"; border.width: 1; border.color: "#e3e9f3"
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
                    Layout.fillWidth: true; implicitHeight: helpContents.implicitHeight + 40; radius: 16; color: "white"; border.width: 1; border.color: "#e3e9f3"
                    ColumnLayout {
                        id: helpContents
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 20; spacing: 12
                        Repeater {
                            model: [{key:"tutorial", title:"使用教程", note:"跟着图一步步上手，附常见问题", icon:"help"},
                                    {key:"help", title:"排查与反馈", note:"查看运行信息，导出排查文件", icon:"note"}]
                            delegate: ToolButton {
                                required property var modelData
                                objectName: "homeEntry-" + modelData.key
                                Layout.fillWidth: true; implicitHeight: 56
                                onClicked: modelData.key === "tutorial" ? tutorialService.open("") : clientCoordinator.navigate("help")
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
            AppCheckBox { text: "启用游戏内操作快捷键"; checked: clientCoordinator.inputEnabled; onClicked: clientCoordinator.setInputEnabled(checked) }
            Label { text: clientCoordinator.message; color: "#567096"; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 12 }
        }
    }
}
