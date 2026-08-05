; Twitch Auto Recorder - Inno Setup installer
#define MyAppName "Twitch Auto Recorder"
#define MyAppVersion "1.2.10"
#define MyAppPublisher "g3fps"
#define MyAppURL "https://github.com/g3fps/twitch-recorder"
#define MyAppExeName "TwitchRecorder.exe"
#define MyAppId "{{A8F3C2E1-9B47-4D6A-8E21-7C0F5B1D4A92}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\TwitchRecorder
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
LicenseFile=
InfoBeforeFile=
OutputDir=dist_installer
OutputBaseFilename=TwitchRecorderSetup
SetupIconFile=
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion=1.2.10.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startmenu"; Description: "Create a Start Menu shortcut"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; One-dir PyInstaller build — no runtime unpack to %TEMP%\_MEI* (Defender often deletes python*.dll there)
Source: "dist\TwitchRecorder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "config.example.yaml"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: startmenu
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
function IsUpgrade(): Boolean;
var
  UninstallKey: String;
begin
  UninstallKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A8F3C2E1-9B47-4D6A-8E21-7C0F5B1D4A92}_is1';
  Result := RegKeyExists(HKCU, UninstallKey) or RegKeyExists(HKLM, UninstallKey);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // Fresh install: show shortcut tasks. Updates: skip — keep existing shortcuts.
  Result := IsUpgrade() and (PageID = wpSelectTasks);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
end;
