; Windows installer for Sedjem (Inno Setup 6): run after `python desktop\build.py`
;   iscc desktop\installer.iss                 -> dist\Sedjem-<version>-setup.exe
;   set SEDJEM_VERSION=0.3.0 before, to stamp another version
; Installs for the current user (no admin rights needed). Transcriptions, keys and downloaded models live
; in %LOCALAPPDATA%\Sedjem and are kept when the app is uninstalled or updated.
;
; Update mode, used by Sedjem's own updates: installs over the existing installation without a question and
; starts the new Sedjem when it is done, although setup is silent:
;   Sedjem-<version>-setup.exe /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /UPDATE=1
;
; Installed by hand, setup shows a Privacy page with the box for the anonymous usage counts (see [Code]). A silent
; installation (winget, Sedjem's own updates) never shows it and never changes the setting, unless /USAGECOUNTS=0
; asks it to turn the counts off:
;   Sedjem-<version>-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /USAGECOUNTS=0

#define AppVersion GetEnv("SEDJEM_VERSION")
#if AppVersion == ""
  #define AppVersion "0.3.1"
#endif

; the version as the four numbers of setup's own version resource: 0.3.1-preview.20261030 is 0.3.1.0
#define NumericVersion Pos("-", AppVersion) ? Copy(AppVersion, 1, Pos("-", AppVersion) - 1) : AppVersion

; the Privacy page's link (TNewLinkLabel) came with Inno Setup 6.3
#if Ver < EncodeVer(6, 3, 0)
  #error Inno Setup 6.3 or later is needed
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
; setup's version resource, which Windows shows in the file's properties: the same product name and version as
; Sedjem.exe's (desktop/build.py), the version in full as text and as four numbers
VersionInfoProductName=Sedjem
VersionInfoProductTextVersion={#AppVersion}
VersionInfoProductVersion={#NumericVersion}
VersionInfoVersion={#NumericVersion}
VersionInfoCompany=Mohammed El-sayed Ahmed
VersionInfoDescription=Sedjem Setup
VersionInfoCopyright=Copyright (C) 2026 Mohammed El-sayed Ahmed
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

[UninstallDelete]
; the Privacy page's choice, if Sedjem never started to take it (see [Code])
Type: files; Name: "{app}\installer.json"

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

// Sedjem starts its update and then quits, so setup can reach the files before the old Sedjem has let go of them,
// and a silent setup then gives up (exit code 5). An update first waits, up to a minute, until Sedjem.exe can be
// opened for writing, which Windows refuses while the program runs. Older versions update the same way.
function SedjemHasQuit: Boolean;
var
  Exe: String;
  I: Integer;
  F: TFileStream;
begin
  Result := True;
  Exe := ExpandConstant('{app}\Sedjem.exe');
  if not FileExists(Exe) then
    exit;
  for I := 1 to 120 do
  begin
    try
      F := TFileStream.Create(Exe, fmOpenReadWrite or fmShareExclusive);
      F.Free;
      exit;
    except
      Sleep(500);
    end;
  end;
  Result := False;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if IsUpdate and not SedjemHasQuit then
    Result := 'Sedjem is still running, so it can''t be updated. Quit Sedjem, then update it again.';
end;

// The Privacy page, after the licence in an installation by hand: what Sedjem sends, a link to the privacy policy,
// and the box for the anonymous usage counts (docs/privacy.md, docs/13-usage-counts.md), which counts as Sedjem's
// first-start notice. The choice is left beside Sedjem.exe in installer.json, {"usage_counts": the box, "changed":
// whether it was changed from how the page showed it}, and Sedjem applies it at its next start to whichever data folder
// it uses, SEDJEM_HOME included, then deletes it (app/config.py, Config.apply_installer_choice). Setup never writes to
// the data folder.
const
  PrivacyPolicy = 'https://github.com/MohammedEl-sayedAhmed/sedjem/blob/main/docs/privacy.md';

var
  UsageBox: TNewCheckBox;
  UsageWasOff: Boolean;  // settings.json already has the counts off, so the box starts unticked

// Where the installed Sedjem keeps its data, found as app/config.py finds it, only to start the box as it is set:
// %LOCALAPPDATA%\Sedjem, or the Tafrigh folder of an older version that Sedjem hasn't renamed yet. '' when SEDJEM_HOME
// or TAFRIGH_HOME is set here, since Sedjem may be started with other values: the box then starts ticked, and leaving
// it so never turns back on counts that were off (see "changed").
function DataFolder: String;
var
  Base: String;
begin
  Result := '';
  if (GetEnv('SEDJEM_HOME') <> '') or (GetEnv('TAFRIGH_HOME') <> '') then
    exit;
  Base := GetEnv('LOCALAPPDATA');
  if Base = '' then
    Base := ExpandConstant('{localappdata}');
  Result := AddBackslash(Base) + 'Sedjem';
  if not DirExists(Result) and DirExists(AddBackslash(Base) + 'Tafrigh') then
    Result := AddBackslash(Base) + 'Tafrigh';
end;

// Whether settings.json has the counts turned off, as Sedjem writes it: "usage_counts": false
function UsageCountsOffBefore: Boolean;
var
  Folder: String;
  Saved: AnsiString;
  Settings: String;
begin
  Result := False;
  Folder := DataFolder;
  if (Folder <> '') and LoadStringFromFile(AddBackslash(Folder) + 'app_data\settings.json', Saved) then begin
    Settings := Saved;
    StringChangeEx(Settings, ' ', '', True);
    StringChangeEx(Settings, #9, '', True);
    StringChangeEx(Settings, #13, '', True);
    StringChangeEx(Settings, #10, '', True);
    Result := Pos('"usage_counts":false', Settings) > 0;
  end;
end;

// /USAGECOUNTS=0: the counts off, in a silent installation too
function UsageCountsOffParam: Boolean;
begin
  Result := ExpandConstant('{param:USAGECOUNTS|1}') = '0';
end;

function JsonBool(Value: Boolean): String;
begin
  if Value then
    Result := 'true'
  else
    Result := 'false';
end;

// installer.json beside Sedjem.exe, for Sedjem's next start (see above)
procedure LeaveUsageChoice(Value, Changed: Boolean);
begin
  SaveStringToFile(ExpandConstant('{app}\installer.json'),
    '{"usage_counts": ' + JsonBool(Value) + ', "changed": ' + JsonBool(Changed) + '}', False);
end;

procedure OpenLink(Sender: TObject; const Link: String; LinkType: TSysLinkType);
var
  ErrorCode: Integer;
begin
  ShellExecAsOriginalUser('open', Link, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure InitializeWizard;
var
  Page: TWizardPage;
  Summary: TNewStaticText;
  Link: TNewLinkLabel;
begin
  UsageWasOff := UsageCountsOffBefore;
  Page := CreateCustomPage(wpLicense, 'Privacy', 'What Sedjem sends, and the anonymous usage counts');

  Summary := TNewStaticText.Create(Page);
  Summary.AutoSize := False;
  Summary.WordWrap := True;
  Summary.Width := Page.SurfaceWidth;
  Summary.Caption :=
    'Sedjem works on this computer: recordings, transcripts and keys stay in its data folder, and there is no ' +
    'account. It goes online only for what you ask of it: the models you download, the YouTube links you add, ' +
    'the hosted services you choose with your own keys, and update checks when you allow them.' + #13#10#13#10 +
    'It also sends anonymous usage counts to Aptabase, in the EU, a few times a month: the version, the system ' +
    'and the computer in broad terms, some settings, which models are downloaded, and how Sedjem is used, such ' +
    'as how many transcriptions were made with which models. Never recordings, transcripts, names, file names ' +
    'or keys, and no identifier. Settings > About in Sedjem turns them on or off at any time.';
  Summary.Parent := Page.Surface;
  Summary.AdjustHeight;

  Link := TNewLinkLabel.Create(Page);
  Link.AutoSize := False;
  Link.Top := Summary.Top + Summary.Height + ScaleY(12);
  Link.Width := Page.SurfaceWidth;
  Link.Caption := 'The <a href="' + PrivacyPolicy + '">privacy policy</a> has everything Sedjem sends, to whom ' +
    'and when.';
  Link.OnLinkClick := @OpenLink;
  Link.Parent := Page.Surface;
  Link.AdjustHeight;

  UsageBox := TNewCheckBox.Create(Page);
  UsageBox.Top := Link.Top + Link.Height + ScaleY(16);
  UsageBox.Width := Page.SurfaceWidth;
  UsageBox.Height := ScaleY(17);
  UsageBox.Caption := '&Send anonymous usage counts';
  UsageBox.Checked := not (UsageWasOff or UsageCountsOffParam);
  UsageBox.Parent := Page.Surface;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    exit;
  if not WizardSilent then
    // changed: the box differs from what the page showed by itself, ticked unless the counts were off
    LeaveUsageChoice(UsageBox.Checked, UsageBox.Checked = UsageWasOff)
  else if UsageCountsOffParam then
    LeaveUsageChoice(False, True);
end;
