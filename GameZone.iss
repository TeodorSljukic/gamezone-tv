#define MyAppName "GameZone Igraonica"
#define MyAppVersion "1.5.7"
#define MyAppExe "GameZone-TV.exe"

[Setup]
AppId={{8F3A6C2E-5B91-4D7A-9E10-1A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=GameZone
DefaultDirName={autopf}\GameZone
DefaultGroupName=GameZone
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
OutputDir=installer
OutputBaseFilename=GameZone-Setup
SetupIconFile=icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Napravi precicu na desktopu"; GroupDescription: "Precice:"
Name: "startup"; Description: "Pokreni automatski kad se upali Windows"; GroupDescription: "Pokretanje:"

[Files]
Source: "dist\GameZone-TV.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\POMOC.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\dozvoli-firewall.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\GameZone Igraonica"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Pomoc"; Filename: "{app}\POMOC.txt"
Name: "{group}\Deinstaliraj GameZone"; Filename: "{uninstallexe}"
Name: "{autodesktop}\GameZone Igraonica"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon
Name: "{userstartup}\GameZone Igraonica"; Filename: "{app}\{#MyAppExe}"; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Pokreni GameZone sad"; Flags: nowait postinstall
