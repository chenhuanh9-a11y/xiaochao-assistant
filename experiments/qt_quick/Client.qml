// Operate: persistent Qt pages, actual shared services, separate passive game surfaces.
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ApplicationWindow {
    id: root
    palette: ClientPalette {}
    objectName: "qtClientMain"
    width: 1000; height: 720; minimumWidth: 650; minimumHeight: 470
    visible: true; title: productionProduct ? "星图" : "星图 · Qt 私测"
    // Light product palette, including when Windows is using a dark theme.
    // client_theme.py pins the application color scheme so controls stay readable.
    color: "#f3f6fc"; font.family: "Microsoft YaHei UI"; font.pixelSize: 13
    property string page: "home"
    property var pages: ["home", "gallery", "resources", "settings", "help", "custom", "downloads", "plaza"]
    readonly property bool compactRail: width < 800 || height < 660
    readonly property int workspaceMargin: width >= 1200 ? 48 : width < 760 ? 16 : 28
    readonly property bool reduceMotion: settingsService.values.reduceMotion === true
    onReduceMotionChanged: if (reduceMotion) { pageFadeDone.stop(); pageFade.stop(); clientPages.opacity = 1 }
    readonly property bool shellKeysEnabled: active && !clientCoordinator.textEntryActive
        && !clientCoordinator.closing && !closeChoice.opened
        && !(Overlay.overlay && Overlay.overlay.visible)
    readonly property var sectionOrder: ["home", "gallery", "custom", "plaza", "settings", "help"]
    readonly property bool shellHeadingVisible: page === "home" || page === "resources" || page === "downloads"
    property string pendingNavFocus: ""
    property bool pageFadeReady: false
    function section(name) { return name === "resources" || name === "downloads" ? "gallery" : name }
    function heading(name) { return ({home:"主页面",gallery:"地图资源",resources:"图包管理",downloads:"在线图包",settings:"设置",help:"帮助与公告",custom:"自定义路线",plaza:"路线广场"})[name] || "星图" }
    function activateSection(name) {
        if (sectionOrder.indexOf(name) < 0) return
        pendingNavFocus = name
        page = name
    }
    function moveSection(delta) {
        var index = sectionOrder.indexOf(section(page))
        if (index < 0) index = 0
        activateSection(sectionOrder[(index + delta + sectionOrder.length) % sectionOrder.length])
    }
    readonly property var routeOptions: {
        if (routeChoices.busy || routeChoices.season !== (settingsService.values.gameSeason || "s0")
                || routeChoices.mode !== settingsService.values.gameMode) return []
        var options = routeChoices.profiles.filter(function(row) { return row.runtimeSelectable !== false }).map(function(row) {
            return {id: row.runtimeProfileId || row.id, name: row.name}
        })
        var custom = customGalleryService.snapshot
        if (!routeChoices.busy && routeChoices.season === "s0" && routeChoices.mode === "nightmare" && custom.available)
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
        sequence: settingsService.applicationPanelShortcut
        context: Qt.ApplicationShortcut; autoRepeat: false
        enabled: !clientCoordinator.textEntryActive
        onActivated: clientCoordinator.toggleControlsKeyboard()
    }
    Shortcut { sequence: "Ctrl+1"; context: Qt.WindowShortcut; autoRepeat: false; enabled: root.shellKeysEnabled; onActivated: root.activateSection("home") }
    Shortcut { sequence: "Ctrl+2"; context: Qt.WindowShortcut; autoRepeat: false; enabled: root.shellKeysEnabled; onActivated: root.activateSection("gallery") }
    Shortcut { sequence: "Ctrl+3"; context: Qt.WindowShortcut; autoRepeat: false; enabled: root.shellKeysEnabled; onActivated: root.activateSection("custom") }
    Shortcut { sequence: "Ctrl+4"; context: Qt.WindowShortcut; autoRepeat: false; enabled: root.shellKeysEnabled; onActivated: root.activateSection("plaza") }
    Shortcut { sequence: "Ctrl+5"; context: Qt.WindowShortcut; autoRepeat: false; enabled: root.shellKeysEnabled; onActivated: root.activateSection("settings") }
    Shortcut { sequence: "Ctrl+6"; context: Qt.WindowShortcut; autoRepeat: false; enabled: root.shellKeysEnabled; onActivated: root.activateSection("help") }
    Shortcut { sequence: "Ctrl+Tab"; context: Qt.WindowShortcut; autoRepeat: false; enabled: root.shellKeysEnabled; onActivated: root.moveSection(1) }
    Shortcut { sequence: "Ctrl+Shift+Tab"; context: Qt.WindowShortcut; autoRepeat: false; enabled: root.shellKeysEnabled; onActivated: root.moveSection(-1) }
    Shortcut { sequence: "Ctrl+PgDown"; context: Qt.WindowShortcut; autoRepeat: false; enabled: root.shellKeysEnabled; onActivated: root.moveSection(1) }
    Shortcut { sequence: "Ctrl+PgUp"; context: Qt.WindowShortcut; autoRepeat: false; enabled: root.shellKeysEnabled; onActivated: root.moveSection(-1) }
    Shortcut {
        sequence: "Alt+Left"; context: Qt.WindowShortcut; autoRepeat: false
        enabled: root.shellKeysEnabled && (root.page === "resources" || root.page === "downloads")
        onActivated: root.activateSection("gallery")
    }
    Component.onCompleted: pageFadeReady = true
    ClientOnboarding {}
    ClientTutorial {}
    ClientAccount {}
    ClientAnnouncement {
        hostWindow: root
        blocked: clientCoordinator.closing || closeChoice.visible || onboardingService.active
    }
    onClosing: function(event) {
        if (clientCoordinator.closeReady) {
            closeChoice.close()
            event.accepted = true
        } else {
            event.accepted = false
            if (!clientCoordinator.closing) closeChoice.open()
        }
    }
    RowLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            objectName: "clientNavigationRail"
            Layout.preferredWidth: root.compactRail ? 80 : 96; Layout.fillHeight: true; color: "white"
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: "#e3e9f3" }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 8; spacing: 4
                Item { Layout.fillWidth: true; Layout.preferredHeight: root.compactRail ? 84 : 122
                    Column { anchors.centerIn: parent; spacing: 7
                        Image { anchors.horizontalCenter: parent.horizontalCenter; width: root.compactRail ? 38 : 46; height: width; source: "icons/xingtu-round.png"; sourceSize.width: 104; sourceSize.height: 104 }
                        Label { anchors.horizontalCenter: parent.horizontalCenter; visible: !root.compactRail; text: "星图"; color: "#17365e"; font.bold: true; font.pixelSize: 15; font.letterSpacing: 2 }
                    }
                }
                Repeater {
                    model: [{key:"home",label:"主页面",icon:"home"},{key:"gallery",label:"地图资源",icon:"map"},
                            {key:"custom",label:"自定义",icon:"route"},{key:"plaza",label:"路线广场",icon:"note"},{key:"settings",label:"设置",icon:"settings"},{key:"help",label:"帮助",icon:"help"}]
                    AppButton {
                        id: navigation
                        required property var modelData
                        required property int index
                        objectName: "nav_"+modelData.key
                        Layout.fillWidth: true; Layout.preferredHeight: root.compactRail ? 48 : 68
                        implicitWidth: 0; Layout.minimumWidth: 0; padding: 8
                        selected: root.section(root.page) === modelData.key
                        Accessible.name: modelData.label
                        Accessible.description: "Ctrl+" + (index + 1)
                        ToolTip.visible: hovered; ToolTip.delay: 450
                        ToolTip.text: modelData.label + "（Ctrl+" + (index + 1) + "）"
                        onClicked: root.page = modelData.key
                        function takeKeyboardFocus() {
                            if (root.pendingNavFocus === modelData.key && root.section(root.page) === modelData.key) {
                                root.pendingNavFocus = ""
                                forceActiveFocus()
                            }
                        }
                        onSelectedChanged: takeKeyboardFocus()
                        Connections {
                            target: root
                            function onPendingNavFocusChanged() { navigation.takeKeyboardFocus() }
                        }
                        // Navigation reads as a list, not a stack of buttons: only the
                        // current page is filled and marked; hover is a light tint.
                        background: Rectangle {
                            radius: 12
                            color: navigation.selected ? "#e3ecff" : navigation.down ? "#e3ecff" : navigation.hovered ? "#f1f5fd" : "transparent"
                            border.width: navigation.activeFocus ? 2 : 0; border.color: "#2f66eb"
                            Behavior on color { ColorAnimation { duration: 110 } }
                            Rectangle {
                                visible: navigation.selected; width: 4; height: 24; radius: 2; color: "#2f66eb"
                                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                        contentItem: Column {
                            spacing: 5
                            Image { anchors.horizontalCenter: parent.horizontalCenter; width: 23; height: 23; source: "icons/"+navigation.modelData.icon+".png"; sourceSize.width: 46; sourceSize.height: 46; opacity: root.section(root.page) === navigation.modelData.key ? 1 : .65 }
                            Label { anchors.horizontalCenter: parent.horizontalCenter; visible: !root.compactRail; text: navigation.modelData.label; color: root.section(root.page) === navigation.modelData.key ? "#245de7" : "#4c6282"; font.pixelSize: 12 }
                        }
                    }
                }
                Item { Layout.fillHeight: true }
                Rectangle {
                    visible: !root.compactRail
                    Layout.fillWidth: true; implicitHeight: railStatus.implicitHeight + 20
                    radius: 10; color: "#f3f7ff"
                    ColumnLayout {
                        id: railStatus; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10; spacing: 6
                        RowLayout {
                            Layout.fillWidth: true; spacing: 6
                            Rectangle {
                                Layout.alignment: Qt.AlignVCenter
                                Layout.preferredWidth: 8; Layout.preferredHeight: 8; radius: 4
                                color: clientCoordinator.boundWindow ? "#17775a" : "#b7791f"
                            }
                            Label {
                                text: clientCoordinator.boundWindow ? "已连接" : "未连接"
                                color: "#354f75"; font.pixelSize: 11; Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }
                        Label { text: infoService.facts.version; color: "#5b7294"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: "#d5e2f5"; Layout.leftMargin: 4; Layout.rightMargin: 4 }
                AppButton { id: exitNavigation; objectName: "nav_exit"; implicitWidth: 0; Layout.minimumWidth: 0
                    Layout.fillWidth: true; Layout.preferredHeight: root.compactRail ? 42 : 64
                    Accessible.name: "退出星图"; enabled: !clientCoordinator.closing; onClicked: closeChoice.open()
                    background: Rectangle {
                        radius: 12; color: exitNavigation.down ? "#fbe7ea" : exitNavigation.hovered ? "#fdf1f3" : "transparent"
                        border.width: exitNavigation.activeFocus ? 2 : 0; border.color: "#2f66eb"
                    }
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
            AppButton {
                visible: root.page === "resources" || root.page === "downloads"
                text: "返回"; Accessible.description: "返回地图资源，或按 Alt+左方向键"
                implicitWidth: 62
                onClicked: root.page = root.section(root.page)
            }
            // Pages with their own large title skip the duplicate small one.
            Label {
                visible: root.shellHeadingVisible
                text: root.heading(root.page); color: "#142b4b"; font.pixelSize: 16; font.bold: true
                elide: Text.ElideRight; Layout.fillWidth: true
            }
            Item { visible: !root.shellHeadingVisible; Layout.fillWidth: true }
            RowLayout {
                objectName: "connectionStatusChip"
                spacing: 6
                Layout.maximumWidth: root.width < 860 ? 108 : 168
                Rectangle {
                    Layout.alignment: Qt.AlignVCenter
                    Layout.preferredWidth: 8; Layout.preferredHeight: 8; radius: 4
                    color: clientCoordinator.boundWindow ? "#17775a" : "#b7791f"
                    Behavior on color { ColorAnimation { duration: root.reduceMotion ? 0 : 140 } }
                }
                Label {
                    objectName: "connectionStatusText"
                    Layout.fillWidth: true
                    text: {
                        var bound = !!clientCoordinator.boundWindow
                        return root.width < 860 ? (bound ? "已连接" : "未连接") : (bound ? "已连上游戏" : "还没连上游戏")
                    }
                    color: "#354f75"; font.pixelSize: 12; elide: Text.ElideRight
                    Accessible.name: clientCoordinator.boundWindow ? "已连上游戏" : "还没连上游戏"
                }
            }
        }
        StackLayout {
            id: clientPages
            objectName: "clientPages"
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.leftMargin: root.workspaceMargin; Layout.rightMargin: root.workspaceMargin; Layout.bottomMargin: 24
            currentIndex: root.pages.indexOf(root.page)
            onCurrentIndexChanged: {
                if (!root.pageFadeReady) return
                if (root.reduceMotion) {
                    pageFadeDone.stop()
                    pageFade.stop()
                    opacity = 1
                    return
                }
                pageFade.restart()
                pageFadeDone.restart()
            }
            ClientHome {}
            ClientLazyPage {
                objectName: "lazyGalleryPage"; selected: root.page === "gallery"
                sourceComponent: Component { ClientGallery { packageActions: resourcePage } }
            }
            ClientImportedResources {
                id: resourcePage
                active: root.page === "resources" && !clientCoordinator.closing
                refreshAllowed: !session.busy && !session.snapshot.resourceLocked
                    && !session.snapshot.contextPending && !session.snapshot.roundActive
                    && !session.snapshot.roundWorkActive && !session.snapshot.roundPreparing
                    && !session.snapshot.closed && !clientCoordinator.closing
            }
            ClientLazyPage {
                objectName: "lazySettingsPage"; selected: root.page === "settings"
                sourceComponent: Component { ClientSettings { profileOptions: root.routeOptions; profilesMode: routeChoices.mode; profilesSeason: routeChoices.season; profilesBusy: routeChoices.busy } }
            }
            ClientLazyPage { objectName: "lazyHelpPage"; selected: root.page === "help"; sourceComponent: Component { ClientHelp {} } }
            ClientLazyPage { objectName: "lazyCustomPage"; selected: root.page === "custom"; sourceComponent: Component { ClientCustomGallery {} } }
            ClientLazyPage { objectName: "lazyDownloadsPage"; selected: root.page === "downloads"; sourceComponent: Component { ClientOnlinePackages {} } }
            ClientLazyPage { objectName: "lazyPlazaPage"; selected: root.page === "plaza"; sourceComponent: Component { ClientRoutePlaza {} } }
        }
        }
    }
    NumberAnimation {
        id: pageFade; objectName: "clientPageFade"
        target: clientPages; property: "opacity"; from: 0.72; to: 1; duration: 160
        easing.type: Easing.OutCubic
        onFinished: clientPages.opacity = 1
    }
    Timer {
        id: pageFadeDone
        interval: 180
        onTriggered: { pageFade.stop(); clientPages.opacity = 1 }
    }
    Dialog {
        id: closeChoice; objectName: "clientCloseChoice"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(420, root.width - 40)
        modal: true; focus: true
        closePolicy: Popup.CloseOnEscape
        title: "关闭星图"
        background: Rectangle { color: "white"; radius: 12; border.width: 1; border.color: "#d6e0ef" }
        header: Label { text: closeChoice.title; padding: 20; color: "#142b4b"; font.pixelSize: 17; font.bold: true }
        // Button ids inside contentItem are not visible on the dialog itself.
        onOpened: Qt.callLater(function() {
            var pending = [closeChoice, closeChoice.contentItem]
            while (pending.length) {
                var node = pending.pop()
                if (!node) continue
                if (node.objectName === "cancelClose") { node.forceActiveFocus(); return }
                var kids = node.children || []
                for (var i = 0; i < kids.length; ++i) pending.push(kids[i])
            }
        })
        contentItem: ColumnLayout {
            spacing: 10
            Label {
                objectName: "closeChoiceHint"
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#526785"
                text: "后台运行会收起窗口，快捷键仍然可用。退出会结束星图。取消则留在当前页面。"
            }
            AppButton { objectName: "closeToBackground"; Layout.fillWidth: true; text: "后台运行"
                // Establish the visibility gate before releasing the dialog gate.
                onClicked: { windows.hide("main"); closeChoice.close() }
            }
            AppButton { objectName: "closeApplication"; Layout.fillWidth: true; text: "退出星图"; primary: true
                onClicked: {
                    if (typeof gameStartupService !== "undefined") gameStartupService.suppressCurrentGame()
                    clientCoordinator.requestClose()
                    closeChoice.close()
                }
            }
            AppButton { objectName: "cancelClose"; Layout.fillWidth: true; text: "取消"; onClicked: closeChoice.close() }
        }
    }
    Popup {
        objectName: "clientClosingNotice"
        anchors.centerIn: parent
        width: Math.min(360, Math.max(220, root.width - 40))
        height: 132
        padding: 18
        modal: true; closePolicy: Popup.NoAutoClose
        visible: clientCoordinator.closing && !clientCoordinator.closeReady
        background: Rectangle { color: "white"; radius: 12; border.width: 1; border.color: "#d6e0ef" }
        contentItem: ColumnLayout {
            spacing: 12
            Label {
                Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                text: "正在保存并退出，请稍候…"; color: "#183a73"
            }
            ProgressBar {
                objectName: "clientClosingProgress"
                Layout.fillWidth: true; indeterminate: true
                Accessible.name: "正在保存并退出"
            }
        }
    }
}
