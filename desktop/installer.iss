; Windows installer for Sedjem (Inno Setup 6): run after `python desktop\build.py`
;   iscc desktop\installer.iss                 -> dist\Sedjem-<version>-setup.exe
;   set SEDJEM_VERSION=0.3.0 before, to stamp another version
; Installs for the current user (no admin rights needed). Transcriptions, keys and downloaded models live
; in %LOCALAPPDATA%\Sedjem and are kept when the app is uninstalled or updated.
;
; Update mode, used by Sedjem's own updates: installs over the existing installation without a question and
; starts the new Sedjem when it is done, although setup is silent:
;   Sedjem-<version>-setup.exe /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /UPDATE=1

#define AppVersion GetEnv("SEDJEM_VERSION")
#if AppVersion == ""
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{6F3C2E9A-4B71-4D5C-9A2E-8B1F0C7D3E45}
AppName=Sedjem
AppVersion={#AppVersion}
AppVerName=Sedjem {#AppVersion}
AppPublisher=Mohammed El-sayed Ahmed
AppPublisherURL=https://github.com/MohammedEl-sayedAhmed
AppSupportURL=https://github.com/MohammedEl-sayedAhmed/sedjem
AppCopyright=Copyright (C) 2026 Mohammed El-sayed Ahmed
LicenseFile=..\LICENSE
DefaultDirName={autopf}\Sedjem
DefaultGroupName=Sedjem
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Sedjem-{#AppVersion}-setup
SetupIconFile=sedjem.ico
UninstallDisplayIcon={app}\Sedjem.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; the libraries of the version being replaced: a newer build can rename or drop some, and leftovers (an old
; package's metadata, for one) would be loaded beside the new ones. The data folder is elsewhere.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Sedjem\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Sedjem"; Filename: "{app}\Sedjem.exe"
; the same app in an Edge (or default browser) window, if the built-in window ever misbehaves
Name: "{autoprograms}\Sedjem (browser window)"; Filename: "{app}\Sedjem.exe"; Parameters: "--browser"
Name: "{autodesktop}\Sedjem"; Filename: "{app}\Sedjem.exe"; Tasks: desktopicon

[Run]
; an installation by hand: the box on the last page (a silent one starts nothing)
Filename: "{app}\Sedjem.exe"; Description: "{cm:LaunchProgram,Sedjem}"; Flags: nowait postinstall skipifsilent; Check: not IsUpdate
; an update (/UPDATE=1): the new Sedjem starts as soon as it is installed, silent or not
Filename: "{app}\Sedjem.exe"; Flags: nowait runasoriginaluser; Check: IsUpdate

[Code]
// True when setup was started with /UPDATE=1, as Sedjem does to update itself.
function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;
