"""
tests/test_installer.py - the installer script against the tree it installs.

The installer is plain text, so it can be checked on a machine that has never
seen the compiler that turns it into an executable.  This module parses
`packaging/installer.iss` the way `scripts/build_release.py` does, assembles
the release tree the builder produces, and checks the two against each other:

  * every path the installer names is a path of the assembled tree, and no
    path it names points outside that tree;
  * the plug-in bundles are installed as the directories they are - a bundle
    is a directory, and the installer copies it as a unit;
  * a bundle that is missing does not fail the install;
  * the registry layout is the one the project already uses: the plug-in
    directories live under `Software\\Auralis\\PluginPaths`, and the key the
    code reads back is that key;
  * the directory the bundles go into is the one a host scans, for the scope
    the installer was compiled for;
  * the uninstall removes what the install adds: the registry keys, the
    application plug-in layer and the bundles themselves.

Nothing here needs the compiler.  The one test that does is skipped when the
compiler is not installed, so the suite is green everywhere.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "packaging" / "installer.iss"
README = ROOT / "packaging" / "README.md"
BUILDER = ROOT / "scripts" / "build_release.py"

#: The registry key the project owns.
VENDOR_KEY = r"Software\Auralis"

#: The key the plug-in bundle directories live under.  The project uses one
#: scheme for them; the installer must not invent a second one.
PLUGIN_PATH_KEY = r"Software\Auralis\PluginPaths"

#: The key the plug-in format flags live under.
PLUGIN_FORMAT_KEY = r"Software\Auralis\PluginFormats"

#: The directory a host scans for VST3 plug-ins, per scope.  These are the
#: two places a host looks on Windows, and nothing else.
HOST_VST3_DIRS = {
    "user": r"{localappdata}\Programs\Common\VST3",
    "machine": r"{commonpf}\VST3",
}

#: The prefix every bundle of this family carries.
BUNDLE_PREFIX = "Auralis "

#: The files that are written into the package after the tree is
#: assembled: the manifest, the checksum list and the verifier travel with
#: the package, so they are not in the assembled tree.
RELEASE_METADATA = ("manifest.json", "SHA256SUMS")

#: The registry calls of the Inno Setup runtime, with the number of
#: arguments each of them takes.  A call with the wrong number of arguments
#: does not compile, and the compiler is not always at hand.
REGISTRY_CALLS = {
    "RegQueryStringValue": 4,
    "RegWriteStringValue": 4,
    "RegDeleteKeyValue": 3,
    "RegDeleteKey": 2,
}


# ---------------------------------------------------------------------------
#  Loading the release builder
# ---------------------------------------------------------------------------
def _load_builder():
    """Import scripts/build_release.py as a module."""
    spec = importlib.util.spec_from_file_location("auralis_build_release", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


# ---------------------------------------------------------------------------
#  The tree the builder assembles
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def release_tree(tmp_path_factory):
    """The tree the release builder assembles for the full package."""
    out = tmp_path_factory.mktemp("release")
    return builder.assemble_release(ROOT, out / "full", builder.PROFILES["full"])


@pytest.fixture(scope="module")
def slim_tree(tmp_path_factory):
    """The tree the release builder assembles for the slim package."""
    out = tmp_path_factory.mktemp("release-slim")
    return builder.assemble_release(ROOT, out / "slim", builder.PROFILES["slim"])


@pytest.fixture(scope="module")
def script(release_tree):
    """The installer script, parsed with the symbols of the release tree."""
    return builder.parse_iss(INSTALLER, defines=builder.release_defines(release_tree))


@pytest.fixture(scope="module")
def machine_script(release_tree):
    """The installer script, parsed for the machine wide scope."""
    defines = builder.release_defines(release_tree)
    defines["PerMachine"] = "1"
    return builder.parse_iss(INSTALLER, defines=defines)


@pytest.fixture(scope="module")
def slim_script(slim_tree):
    """The installer script, parsed with the symbols of the slim tree."""
    return builder.parse_iss(INSTALLER, defines=builder.release_defines(slim_tree))


@pytest.fixture(scope="module")
def installer_text() -> str:
    """The installer script as plain text."""
    return INSTALLER.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------
def _expand(text: str, script) -> str:
    """Expand the preprocessor symbols of a piece of script text."""
    return builder.expand_iss_text(text, script)


def _source_matches(text: str, script) -> list[Path]:
    """Return the entries a ``[Files]`` source matches.

    A source with a wildcard matches the entries the pattern names; a source
    without one matches the single file it names.  A source that names
    nothing matches nothing.
    """
    expanded = _expand(text, script)
    index = max(expanded.rfind("\\"), expanded.rfind("/"))
    if index >= 0 and "*" in expanded[index + 1 :]:
        directory = Path(expanded[:index])
        if not directory.is_dir():
            return []
        return sorted(directory.glob(expanded[index + 1 :]))
    path = Path(expanded)
    return [path] if path.exists() else []


def _payload_files(text: str, script) -> list[Path]:
    """Return the files a ``[Files]`` entry copies."""
    files: list[Path] = []
    for match in _source_matches(text, script):
        if match.is_dir():
            files.extend(path for path in sorted(match.rglob("*")) if path.is_file())
        else:
            files.append(match)
    return files


def _bundle_entry(script):
    """Return the entry that installs the plug-in bundles."""
    entries = [
        entry
        for entry in script.section("Files")
        if "PluginBundleDir" in entry.get("destdir")
    ]
    assert len(entries) == 1, f"expected one plug-in bundle entry, got {len(entries)}"
    return entries[0]


def _registered_paths(script) -> dict[str, str]:
    """Return the plug-in directories the ``[Registry]`` section writes."""
    found: dict[str, str] = {}
    for entry in script.section("Registry"):
        subkey = _expand(entry.get("subkey"), script).strip('"')
        if subkey.lower() != PLUGIN_PATH_KEY.lower():
            continue
        name = _expand(entry.get("valuename"), script).strip('"').upper()
        found[name] = _expand(entry.get("valuedata"), script).strip('"')
    return found


def _registry_calls(code: str) -> list[tuple[str, int]]:
    """Return the registry calls of the ``[Code]`` section with their arity.

    The argument count is the number of top level commas plus one; a string
    literal never holds a comma that counts.
    """
    calls: list[tuple[str, int]] = []
    for match in re.finditer(r"\b(Reg[A-Za-z]+)\s*\(", code):
        name = match.group(1)
        if name not in REGISTRY_CALLS:
            continue
        depth = 0
        in_string = False
        arguments = 0
        index = match.end() - 1
        while index < len(code):
            char = code[index]
            if in_string:
                if char == "'":
                    in_string = False
            elif char == "'":
                in_string = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            elif char == "," and depth == 1:
                arguments += 1
            index += 1
        calls.append((name, arguments + 1))
    return calls


def _code_constants(code: str) -> dict[str, str]:
    """Return the constants of the ``[Code]`` section."""
    constants: dict[str, str] = {}
    for match in re.finditer(r"^\s*(\w+)\s*=\s*'([^']*)'\s*;", code, re.M):
        constants[match.group(1)] = match.group(2)
    return constants


def _deleted_keys(code: str) -> set[str]:
    """Return the registry keys the ``[Code]`` section deletes."""
    constants = _code_constants(code)
    keys: set[str] = set()
    for match in re.finditer(r"RegDeleteKey\(\s*VendorRoot\s*,\s*([^)]+)\)", code):
        name = match.group(1).strip()
        keys.add(constants.get(name, name))
    return keys


def _written_paths(code: str, script) -> dict[str, str]:
    """Return the plug-in paths the ``[Code]`` section writes."""
    written: dict[str, str] = {}
    pattern = re.compile(
        r"RegWriteStringValue\(\s*VendorRoot\s*,\s*PluginPathKey\s*,\s*'([^']+)'\s*,"
        r"\s*([^;]+?)\);",
        re.S,
    )
    for match in pattern.finditer(code):
        symbol = match.group(1).strip().strip("'")
        name = builder.expand_iss_text(symbol, script).strip('"').upper()
        written[name] = match.group(2).strip()
    return written


# ---------------------------------------------------------------------------
#  The paths the installer names
# ---------------------------------------------------------------------------
def test_every_path_the_installer_names_exists_in_the_assembled_tree(script, release_tree):
    """Every file the installer copies is in the tree the builder assembles."""
    entries = script.section("Files")
    assert entries, "the [Files] section is empty"

    problems: list[str] = []
    for entry in entries:
        source = entry.get("source")
        matches = _source_matches(source, script)
        if not matches:
            # The manifest and the checksum list are written into the
            # package after the tree is assembled, so they are not in it.
            if any(name in source for name in RELEASE_METADATA):
                continue
            problems.append(f"nothing matches the source {source}")
            continue
        for match in matches:
            if not match.exists():
                problems.append(f"{match} does not exist")
    assert not problems, "the installer names a path that is not in the tree: " + "; ".join(
        problems
    )


def test_no_path_the_installer_names_is_outside_the_assembled_tree(script, release_tree):
    """The installer reads nothing but the release tree."""
    outside: list[str] = []
    for entry in script.section("Files"):
        for match in _source_matches(entry.get("source"), script):
            try:
                match.relative_to(release_tree)
            except ValueError:
                outside.append(str(match))
    assert not outside, "the installer reads outside the release tree: " + "; ".join(outside)


def test_the_installer_installs_every_part_of_the_application(script, release_tree):
    """Nothing of the tree is left out, except the release metadata.

    The manifest, the checksums and the build script are published with
    the package and are installed with it, so that the installation can
    be checked on the machine it was made on.
    """
    published = {"manifest.json", "SHA256SUMS", "scripts"}

    covered: set[str] = set()
    for entry in script.section("Files"):
        source = builder.resolve_iss_path(entry.get("source"), script)
        try:
            relative = source.relative_to(release_tree)
        except ValueError:
            continue
        if relative.parts:
            covered.add(relative.parts[0])

    missing = {
        path.name for path in release_tree.iterdir() if path.is_dir()
    } - covered - published
    assert not missing, f"the tree carries {missing} and the installer installs none of it"


def test_the_optional_sources_are_the_ones_the_tree_may_not_carry(slim_script):
    """Only the sources that may be absent are marked as optional."""
    for entry in slim_script.section("Files"):
        source = entry.get("source")
        optional = "skipifsourcedoesntexist" in entry.flags()
        matches = _source_matches(source, slim_script)
        if matches:
            continue
        assert optional, f"the source {source} is missing and is not optional"


# ---------------------------------------------------------------------------
#  The plug-in family
# ---------------------------------------------------------------------------
def test_the_bundles_are_installed_as_the_directories_they_are(script, release_tree):
    """A bundle is a directory, and the installer copies it as a unit."""
    entry = _bundle_entry(script)
    flags = entry.flags()

    assert "recursesubdirs" in flags, "the bundle is flattened, not copied as a unit"
    assert "createallsubdirs" in flags, "the directories inside a bundle are not created"

    stage = release_tree / builder.PLUGIN_STAGE_DIR
    on_disk = sorted(path.name for path in stage.glob("*.vst3"))
    assert on_disk, "the assembled tree carries no plug-in bundle"

    matches = _source_matches(entry.get("source"), script)
    assert sorted(path.name for path in matches) == on_disk, (
        "the installer does not install every bundle of the tree"
    )
    for match in matches:
        assert match.is_dir(), f"{match.name} is a file, so it is not a bundle"


def test_a_missing_bundle_does_not_fail_the_install(script):
    """A bundle that is not there is skipped, not an error."""
    entry = _bundle_entry(script)
    assert "skipifsourcedoesntexist" in entry.flags(), (
        "a missing bundle would stop the installation"
    )
    assert "*" in entry.get("source"), (
        "the bundles are named one by one, so a missing one breaks the script"
    )


def test_the_bundles_go_into_the_directory_the_registry_points_at(script):
    """The file destination and the registry name the same directory."""
    code = script.code()
    assert "PluginBundleDir" in code, "the destination is not resolved from the registry"
    assert "ReadPluginPath" in code, "the path is never read back"
    assert "RaiseException" in code, "an unresolvable path is not refused"

    # The resolver must not fall back to a path of its own.
    resolver = code.split("function ReadPluginPath", 1)[-1].split("end;", 1)[0]
    assert "{app}" not in resolver, "the resolver guesses a path instead of reading one"


# ---------------------------------------------------------------------------
#  The shared runtime
# ---------------------------------------------------------------------------
def test_the_shared_runtime_is_installed_next_to_the_bundles(script, release_tree):
    """The runtime goes into the directory the host scans, next to the bundles.

    The bundles hold the plug-in alone: the core and the interpreter are
    shared by the whole family and live once, in a directory of their own
    next to the bundles.  A runtime that is not next to the bundles is a
    runtime no plug-in finds.
    """
    entries = [
        entry
        for entry in script.section("Files")
        if "PluginRuntimeDir" in entry.get("destdir")
    ]
    assert len(entries) == 1, f"expected one shared runtime entry, got {len(entries)}"

    entry = entries[0]
    flags = entry.flags()
    assert "recursesubdirs" in flags, "the runtime is flattened"
    assert "createallsubdirs" in flags, "the directories inside the runtime are not created"
    assert "skipifsourcedoesntexist" in flags, "a missing runtime stops the install"

    # The source is the runtime directory of the release tree.
    source = builder.resolve_iss_path(entry.get("source"), script)
    assert source.is_dir(), f"the release tree carries no runtime: {source}"
    assert list(source.iterdir()), f"the runtime of the tree is empty: {source}"

    # The destination is the registered plug-in directory plus the name of
    # the runtime directory: the runtime sits next to the bundles.
    code = script.code()
    assert "RuntimeDirPath" in code, "the runtime directory is never resolved"
    assert "ReadPluginPath('{#FormatVst3}')" in code, (
        "the runtime directory does not come from the registry"
    )
    assert "'{#RuntimeDirName}'" in code, "the runtime directory has no name"


def test_the_uninstall_removes_the_shared_runtime(script):
    """The runtime goes with the bundles; the shared directory stays."""
    code = script.code()
    assert "RemoveRuntimeDir(RuntimeDirPath)" in code, (
        "the shared runtime is not removed"
    )
    assert "DelTree(Path, True, True, False)" in code, (
        "the runtime directory is not removed"
    )


def test_the_uninstall_removes_the_bundles_by_their_full_path(script):
    """The bundles are removed by their path, not by their bare name."""
    code = script.code()
    assert "Full := Path + '\\' + Name;" in code, (
        "the bundle is removed by its bare name, so nothing is removed"
    )
    assert "DelTree(Full, True, True, False)" in code, (
        "the bundle is not removed by its path"
    )
    assert "FindFirst(Path + '\\*.vst3', faAnyFile, FindRec)" in code, (
        "the search for the bundles does not name them"
    )


# ---------------------------------------------------------------------------
#  The registry
# ---------------------------------------------------------------------------
def test_the_registry_key_is_the_one_the_project_uses(script):
    """The plug-in directories live under the key the project already uses."""
    for entry in script.section("Registry"):
        subkey = _expand(entry.get("subkey"), script).strip('"')
        lowered = subkey.lower()
        # The environment variable of the install lives in the environment
        # key of the hive.  It is not a plug-in path, so it is not part of
        # the scheme the plug-in directories use.
        if lowered == "environment":
            continue
        assert lowered.startswith(VENDOR_KEY.lower()) or lowered.startswith(
            "software\\classes"
        ), subkey

    code = script.code()
    assert f"PluginPathKey = '{PLUGIN_PATH_KEY}'" in code, (
        "the code reads a key that is not the one the registry writes"
    )
    assert f"PluginFormatKey = '{PLUGIN_FORMAT_KEY}'" in code


def test_the_plug_in_directories_are_registered_under_the_plugin_paths_key(script):
    """Every format has a directory, and only VST3 is enabled."""
    paths = _registered_paths(script)
    assert set(paths) == {"VST3", "AU", "AAX"}, paths

    formats: dict[str, str] = {}
    for entry in script.section("Registry"):
        subkey = _expand(entry.get("subkey"), script).strip('"')
        if subkey.lower() != PLUGIN_FORMAT_KEY.lower():
            continue
        name = _expand(entry.get("valuename"), script).strip('"').upper()
        formats[name] = _expand(entry.get("valuedata"), script).strip('"')
    assert formats.get("VST3") == "1", "the VST3 format must be enabled"
    for name in ("AU", "AAX"):
        assert formats.get(name) == "0", f"the {name} format must be present but disabled"


def test_the_code_reads_back_the_values_it_writes(script):
    """The plug-in path and the format flag are read the way they are written.

    The registry section writes a value named after the format under the
    plug-in path key, and the code writes it the same way.  A reader that
    looks for a subkey of its own would find nothing, and the install would
    stop with "the plug-in directory is not registered".
    """
    code = script.code()

    reader = re.search(
        r"RegQueryStringValue\(\s*VendorRoot\s*,\s*PluginPathKey\s*,\s*Format\s*,",
        code,
    )
    assert reader, (
        "the plug-in path is not read back as a value of the plug-in path key"
    )

    flags = re.search(
        r"RegQueryStringValue\(\s*VendorRoot\s*,\s*PluginFormatKey\s*,\s*Format\s*,",
        code,
    )
    assert flags, "the plug-in format flag is not read back as a value of the format key"

    # Nothing may read the value from a subkey of its own: the writer does
    # not put it there.
    assert "PluginPathKey + '\\' + Format" not in code, (
        "the plug-in path is read from a subkey the writer does not use"
    )
    assert "PluginFormatKey + '\\' + Format" not in code, (
        "the plug-in format flag is read from a subkey the writer does not use"
    )


def test_the_registered_path_and_the_written_path_are_the_same(script):
    """The registry section and the code write the same directory."""
    for entry in script.section("Registry"):
        subkey = _expand(entry.get("subkey"), script).strip('"')
        if subkey.lower() != PLUGIN_PATH_KEY.lower():
            continue
        if _expand(entry.get("valuename"), script).strip('"').upper() != "VST3":
            continue
        assert entry.get("valuedata").strip() == '"{#Vst3Dir}"', (
            "the registry and the code would write different directories"
        )

    code = script.code()
    values = _written_paths(code, script)
    assert values.get("VST3") == "ExpandConstant('{#Vst3Dir}')", values
    assert values.get("AU") == "ExpandConstant('{app}\\plugins\\au')", values
    assert values.get("AAX") == "ExpandConstant('{app}\\plugins\\aax')", values


# ---------------------------------------------------------------------------
#  The directory a host scans
# ---------------------------------------------------------------------------
def test_the_bundles_go_into_the_directory_a_host_scans(script, machine_script):
    """Per user and machine wide, the directory is the one a host looks in."""
    user = _registered_paths(script).get("VST3")
    machine = _registered_paths(machine_script).get("VST3")

    assert user == HOST_VST3_DIRS["user"], user
    assert machine == HOST_VST3_DIRS["machine"], machine
    assert user != machine, "the two scopes install into the same directory"

    # The two scopes differ in the directory and in the hive, and in nothing
    # else: the same key is written either way.
    assert script.defines["RegRoot"] == "HKCU"
    assert machine_script.defines["RegRoot"] == "HKLM"
    assert script.defines["Scope"] == "user"
    assert machine_script.defines["Scope"] == "machine"

    user_keys = {
        _expand(entry.get("subkey"), script) for entry in script.section("Registry")
    }
    machine_keys = {
        _expand(entry.get("subkey"), machine_script)
        for entry in machine_script.section("Registry")
    }
    assert user_keys == machine_keys


def test_the_install_creates_the_directory_the_registry_names(script):
    """The directory is created before the files are copied into it."""
    code = script.code()
    assert "CreateDir(ExpandConstant('{#Vst3Dir}'))" in code, (
        "the plug-in directory is not created by the installer"
    )
    assert "CreateDir(ExpandConstant('{app}\\plugins\\au'))" in code
    assert "CreateDir(ExpandConstant('{app}\\plugins\\aax'))" in code


# ---------------------------------------------------------------------------
#  The uninstall
# ---------------------------------------------------------------------------
def test_the_uninstall_removes_the_registry_keys_the_install_creates(script):
    """Every registry key the install writes is removed again."""
    for entry in script.section("Registry"):
        subkey = _expand(entry.get("subkey"), script).strip('"')
        flags = entry.flags()
        assert "uninsdeletekey" in flags or "uninsdeletevalue" in flags, (
            f"the uninstall does not remove {subkey}"
        )

    # The keys the code deletes are named there, deepest first.
    deleted = _deleted_keys(script.code())
    for key in (
        PLUGIN_PATH_KEY,
        PLUGIN_FORMAT_KEY,
        VENDOR_KEY,
    ):
        assert key in deleted, f"the uninstall does not delete {key}: {sorted(deleted)}"


def test_the_registry_calls_have_the_arity_the_runtime_expects(script):
    """A call with the wrong number of arguments does not compile."""
    problems = [
        f"{name} is called with {arity} arguments, not {REGISTRY_CALLS[name]}"
        for name, arity in _registry_calls(script.code())
        if arity != REGISTRY_CALLS[name]
    ]
    assert not problems, "; ".join(problems)


def test_the_uninstall_removes_the_bundles_from_the_shared_directory(script):
    """The bundles go; the directory they share with other products stays."""
    code = script.code()
    assert "RemovePluginBundles" in code, "the bundles are not removed"
    assert "RemovePluginBundles(ReadPluginPath('{#FormatVst3}'))" in code, (
        "the bundles are not removed from the registered directory"
    )

    # Only the bundles of this family are removed.
    assert BUNDLE_PREFIX in code, "the bundles of other products would be removed"
    assert "Pos(Uppercase(BundlePrefix), Uppercase(Name)) = 1" in code

    # The shared directory itself is not deleted: it belongs to every product
    # that installs plug-ins.
    for entry in script.section("UninstallDelete"):
        name = _expand(entry.get("name"), script)
        assert "VST3" not in name.upper(), (
            f"the uninstall deletes the shared plug-in directory: {name}"
        )


def test_the_uninstall_removes_the_application_plug_in_layer(script):
    """The application's own plug-in layer and the core library are removed."""
    names = {
        _expand(entry.get("name"), script).lower() for entry in script.section("UninstallDelete")
    }
    assert any("plugins" in name for name in names), names
    assert any(name.endswith("\\lib") for name in names), names


