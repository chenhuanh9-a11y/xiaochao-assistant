import QtQuick

Item {
    id: view
    property var record: ({})
    property real routeAlpha: 0.55
    readonly property bool valid: !!record.frameUrl && !!record.routeUrl
    readonly property var matrix: record.matrix || [[1,0,0],[0,1,0],[0,0,1]]
    readonly property bool imagesReady: frame.status === Image.Ready && route.status === Image.Ready
    readonly property bool imageError: frame.status === Image.Error || route.status === Image.Error
    clip: true
    Item {
        objectName: "replayViewport"
        width: view.record.clientWidth || 1
        height: view.record.clientHeight || 1
        scale: Math.min(view.width / width, view.height / height)
        transformOrigin: Item.TopLeft
        x: (view.width - width * scale) / 2
        y: (view.height - height * scale) / 2
        clip: true
        Image { id: frame; width: parent.width; height: parent.height; source: view.record.frameUrl || ""; asynchronous: true; fillMode: Image.Stretch }
        Image {
            id: route
            objectName: "recordedRouteImage"
            width: view.record.routeWidth || 1
            height: view.record.routeHeight || 1
            source: view.record.routeUrl || ""
            asynchronous: true
            opacity: view.routeAlpha
            fillMode: Image.Stretch
            transform: Matrix4x4 {
                matrix: Qt.matrix4x4(view.matrix[0][0], view.matrix[0][1], 0, view.matrix[0][2],
                                     view.matrix[1][0], view.matrix[1][1], 0, view.matrix[1][2],
                                     0, 0, 1, 0,
                                     view.matrix[2][0], view.matrix[2][1], 0, view.matrix[2][2])
            }
        }
    }
}
