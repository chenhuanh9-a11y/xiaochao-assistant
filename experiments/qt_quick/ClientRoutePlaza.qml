pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root
    objectName: "routePlazaPage"
    readonly property bool compact: width < 680
    property var state: ({busy:false, username:"", operation:"", message:"", error:false, loaded:false, downloading:""})
    function sync() { if (visible) state = routePlazaService.snapshot }
    Connections {
        target: routePlazaService
        function onChanged() { root.sync() }
        function onDownloadBlocked(message) {
            if (!root.visible) return
            resourceRequired.message = message
            resourceRequired.open()
        }
        function onOpenCustomRequested() { if (root.visible) clientCoordinator.navigate("custom") }
    }
    onVisibleChanged: {
        if (!visible) resourceRequired.close()
        sync(); routePlazaService.setActive(visible)
    }
    Component.onCompleted: { sync(); routePlazaService.setActive(visible) }
    ClientRouteSubmission {}
    ClientRouteDiscussion {}
    ClientRoutePreview {}
    Dialog {
        id: resourceRequired
        objectName: "plazaStructureRequired"
        property string message: ""
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(460, parent.width - 32)
        title: "无法下载路线"
        modal: true; standardButtons: Dialog.Ok
        contentItem: Label {
            text: resourceRequired.message; textFormat: Text.PlainText
            wrapMode: Text.Wrap; color: "#354f75"
        }
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 8
        GridLayout {
            objectName: "plazaToolbar"
            Layout.fillWidth: true
            columns: root.compact ? 2 : 3; columnSpacing: 8; rowSpacing: 6
            Label { text: "路线广场"; color: "#142b4b"; font.pixelSize: 22; font.bold: true; Layout.fillWidth: true }
            RowLayout {
                spacing: 4
                AppButton { objectName: "plazaSquare"; text: "广场"; selected: root.state.tab !== "mine"; enabled: !root.state.busy; onClicked: routePlazaService.selectTab("square") }
                AppButton { objectName: "plazaMine"; text: "我的投稿"; selected: root.state.tab === "mine"; enabled: !root.state.busy; onClicked: routePlazaService.selectTab("mine") }
            }
            RowLayout {
                Layout.columnSpan: root.compact ? 2 : 1; Layout.alignment: Qt.AlignRight
                spacing: 6
                Label { objectName: "plazaAccountStatus"; text: root.state.username ? "已登录：" + root.state.username : "未登录"; color: "#526785"; textFormat: Text.PlainText; Layout.maximumWidth: 118; elide: Text.ElideRight }
                AppButton { objectName: "plazaLogin"; text: "登录"; visible: !root.state.username; enabled: !root.state.busy; onClicked: routePlazaService.openAccount() }
                AppButton { objectName: "plazaRecovery"; text: "领取恢复码"; visible: !!root.state.recoveryPending; enabled: !root.state.busy; onClicked: routePlazaService.openAccount() }
                AppButton { objectName: "plazaLogout"; text: "退出登录"; visible: !!root.state.username; enabled: !root.state.busy; onClicked: routePlazaService.logout() }
                AppButton { objectName: "plazaSubmit"; text: "投稿"; enabled: !root.state.busy; onClicked: routePlazaService.openSubmission("") }
            }
        }
        Label { visible: settingsService && (settingsService.loadFailed || settingsService.message.indexOf("失败") >= 0); text: settingsService ? settingsService.message : ""; color: "#b91c1c"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        GridLayout {
            visible: root.state.tab !== "mine"
            Layout.fillWidth: true
            columns: root.compact ? 1 : 2
            columnSpacing: 8; rowSpacing: 6
            RowLayout {
                Layout.fillWidth: true; spacing: 6
                TextField { id: search; objectName: "plazaSearch"; Layout.fillWidth: true; Layout.minimumWidth: 100; Layout.preferredHeight: 38; placeholderText: "搜索路线"; Accessible.name: "搜索路线"; selectByMouse: true; maximumLength: 80; onAccepted: routePlazaService.search(text) }
                AppButton { objectName: "plazaSearchButton"; text: "搜索"; enabled: !root.state.busy; onClicked: routePlazaService.search(search.text) }
            }
            RowLayout {
                Layout.fillWidth: root.compact; spacing: 6
                ComboBox {
                    objectName: "plazaSeasonFilter"
                    Layout.minimumWidth: 104; Layout.preferredWidth: 104; Layout.maximumWidth: 104
                    model: root.state.seasonOptions || [{id:"",name:"全部赛季"},{id:"s0",name:"灾厄之女"},{id:"s1",name:"磷光症"}]
                    textRole: "name"; valueRole: "id"; Accessible.name: "按赛季筛选"
                    currentIndex: indexOfValue(root.state.publicSeason || "")
                    enabled: !root.state.busy
                    onActivated: routePlazaService.setPublicFilter(currentValue, root.state.publicMode || "")
                }
                ComboBox {
                    objectName: "plazaModeFilter"
                    Layout.minimumWidth: 104; Layout.preferredWidth: 104; Layout.maximumWidth: 104
                    model: [{id:"",name:"全部难度"},{id:"nightmare",name:"噩梦"},{id:"hard",name:"困难"}]
                    textRole: "name"; valueRole: "id"; Accessible.name: "按难度筛选"
                    currentIndex: indexOfValue(root.state.publicMode || "")
                    enabled: !root.state.busy
                    onActivated: routePlazaService.setPublicFilter(root.state.publicSeason || "", currentValue)
                }
                ComboBox {
                    objectName: "plazaSort"
                    Layout.minimumWidth: 104; Layout.preferredWidth: 104; Layout.maximumWidth: 104
                    model: [{id:"latest",name:"最新"},{id:"likes",name:"点赞最多"},{id:"comments",name:"讨论最多"}]
                    textRole: "name"; valueRole: "id"; Accessible.name: "路线排序"
                    currentIndex: indexOfValue(root.state.sort || "latest")
                    enabled: !root.state.busy
                    onActivated: routePlazaService.setSort(currentValue)
                }
                StackLayout {
                    Layout.fillWidth: false; Layout.fillHeight: false
                    Layout.preferredWidth: 92; Layout.preferredHeight: 38
                    currentIndex: root.state.operation === "download" ? 1 : 0
                    AppButton { objectName: "plazaRefresh"; text: "刷新"; enabled: !root.state.busy; onClicked: routePlazaService.search(search.text) }
                    AppButton { objectName: "plazaCancelDownload"; text: "取消下载"; onClicked: routePlazaService.cancelDownload() }
                }
            }
        }
        Label { objectName: "plazaStatus"; text: root.state.message; textFormat: Text.PlainText; color: root.state.error ? "#b91c1c" : "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RowLayout {
            visible: root.state.tab === "mine"
            Layout.fillWidth: true
            Label { text: "投稿范围"; color: "#526785" }
            ComboBox {
                objectName: "plazaRouteScope"
                Layout.fillWidth: true
                model: root.state.scopeOptions || []; textRole: "name"; valueRole: "id"
                currentIndex: indexOfValue(root.state.scopeId)
                enabled: !root.state.busy && !root.state.editorOpen && !root.state.galleryBusy
                onActivated: routePlazaService.selectScope(currentValue)
            }
        }
        RowLayout {
            visible: root.state.tab === "mine"
            Layout.fillWidth: true
            Label { text: "显示最近 100 个版本。草稿可继续投稿，审核中可撤回，已上架可修订或撤回。"; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#526785" }
            AppButton { objectName: "plazaMineRefresh"; text: "刷新"; enabled: !root.state.busy; onClicked: routePlazaService.loadMine() }
        }
        Rectangle {
            visible: root.state.tab !== "mine"
            Layout.fillWidth: true; Layout.fillHeight: true; radius: 12; color: "white"; border.color: "#d6e0ef"
            GridView {
                id: routes
                objectName: "plazaRouteList"
                readonly property int columns: Math.max(1, Math.floor(width / 232))
                readonly property bool shortViewport: height < 220
                anchors.fill: parent; anchors.margins: 8
                clip: true; model: routePlazaService.routeModel
                cellWidth: width / columns
                cellHeight: shortViewport ? 164 : root.compact ? 218 : 246
                cacheBuffer: cellHeight; boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}
                delegate: Item {
                    id: card
                    required property string key
                    required property string title
                    required property string description
                    required property string author
                    required property string scopeLabel
                    required property int mapCount
                    required property int downloadCount
                    required property string updatedDate
                    required property bool installed
                    required property bool updateAvailable
                    required property string thumbnailUrl
                    required property string coverUrl
                    readonly property bool hasPreview: publicCover.visible || thumbnail.visible
                    readonly property bool inViewport: root.visible && routes.visible
                        && y < routes.contentY + routes.height && y + height > routes.contentY
                    width: routes.cellWidth; height: routes.cellHeight
                    Rectangle {
                        anchors.fill: parent; anchors.rightMargin: 8; anchors.bottomMargin: 8
                        radius: 8; color: "#f3f6fc"; border.color: "#d6e0ef"
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: routes.shortViewport ? 8 : 10
                            spacing: routes.shortViewport ? 3 : 4
                            Button {
                                id: preview
                                objectName: "plazaPreview_" + card.key
                                Layout.fillWidth: true; Layout.fillHeight: true
                                Layout.minimumHeight: routes.shortViewport ? 38 : root.compact ? 60 : 86
                                padding: 5; hoverEnabled: true; focusPolicy: Qt.StrongFocus
                                enabled: !root.state.busy
                                Accessible.name: "预览 " + card.title
                                ToolTip.visible: hovered && enabled
                                ToolTip.delay: 500; ToolTip.text: "查看路线预览"
                                HoverHandler { cursorShape: preview.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
                                onClicked: routePlazaService.openPreview(card.key)
                                background: Rectangle {
                                    radius: 6; color: preview.down ? "#dce8ff" : preview.hovered ? "#e7effc" : "#edf1f7"
                                    border.width: preview.activeFocus ? 2 : 0; border.color: "#2f66eb"
                                }
                                contentItem: Item {
                                    Image {
                                        id: publicCover
                                        objectName: "plazaPublicCover_" + card.key
                                        anchors.fill: parent
                                        source: card.inViewport ? card.coverUrl : ""
                                        asynchronous: true; cache: false; fillMode: Image.PreserveAspectFit
                                        visible: card.inViewport && status === Image.Ready && sourceSize.width > 1 && sourceSize.height > 1
                                    }
                                    Image {
                                        id: thumbnail
                                        objectName: "plazaInlinePreview_" + card.key
                                        anchors.fill: parent
                                        source: card.inViewport && (!card.coverUrl || publicCover.status === Image.Error
                                            || (publicCover.status === Image.Ready && !publicCover.visible)) ? card.thumbnailUrl : ""
                                        asynchronous: true; cache: false; fillMode: Image.PreserveAspectFit
                                        visible: card.inViewport && status === Image.Ready && sourceSize.width > 1 && sourceSize.height > 1
                                    }
                                    ColumnLayout {
                                        anchors.centerIn: parent; width: parent.width - 8; spacing: 0
                                        visible: !card.hasPreview
                                        Label {
                                            Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
                                            text: card.mapCount + " 张地图"; color: "#526785"; font.pixelSize: 13
                                        }
                                        Label {
                                            visible: routes.shortViewport && !!card.description
                                            Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
                                            text: card.description; textFormat: Text.PlainText; color: "#354f75"
                                            font.pixelSize: 11; elide: Text.ElideRight
                                        }
                                    }
                                }
                            }
                            Label { text: card.title; textFormat: Text.PlainText; font.bold: true; font.pixelSize: 16; color: "#183a73"; Layout.fillWidth: true; elide: Text.ElideRight }
                            Label {
                                text: card.scopeLabel + (card.hasPreview ? " · " + card.mapCount + " 张图" : "") + " · 下载 " + card.downloadCount + " 次"
                                color: "#526785"; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight
                            }
                            Label {
                                text: "作者 " + card.author + (card.updatedDate ? " · " + card.updatedDate : "")
                                textFormat: Text.PlainText; color: "#526785"; font.pixelSize: 12
                                Layout.fillWidth: true; elide: Text.ElideRight
                            }
                            Label {
                                visible: !routes.shortViewport
                                text: card.description; textFormat: Text.PlainText; color: "#354f75"; font.pixelSize: 12
                                Layout.fillWidth: true; Layout.preferredHeight: 16; elide: Text.ElideRight
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 6
                                AppButton { objectName: "plazaDetails_" + card.key; text: "讨论"; Accessible.description: card.title; enabled: !root.state.busy || !!root.state.downloading; onClicked: routePlazaService.openDetails(card.key) }
                                Item { Layout.fillWidth: true }
                                AppButton {
                                    objectName: "plazaDownload_" + card.key
                                    text: root.state.downloading === card.key ? "下载中…" : card.installed ? "去使用 ›" : card.updateAvailable ? "更新路线" : "下载路线"
                                    Accessible.description: card.title
                                    primary: !card.installed; enabled: card.installed ? !root.state.galleryBusy : !root.state.busy
                                    onClicked: card.installed ? routePlazaService.openInstalledRoute(card.key) : routePlazaService.downloadRoute(card.key)
                                }
                            }
                        }
                    }
                }
                Label { anchors.centerIn: parent; visible: routes.count === 0; text: root.state.busy ? "正在读取…" : root.state.loaded ? "暂无可显示的路线" : "读取公开路线"; color: "#526785" }
            }
        }
        Rectangle {
            visible: root.state.tab === "mine"
            Layout.fillWidth: true; Layout.fillHeight: true; radius: 12; color: "white"; border.color: "#d6e0ef"
            ListView {
                id: mine; objectName: "plazaSubmissionList"
                anchors.fill: parent; anchors.margins: 12; clip: true; spacing: 10
                model: routePlazaService.submissionModel
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle {
                    id: submission
                    required property string key
                    required property string title
                    required property string description
                    required property int revision
                    required property string statusLabel
                    required property string reason
                    required property string updatedDate
                    required property bool editable
                    required property bool withdrawable
                    required property bool canSubmit
                    width: mine.width; implicitHeight: details.implicitHeight + 24
                    radius: 10; color: "#f3f7ff"
                    ColumnLayout {
                        id: details; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12; spacing: 6
                        Label { text: submission.title; color: "#142b4b"; font.bold: true; font.pixelSize: 16; Layout.fillWidth: true; elide: Text.ElideRight }
                        Label { text: submission.statusLabel + " · 第 " + submission.revision + " 版 · " + submission.updatedDate; color: "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        Label { visible: !!submission.description; text: submission.description; color: "#142b4b"; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Label { visible: !!submission.reason; text: "审核说明：" + submission.reason; color: "#b91c1c"; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton { objectName: "submissionEdit_" + submission.key; visible: submission.editable; text: "更新修订"; enabled: !root.state.busy; onClicked: routePlazaService.openSubmission(submission.key) }
                            AppButton { objectName: "submissionReview_" + submission.key; visible: submission.canSubmit; text: "提交审核"; primary: true; enabled: !root.state.busy; onClicked: routePlazaService.submitDraft(submission.key) }
                            AppButton { objectName: "submissionWithdraw_" + submission.key; visible: submission.withdrawable; text: "撤回"; enabled: !root.state.busy; onClicked: routePlazaService.withdrawSubmission(submission.key) }
                        }
                    }
                }
                Label { anchors.centerIn: parent; visible: mine.count === 0; text: !root.state.username ? "登录后查看我的投稿" : root.state.busy ? "正在读取…" : "还没有路线投稿"; color: "#526785" }
            }
        }
    }
}
