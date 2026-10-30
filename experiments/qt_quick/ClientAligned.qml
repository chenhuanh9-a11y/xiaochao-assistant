import QtQuick

Window {
    id: aligned
    objectName: "clientAlignedWindow"
    title: "星图 · 当前帧贴合层"
    visible: false
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
    readonly property var alignedData: clientCoordinator.alignedData
    readonly property var pendingData: clientCoordinator && clientCoordinator.pendingAlignedData !== undefined ? clientCoordinator.pendingAlignedData : ({})
    readonly property var matrix: alignedData.matrix || []
    readonly property bool valid: validPayload()
    function reportSurface() {
        if (!clientCoordinator || typeof clientCoordinator.reportPresentation !== "function") return
        var labels = ["empty", "ready", "loading", "error"]
        var matches = routeImage.source.toString() === String(alignedData.routeUrl || "")
        var corners = [routeImage.mapToItem(viewport, 0, 0), routeImage.mapToItem(viewport, routeImage.width, 0),
                       routeImage.mapToItem(viewport, 0, routeImage.height), routeImage.mapToItem(viewport, routeImage.width, routeImage.height)]
        var xs = corners.map(function(p) { return p.x }), ys = corners.map(function(p) { return p.y })
        clientCoordinator.reportPresentation("aligned", {token: String(alignedData.token || ""), visible: visible, validPayload: valid,
            imageStatus: labels[routeImage.status] || "unknown", materialReady: valid && matches && routeImage.status === Image.Ready,
            sourceMatchesPayload: matches, effectiveOpacity: opacity * routeImage.opacity,
            geometry: [x, y, width, height], mappedBounds: [Math.min.apply(null,xs), Math.min.apply(null,ys), Math.max.apply(null,xs), Math.max.apply(null,ys)]})
    }
    function scheduleReport() { Qt.callLater(reportSurface) }
    function reportPendingMaterial() {
        if (!clientCoordinator || typeof clientCoordinator.acceptSiblingMaterial !== "function" || !pendingData.token) return
        if (!validData(pendingData)) {
            clientCoordinator.acceptSiblingMaterial(String(pendingData.token), false)
            return
        }
        if (pendingMaterial.source.toString() !== String(pendingData.routeUrl || "")) return
        if (pendingMaterial.status === Image.Ready || pendingMaterial.status === Image.Error)
            clientCoordinator.acceptSiblingMaterial(String(pendingData.token), pendingMaterial.status === Image.Ready)
    }
    onPendingDataChanged: Qt.callLater(reportPendingMaterial)
    onVisibleChanged: scheduleReport()
    onValidChanged: scheduleReport()
    onWidthChanged: scheduleReport()
    onHeightChanged: scheduleReport()
    onXChanged: scheduleReport()
    onYChanged: scheduleReport()
    onAlignedDataChanged: scheduleReport()
    x: alignedData.originX || 0
    y: alignedData.originY || 0
    width: valid ? Math.ceil(alignedData.clientWidth / alignedData.deviceScale) : 1
    height: valid ? Math.ceil(alignedData.clientHeight / alignedData.deviceScale) : 1
    function validPayload() {
        return validData(alignedData)
    }
    function validData(data) {
        var candidateMatrix = data.matrix || []
        if (data.liveAuthorization !== true || !data.routeUrl || !(data.routeWidth > 0) || !(data.routeHeight > 0)
            || !(data.clientWidth > 0) || !(data.clientHeight > 0)
            || !(data.deviceScale > 0) || candidateMatrix.length !== 3) return false
        var dimensions = [data.routeWidth, data.routeHeight, data.clientWidth,
                          data.clientHeight, data.originX, data.originY, data.deviceScale]
        for (var index = 0; index < dimensions.length; ++index)
            if (typeof dimensions[index] !== "number" || !isFinite(dimensions[index])) return false
        for (var row = 0; row < 3; ++row) {
            if (!candidateMatrix[row] || candidateMatrix[row].length !== 3) return false
            for (var column = 0; column < 3; ++column)
                if (typeof candidateMatrix[row][column] !== "number" || !isFinite(candidateMatrix[row][column])) return false
        }
        return true
    }
    function routeTransform(data) {
        if (!validData(data)) return Qt.matrix4x4(1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1)
        var current = data.matrix
        return Qt.matrix4x4(current[0][0], current[0][1], 0, current[0][2],
                            current[1][0], current[1][1], 0, current[1][2],
                            0, 0, 1, 0,
                            current[2][0], current[2][1], 0, current[2][2])
    }
    // Loading a sibling must not replace the current valid surface. The same
    // image URL is handed to the visible surface only after the runtime accepts
    // this material and commits identity atomically. No GPU-present ACK implied.
    Image {
        id: pendingMaterial
        objectName: "clientAlignedPendingMaterial"
        visible: false
        source: aligned.validData(aligned.pendingData) ? aligned.pendingData.routeUrl : ""
        asynchronous: true
        width: aligned.pendingData.routeWidth || 1
        height: aligned.pendingData.routeHeight || 1
        onStatusChanged: Qt.callLater(aligned.reportPendingMaterial)
    }
    Item {
        id: viewport
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
                id: routeImage
                objectName: "clientAlignedRouteImage"
                visible: aligned.valid
                source: aligned.valid ? aligned.alignedData.routeUrl : ""
                width: aligned.valid ? aligned.alignedData.routeWidth : 1
                height: aligned.valid ? aligned.alignedData.routeHeight : 1
                asynchronous: true
                fillMode: Image.Stretch
                opacity: settingsService.values.sheetOpacity
                onStatusChanged: aligned.scheduleReport()
                onOpacityChanged: aligned.scheduleReport()
                transform: Matrix4x4 {
                    matrix: aligned.routeTransform(aligned.alignedData)
                }
            }
        }
    }
}
