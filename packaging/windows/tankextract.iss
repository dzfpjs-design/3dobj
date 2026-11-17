; Inno Setup script. Version is passed on the command line: ISCC /DMyAppVersion=1.0.0
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "罐体提取"
#define MyAppPublisher "tankextract"
#define MyAppExeName "TankExtract.exe"

[Setup]
AppId={{A7C3E1B2-4F5D-4A91-9C2E-7B6D8E0F1A23}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\TankExtract
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=no
OutputDir=..\..\dist\installer
OutputBaseFilename=TankExtract-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked

[Files]
Source: "..\..\dist\TankExtract\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\罐体提取"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\命令行 tankextract"; Filename: "{app}\tankextract.exe"
Name: "{group}\卸载罐体提取"; Filename: "{uninstallexe}"
Name: "{autodesktop}\罐体提取"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动罐体提取"; Flags: nowait postinstall skipifsilent
