import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    property var scrollPositions: ({})
    Connections {
        target: backend
        function onViewChanging(key) { page.scrollPositions[key] = gallery.contentY }
        function onViewChanged(key) { Qt.callLater(function() { gallery.contentY = Math.min(page.scrollPositions[key] || 0, Math.max(0, gallery.contentHeight-gallery.height)) }) }
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 16
        RowLayout {
            Label { text: "地图资源"; font.pixelSize: 26; font.bold: true; color: "#142b4b" }
            Item { Layout.fillWidth: true }
            AppButton { text: "刷新列表"; enabled: !backend.busy && !resourceService.busy; onClicked: backend.refresh() }
            AppButton { text: "管理图包"; onClicked: clientCoordinator.navigate("resources") }
        }
        RowLayout {
            AppButton { text: "噩梦"; selected: backend.mode === "nightmare"; onClicked: backend.setMode("nightmare") }
            AppButton { text: "困难"; selected: backend.mode === "difficult"; onClicked: backend.setMode("difficult") }
            Item { Layout.fillWidth: true }
            TextField {
                id: search; objectName: "clientGallerySearch"
                Layout.preferredWidth: 230; placeholderText: "搜索地图"; selectByMouse: true
                onTextEdited: debounce.restart()
                Timer { id: debounce; interval: 140; onTriggered: backend.search(search.text) }
            }
        }
        RowLayout {
            Label { text: "浏览路线"; color: "#526785" }
            ComboBox {
                id: profiles; Layout.preferredWidth: 170
                model: backend.profiles; textRole: "name"; valueRole: "id"
                currentIndex: { for (var n=0;n<backend.profiles.length;n++) if(backend.profiles[n].id===backend.profileId)return n; return -1 }
                onActivated: backend.setProfile(currentValue)
            }
            Repeater {
                model: [{label:"全部",value:""},{label:"1楼",value:"1"},{label:"2楼",value:"2"},{label:"B1",value:"B1"}]
                AppButton { required property var modelData; implicitWidth: 48; text: modelData.label; selected: backend.floor===modelData.value; onClicked: backend.setFloor(modelData.value) }
            }
            Item { Layout.fillWidth: true }
            Label { text: backend.count + " 张图层"; color: "#526785" }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 20
            GridView {
                id: gallery; objectName: "clientResourceGrid"
                Layout.fillWidth: true; Layout.fillHeight: true
                cellWidth: width / Math.max(2, Math.floor(width/230)); cellHeight: 202
                clip: true; reuseItems: true; cacheBuffer: 202; model: resourceModel
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}
                delegate: Item {
                    id: card
                    required property string key
                    required property string title
                    required property string floor
                    required property string variant
                    required property string imageUrl
                    required property bool available
                    width: gallery.cellWidth; height: gallery.cellHeight
                    Rectangle {
                        anchors.fill: parent; anchors.rightMargin: 12; anchors.bottomMargin: 12
                        radius: 12; color: "white"
                        border.width: backend.selectedKey===card.key || activeFocus ? 2 : 0; border.color: "#2f66eb"
                        Rectangle { x: 8; y: 8; width: parent.width-16; height: 130; radius: 7; color: "#edf2fa"
                            Image { id: image; anchors.fill: parent; anchors.margins: 5; source: card.imageUrl; sourceSize: Qt.size(440,250); asynchronous: true; fillMode: Image.PreserveAspectFit }
                            Label { anchors.centerIn: parent; visible: image.status!==Image.Ready; text: !card.available ? "尚未安装" : image.status===Image.Error ? "图片无法读取" : "载入中…"; color: "#526785" }
                        }
                        Label { x: 14; y: 146; width: parent.width-28; text: card.title; elide: Text.ElideRight; color: "#142b4b"; font.bold: true }
                        Label { x: 14; y: 169; width: parent.width-28; text: card.floor+"楼 · "+card.variant; elide: Text.ElideRight; color: "#526785"; font.pixelSize: 11 }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: backend.select(card.key) }
                        activeFocusOnTab: true; Keys.onReturnPressed: backend.select(card.key)
                        Accessible.role: Accessible.Button; Accessible.name: card.title+" "+card.floor+"楼"
                        Accessible.onPressAction: backend.select(card.key)
                    }
                }
                Label { anchors.centerIn: parent; visible: backend.count===0; text: backend.busy ? "读取资源中…" : "没有符合条件的图层"; color: "#526785" }
            }
            Rectangle {
                Layout.preferredWidth: 245; Layout.fillHeight: true; radius: 12; color: "white"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 18; spacing: 12
                    Label { text: backend.selectedTitle; font.pixelSize: 17; font.bold: true; color: "#142b4b"; Layout.fillWidth: true; elide: Text.ElideRight }
                    Image { source: backend.selectedImage; sourceSize: Qt.size(620,620); asynchronous: true; fillMode: Image.PreserveAspectFit; Layout.fillWidth: true; Layout.fillHeight: true }
                    AppButton { text: "打开定点预览"; Layout.fillWidth: true; enabled: backend.selectedImage!==""; onClicked: clientCoordinator.showReference() }
                    Label { text: "浏览选择不改变识别或运行路线"; wrapMode: Text.WordWrap; Layout.fillWidth: true; color: "#526785"; font.pixelSize: 11 }
                }
            }
        }
        Label { text: backend.message; color: backend.hasError ? "#b8364a" : "#526785"; Layout.fillWidth: true; elide: Text.ElideRight }
    }
}
