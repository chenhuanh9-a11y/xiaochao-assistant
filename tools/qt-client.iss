; Separate private test product; no installation or launch is performed by build.
#ifndef StageDir
  #error StageDir is required
#endif
#ifndef SetupOutputDir
  #error SetupOutputDir is required
#endif
#ifndef SetupName
  #error SetupName is required
#endif
#ifndef DisplayVersion
  #error DisplayVersion is required
#endif
#ifndef NumericVersion
  #error NumericVersion is required
#endif
#ifndef InstallerAppId
  #define InstallerAppId "{{AF19AFD9-3016-44C9-9D20-6F216C5D8C51}"
#endif
#ifndef InstallerAppName
  #define InstallerAppName "Xingtu Qt Test Client"
#endif
#ifndef InstallerFolder
  #define InstallerFolder "XingtuQtClientTest"
#endif
#ifndef InstallerGroup
  #define InstallerGroup "Xingtu Qt Test Client"
#endif

[Setup]
AppId={#InstallerAppId}
AppName={#InstallerAppName}
AppVersion={#DisplayVersion}
AppVerName={#InstallerAppName} {#DisplayVersion}
VersionInfoVersion={#NumericVersion}
AppPublisher=Xingtu project (private Qt test)
DefaultDirName={autopf}\{#InstallerFolder}
DefaultGroupName={#InstallerGroup}
UsePreviousAppDir=no
DisableDirPage=yes
DisableProgramGroupPage=yes
UninstallDisplayName={#InstallerAppName}
UninstallDisplayIcon={app}\XingtuQtClientTest.exe
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#SetupOutputDir}
OutputBaseFilename={#SetupName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
SetupLogging=yes

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#InstallerAppName}"; Filename: "{app}\XingtuQtClientTest.exe"; WorkingDir: "{app}"

; No [Run], [UninstallDelete], update takeover, portable archive, or public URL.
; The EXE requests UAC itself; shortcuts point straight to it. Runtime work/data
; is created only by this test product and is preserved on uninstall.
