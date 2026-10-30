import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    function enabled(action) { return session.snapshot.commands && session.snapshot.commands[action] && session.snapshot.commands[action].available && !session.busy && !resourceService.busy && (action!=="start_round" || clientCoordinator.boundWindow!=="") }
    ColumnLayout {
        anchors.fill: parent; spacing: 22
        Label { text: "星图"; font.pixelSize: 30; font.bold: true; color: "#142b4b" }
        Label { text: "Qt 迁移测试客户端"; color: "#526785" }
        Rectangle {
            Layout.fillWidth: true; implicitHeight: 145; radius: 14; color: "#e5efff"
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 24; spacing: 12
                Label { text: session.snapshot.liveEngineAvailable ? "实时引擎已就绪" : "当前使用本地资源与保存结果"; font.pixelSize: 20; font.bold: true; color: "#183a73" }
                Label { text: clientCoordinator.runtimeStatus; color: "#354f75"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                RowLayout {
                    AppButton { objectName: "clientStartRound"; text: "开始本局"; primary: true; enabled: page.enabled("start_round"); onClicked: clientCoordinator.startRound() }
                    AppButton { text: "结束本局"; enabled: page.enabled("end_round"); onClicked: session.dispatch("end_round") }
                    AppButton { text: "悬浮控件"; onClicked: windows.show("controls") }
                }
            }
        }
        RowLayout {
            spacing: 8
            Label { text: "工作模式"; color: "#526785" }
            AppButton { text: "噩梦"; selected: session.currentMode==="nightmare"; enabled: !session.busy && !resourceService.busy && !settingsService.busy; onClicked: clientCoordinator.setMode("nightmare") }
            AppButton { text: "困难"; selected: session.currentMode==="difficult"; enabled: !session.busy && !resourceService.busy && !settingsService.busy; onClicked: clientCoordinator.setMode("difficult") }
            Label { text: "与浏览筛选分开保存"; color: "#526785"; Layout.leftMargin: 12 }
        }
        RowLayout {
            Layout.fillWidth: true
            ComboBox {
                id: gameWindow; objectName: "gameWindowPicker"
                Layout.fillWidth: true; model: clientCoordinator.gameWindows
                textRole: "title"; valueRole: "hwnd"
                displayText: currentIndex < 0 ? "选择游戏窗口" : currentText
            }
            AppButton { text: "查找游戏"; onClicked: clientCoordinator.refreshGameWindows() }
            AppButton { text: "连接窗口"; enabled: gameWindow.currentIndex >= 0; onClicked: clientCoordinator.bindGameWindow(gameWindow.currentValue) }
            CheckBox { text: "启用快捷键"; checked: clientCoordinator.inputEnabled; onClicked: clientCoordinator.setInputEnabled(checked) }
        }
        Label { text: clientCoordinator.message; color: "#526785"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        Label { text: "继续工作"; font.pixelSize: 18; font.bold: true; color: "#142b4b"; Layout.topMargin: 12 }
        GridLayout {
            columns: width < 650 ? 1 : 2
            Layout.fillWidth: true; columnSpacing: 16; rowSpacing: 12
            AppButton { text: "浏览地图资源"; Layout.fillWidth: true; implicitHeight: 52; onClicked: clientCoordinator.navigate("gallery") }
            AppButton { text: "图包导入与管理"; Layout.fillWidth: true; implicitHeight: 52; onClicked: clientCoordinator.navigate("resources") }
            AppButton { text: "显示与快捷键设置"; Layout.fillWidth: true; implicitHeight: 52; onClicked: clientCoordinator.navigate("settings") }
            AppButton { text: "查看保存的识别结果"; Layout.fillWidth: true; implicitHeight: 52; onClicked: clientCoordinator.navigate("replay") }
        }
        Item { Layout.fillHeight: true }
        Label { text: "首次私测请先退出旧版，再连接游戏窗口并开始本局。此版本使用独立设置与图包。"; wrapMode: Text.WordWrap; Layout.fillWidth: true; color: "#526785" }
    }
}
