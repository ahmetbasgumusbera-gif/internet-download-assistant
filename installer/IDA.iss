; Internet Download Assistant - Inno Setup kurulum betigi
; Derleme: build.ps1 (ya da  ISCC.exe /DMyAppVersion=1.0.0 installer\IDA.iss ).  Dosya UTF-8 (BOM'lu) kaydedilmelidir.

#define MyAppName "İnternet Download Assistant"
#define MyAppExe "IDA.exe"
#define MyAppPublisher "IDA"
#define MyAppMutex "IDA.InternetDownloadAssistant.Mutex"
#ifndef MyAppVersion
  #error Surum belirtilmedi. Kurulum paketini .\build.ps1 ile derleyin (Inno Setup penceresinden F9 ile degil).
#endif

[Setup]
AppId={{B7E4A9D2-5C31-4F0E-9A57-1D2C8E6F4A10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Yönetici izni gerekmez: varsayılan kullanıcıya özel kurulum; kurulum sihirbazı "tüm kullanıcılar" seçeneği de sunar
PrivilegesRequired=lowest
; (tüm kullanıcılar / yönetici seçeneği kaldırıldı: Program Files ve izin sorunları yaşanmasın, veri doğru kullanıcıya düşsün)
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\dist_installer
#ifdef LITE
OutputBaseFilename=IDA-Online-Setup-{#MyAppVersion}
#else
OutputBaseFilename=IDA-Setup-{#MyAppVersion}
#endif
SetupIconFile=IDA.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Uygulama çalışıyorsa (IDA.exe bu isimli mutex'i tutar) kurulum/kaldırma onu kapatmayı önerir
AppMutex={#MyAppMutex}
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
SetupLogging=yes

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "Masaüstüne simge koy"; Flags: unchecked

[Dirs]
; Kullanıcı verisi klasörü (ayarlar, geçmiş, günlükler, güncellenen araçlar) kurulumda hazırlanır; kaldırmada sorulur
Name: "{localappdata}\InternetDownloadAssistant"; Flags: uninsneveruninstall

[InstallDelete]
; üzerine kurulumda eski sürümden kalan dosyalar silinir (ayarlar ve geçmiş %LOCALAPPDATA% altında, dokunulmaz)
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\extension"
Type: filesandordirs; Name: "{app}\bin"

[Files]
Source: "..\dist\IDA\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifndef LITE
Source: "..\bin\*"; DestDir: "{app}\bin"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif
Source: "..\extension\*"; DestDir: "{app}\extension"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "redist\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: VCRedistNeeded

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Tarayıcı eklentisini yükle (klasörü aç)"; Filename: "{win}\explorer.exe"; Parameters: """{app}\extension"""
Name: "{group}\{#MyAppName} - kaldır"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
; Uygulamanın "Windows ile başlat" ayarının yazdığı kaydı kaldırırken temizler (kurulumda bir şey oluşturmaz)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "InternetDownloadAssistant"; Flags: dontcreatekey uninsdeletevalue

[Run]
; Visual C++ çalışma zamanı yoksa küçük bir ilerleme penceresiyle (etkileşimsiz) kurulur
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/passive /norestart"; StatusMsg: "Gerekli bileşenler kuruluyor (Visual C++ çalışma zamanı)..."; Flags: shellexec waituntilterminated; Check: VCRedistNeeded
Filename: "{app}\extension\README.txt"; Description: "Tarayıcı eklentisinin kurulum yönergelerini aç"; Flags: postinstall shellexec skipifsilent unchecked
Filename: "{app}\{#MyAppExe}"; Description: "{#MyAppName} uygulamasını başlat"; Flags: nowait postinstall skipifsilent runasoriginaluser
; uygulama içi güncelleme Setup'ı sessiz çalıştırır: bitince uygulama kendiliğinden yeniden açılır
Filename: "{app}\{#MyAppExe}"; Flags: nowait skipifnotsilent runasoriginaluser

[Code]
// Visual C++ 2015-2022 (x64) çalışma zamanı kurulu ve yeterince yeni mi?
function VCRedistNeeded: Boolean;
var
  Installed, Build: Cardinal;
begin
  Result := True;
  if not IsAdminInstallMode then begin Result := False; Exit; end;   // UAC istemesin: çalışma zamanı DLL'leri pakette
  if RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) then
    if Installed = 1 then
    begin
      Result := False;
      if RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Bld', Build) then
        Result := (Build < 32000);
    end;
end;

function InitializeSetup(): Boolean;
var
  Cmd, Key: String;
  Code: Integer;
begin
  Result := True;
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B7E4A9D2-5C31-4F0E-9A57-1D2C8E6F4A10}_is1';
  // Daha önce yönetici olarak (Program Files) kurulmuş eski sürüm varsa yan yana kalmasın: önce o kaldırılır
  if (not IsAdminInstallMode) and RegQueryStringValue(HKLM, Key, 'UninstallString', Cmd) then
  begin
    Cmd := RemoveQuotes(Cmd);
    if FileExists(Cmd) then
    begin
      if WizardSilent or (MsgBox('Bilgisayarda eski bir sürüm (tüm kullanıcılar için) kurulu. Yan yana kalmaması için önce kaldırılacak.' + #13#10 +
         'Ayarlarınız ve indirme geçmişiniz silinmez. Devam edilsin mi?', mbConfirmation, MB_YESNO) = IDYES) then
        ShellExec('runas', Cmd, '/VERYSILENT /NORESTART', '', SW_SHOW, ewWaitUntilTerminated, Code);
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Result := '';
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM IDA.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    if MsgBox('Ayarlar, indirme geçmişi ve güncellenen araçlar da silinsin mi?' + #13#10 +
              '(Evet derseniz bu bilgiler geri getirilemez. İndirdiğiniz dosyalara dokunulmaz.)',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{localappdata}\InternetDownloadAssistant'), True, True, True);
end;
