import QtQuick
import QtQuick.Controls.Basic

Loader {
    id: loader
    property bool selected: false
    property bool visited: false
    active: selected || visited
    asynchronous: true
    onSelectedChanged: if (selected) visited = true
    Component.onCompleted: if (selected) visited = true
    // First visit creates the page once. Leaving it never destroys drafts,
    // selection, scroll or the connections owned by that page.
    ProgressBar {
        anchors.centerIn: parent
        width: Math.min(320, Math.max(100, parent.width - 48))
        visible: loader.selected && loader.status === Loader.Loading
        indeterminate: visible
        Accessible.name: "正在加载页面"
    }
}
