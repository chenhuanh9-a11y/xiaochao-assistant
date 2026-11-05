import QtQuick

AppSwitch {
    objectName: "routePointNumbersSwitch"
    text: "选中点标号"
    checked: settingsService && settingsService.persistedValues.routePointNumbers || false
    enabled: !!settingsService && !settingsService.busy && !settingsService.loadFailed
    onClicked: {
        if (settingsService.setValue("routePointNumbers", checked))
            settingsService.saveFields(["routePointNumbers"])
        checked = Qt.binding(function() { return settingsService && settingsService.persistedValues.routePointNumbers || false })
    }
}
