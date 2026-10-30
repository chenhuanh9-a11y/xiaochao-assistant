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

[Setup]
AppId={{AF19AFD9-3016-44C9-9D20-6F216C5D8C51}
AppName=Xingtu Qt Test Client
AppVersion={#DisplayVersion}
AppVerName=Xingtu Qt Test Client {#DisplayVersion}
VersionInfoVersion={#NumericVersion}
AppPublisher=Xingtu project (private Qt test)
DefaultDirName={autopf}\XingtuQtClientTest
DefaultGroupName=Xingtu Qt Test Client
UsePreviousAppDir=no
DisableDirPage=yes
DisableProgramGroupPage=yes
UninstallDisplayName=Xingtu Qt Test Client
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
Name: "{group}\Xingtu Qt Test Client"; Filename: "{app}\XingtuQtClientTest.exe"; WorkingDir: "{app}"

; No [Run], [UninstallDelete], update takeover, portable archive, or public URL.
; The EXE requests UAC itself; shortcuts point straight to it. Runtime work/data
; is created only by this test product and is preserved on uninstall.
