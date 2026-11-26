import QtQuick

// S1 map landmarks (room names / minecarts); saved at once, like the
// route point-number switch. One preference for the editor and game maps.
AppSwitch {
    id: control
    property string settingKey: "mapLandmarkRooms"
    objectName: "mapLandmarkSwitch_" + settingKey
    checked: !settingsService || settingsService.persistedValues[settingKey] !== false
    enabled: !!settingsService && !settingsService.busy && !settingsService.loadFailed
    onClicked: {
        if (settingsService.setValue(settingKey, checked))
            settingsService.saveFields([settingKey])
        checked = Qt.binding(function() { return !settingsService || settingsService.persistedValues[control.settingKey] !== false })
    }
}
