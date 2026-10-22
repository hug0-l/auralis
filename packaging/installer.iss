; =============================================================================
;  Auralis - installation script
; -----------------------------------------------------------------------------
;  Compiler : Inno Setup 6.x (iscc).  The script uses nothing the 5.5.7
;             compiler does not know, so the older compiler reads it as
;             well.
;  Purpose  : install the workbench (aurui / aurcore / aurserver), the shipped
;             preset library, the resources, the help, the core C ABI library
;             and the built plug-in bundles.
;
;  This file is the single source of truth for the install layout.  It is plain
;  text on purpose: scripts/build_release.py parses it (no Inno Setup required)
;  and tests/test_installer.py checks it against the tree the release builder
;  assembles - every source path, the registry layout, the plug-in directory
;  and the uninstall.
;
;  Build symbols (all optional - the defaults build straight from the
;  repository):
;
;    SourceRoot      the tree the application files are copied from
;    PresetsRoot     the shipped preset library
;    AbiRoot         the directory holding the built core C ABI library.
;                    The default is the repository's build output; the
;                    release builder points it at the `lib` directory of the
;                    release tree, so the library is installed next to the
;                    application.
;    PluginRoot      the directory holding the built plug-in bundles.  The
;                    release builder points it at the tree it packages; the
;                    default is the tree the last build assembled.
;    OutputDir       where the compiled installer is written
;    MyAppVersion    the version string
;    PerMachine      define to build the machine-wide installer
;
;  Per-user vs per-machine is decided when the script is compiled, never at
;  run time, so the install directory, the registry root and the plug-in
;  directory can never disagree:
;
;    per-user     {localappdata}\Programs\Auralis             HKCU
;                 {localappdata}\Programs\Common\VST3
;    per-machine  {autopf}\Auralis                            HKLM
;                 {commonpf}\VST3
; =============================================================================

; ---------------------------------------------------------------------------
;  Build symbols
; ---------------------------------------------------------------------------
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0a0"
#endif

#ifndef MyAppVersionNumeric
  #define MyAppVersionNumeric "0.1.0.0"
#endif

#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif

#ifndef SourceRoot
  #define SourceRoot "{src}\.."
#endif

#ifndef PresetsRoot
  #define PresetsRoot "{src}\..\var\presets\builtin"
#endif

#ifndef AbiRoot
  #define AbiRoot "{src}\..\plugins\abi\build"
#endif

#ifndef PluginRoot
  ; The directory the built plug-in bundles are staged in.  The release
  ; builder always points this at the tree it is packaging; the default is
  ; the tree the last build assembled, so the script can also be compiled
  ; from the repository alone.
  #define PluginRoot "{src}\..\dist\full\plugins\vst3\build\stamped"
#endif

#ifndef RuntimeDirName
  ; The directory the shared runtime of the family lives in.  The bundles
  ; hold the plug-in alone: the core and the interpreter are shared by the
  ; whole family and live once, in a directory of this name next to the
  ; bundles.  The plug-in looks for a directory of this name next to its
  ; own bundle, so the name is part of the contract between the build and
  ; the plug-in.
  #define RuntimeDirName "Auralis Runtime"
#endif

#ifdef PerMachine
  #define RegRoot "HKLM"
  #define Scope "machine"
  #define DefaultDir "{autopf}\Auralis"
  #define Privileges "admin"
  ; The directory a host scans for machine wide plug-ins.  The symbol carries
  ; no quotes of its own: it is used inside string constants only.
  #define Vst3Dir {commonpf}\VST3
#else
  #define RegRoot "HKCU"
  #define Scope "user"
  #define DefaultDir "{localappdata}\Programs\Auralis"
  #define Privileges "lowest"
  ; The directory a host scans for plug-ins of the current user.
  #define Vst3Dir {localappdata}\Programs\Common\VST3
#endif

; The plug-in formats.  VST3 is the format that exists today; the other two are
; registered as well, but disabled, so that enabling one is a matter of
; flipping a flag instead of inventing a new registry layout.
#define FormatVst3 "VST3"
#define FormatAu "AU"
#define FormatAax "AAX"

; The session document format.
#define DocumentExtension "aurdoc"
#define DocumentProgId "Auralis.SessionDocument"

