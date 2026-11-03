// In-app tutorial over the main window: chapter index, step player and FAQ.
// Only the current step image is bound; changing step or closing releases it.
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Popup {
    id: root
    objectName: "clientTutorial"
    parent: Overlay.overlay
    x: 0; y: 0; width: parent ? parent.width : 900; height: parent ? parent.height : 700
    padding: 0; modal: true; focus: true
    closePolicy: Popup.NoAutoClose
    property string chapterId: ""
    property int step: 0
    readonly property var chapters: tutorialService.chapters
    readonly property var chapter: {
        for (var i = 0; i < chapters.length; i++)
            if (chapters[i].id === chapterId) return chapters[i]
        return null
    }
    readonly property var current: chapter ? chapter.steps[Math.min(step, chapter.steps.length - 1)] : null
    readonly property bool last: !!chapter && step === chapter.steps.length - 1

    function openChapter(id) {
        if (id) { chapterId = id; step = 0 }
        if (!opened) open()
        keys.forceActiveFocus()
    }
    function showIndex() { chapterId = ""; step = 0; keys.forceActiveFocus() }
    function go(index) { if (chapter) step = Math.max(0, Math.min(chapter.steps.length - 1, index)) }
    function next() {
        if (!chapter) return
        if (!last) { go(step + 1); return }
        tutorialService.markDone(chapterId)
        showIndex()
    }
    function back() { if (chapter) showIndex(); else close() }
    function openPage() {
        var page = chapter ? chapter.page : ""
        close()
        if (page) clientCoordinator.navigate(page)
    }
    Connections { target: tutorialService; function onOpenRequested(id) { root.openChapter(id) } }

    component Note: Label {
        font.family: "Microsoft YaHei UI"; font.pixelSize: 13; color: "#526785"
        wrapMode: Text.WordWrap; Layout.fillWidth: true
    }
    background: Rectangle { color: "#f3f5fa" }
    contentItem: FocusScope {
        id: keys
        objectName: "tutorialKeys"
        focus: true
        Keys.onEscapePressed: root.back()
        Keys.onLeftPressed: root.go(root.step - 1)
        Keys.onRightPressed: root.go(root.step + 1)
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 28; spacing: 14
            RowLayout {
                Layout.fillWidth: true; spacing: 12
                AppButton { objectName: "tutorialBack"; text: root.chapter ? "‹ 目录" : "关闭"; implicitHeight: 34; onClicked: root.back() }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 2
                    Label { objectName: "tutorialTitle"; text: root.chapter ? root.chapter.title : "使用教程"; color: "#142b4b"; font.pixelSize: 24; font.bold: true }
                    Note { text: root.chapter ? root.chapter.summary : root.chapters.length ? "跟着图一步步上手，看完的章节会标上“已看完”。" : "教程数据暂不可用，请到帮助页查看操作说明。" }
                }
                Label { visible: !!root.chapter; text: root.chapter ? (root.step + 1) + " / " + root.chapter.steps.length : ""; color: "#8a95a8"; font.pixelSize: 13 }
            }
            StackLayout {
                Layout.fillWidth: true; Layout.fillHeight: true
                currentIndex: root.chapter ? 1 : 0
                ScrollView {
                    id: indexScroll
                    objectName: "tutorialIndex"
                    clip: true; contentWidth: availableWidth
                    ColumnLayout {
                        width: indexScroll.availableWidth; spacing: 14
                        Rectangle {
                            Layout.fillWidth: true; implicitHeight: hero.implicitHeight + 44; radius: 16; color: "#13214a"
                            ColumnLayout {
                                id: hero; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 22; spacing: 10
                                Label { text: "一分钟看懂星图怎么用"; color: "#f1f5fd"; font.pixelSize: 20; font.bold: true }
                                Label { text: "不知道从哪开始？先看“快速上手”，再挑你要用的功能。"; color: "#aebddf"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                Row {
                                    spacing: 8
                                    Repeater {
                                        model: ["1  选路线", "2  开始本局", "3  按地图键开图"]
                                        Rectangle {
                                            required property string modelData
                                            radius: 10; color: "#22356b"; width: chip.implicitWidth + 20; height: chip.implicitHeight + 10
                                            Label { id: chip; anchors.centerIn: parent; text: parent.modelData; color: "#f1f5fd"; font.bold: true; font.pixelSize: 12 }
                                        }
                                    }
                                }
                                AppButton { objectName: "tutorialStart"; text: "开始学习"; primary: true; enabled: root.chapters.length > 0; onClicked: root.openChapter("start") }
                            }
                        }
                        Label { text: "按功能学习"; color: "#142b4b"; font.pixelSize: 17; font.bold: true; Layout.topMargin: 4 }
                        GridLayout {
                            Layout.fillWidth: true; columns: indexScroll.availableWidth >= 560 ? 2 : 1; columnSpacing: 12; rowSpacing: 12
                            Repeater {
                                model: root.chapters
                                delegate: ToolButton {
                                    id: chapterCard
                                    required property var modelData
                                    objectName: "tutorialChapter-" + modelData.id
                                    Layout.fillWidth: true; implicitHeight: 76
                                    Accessible.name: modelData.title
                                    onClicked: root.openChapter(modelData.id)
                                    contentItem: RowLayout {
                                        spacing: 12
                                        Rectangle {
                                            Layout.preferredWidth: 36; Layout.preferredHeight: 36; radius: 10; color: "#e9effd"
                                            Image { anchors.centerIn: parent; source: "icons/" + chapterCard.modelData.icon + ".png"; sourceSize: Qt.size(40, 40); width: 20; height: 20 }
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true; spacing: 3
                                            Label { text: chapterCard.modelData.title; color: "#17365e"; font.bold: true; font.pixelSize: 14 }
                                            Label { text: chapterCard.modelData.summary + " · " + chapterCard.modelData.steps.length + " 步"; color: "#8a95a8"; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                        }
                                        Label { visible: chapterCard.modelData.done; text: "已看完"; color: "#17775a"; font.bold: true; font.pixelSize: 11 }
                                        Label { text: "›"; color: "#8a95a8"; font.pixelSize: 18 }
                                    }
                                    background: Rectangle { radius: 12; color: parent.hovered ? "#f7f9fe" : "white"; border.color: parent.activeFocus ? "#2f66eb" : "#e3e8f1"; border.width: parent.activeFocus ? 2 : 1 }
                                }
                            }
                        }
                        Label { text: "常见问题"; color: "#142b4b"; font.pixelSize: 17; font.bold: true; Layout.topMargin: 4 }
                        Rectangle {
                            Layout.fillWidth: true; implicitHeight: faqColumn.implicitHeight + 16; radius: 12; color: "white"; border.color: "#e3e8f1"
                            ColumnLayout {
                                id: faqColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8; spacing: 0
                                Repeater {
                                    model: tutorialService.faq
                                    delegate: ColumnLayout {
                                        id: faqItem
                                        required property var modelData
                                        required property int index
                                        property bool expanded: false
                                        Layout.fillWidth: true; spacing: 4
                                        Rectangle { visible: faqItem.index > 0; Layout.fillWidth: true; height: 1; color: "#e3e8f1" }
                                        ToolButton {
                                            objectName: "tutorialFaq-" + faqItem.index
                                            Layout.fillWidth: true; implicitHeight: 44
                                            onClicked: faqItem.expanded = !faqItem.expanded
                                            contentItem: RowLayout {
                                                Label { text: faqItem.modelData.q; color: "#17365e"; font.bold: true; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                                Label { text: faqItem.expanded ? "收起" : "展开"; color: "#1f50cc"; font.pixelSize: 12 }
                                            }
                                            background: Rectangle { radius: 8; color: parent.hovered ? "#f1f5fc" : "transparent" }
                                        }
                                        Repeater {
                                            model: faqItem.expanded ? faqItem.modelData.a : []
                                            delegate: Note { required property string modelData; text: "·  " + modelData; Layout.leftMargin: 12; Layout.bottomMargin: 4 }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                Rectangle {
                    objectName: "tutorialPlayer"
                    radius: 16; color: "white"; border.color: "#e3e8f1"
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 22; spacing: 10
                        Row {
                            Layout.fillWidth: true; spacing: 6
                            Repeater {
                                model: root.chapter ? root.chapter.steps.length : 0
                                Rectangle {
                                    required property int index
                                    width: (parent.width - 6 * ((root.chapter ? root.chapter.steps.length : 1) - 1)) / (root.chapter ? root.chapter.steps.length : 1)
                                    height: 4; radius: 2; color: index <= root.step ? "#2f66eb" : "#eef1f7"
                                }
                            }
                        }
                        Item {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            Image {
                                id: stepImage
                                objectName: "tutorialImage"
                                anchors.fill: parent
                                source: root.opened && root.current ? root.current.imageUrl : ""
                                asynchronous: true; cache: false; fillMode: Image.PreserveAspectFit
                                sourceSize.width: 1120; smooth: true; mipmap: true
                            }
                            Label {
                                objectName: "tutorialImageFallback"
                                anchors.centerIn: parent; width: parent.width - 24; wrapMode: Text.WordWrap
                                horizontalAlignment: Text.AlignHCenter; visible: stepImage.status === Image.Error
                                text: "这一步的配图暂不可用，按下方文字操作即可。"; color: "#526785"
                            }
                        }
                        RowLayout {
                            spacing: 10
                            Rectangle {
                                implicitWidth: badge.implicitWidth + 16; implicitHeight: badge.implicitHeight + 6; radius: 4; color: "#e9effd"
                                Label { id: badge; anchors.centerIn: parent; text: "第 " + (root.step + 1) + " 步"; color: "#1f50cc"; font.bold: true; font.pixelSize: 12 }
                            }
                            Label { objectName: "tutorialSketch"; visible: !!root.current && root.current.sketch; text: "示意图 · 以实际画面为准"; color: "#8a95a8"; font.pixelSize: 12 }
                        }
                        Label { objectName: "tutorialStepTitle"; text: root.current ? root.current.title : ""; color: "#142b4b"; font.pixelSize: 19; font.bold: true; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Label { text: root.current ? root.current.text : ""; color: "#354f75"; font.pixelSize: 14; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Label {
                            objectName: "tutorialBindings"
                            visible: !!root.current && !!root.current.bindings
                            text: root.current && root.current.bindings ? "你当前的绑定：" + root.current.bindings : ""
                            color: "#2f66eb"; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true
                        }
                        Repeater {
                            model: root.current ? root.current.tips : []
                            delegate: Note { required property string modelData; text: "·  " + modelData }
                        }
                        RowLayout {
                            Layout.fillWidth: true; Layout.topMargin: 6; spacing: 8
                            Label { text: "← → 键也能切换"; color: "#8a95a8"; font.pixelSize: 12; Layout.fillWidth: true }
                            AppButton { objectName: "tutorialPage"; visible: root.last && !!root.chapter && !!root.chapter.pageAction; text: root.chapter ? root.chapter.pageAction + "  ›" : ""; onClicked: root.openPage() }
                            AppButton { objectName: "tutorialPrev"; text: "上一步"; enabled: root.step > 0; onClicked: root.go(root.step - 1) }
                            AppButton { objectName: "tutorialNext"; text: root.last ? "看完了" : "下一步"; primary: true; onClicked: root.next() }
                        }
                    }
                }
            }
        }
    }
}