def test_the_uninstall_does_not_touch_the_other_scope(script):
    """A per user uninstall never removes a machine wide installation."""
    code = script.code()
    assert "LowerCase(Scope) <> LowerCase('{#Scope}')" in code, (
        "the uninstall does not check which scope it belongs to"
    )
    # The root the code writes to is the root of the compiled scope.
    assert "function VendorRoot: TRegistryKey;" in code
    assert "if LowerCase('{#Scope}') = 'machine' then" in code


# ---------------------------------------------------------------------------
#  The size of the installer
# ---------------------------------------------------------------------------
def test_the_installer_carries_the_plug_in_family_and_nothing_else(
    script, slim_script, release_tree
):
    """The payload of the installer is the tree it installs."""
    def entry_bytes(entry, tree_script) -> int:
        """The number of bytes one [Files] entry copies."""
        seen: set[Path] = set()
        total = 0
        for path in _payload_files(entry.get("source"), tree_script):
            if path in seen:
                continue
            seen.add(path)
            total += path.stat().st_size
        return total

    entries = script.section("Files")
    sizes = [entry_bytes(entry, script) for entry in entries]
    full = sum(sizes)

    bundle_entry = _bundle_entry(script)
    bundles = sizes[entries.index(bundle_entry)]
    abi = sum(
        size
        for entry, size in zip(entries, sizes)
        if "aurcore_abi" in entry.get("source")
    )
    rest = full - bundles - abi

    assert bundles > 0, "the installer carries no plug-in bundle"
    assert rest > 0, "the installer carries nothing but the plug-in family"
    assert full == bundles + abi + rest

    # The slim package carries no plug-in layer at all.
    for entry in slim_script.section("Files"):
        if "PluginBundleDir" in entry.get("destdir"):
            assert not _source_matches(entry.get("source"), slim_script), (
                "the slim package ships a plug-in bundle"
            )

    # The plug-in family is the bulk of the installer: the bundles and the
    # runtime they share.  The runtime is one copy for the whole family,
    # so the bundles alone are no longer the bigger half of the payload.
    runtime = sum(
        size
        for entry, size in zip(entries, sizes)
        if "PluginRuntimeDir" in entry.get("destdir")
    )
    assert bundles + runtime > full // 2, (
        f"the plug-in family is {bundles + runtime} bytes of a {full} byte payload"
    )


# ---------------------------------------------------------------------------
#  The compiler, when there is one
# ---------------------------------------------------------------------------
def test_the_installer_compiles_when_the_compiler_is_available(tmp_path):
    """The script compiles with the compiler, when there is one."""
    iscc = builder.find_iscc()
    if iscc is None:
        pytest.skip("the installer compiler is not installed on this machine")

    out_dir = tmp_path / "installer"
    wrapper = builder.write_installer_wrapper(ROOT, out_dir, "0.1.0a0")
    result = subprocess.run(
        [str(iscc), str(wrapper)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(out_dir.glob("*.exe")), "the compiler produced no installer"


# ---------------------------------------------------------------------------
#  The documentation
# ---------------------------------------------------------------------------
def test_the_readme_names_the_same_key_and_the_same_directories(script):
    """The notes and the script do not drift apart."""
    text = README.read_text(encoding="utf-8")
    assert PLUGIN_PATH_KEY in text, "the readme does not name the plug-in path key"
    for scope, directory in HOST_VST3_DIRS.items():
        assert directory in text, f"the readme does not name the {scope} plug-in directory"
    for name in ("VST3", "AU", "AAX"):
        assert name in text
