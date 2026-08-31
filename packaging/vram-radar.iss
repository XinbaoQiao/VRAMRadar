#define MyAppName "VRAM Radar"
#ifndef MyAppVersion
#define MyAppVersion "0.8.1"
#endif
#define MyAppPublisher "VRAM Radar"
#define MyAppExeName "VRAMRadar.exe"

[Setup]
AppId={{1B2F9822-D7AF-47E9-9757-72F98DB2C106}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
LicenseFile=..\LICENSE
DefaultDirName={localappdata}\Programs\VRAM Radar
DefaultGroupName=VRAM Radar
UsePreviousAppDir=yes
UsePreviousTasks=yes
DisableDirPage=no
Uninstallable=not IsValidationInstall
CreateUninstallRegKey=not IsValidationInstall
PrivilegesRequired=lowest
OutputDir=..\dist-installer
OutputBaseFilename=VRAMRadar-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=app-icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=yes

[InstallDelete]
Type: files; Name: "{group}\VRAM Radar.lnk"; Check: not IsValidationInstall
Type: files; Name: "{autodesktop}\VRAM Radar.lnk"; Check: not IsValidationInstall
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\VRAMRadar.exe"
Type: files; Name: "{app}\VRAMRadarAskPass.exe"
Type: files; Name: "{app}\VRAMRadarUpdater.exe"

[Files]
Source: "..\dist\VRAMRadar\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "installed-marker.txt"; DestDir: "{app}"; DestName: ".vram-radar-installed"; Flags: ignoreversion

[Icons]
Name: "{group}\VRAM Radar"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; AppUserModelID: "VRAMRadar.Desktop"; Check: not IsValidationInstall
Name: "{autodesktop}\VRAM Radar"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; AppUserModelID: "VRAMRadar.Desktop"; Tasks: desktopicon; Check: not IsValidationInstall

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 VRAM Radar"; Flags: nowait postinstall skipifsilent

[Code]
function IsValidationInstall: Boolean;
begin
  Result := CompareText(ExpandConstant('{param:VRAMRADARVALIDATION|0}'), '1') = 0;
end;

function VerifyInstallDirectory(var ErrorMessage: String): Boolean;
var
  InstallDirectory: String;
  ProbeFile: String;
begin
  Result := False;
  InstallDirectory := ExpandConstant('{app}');

  if not ForceDirectories(InstallDirectory) then
  begin
    ErrorMessage :=
      'VRAM Radar cannot create the selected install folder:' + #13#10 +
      InstallDirectory + #13#10#13#10 +
      'Choose a folder your account can write to, such as D:\Apps\VRAM Radar. ' +
      'To use Program Files, close Setup and run it as administrator.' + #13#10#13#10 +
      '无法创建所选安装目录。请选择当前账户可写入的目录，例如 D:\Apps\VRAM Radar；' +
      '如需安装到 Program Files，请退出后右键以管理员身份运行安装程序。';
    exit;
  end;

  ProbeFile := AddBackslash(InstallDirectory) +
    '.vram-radar-write-probe-' + IntToStr(Random(2147483647)) + '.tmp';
  if not SaveStringToFile(ProbeFile, 'VRAM Radar install directory probe', False) then
  begin
    ErrorMessage :=
      'VRAM Radar cannot write to the selected install folder:' + #13#10 +
      InstallDirectory + #13#10#13#10 +
      'Choose a folder your account can write to, such as D:\Apps\VRAM Radar. ' +
      'To use Program Files, close Setup and run it as administrator.' + #13#10#13#10 +
      '无法写入所选安装目录。请选择当前账户可写入的目录，例如 D:\Apps\VRAM Radar；' +
      '如需安装到 Program Files，请退出后右键以管理员身份运行安装程序。';
    exit;
  end;

  if not DeleteFile(ProbeFile) then
  begin
    ErrorMessage :=
      'VRAM Radar created a permission-test file but could not remove it:' + #13#10 +
      ProbeFile + #13#10#13#10 +
      'Check the folder permissions or choose another install folder.' + #13#10#13#10 +
      '安装器无法删除目录权限测试文件。请检查目录权限，或选择其他安装目录。';
    exit;
  end;

  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  InstalledExecutable: String;
begin
  Result := '';
  if not VerifyInstallDirectory(Result) then
    exit;

  InstalledExecutable := ExpandConstant('{app}\{#MyAppExeName}');
  if FileExists(InstalledExecutable) then
  begin
    if not Exec(
      InstalledExecutable,
      '--quit-existing',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    ) then
    begin
      Result :=
        'VRAM Radar could not request the existing application to close.' + #13#10 +
        'Please exit VRAM Radar from its notification-area menu, then run Setup again.' + #13#10#13#10 +
        '无法请求当前 VRAM Radar 退出。请先在任务栏通知区域右键退出，再重新运行安装程序。';
      exit;
    end;
    if ResultCode <> 0 then
    begin
      Result :=
        'VRAM Radar is still running, so Setup stopped before replacing any application files.' + #13#10 +
        'Please exit VRAM Radar from its notification-area menu, then run Setup again.' + #13#10#13#10 +
        'VRAM Radar 仍在运行；安装器已在替换文件前停止。' +
        '请先在任务栏通知区域右键退出，再重新运行安装程序。';
      exit;
    end;
  end;
end;