[Setup]
AppId={{7B3A1C42-9E55-4C7B-9A0E-2F5D8C6A1E30}
AppName=Auralis
AppVersion={#MyAppVersion}
AppVerName=Auralis {#MyAppVersion}
AppPublisher=Auralis
DefaultDirName={#DefaultDir}
DefaultGroupName=Auralis
DisableProgramGroupPage=yes
AllowNoIcons=yes
OutputDir={#OutputDir}
OutputBaseFilename=Auralis-{#MyAppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
UninstallLogging=yes
CloseApplications=yes
RestartApplications=no
RestartIfNeededByRun=no
PrivilegesRequired={#Privileges}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
VersionInfoVersion={#MyAppVersionNumeric}
VersionInfoCompany=Auralis
VersionInfoDescription=Auralis - audio restoration workbench
VersionInfoProductName=Auralis
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; --- the application -------------------------------------------------------
Source: "{#SourceRoot}\aurui\*"; DestDir: "{app}\aurui"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\aurcore\*"; DestDir: "{app}\aurcore"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\aurserver\*"; DestDir: "{app}\aurserver"; Flags: ignoreversion recursesubdirs createallsubdirs
; --- the MCP server and the model library ------------------------------------
; the server lets an AI agent drive the product, and the models are the
; learned artefacts the offline path runs
Source: "{#SourceRoot}\aurmcp\*"; DestDir: "{app}\aurmcp"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\models\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs createallsubdirs
; --- the resources ----------------------------------------------------------
Source: "{#SourceRoot}\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs
; --- the help --------------------------------------------------------------
Source: "{#SourceRoot}\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs
; --- the shipped preset library --------------------------------------------
Source: "{#PresetsRoot}\*"; DestDir: "{app}\var\presets\builtin"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; --- the launchers ----------------------------------------------------------
Source: "{#SourceRoot}\bin\auralis.cmd"; DestDir: "{app}\bin"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#SourceRoot}\bin\auralis-server.cmd"; DestDir: "{app}\bin"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#SourceRoot}\bin\auralis.sh"; DestDir: "{app}\bin"; Flags: ignoreversion skipifsourcedoesntexist
; --- the core C ABI library -------------------------------------------------
Source: "{#AbiRoot}\aurcore_abi.dll"; DestDir: "{app}\lib"; Flags: ignoreversion skipifsourcedoesntexist
; --- the manifest and the checksum list -------------------------------------
; The package describes itself: the manifest names every file with its size
; and its hash, the checksum list is the form the standard checksum tool
; reads and the verifier travels with them.  Without these three files the
; installed tree cannot be checked by the user.
Source: "{#SourceRoot}\manifest.json"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#SourceRoot}\SHA256SUMS"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#SourceRoot}\scripts\build_release.py"; DestDir: "{app}\scripts"; Flags: ignoreversion skipifsourcedoesntexist
; --- the plug-in bundles ----------------------------------------------------
; The bundle is a directory, not a file: a Windows bundle is
;   <name>.vst3\<name>.vst3.dll
; and a macOS bundle is
;   <name>.vst3\Contents\MacOS\<name>.vst3
; The whole directory is copied, with the layout it was built with, so the
; files inside it keep their own sub-directories.  That is what
; recursesubdirs and createallsubdirs do together.
;
; The destination is the directory a host scans for the scope the installer
; was compiled for - {commonpf}\VST3 for a machine wide install and
; {localappdata}\Programs\Common\VST3 for a per user one.  The bundles are
; deliberately not installed into {app}\plugins\vst3: a host does not look
; there, so a bundle installed there is a bundle no host can load.  The
; application keeps {app}\plugins as its own plug-in layer root; it holds the
; two formats that have no system wide location yet (AU and AAX, both
; registered and both disabled).
;
; The destination is read back from the registry rather than written out a
; second time, so the registry and the file layout can never disagree.
Source: "{#PluginRoot}\*.vst3"; DestDir: "{code:PluginBundleDir('VST3')}"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; --- the shared runtime -----------------------------------------------------
; The core and the interpreter are shared by the whole family: a bundle
; holds the plug-in alone.  The runtime goes into the directory the host
; scans, next to the bundles, because that is where the plug-in looks for
; it - a directory called "Auralis Runtime" next to the bundle.  A runtime
; that is somewhere else is a runtime no plug-in finds.
Source: "{#PluginRoot}\{#RuntimeDirName}\*"; DestDir: "{code:PluginRuntimeDir('VST3')}"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Registry]
; --- the application itself ------------------------------------------------
Root: {#RegRoot}; Subkey: "Software\Auralis"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"; Flags: uninsdeletekey uninsdeletevalue
Root: {#RegRoot}; Subkey: "Software\Auralis"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"; Flags: uninsdeletekey uninsdeletevalue
; The plug-in bundle finds the core package through this variable, and the
; bundle does not carry it.  It only reaches the processes that are started
; after the install, so the host has to be started again.
Root: {#RegRoot}; Subkey: "Environment"; ValueType: expandsz; ValueName: "AURALIS_ROOT"; ValueData: "{app}"; Flags: uninsdeletevalue
Root: {#RegRoot}; Subkey: "Software\Auralis"; ValueType: string; ValueName: "Scope"; ValueData: "{#Scope}"; Flags: uninsdeletekey uninsdeletevalue
; --- the plug-in bundle directories ----------------------------------------
; The paths are the only source of truth: the runtime resolves them from here
; and refuses to guess when they are missing.  VST3 points at the directory
; the host scans; AU and AAX point inside the application, because they have
; no system wide location yet.
Root: {#RegRoot}; Subkey: "Software\Auralis\PluginPaths"; ValueType: string; ValueName: "VST3"; ValueData: "{#Vst3Dir}"; Flags: uninsdeletekey uninsdeletevalue
Root: {#RegRoot}; Subkey: "Software\Auralis\PluginPaths"; ValueType: string; ValueName: "AU"; ValueData: "{app}\plugins\au"; Flags: uninsdeletekey uninsdeletevalue
Root: {#RegRoot}; Subkey: "Software\Auralis\PluginPaths"; ValueType: string; ValueName: "AAX"; ValueData: "{app}\plugins\aax"; Flags: uninsdeletekey uninsdeletevalue
; --- the plug-in formats ---------------------------------------------------
; 1 = the format is installed and enabled, 0 = it is present but disabled.
Root: {#RegRoot}; Subkey: "Software\Auralis\PluginFormats"; ValueType: string; ValueName: "VST3"; ValueData: "1"; Flags: uninsdeletekey uninsdeletevalue
Root: {#RegRoot}; Subkey: "Software\Auralis\PluginFormats"; ValueType: string; ValueName: "AU"; ValueData: "0"; Flags: uninsdeletekey uninsdeletevalue
Root: {#RegRoot}; Subkey: "Software\Auralis\PluginFormats"; ValueType: string; ValueName: "AAX"; ValueData: "0"; Flags: uninsdeletekey uninsdeletevalue
; --- the session document association --------------------------------------
Root: {#RegRoot}; Subkey: "Software\Classes\.{#DocumentExtension}"; ValueType: string; ValueName: ""; ValueData: "{#DocumentProgId}"; Flags: uninsdeletekey uninsdeletevalue
Root: {#RegRoot}; Subkey: "Software\Classes\{#DocumentProgId}"; ValueType: string; ValueName: ""; ValueData: "Auralis Session Document"; Flags: uninsdeletekey uninsdeletevalue
Root: {#RegRoot}; Subkey: "Software\Classes\{#DocumentProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\bin\auralis.cmd,0"; Flags: uninsdeletekey uninsdeletevalue
Root: {#RegRoot}; Subkey: "Software\Classes\{#DocumentProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\bin\auralis.cmd"" ""%1"""; Flags: uninsdeletekey uninsdeletevalue

[Icons]
Name: "{group}\Auralis"; Filename: "{app}\bin\auralis.cmd"; WorkingDir: "{app}"; Comment: "Auralis - audio restoration workbench"
Name: "{group}\Auralis (server only)"; Filename: "{app}\bin\auralis-server.cmd"; WorkingDir: "{app}"; Comment: "Start the Auralis server without opening a browser"
Name: "{group}\Documentation"; Filename: "{app}\docs"; Comment: "The Auralis documentation and the module reference"
Name: "{autodesktop}\Auralis"; Filename: "{app}\bin\auralis.cmd"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\bin\auralis.cmd"; Description: "{cm:LaunchProgram,Auralis}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The application's own plug-in layer root and the core library next to it.
; The directory the host scans is not in this list: it is shared with the
; plug-ins of other products and stays.  The bundles of this family are
; removed from it by the uninstall code.
Type: filesandordirs; Name: "{app}\plugins"
Type: filesandordirs; Name: "{app}\lib"

[Code]
; ---------------------------------------------------------------------------
;  Runtime support.
;
;  The rule that runs through this section: the plug-in bundle directories are
;  read back from the registry.  When a path cannot be resolved the installer
;  refuses to continue instead of falling back to a guess.
; ---------------------------------------------------------------------------

const
  VendorKey = 'Software\Auralis';
  PluginPathKey = 'Software\Auralis\PluginPaths';
  PluginFormatKey = 'Software\Auralis\PluginFormats';
  ExtensionKey = 'Software\Classes\.{#DocumentExtension}';
  ProgIdKey = 'Software\Classes\{#DocumentProgId}';
  Formats: array[0..2] of String = ('{#FormatVst3}', '{#FormatAu}', '{#FormatAax}');
  { The prefix every bundle of this family carries.  The uninstall removes the
    bundles with this prefix from the shared plug-in directory and leaves the
    bundles of other products alone. }
  BundlePrefix = 'Auralis ';

{ The registry root this installer owns.  The scope is fixed when the
  installer is compiled, so a per-user install never writes into the machine
  hive and the other way round. }
function VendorRoot: TRegistryKey;
begin
  if LowerCase('{#Scope}') = 'machine' then
    Result := HKLM
  else
    Result := HKCU;
end;

{ Write the plug-in bundle directories into the registry.  These values are
  the only place the paths live; everything else reads them back. }
procedure RegisterPluginPaths;
begin
  RegWriteStringValue(VendorRoot, PluginPathKey, '{#FormatVst3}',
    ExpandConstant('{#Vst3Dir}'));
  RegWriteStringValue(VendorRoot, PluginPathKey, '{#FormatAu}',
    ExpandConstant('{app}\plugins\au'));
  RegWriteStringValue(VendorRoot, PluginPathKey, '{#FormatAax}',
    ExpandConstant('{app}\plugins\aax'));
end;

{ Read one plug-in bundle directory from the registry.  An empty result means
  "not registered" - the caller must not substitute a path of its own.

  The value is read the way it is written: a value named after the format
  under the plug-in path key.  Reading it from a subkey of its own would
  find nothing, and the install would stop with "not registered". }
function ReadPluginPath(const Format: String): String;
var
  Path: String;
begin
  Result := '';
  if not RegQueryStringValue(VendorRoot, PluginPathKey, Format, Path) then
    Exit;
  Result := Trim(Path);
end;

{ Is the format enabled?  A format can be present and disabled.  The flag is
  read the way it is written: a value named after the format under the plug-in
  format key. }
function PluginEnabled(const Format: String): Boolean;
var
  Flag: String;
begin
  Result := False;
  if not RegQueryStringValue(VendorRoot, PluginFormatKey, Format, Flag) then
    Exit;
  Result := (Trim(Flag) = '1');
end;

{ The destination of the plug-in bundles.  The path comes from the registry;
  when it is not there the install is refused rather than guessed. }
function PluginBundleDir(const Format: String): String;
var
  Path: String;
begin
  Path := ReadPluginPath(Format);
  if Path = '' then
    RaiseException('The ' + Format + ' plug-in directory is not registered. '
      + 'The installer will not guess a path.');
  Result := Path;
end;

{ The directory the shared runtime of the family lives in: the directory
  the bundles go into, with the name the plug-in looks for.  It is empty
  when the plug-in directory is not registered - the caller must not
  substitute a path of its own. }
function RuntimeDirPath: String;
var
  Path: String;
begin
  Result := '';
  Path := ReadPluginPath('{#FormatVst3}');
  if Path = '' then
    Exit;
  Result := Path + '\' + '{#RuntimeDirName}';
end;

{ The destination of the shared runtime.  It sits next to the bundles,
  because that is where the plug-in looks for it.  The path comes from the
  registry; when it is not there the install is refused rather than
  guessed. }
function PluginRuntimeDir(const Format: String): String;
var
  Path: String;
begin
  Path := RuntimeDirPath;
  if Path = '' then
    RaiseException('The ' + Format + ' plug-in directory is not registered. '
      + 'The installer will not guess a path.');
  Result := Path;
end;

{ Remove the shared runtime of this family.  The directory holds the core
  and the interpreter of this product only, so it goes with them; the
  directory the host scans stays, it belongs to every product that
  installs plug-ins. }
procedure RemoveRuntimeDir(const Path: String);
begin
  if Path = '' then
    Exit;
  if not DirExists(Path) then
    Exit;
  DelTree(Path, True, True, False);
end;

{ Remove the bundles of this family from a shared plug-in directory.  The
  directory belongs to every product that installs plug-ins, so only the
  bundles whose name carries the family prefix are removed and the directory
  itself is left in place. }
procedure RemovePluginBundles(const Path: String);
var
  FindRec: TFindRec;
  Found: Integer;
  Name: String;
  Full: String;
begin
  if Path = '' then
    Exit;
  if not DirExists(Path) then
    Exit;

  { The name of a found item is the name of the bundle; the path of it is
    the directory the host scans plus that name. }
  Found := FindFirst(Path + '\*.vst3', faAnyFile, FindRec);
  try
    while Found = 0 do
    begin
      Name := FindRec.Name;
      Full := Path + '\' + Name;
      if (Pos(Uppercase(BundlePrefix), Uppercase(Name)) = 1) and
         DirExists(Full) then
        DelTree(Full, True, True, False);
      Found := FindNext(FindRec);
    end;
  finally
    FindClose(FindRec);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  I: Integer;
begin
  Result := '';
  NeedsRestart := False;

  { The bundle directories are registered before a single file is copied: the
    file destinations are resolved from the registry. }
  RegisterPluginPaths;

  { Every registered directory is created up front, so the registry never
    points at a path that does not exist. }
  CreateDir(ExpandConstant('{app}\plugins'));
  CreateDir(ExpandConstant('{app}\plugins\au'));
  CreateDir(ExpandConstant('{app}\plugins\aax'));
  CreateDir(ExpandConstant('{#Vst3Dir}'));

  { The shared runtime goes next to the bundles, so it is created there
    too. }
  CreateDir(RuntimeDirPath);

  { An enabled format that cannot be resolved stops the install. }
  for I := 0 to 2 do
  begin
    if PluginEnabled(Formats[I]) then
    begin
      if ReadPluginPath(Formats[I]) = '' then
      begin
        Result := 'The ' + Formats[I] + ' plug-in directory could not be '
          + 'resolved from the registry.';
        Exit;
      end;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  I: Integer;
  Format: String;
  Path: String;
begin
  if CurStep <> ssPostInstall then
    Exit;

  { Verify the registration: every enabled format must resolve, through the
    registry, to a directory that exists. }
  for I := 0 to 2 do
  begin
    Format := Formats[I];
    if PluginEnabled(Format) then
    begin
      Path := ReadPluginPath(Format);
      if Path = '' then
        RaiseException('The ' + Format + ' plug-in directory is not registered. '
          + 'The installer will not guess a path.');
      if not DirExists(Path) then
        RaiseException('The registered ' + Format + ' plug-in directory does '
          + 'not exist: ' + Path);
    end;
  end;
end;

{ Remove the registry keys this installer created.  The scope decides which
  hive is touched: a per-user uninstall must never remove a machine-wide
  installation. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Scope: String;
begin
  if CurUninstallStep <> usUninstall then
    Exit;

  { The scope is the one the installer was compiled for.  A key that belongs
    to the other scope is left alone. }
  Scope := '';
  if RegQueryStringValue(VendorRoot, VendorKey, 'Scope', Scope) then
    if LowerCase(Scope) <> LowerCase('{#Scope}') then
      Exit;

  { The bundles go first: they live in a directory the host scans, and the
    directory is shared with the plug-ins of other products.  Only the
    bundles of this family are removed. }
  RemovePluginBundles(ReadPluginPath('{#FormatVst3}'));

  { The shared runtime goes with them: it holds the core and the
    interpreter of this family and nothing else. }
  RemoveRuntimeDir(RuntimeDirPath);

  { Deepest first: a key with subkeys cannot simply be deleted. }
  RegDeleteKey(VendorRoot, 'Software\Classes\{#DocumentProgId}\DefaultIcon');
  RegDeleteKey(VendorRoot, 'Software\Classes\{#DocumentProgId}\shell\open\command');
  RegDeleteKey(VendorRoot, 'Software\Classes\{#DocumentProgId}\shell\open');
  RegDeleteKey(VendorRoot, 'Software\Classes\{#DocumentProgId}\shell');
  RegDeleteKey(VendorRoot, ProgIdKey);
  RegDeleteKey(VendorRoot, ExtensionKey);
  RegDeleteKey(VendorRoot, PluginPathKey);
  RegDeleteKey(VendorRoot, PluginFormatKey);
  RegDeleteKey(VendorRoot, VendorKey);
end;
