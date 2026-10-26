; Inno Setup 6. Build through tools/build_windows.py to supply these constants.
#if PREPROCVER != 0x06070300
  #error Reproducible PyDrop installers require Inno Setup 6.7.3
#endif
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  #error SourceDir must point to the completed dist\PyDrop directory
#endif
#ifndef OutputDir
  #error OutputDir must point to the distribution directory
#endif
#ifndef BuildArch
  #define BuildArch "x64"
#endif

[Setup]
AppId={{6E348C29-3838-4D1A-B7FC-1049F654A426}
AppName=PyDrop
AppVersion={#AppVersion}
AppVerName=PyDrop {#AppVersion}
AppPublisher=PyDrop
AppComments=Visual Python programming for desktop, data and Arduino projects
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany=PyDrop
VersionInfoDescription=PyDrop desktop installer
DefaultDirName={localappdata}\Programs\PyDrop
DefaultGroupName=PyDrop
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=PyDrop-{#AppVersion}-win-{#BuildArch}-setup
SetupIconFile={#SourcePath}..\assets\pydrop.ico
UninstallDisplayIcon={app}\PyDrop.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
#if BuildArch == "x64"
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#else
ArchitecturesAllowed=x86compatible
#endif

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
; Install bundled program resources only. User projects and LocalAppData\PyDrop
; are deliberately outside this tree and are never removed by the installer.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PyDrop"; Filename: "{app}\PyDrop.exe"; WorkingDir: "{userdocs}"
Name: "{group}\Uninstall PyDrop"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PyDrop"; Filename: "{app}\PyDrop.exe"; WorkingDir: "{userdocs}"; Tasks: desktopicon

[Run]
Filename: "{app}\PyDrop.exe"; Description: "Launch PyDrop"; Flags: nowait postinstall skipifsilent
