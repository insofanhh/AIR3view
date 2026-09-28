#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{DA4F41AF-AE0F-4D39-895A-74950252C08F}
AppName=AIR3view
AppVersion={#AppVersion}
AppPublisher=AIR3view
AppPublisherURL=https://github.com/insofanhh/AIR3view
DefaultDirName={localappdata}\Programs\AIR3view
DefaultGroupName=AIR3view
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\build\windows\release
OutputBaseFilename=AIR3view-Setup-{#AppVersion}-win64
SetupIconFile=..\build\windows\app\AIR3view.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\AIR3view.ico
AppMutex=Local\AIR3viewStudio-8765

[Files]
Source: "..\build\windows\app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AIR3view"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\AIR3view.ico"
Name: "{group}\Đăng nhập Codex"; Filename: "{app}\tools\codex.exe"; Parameters: "login"; WorkingDir: "{app}"
Name: "{autodesktop}\AIR3view"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\AIR3view.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng AIR3view trên Desktop"; GroupDescription: "Biểu tượng:"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; Description: "Mở AIR3view"; Flags: nowait postinstall skipifsilent
