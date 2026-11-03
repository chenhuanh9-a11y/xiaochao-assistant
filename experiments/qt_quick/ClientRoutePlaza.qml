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
    ColumnLayout {
        anchors.fill: parent; spacing: 12
        Label { text: "路线广场"; color: "#142b4b"; font.pixelSize: 24; font.bold: true }
        Label { text: "下载玩家分享的噩梦路线，到「自定义图库」中选用或复制编辑。"; color: "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RowLayout {
            Layout.fillWidth: true
            Label { objectName: "plazaAccountStatus"; text: root.state.username ? "已登录：" + root.state.username : "未登录 · 登录后可下载"; color: "#354f75"; Layout.fillWidth: true; elide: Text.ElideRight }
            AppButton { objectName: "plazaLogin"; text: "账号登录"; primary: true; visible: !root.state.username; enabled: !root.state.busy; onClicked: routePlazaService.openAccount() }
            AppButton { objectName: "plazaRecovery"; text: "领取恢复码"; primary: true; visible: !!root.state.recoveryPending; enabled: !root.state.busy; onClicked: routePlazaService.openAccount() }
            AppButton { objectName: "plazaLogout"; text: "退出登录"; visible: !!root.state.username; enabled: !root.state.busy; onClicked: routePlazaService.logout() }
            AppButton { text: "自定义图库"; onClicked: clientCoordinator.navigate("custom") }
        }
        RowLayout {
            Layout.fillWidth: true
            TextField { id: search; objectName: "plazaSearch"; Layout.fillWidth: true; placeholderText: "搜索路线"; selectByMouse: true; maximumLength: 80; onAccepted: routePlazaService.search(text) }
            AppButton { objectName: "plazaSearchButton"; text: "搜索"; enabled: !root.state.busy; onClicked: routePlazaService.search(search.text) }
            AppButton { objectName: "plazaRefresh"; text: "刷新"; enabled: !root.state.busy; onClicked: routePlazaService.search(search.text) }
            AppButton { objectName: "plazaCancelDownload"; visible: root.state.operation === "download"; text: "取消下载"; onClicked: routePlazaService.cancelDownload() }
        }
        Label { objectName: "plazaStatus"; text: root.state.message; color: root.state.error ? "#b91c1c" : "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        Rectangle {
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
    }
}
