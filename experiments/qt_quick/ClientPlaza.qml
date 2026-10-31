import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: panel
    readonly property var state: plazaService.snapshot
    ColumnLayout {
        anchors.fill: parent
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Label { text: "地图广场"; font.pixelSize: 20; font.bold: true; color: "#17365e" }
            Item { Layout.fillWidth: true }
            AppButton { text: "打开自定义图库"; onClicked: plazaService.openGallery() }
            AppButton { visible: !panel.state.loggedIn; text: "登录账号"; onClicked: plazaService.requestLogin() }
        }
        Label {
            text: "玩家分享的噩梦路线。下载图库可以直接使用，也可以复制后编辑。"
            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#567096"
        }
        RowLayout {
            Layout.fillWidth: true
            TextField {
                id: query; objectName: "plazaQuery"; Layout.fillWidth: true
                placeholderText: "搜索路线名称或作者"; maximumLength: 80
                enabled: panel.state.canSearch
                onAccepted: plazaService.search(text)
            }
            AppButton { objectName: "plazaSearch"; text: "搜索"; primary: true; enabled: panel.state.canSearch; onClicked: plazaService.search(query.text) }
            AppButton { text: "刷新"; enabled: panel.state.canSearch; onClicked: plazaService.refresh() }
            AppButton { text: "取消"; visible: panel.state.busy; enabled: panel.state.canCancel; onClicked: plazaService.cancel() }
        }
        Label { text: panel.state.message; textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: panel.state.stage === "error" ? "#a52e3b" : "#567096" }
        ProgressBar { visible: panel.state.busy; Layout.fillWidth: true; indeterminate: true }
        ListView {
            id: routes; objectName: "plazaRoutes"; Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; spacing: 8; model: panel.state.rows
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                required property var modelData
                width: ListView.view.width; height: content.implicitHeight + 28
                radius: 12; color: "white"
                border.color: modelData.versionId === panel.state.selectedRouteId ? "#8db4fb" : "#e3ebf6"
                ColumnLayout {
                    id: content; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 14; spacing: 7
                    Label { text: modelData.title; textFormat: Text.PlainText; font.bold: true; font.pixelSize: 16; color: "#17365e"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: modelData.description; textFormat: Text.PlainText; visible: text.length > 0; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#567096" }
                    Label { text: modelData.author + " · " + modelData.mapCount + " 张地图 · " + modelData.downloadCount + " 次下载 · " + modelData.updatedDate; textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#567096"; font.pixelSize: 12 }
                    RowLayout {
                        Item { Layout.fillWidth: true }
                        AppButton { text: "选中"; enabled: !panel.state.busy; selected: modelData.versionId === panel.state.selectedRouteId; onClicked: plazaService.selectRoute(modelData.versionId) }
                        AppButton { text: panel.state.loggedIn ? "下载到图库" : "登录后下载"; primary: true; enabled: !panel.state.busy; onClicked: plazaService.downloadRoute(modelData.versionId) }
                    }
                }
            }
            Label {
                anchors.centerIn: parent; width: Math.max(0, parent.width - 32); horizontalAlignment: Text.AlignHCenter
                visible: !panel.state.busy && routes.count === 0; wrapMode: Text.WordWrap; color: "#567096"
                text: panel.state.loaded ? "暂无匹配的路线" : "点击刷新浏览路线，也可以输入关键词搜索。"
            }
        }
        Label { text: "下载内容为只读副本；在自定义图库中可选择使用、复制编辑或删除下载副本。"; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#567096"; font.pixelSize: 12 }
    }
}
