import QtQuick

Window {
    id: aligned
    objectName: "clientAlignedWindow"
    title: "星图 · 当前帧贴合层"
    visible: false
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
    readonly property var alignedData: clientCoordinator.alignedData
    readonly property var matrix: alignedData.matrix || []
    readonly property bool valid: validPayload()
    x: alignedData.originX || 0
    y: alignedData.originY || 0
    width: valid ? Math.ceil(alignedData.clientWidth / alignedData.deviceScale) : 1
    height: valid ? Math.ceil(alignedData.clientHeight / alignedData.deviceScale) : 1
    function validPayload() {
        if (alignedData.liveAuthorization !== true || !alignedData.routeUrl || !(alignedData.routeWidth > 0) || !(alignedData.routeHeight > 0)
            || !(alignedData.clientWidth > 0) || !(alignedData.clientHeight > 0)
            || !(alignedData.deviceScale > 0) || matrix.length !== 3) return false
        var dimensions = [alignedData.routeWidth, alignedData.routeHeight, alignedData.clientWidth,
                          alignedData.clientHeight, alignedData.originX, alignedData.originY, alignedData.deviceScale]
        for (var index = 0; index < dimensions.length; ++index)
            if (typeof dimensions[index] !== "number" || !isFinite(dimensions[index])) return false
        for (var row = 0; row < 3; ++row) {
            if (!matrix[row] || matrix[row].length !== 3) return false
            for (var column = 0; column < 3; ++column)
                if (typeof matrix[row][column] !== "number" || !isFinite(matrix[row][column])) return false
        }
        return true
    }
    Item {
        anchors.fill: parent
        clip: true
        Item {
            // Matrix and route dimensions retain the capture's physical-pixel
            // coordinates. Only this viewport converts them to Qt logical pixels.
            width: aligned.valid ? aligned.alignedData.clientWidth : 1
            height: aligned.valid ? aligned.alignedData.clientHeight : 1
            scale: aligned.valid ? 1 / aligned.alignedData.deviceScale : 1
            transformOrigin: Item.TopLeft
            clip: true
            Image {
                objectName: "clientAlignedRouteImage"
                visible: aligned.valid
                source: aligned.valid ? aligned.alignedData.routeUrl : ""
                width: aligned.valid ? aligned.alignedData.routeWidth : 1
                height: aligned.valid ? aligned.alignedData.routeHeight : 1
                asynchronous: true
                fillMode: Image.Stretch
                opacity: settingsService.values.sheetOpacity
                transform: Matrix4x4 {
                    matrix: aligned.valid ? Qt.matrix4x4(
                        aligned.matrix[0][0], aligned.matrix[0][1], 0, aligned.matrix[0][2],
                        aligned.matrix[1][0], aligned.matrix[1][1], 0, aligned.matrix[1][2],
                        0, 0, 1, 0,
                        aligned.matrix[2][0], aligned.matrix[2][1], 0, aligned.matrix[2][2])
                        : Qt.matrix4x4(1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1)
                }
            }
        }
    }
}
