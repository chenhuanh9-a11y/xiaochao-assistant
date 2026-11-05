import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "routeSubmissionDialog"
    readonly property var state: routePlazaService.snapshot
    property int generation: -1
    property var choices: []
    property string choicesKey: ""
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(540, parent ? parent.width - 32 : 540)
    height: Math.min(580, parent ? parent.height - 24 : 580)
    modal: true; focus: true
    closePolicy: Popup.CloseOnEscape
    title: state.editorVersion ? "更新修订" : "投稿噩梦路线"
    background: Rectangle { color: "#ffffff"; radius: 12; border.color: "#d6e0ef" }
    onClosed: routePlazaService.closeSubmission()
    Connections {
        target: routePlazaService
        function onChanged() {
            if (root.state.editorOpen) {
                var key = JSON.stringify(root.state.submissionChoices || [])
                if (key !== root.choicesKey) {
                    var previous = gallery.currentValue
                    root.choicesKey = key
                    root.choices = root.state.submissionChoices || []
                    var index = gallery.indexOfValue(previous)
                    if (index >= 0) gallery.currentIndex = index
                }
            }
            if (root.state.editorOpen && !root.visible) {
                if (root.generation !== root.state.editorGeneration) {
                    root.generation = root.state.editorGeneration
                    titleInput.text = root.state.editorTitle
                    description.text = root.state.editorDescription
                }
                root.open()
            } else if (!root.state.editorOpen && root.visible) root.close()
        }
    }
    contentItem: ScrollView {
        clip: true; contentWidth: availableWidth
        ColumnLayout {
            width: parent.width; spacing: 10
            Label { text: "选择自己的已保存图库。审核通过后在路线广场公开；仅支持噩梦路线，困难模式暂不支持投稿。"; color: "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label { text: "作品名称（2–48 字）"; color: "#142b4b" }
            TextField { id: titleInput; objectName: "submissionTitle"; Layout.fillWidth: true; selectByMouse: true; maximumLength: 48; enabled: !root.state.busy && !root.state.submissionLocked; Accessible.name: "投稿作品名称" }
            Label { text: "作品说明（最多 500 字）"; color: "#142b4b" }
            TextArea {
                id: description; objectName: "submissionDescription"
                Layout.fillWidth: true; Layout.preferredHeight: 96; wrapMode: TextEdit.Wrap
                selectByMouse: true; enabled: !root.state.busy && !root.state.submissionLocked
                Accessible.name: "投稿作品说明"
                onTextChanged: { if (text.length > 500) text = text.slice(0, 500) }
                background: Rectangle { color: "white"; border.color: "#d6e0ef"; radius: 6 }
            }
            Label { text: "本机个人图库"; color: "#142b4b" }
            ComboBox {
                id: gallery; objectName: "submissionGallery"; Layout.fillWidth: true
                model: root.choices; textRole: "name"; valueRole: "id"
                enabled: !root.state.busy && !root.state.submissionLocked
                Accessible.name: "选择要投稿的图库"
            }
            Label { visible: gallery.count === 0; text: "还没有个人图库，请到自定义图库复制或新建并保存路线。"; color: "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            ProgressBar {
                objectName: "submissionProgress"; Layout.fillWidth: true
                visible: root.state.operation === "submit"
                from: 0; to: root.state.progress.total || 1; value: root.state.progress.sent || 0
                indeterminate: !root.state.progress.total
            }
            Label { objectName: "submissionStatus"; text: root.state.message; color: root.state.error ? "#b91c1c" : "#526785"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            RowLayout {
                Layout.fillWidth: true
                AppButton { objectName: "submissionClose"; text: "关闭"; onClicked: root.close() }
                Item { Layout.fillWidth: true }
                AppButton { objectName: "submissionCancel"; visible: root.state.operation === "submit"; text: "停止投稿"; onClicked: routePlazaService.cancelSubmission() }
                AppButton {
                    objectName: "submissionSubmit"; text: root.state.submissionLocked ? "重试本次投稿" : "打包并提交审核"
                    primary: true; enabled: !root.state.busy && gallery.count > 0
                    onClicked: routePlazaService.submitGallery(gallery.currentValue || "", titleInput.text, description.text)
                }
            }
        }
    }
}
