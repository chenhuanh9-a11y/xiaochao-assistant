pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "routeDiscussion"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(640, parent ? parent.width - 32 : 640)
    height: Math.min(680, parent ? parent.height - 32 : 680)
    modal: true; focus: true
    readonly property var state: routePlazaService.snapshot
    readonly property var detail: state.detail || ({})
    property string reportTarget: ""
    property bool reporting: false
    visible: !!state.detailOpen
    title: detail.title || "路线详情"
    standardButtons: Dialog.Close
    onClosed: routePlazaService.closeDetails()
    onOpened: { comment.text = ""; reporting = false }
    background: Rectangle { color: "white"; radius: 12; border.color: "#d6e0ef" }
    contentItem: ColumnLayout {
        spacing: 10
        Label { text: "作者 " + (root.detail.author || ""); color: "#526785"; textFormat: Text.PlainText }
        Label { text: root.detail.description || ""; textFormat: Text.PlainText; color: "#354f75"; Layout.fillWidth: true; wrapMode: Text.Wrap }
        RowLayout {
            Layout.fillWidth: true
            AppButton { objectName: "routeLike"; text: (root.detail.liked ? "取消点赞 " : "点赞 ") + (root.detail.likeCount || 0)
                enabled: !root.state.busy; onClicked: routePlazaService.likeRoute(!root.detail.liked) }
            Label { text: (root.detail.commentCount || 0) + " 条评论"; color: "#526785"; Layout.fillWidth: true }
            AppButton { text: "举报作品"; enabled: !root.state.busy; onClicked: { root.reportTarget = ""; root.reporting = true } }
        }
        ListView {
            id: comments
            objectName: "routeComments"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; spacing: 8
            model: root.detail.comments || []
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                id: row
                required property var modelData
                width: comments.width; implicitHeight: body.implicitHeight + 20
                color: "#f3f7ff"; radius: 8
                ColumnLayout {
                    id: body
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                    Label { text: row.modelData.author + (row.modelData.mine ? " · 我的评论" : ""); color: "#17365e"; textFormat: Text.PlainText }
                    Label { objectName: "routeCommentText_" + row.modelData.id; text: row.modelData.body; color: "#354f75"; Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText }
                    RowLayout {
                        AppButton { text: (row.modelData.liked ? "取消赞 " : "赞 ") + row.modelData.likeCount; enabled: !root.state.busy
                            onClicked: routePlazaService.likeComment(row.modelData.id, !row.modelData.liked) }
                        AppButton { visible: row.modelData.mine; text: "编辑"; enabled: !root.state.busy
                            onClicked: { comment.text = row.modelData.body; comment.forceActiveFocus() } }
                        AppButton { visible: row.modelData.mine; text: "删除"; enabled: !root.state.busy; onClicked: routePlazaService.deleteComment() }
                        Item { Layout.fillWidth: true }
                        AppButton { text: "举报"; enabled: !root.state.busy; onClicked: { root.reportTarget = row.modelData.id; root.reporting = true } }
                    }
                }
            }
            Label { anchors.centerIn: parent; visible: !comments.count; text: root.state.busy ? "正在读取讨论…" : "还没有评论"; color: "#526785" }
        }
        AppButton { visible: root.detail.nextOffset !== null && root.detail.nextOffset !== undefined; text: "更多评论"; enabled: !root.state.busy; onClicked: routePlazaService.moreComments() }
        TextArea { id: comment; objectName: "routeCommentInput"; Layout.fillWidth: true; Layout.preferredHeight: 72
            placeholderText: root.state.username ? "写评论（2–800 字，每人可更新一条）" : "登录后可评论"; selectByMouse: true; wrapMode: TextEdit.Wrap }
        AppButton { objectName: "routeCommentSubmit"; text: root.detail.myCommentId ? "更新我的评论" : "发布评论"; primary: true
            enabled: !root.state.busy && comment.text.trim().length >= 2 && comment.text.length <= 800
            onClicked: routePlazaService.saveComment(comment.text) }
        ColumnLayout {
            visible: root.reporting; Layout.fillWidth: true
            TextField { id: reason; Layout.fillWidth: true; maximumLength: 32; placeholderText: "举报原因" }
            TextField { id: explanation; Layout.fillWidth: true; maximumLength: 1000; placeholderText: "补充说明（选填）" }
            RowLayout {
                AppButton { text: "提交举报"; enabled: !root.state.busy && reason.text.trim().length > 0
                    onClicked: { routePlazaService.reportContent(root.reportTarget, reason.text, explanation.text); root.reporting = false } }
                AppButton { text: "取消"; onClicked: root.reporting = false }
            }
        }
        Label { text: root.state.message || ""; textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.Wrap; color: root.state.error ? "#b91c1c" : "#526785" }
    }
}
