pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root
    objectName: "routePlazaPage"
    property var state: ({busy:false, username:"", operation:"", message:"", error:false, loaded:false, downloading:""})
    function sync() { if (visible) state = routePlazaService.snapshot }
    Connections { target: routePlazaService; function onChanged() { root.sync() } }
    onVisibleChanged: { sync(); routePlazaService.setActive(visible) }
    Component.onCompleted: { sync(); routePlazaService.setActive(visible) }
    ClientRouteSubmission {}
    ColumnLayout {
        anchors.fill: parent; spacing: 12
        Label { text: "路线广场"; color: "#142b4b"; font.pixelSize: 24; font.bold: true }
        Label { text: "分享和下载玩家的噩梦路线。困难模式暂不支持投稿。"; color: "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RowLayout {
            Layout.fillWidth: true
            RoutePointNumbersSwitch {}
            Label { text: "仅标起终点和手选目标，每层从 1 开始"; color: "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        }
        Label { visible: settingsService && (settingsService.loadFailed || settingsService.message.indexOf("失败") >= 0); text: settingsService ? settingsService.message : ""; color: "#b91c1c"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RowLayout {
            Layout.fillWidth: true
            Label { objectName: "plazaAccountStatus"; text: root.state.username ? "已登录：" + root.state.username : "未登录 · 登录后可下载和投稿"; color: "#354f75"; Layout.fillWidth: true; elide: Text.ElideRight }
            AppButton { objectName: "plazaLogin"; text: "账号登录"; primary: true; visible: !root.state.username; enabled: !root.state.busy; onClicked: routePlazaService.openAccount() }
            AppButton { objectName: "plazaRecovery"; text: "领取恢复码"; primary: true; visible: !!root.state.recoveryPending; enabled: !root.state.busy; onClicked: routePlazaService.openAccount() }
            AppButton { objectName: "plazaLogout"; text: "退出登录"; visible: !!root.state.username; enabled: !root.state.busy; onClicked: routePlazaService.logout() }
            AppButton { text: "自定义图库"; onClicked: clientCoordinator.navigate("custom") }
        }
        RowLayout {
            Layout.fillWidth: true
            AppButton { objectName: "plazaSquare"; text: "广场"; selected: root.state.tab !== "mine"; enabled: !root.state.busy; onClicked: routePlazaService.selectTab("square") }
            AppButton { objectName: "plazaMine"; text: "我的投稿"; selected: root.state.tab === "mine"; enabled: !root.state.busy; onClicked: routePlazaService.selectTab("mine") }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "plazaSubmit"; text: "投稿"; primary: true; enabled: !root.state.busy; onClicked: routePlazaService.openSubmission("") }
        }
        RowLayout {
            visible: root.state.tab !== "mine"
            Layout.fillWidth: true
            TextField { id: search; objectName: "plazaSearch"; Layout.fillWidth: true; placeholderText: "搜索路线"; selectByMouse: true; maximumLength: 80; onAccepted: routePlazaService.search(text) }
            AppButton { objectName: "plazaSearchButton"; text: "搜索"; enabled: !root.state.busy; onClicked: routePlazaService.search(search.text) }
            AppButton { objectName: "plazaRefresh"; text: "刷新"; enabled: !root.state.busy; onClicked: routePlazaService.search(search.text) }
            AppButton { objectName: "plazaCancelDownload"; visible: root.state.operation === "download"; text: "取消下载"; onClicked: routePlazaService.cancelDownload() }
        }
        Label { objectName: "plazaStatus"; text: root.state.message; color: root.state.error ? "#b91c1c" : "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RowLayout {
            visible: root.state.tab === "mine"
            Layout.fillWidth: true
            Label { text: "显示最近 100 个版本。草稿可继续投稿，审核中可撤回，已上架可修订或撤回。"; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#526785" }
            AppButton { objectName: "plazaMineRefresh"; text: "刷新"; enabled: !root.state.busy; onClicked: routePlazaService.loadMine() }
        }
        Rectangle {
            visible: root.state.tab !== "mine"
            Layout.fillWidth: true; Layout.fillHeight: true; radius: 12; color: "white"; border.color: "#d6e0ef"
            ListView {
                id: routes
                objectName: "plazaRouteList"
                anchors.fill: parent; anchors.margins: 12
                clip: true; spacing: 10; model: routePlazaService.routeModel
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle {
                    id: card
                    required property string key
                    required property string title
                    required property string description
                    required property string author
                    required property int mapCount
                    required property int downloadCount
                    required property string updatedDate
                    required property bool installed
                    required property bool updateAvailable
                    width: routes.width; implicitHeight: contents.implicitHeight + 24; radius: 10; color: "#f3f7ff"
                    ColumnLayout {
                        id: contents; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12; spacing: 6
                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: card.title; font.bold: true; font.pixelSize: 16; color: "#17365e"; Layout.fillWidth: true; elide: Text.ElideRight }
                            AppButton {
                                objectName: "plazaDownload_" + card.key
                                text: root.state.downloading === card.key ? "下载中…" : card.installed ? "去使用 ›" : card.updateAvailable ? "更新路线" : "下载路线"
                                primary: !card.installed; enabled: !root.state.busy
                                onClicked: card.installed ? clientCoordinator.navigate("custom") : routePlazaService.downloadRoute(card.key)
                            }
                        }
                        Label { text: "作者 " + card.author + " · " + card.mapCount + " 张图 · 下载 " + card.downloadCount + " 次" + (card.updatedDate ? " · 更新于 " + card.updatedDate : "") + (card.installed ? " · 已下载" : ""); color: "#526785"; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        Label { visible: !!card.description; text: card.description; color: "#354f75"; Layout.fillWidth: true; wrapMode: Text.Wrap }
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
                            AppButton { objectName: "submissionReview_" + submission.key; visible: submission.canSubmit; text: "提交已有包审核"; primary: true; enabled: !root.state.busy; onClicked: routePlazaService.submitDraft(submission.key) }
                            AppButton { objectName: "submissionWithdraw_" + submission.key; visible: submission.withdrawable; text: "撤回"; enabled: !root.state.busy; onClicked: routePlazaService.withdrawSubmission(submission.key) }
                        }
                    }
                }
                Label { anchors.centerIn: parent; visible: mine.count === 0; text: !root.state.username ? "登录后查看我的投稿" : root.state.busy ? "正在读取…" : "还没有路线投稿"; color: "#526785" }
            }
        }
    }
}
