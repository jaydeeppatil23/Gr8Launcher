; Compile with tools/build_windows.py so the package paths and versions match.
#ifndef AppVersion
  #error AppVersion must be supplied with /DAppVersion
#endif
#ifndef NumericVersion
  #error NumericVersion must be supplied with /DNumericVersion
#endif
#ifndef PackageDir
  #error PackageDir must be supplied with /DPackageDir
#endif
#ifndef OutputDir
  #error OutputDir must be supplied with /DOutputDir
#endif
#define ProjectRoot AddBackslash(SourcePath) + ".."

[Setup]
; "{{" escapes a literal "{". Keep this AppId unchanged across releases.
AppId={{449A7BDC-D374-4AAB-8D31-D699214AC088}
AppName=Gr8 Launcher
AppVersion={#AppVersion}
AppPublisher=Gr8 Projects
AppPublisherURL=https://github.com/jaydeeppatil23/Gr8Launcher
AppSupportURL=https://github.com/jaydeeppatil23/Gr8Launcher/issues
AppUpdatesURL=https://github.com/jaydeeppatil23/Gr8Launcher/releases
VersionInfoVersion={#NumericVersion}.0
VersionInfoCompany=Gr8 Projects
VersionInfoProductName=Gr8 Launcher
DefaultDirName={autopf}\Gr8Launcher
DefaultGroupName=Gr8 Projects
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
UsePreviousPrivileges=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
DisableDirPage=auto
AlwaysShowDirOnReadyPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile={#ProjectRoot}\LICENSE
SetupIconFile={#ProjectRoot}\.assets\icons\gr8_icon.ico
UninstallDisplayIcon={app}\Gr8Launcher.exe
OutputDir={#OutputDir}
OutputBaseFilename=Gr8Launcher-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#PackageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Gr8 Launcher"; Filename: "{app}\Gr8Launcher.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Gr8 Launcher"; Filename: "{app}\Gr8Launcher.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Gr8Launcher.exe"; Description: "Launch Gr8 Launcher"; Flags: nowait postinstall skipifsilent

[Code]
var
  OldBundleInventory: String;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExistingInventory: String;
begin
  Result := '';
  ExistingInventory := ExpandConstant('{app}\bundle-manifest.json');
  if FileExists(ExistingInventory) then
  begin
    OldBundleInventory := ExpandConstant('{tmp}\old-bundle-manifest.json');
    if not CopyFile(ExistingInventory, OldBundleInventory, False) then
      Result := 'Could not preserve the launcher bundle inventory. Close other launcher copies and try again.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  CleanupExitCode: Integer;
begin
  if (CurStep = ssPostInstall) and (OldBundleInventory <> '') then
  begin
    if not Exec(ExpandConstant('{app}\updater.exe'),
      '--cleanup-installed "' + OldBundleInventory + '" "' + ExpandConstant('{app}') + '"',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, CleanupExitCode) then
      RaiseException('Could not finish cleaning up the previous launcher bundle. Run setup again to repair it.');
    if CleanupExitCode <> 0 then
      RaiseException('The launcher bundle needs repair. Run setup again before launching.');
  end;
end;
