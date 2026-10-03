#ifndef AppVersion
  #define AppVersion "0.2.1"
#endif
#ifndef AppFiles
  #define AppFiles "..\dist\Factory Switch"
#endif
[Setup]
AppId={{E408AD11-7CA4-4B16-A81B-6C77D147390C}
AppName=Factory Switch
AppVersion={#AppVersion}
AppPublisher=Factory Switch contributors
AppPublisherURL=https://github.com/shelbyluocus-wq/factory-switch
DefaultDirName={localappdata}\Programs\Factory Switch
DefaultGroupName=Factory Switch
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\release
OutputBaseFilename=FactorySwitch-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Factory Switch.exe
SetupIconFile=..\ui\app-icon.ico
CloseApplications=yes

[Files]
Source: "{#AppFiles}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Factory Switch"; Filename: "{app}\Factory Switch.exe"
Name: "{autodesktop}\Factory Switch"; Filename: "{app}\Factory Switch.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\Factory Switch.exe"; Description: "Launch Factory Switch"; Flags: nowait postinstall skipifsilent
