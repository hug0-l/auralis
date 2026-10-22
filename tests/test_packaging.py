"""
tests/test_packaging.py - the packaging and release pipeline.

Nothing in this module needs Inno Setup and nothing needs the network.  The
installer script is read as plain text, the release builder is driven through
its Python interface, and every artefact is produced in a temporary directory.

The three things that are checked here are the ones that break silently:

  * the installer script is well formed - balanced sections, source paths that
    exist in the repository, and an uninstall that removes every registry key
    the install creates;
  * the release builder produces a manifest whose hashes match the files it
    describes;
  * the size budget is reported against the targets.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "packaging" / "installer.iss"
BUILDER = ROOT / "scripts" / "build_release.py"

#: Paths that are produced by a build rather than checked in.  A source path
#: below one of them may legitimately be missing.
GENERATED_PREFIXES = (
    "var/",
    "bin/",
    "build/",
    "dist/",
    "plugins/abi/build/",
    "plugins/vst3/build/",
)

#: The files that are written into the package after the tree is assembled:
#: the manifest and the checksum list travel with the package, so they are
#: not in the repository either.
RELEASE_METADATA = ("manifest.json", "SHA256SUMS")

#: The sections the installer script must declare.
REQUIRED_SECTIONS = ("Setup", "Languages", "Tasks", "Files", "Registry", "Icons", "Run", "Code")

#: The registry subkey the installer owns.
VENDOR_KEY = "Software\\Auralis"


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


@pytest.fixture(scope="module")
def script():
    """The parsed installer script."""
    return builder.parse_iss(INSTALLER)


@pytest.fixture(scope="module")
def installer_text() -> str:
    """The installer script as plain text."""
    return INSTALLER.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
#  The installer script
# ---------------------------------------------------------------------------
def test_the_installer_script_exists():
    """The installer script is part of the repository."""
    assert INSTALLER.is_file(), f"the installer script is missing: {INSTALLER}"
    assert INSTALLER.stat().st_size > 2000, "the installer script looks empty"


def test_the_installer_sections_are_balanced(script):
    """Every entry sits in a section and no section is declared twice."""
    assert script.sections, "the script declares no section at all"

    seen: set[str] = set()
    for name in script.sections:
        lowered = name.lower()
        assert lowered not in seen, f"the section [{name}] is declared twice"
        seen.add(lowered)

    for name in REQUIRED_SECTIONS:
        assert script.has_section(name), f"the section [{name}] is missing"

    # An entry outside a section would be silently dropped by the compiler.
    for entry in script.entries:
        assert entry.section, "an entry sits outside any section"


def test_every_source_path_exists_in_the_repository(script):
    """Every file the installer copies is either in the tree or a build artefact."""
    entries = script.section("Files")
    assert entries, "the [Files] section is empty"

    for entry in entries:
        source = entry.get("source")
        assert source, f"a [Files] entry has no source: {entry.raw}"
        path = builder.resolve_iss_path(source, script)
        optional = "skipifsourcedoesntexist" in entry.flags()

        if path.exists():
            continue

        assert optional, (
            f"the source path {path} does not exist and the entry is not optional"
        )
        relative = path.relative_to(ROOT).as_posix()
        if any(name in relative for name in RELEASE_METADATA):
            # The manifest and the checksum list are written into the
            # package after the tree is assembled.
            continue
        assert any(
            relative.startswith(prefix) for prefix in GENERATED_PREFIXES
        ), f"the source path {relative} is neither in the tree nor a build artefact"


def test_the_installer_copies_the_whole_application(script):
    """The application, the resources, the help, the presets and the ABI library are installed."""
    sources = {
        builder.resolve_iss_path(entry.get("source"), script).name.lower()
        for entry in script.section("Files")
    }
    for expected in ("aurui", "aurcore", "aurserver", "assets", "docs", "builtin"):
        assert expected in sources, f"nothing is installed from {expected}"


def test_the_uninstall_removes_the_registry_keys_the_install_creates(script):
    """Every registry key the install writes is removed again on uninstall."""
    registry_entries = script.section("Registry")
    assert registry_entries, "the [Registry] section is empty"

    code = script.code()
    deleted_by_code: set[str] = set()
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("regdeletekeyvalue"):
            continue
        _, _, argument = stripped.partition("(")
        argument = argument.split(")")[0]
        parts = [part.strip() for part in argument.split(",")]
        if len(parts) < 2:
            continue
        key = parts[1].strip().strip("'")
        deleted_by_code.add(key.replace("'", ""))

    deleted_keys = {key.lower() for key in deleted_by_code}
    removed_values: set[tuple[str, str]] = set()
    removed_keys: set[str] = set()

    for entry in registry_entries:
        subkey = builder.expand_iss_text(entry.get("subkey"), script).strip('"')
        flags = entry.flags()
        if "uninsdeletekey" in flags:
            removed_keys.add(subkey.lower())
        if "uninsdeletevalue" in flags:
            removed_values.add((subkey.lower(), entry.get("valuename").strip('"').lower()))

    def covered(subkey: str, valuename: str) -> bool:
        """Is the key or the value removed by the uninstall?"""
        lowered = subkey.lower()
        name = valuename.lower()
        if (lowered, name) in removed_values:
            return True
        if lowered in removed_keys:
            return True
        for key in removed_keys:
            if lowered.startswith(key + "\\"):
                return True
        for key in deleted_keys:
            if lowered == key.lower() or lowered.startswith(key.lower() + "\\"):
                return True
        return False

    for entry in registry_entries:
        subkey = builder.expand_iss_text(entry.get("subkey"), script).strip('"')
        valuename = entry.get("valuename").strip('"')
        assert covered(subkey, valuename), (
            f"the uninstall does not remove {subkey} / {valuename}"
        )


def test_the_machine_wide_build_uses_the_machine_hive(script):
    """The per-machine variant installs machine wide and registers the same keys."""
    machine = builder.parse_iss(INSTALLER, defines={"PerMachine": "1"})

    assert machine.defines["RegRoot"] == "HKLM"
    assert machine.defines["Scope"] == "machine"
    assert machine.defines["Privileges"] == "admin"
    assert "{autopf}" in machine.defines["DefaultDir"]

    assert script.defines["RegRoot"] == "HKCU"
    assert script.defines["Scope"] == "user"
    assert script.defines["Privileges"] == "lowest"
    assert "{localappdata}" in script.defines["DefaultDir"]

    # The same keys are registered either way; only the hive differs.
    user_keys = {
        builder.expand_iss_text(entry.get("subkey"), script)
        for entry in script.section("Registry")
    }
    machine_keys = {
        builder.expand_iss_text(entry.get("subkey"), machine)
        for entry in machine.section("Registry")
    }
    assert user_keys == machine_keys


def test_the_installer_registers_the_plugin_formats(script):
    """The plug-in bundle directories are registered, and only VST3 is enabled."""
    formats = {
        builder.expand_iss_text(entry.get("valuename"), script).strip('"').upper(): builder
        .expand_iss_text(entry.get("valuedata"), script)
        .strip('"')
        for entry in script.section("Registry")
        if builder.expand_iss_text(entry.get("subkey"), script).endswith("PluginFormats")
    }
    assert formats, "no plug-in format is registered"
    assert formats.get("VST3") == "1", "the VST3 format must be enabled"
    for name in ("AU", "AAX"):
        assert name in formats, f"the {name} format is not registered at all"
        assert formats[name] == "0", f"the {name} format must be present but disabled"

    paths = {
        builder.expand_iss_text(entry.get("valuename"), script).strip('"').upper()
        for entry in script.section("Registry")
        if builder.expand_iss_text(entry.get("subkey"), script).endswith("PluginPaths")
    }
    assert {"VST3", "AU", "AAX"} <= paths, "not every format has a bundle directory"


def test_the_plugin_directories_are_resolved_from_the_registry(installer_text):
    """The bundle directories are read back from the registry, never guessed."""
    code = installer_text.split("[Code]", 1)[-1]
    assert "RegQueryStringValue" in code, "the registry is never read"
    assert "PluginPathKey" in code, "the plug-in path key is never used"
    assert "ReadPluginPath" in code, "the plug-in path is never resolved"
    assert "RaiseException" in code, "an unresolvable path is not refused"

    # The resolver must not fall back to a hard coded directory.
    resolver = code.split("function ReadPluginPath", 1)[-1].split("end;", 1)[0]
    assert "{app}" not in resolver, "the resolver guesses a path instead of reading one"


def test_the_installer_supports_a_silent_install_and_a_repair(script, installer_text):
    """The installer can be run silently and can repair an existing installation."""
    setup = {
        entry.raw.split("=", 1)[0].strip().lower(): entry.raw
        for entry in script.section("Setup")
    }

    assert "appid" in setup, "the installer has no stable AppId, so it cannot repair"
    app_id = setup["appid"].split("=", 1)[1].strip()
    assert app_id.startswith("{{") and app_id.endswith("}"), app_id
    assert app_id.count("-") == 4, "the AppId is not a version independent GUID"

    assert "privilegesrequired" in setup
    assert "wizardstyle" in setup

    # A message box that is not suppressible would hang a silent install.
    for line in installer_text.splitlines():
        if "MsgBox(" in line and "SuppressibleMsgBox" not in line:
            pytest.fail(f"a message box would block a silent install: {line.strip()}")


def test_the_installer_removes_its_own_shortcuts_and_files(script):
    """The uninstall section removes the plug-in bundles it installed."""
    entries = script.section("UninstallDelete")
    assert entries, "the uninstall removes nothing"
    names = {builder.expand_iss_text(entry.get("name"), script) for entry in entries}
    assert any("plugins" in name for name in names), names


def test_the_installer_creates_the_shortcuts_and_the_file_association(script):
    """The start menu entries and the session document association are declared."""
    icons = script.section("Icons")
    assert icons, "no shortcut is created"
    names = " ".join(entry.get("name") for entry in icons)
    assert "{group}" in names, "no start menu entry is created"

    association = [
        entry
        for entry in script.section("Registry")
        if builder.expand_iss_text(entry.get("subkey"), script).lower().startswith(
            "software\\classes"
        )
    ]
    assert association, "the session document format is not associated"
    subkeys = " ".join(
        builder.expand_iss_text(entry.get("subkey"), script).lower() for entry in association
    )
    assert ".aurdoc" in subkeys, "the session document extension is not registered"
    assert "shell\\open\\command" in subkeys, "the association has no open command"


def test_the_installer_compiles_when_the_compiler_is_available(tmp_path):
    """The script compiles with the Inno Setup compiler, when there is one."""
    iscc = builder.find_iscc()
    if iscc is None:
        pytest.skip("the Inno Setup compiler is not installed")

    out_dir = tmp_path / "installer"
    wrapper = builder.write_installer_wrapper(ROOT, out_dir, "0.1.0a0")
    result = subprocess.run(
        [str(iscc), str(wrapper)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(out_dir.glob("*.exe")), "the compiler produced no installer"


# ---------------------------------------------------------------------------
#  The release builder
# ---------------------------------------------------------------------------
def test_the_release_builder_writes_a_manifest_that_matches_the_files(tmp_path):
    """Every file in the manifest is hashed correctly."""
    tree = tmp_path / "release"
    (tree / "aurcore").mkdir(parents=True)
    (tree / "aurui" / "css").mkdir(parents=True)
    (tree / "aurcore" / "api.py").write_bytes(b"print('hello')\n")
    (tree / "aurui" / "index.html").write_bytes(b"<html></html>")
    (tree / "aurui" / "css" / "app.css").write_bytes(b"body { color: #fff; }")

    manifest = builder.build_manifest(tree, profile="full", version="9.9.9")
    path = builder.write_manifest(manifest, tree / "manifest.json")

    assert path.is_file()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["format"] == builder.MANIFEST_FORMAT
    assert written["profile"] == "full"
    assert written["release_version"] == "9.9.9"
    assert written["file_count"] == 3
    assert written["total_size"] == sum(
        (tree / entry["path"]).stat().st_size for entry in written["files"]
    )

    for entry in written["files"]:
        assert len(entry["sha256"]) == 64, entry
        assert entry["sha256"] == entry["sha256"].lower()

    assert builder.verify_manifest(tree, manifest) == []

    # The manifest that is read back describes the same tree.
    reloaded = builder.read_manifest(path)
    assert reloaded.entries == manifest.entries


def test_the_manifest_notices_a_changed_file(tmp_path):
    """A file that changed after the manifest was written is reported."""
    tree = tmp_path / "release"
    tree.mkdir()
    target = tree / "a.txt"
    target.write_bytes(b"one")
    manifest = builder.build_manifest(tree, profile="full", version="1.0.0")
    target.write_bytes(b"two")
    problems = builder.verify_manifest(tree, manifest)
    assert problems, "a changed file was not noticed"
    assert any("mismatch" in problem for problem in problems)


def test_the_release_builder_assembles_the_repository(tmp_path):
    """The assembled tree carries the application, the presets and the launcher."""
    tree = builder.assemble_release(
        ROOT, tmp_path / "full", builder.PROFILES["full"]
    )

    for expected in ("aurui", "aurcore", "aurserver", "assets", "docs", "bin"):
        assert (tree / expected).is_dir(), f"{expected} is missing from the release tree"

    assert (tree / "bin" / "auralis.cmd").is_file(), "the launcher is missing"
    assert (tree / "var" / "presets" / "builtin").is_dir(), "the preset library is missing"

    # Nothing that is only a development artefact is shipped.
    assert not list(tree.rglob("__pycache__")), "byte code was shipped"
    assert not list(tree.rglob("*.pyc")), "byte code was shipped"

    manifest = builder.build_manifest(tree, profile="full", version="0.1.0a0")
    assert builder.verify_manifest(tree, manifest) == []
    assert manifest.file_count > 20
    assert manifest.total_size > 0


def test_the_slim_profile_leaves_the_plugin_layer_out(tmp_path):
    """The slim package has no plug-in bundles and no ABI library."""
    full = builder.assemble_release(ROOT, tmp_path / "full", builder.PROFILES["full"])
    slim = builder.assemble_release(ROOT, tmp_path / "slim", builder.PROFILES["slim"])

    assert (full / "plugins").is_dir()
    assert not (slim / "plugins").exists(), "the slim package ships the plug-in layer"

    full_manifest = builder.build_manifest(full, profile="full", version="0.1.0a0")
    slim_manifest = builder.build_manifest(slim, profile="slim", version="0.1.0a0")
    assert slim_manifest.total_size < full_manifest.total_size


def test_the_release_tree_matches_the_installer_script(tmp_path):
    """The installer script is the contract for the release tree."""
    tree = builder.assemble_release(ROOT, tmp_path / "full", builder.PROFILES["full"])
    assert builder.verify_installer_sources(tree) == []

    # A tree that lost a directory the installer needs is reported.
    import shutil

    shutil.rmtree(tree / "aurui")
    problems = builder.verify_installer_sources(tree)
    assert problems, "a missing directory was not noticed"
    assert any("aurui" in problem for problem in problems)


def test_the_installer_wrapper_points_at_the_release_tree(tmp_path):
    """The generated wrapper carries the build specific symbols."""
    release = tmp_path / "release"
    release.mkdir()
    wrapper = builder.write_installer_wrapper(release, tmp_path / "installer", "1.2.3")
    text = wrapper.read_text(encoding="utf-8")

    assert '#define SourceRoot "' in text
    assert str(release).replace("\\", "/") in text
    assert '#define MyAppVersion "1.2.3"' in text
    assert '#define MyAppVersionNumeric "1.2.3.0"' in text
    assert "#include" in text

    machine = builder.write_installer_wrapper(
        release, tmp_path / "installer", "1.2.3", per_machine=True
    )
    assert "#define PerMachine 1" in machine.read_text(encoding="utf-8")


def test_the_size_budget_is_reported():
    """The budget report compares the measured size with the target."""
    manifests = {
        "full": builder.Manifest(
            release_version="0.1.0a0",
            profile="full",
            created_utc="2026-01-01T00:00:00Z",
            entries=[
                builder.ManifestEntry(path="a", size=10 * 1024 * 1024, sha256="0" * 64),
            ],
        ),
        "slim": builder.Manifest(
            release_version="0.1.0a0",
            profile="slim",
            created_utc="2026-01-01T00:00:00Z",
            entries=[
                builder.ManifestEntry(path="a", size=1024, sha256="0" * 64),
            ],
        ),
    }

    lines = builder.budget_report(manifests)
    assert [line.profile for line in lines] == ["full", "slim"]

    full = next(line for line in lines if line.profile == "full")
    assert full.budget == 700 * 1024 * 1024
    assert full.measured == 10 * 1024 * 1024
    assert full.ok
    assert full.headroom == full.budget - full.measured
    assert full.verdict == "PASS"

    slim = next(line for line in lines if line.profile == "slim")
    assert slim.budget == 145 * 1024 * 1024
    assert slim.ok

    report = builder.format_budget_report(lines)
    assert "SIZE BUDGET" in report
    assert "full" in report and "slim" in report
    assert "PASS" in report
    assert "700.0 MiB" in report
    assert "145.0 MiB" in report


def test_a_package_over_its_budget_is_reported_as_such():
    """A package that is too big is reported, not silently accepted."""
    oversized = builder.Manifest(
        release_version="0.1.0a0",
        profile="slim",
        created_utc="2026-01-01T00:00:00Z",
        entries=[builder.ManifestEntry(path="a", size=200 * 1024 * 1024, sha256="0" * 64)],
    )
    line = builder.budget_report({"slim": oversized})[0]
    assert not line.ok
    assert line.verdict == "OVER"
    assert "OVER" in builder.format_budget_report([line])


def test_the_build_refuses_to_run_when_a_check_fails(tmp_path):
    """A failing check stops the build."""
    results = builder.run_checks(
        tmp_path,
        commands=(
            ("ok", ("-c", "print('fine')"), True),
            ("broken", ("-c", "raise SystemExit(3)"), True),
        ),
    )
    assert [result.name for result in results] == ["ok", "broken"]
    assert results[0].ok
    assert not results[1].ok

    ok, failures = builder.check_gate(results)
    assert not ok, "the gate let a failing check through"
    assert [failure.name for failure in failures] == ["broken"]


def test_a_skipped_check_does_not_stop_the_build():
    """A check that is skipped is not a failure."""
    results = builder.run_checks(
        ROOT,
        commands=(("optional", ("-c", "print('skipped')"), False),),
    )
    ok, failures = builder.check_gate(results)
    assert ok
    assert not failures


def test_the_check_suite_is_the_one_the_project_documents():
    """The gate runs the schema validator, the CSS check and the ABI tests."""
    names = [name for name, _, _ in builder.CHECK_COMMANDS]
    assert names == ["schema", "css-coverage", "plugin-abi"]
    commands = {name: argv for name, argv, _ in builder.CHECK_COMMANDS}
    assert commands["schema"][0] == "tools/validate_schema.py"
    assert commands["css-coverage"][0] == "qa/check_css_coverage.py"
    assert "tests/test_plugin_abi.py" in commands["plugin-abi"]
    assert all(required for _, _, required in builder.CHECK_COMMANDS)


def test_the_zip_is_written_and_readable(tmp_path):
    """The zip contains the release tree."""
    tree = tmp_path / "release"
    (tree / "aurcore").mkdir(parents=True)
    (tree / "aurcore" / "api.py").write_bytes(b"x = 1\n")
    zip_path = builder.make_zip(tree, tmp_path / "out.zip", arcname="full")
    assert zip_path.is_file()
    import zipfile

    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
    assert "full/aurcore/api.py" in names


# ---------------------------------------------------------------------------
#  The plug-in layer
# ---------------------------------------------------------------------------
def _make_bundle(root: Path, name: str = "Auralis De-Click.vst3", size: int = 4096) -> Path:
    """Create a fake but realistic VST3 bundle folder.

    The layout is the one a Windows build produces: the bundle is a folder
    and the module itself lives inside it.
    """
    bundle = Path(root) / name
    inner = bundle / "Contents" / "x86_64-win"
    inner.mkdir(parents=True, exist_ok=True)
    (inner / name).write_bytes(b"\x00" * size)
    return bundle


def _bundle_size(bundle: Path) -> int:
    """Return the size of a bundle folder, in bytes."""
    return sum(path.stat().st_size for path in bundle.rglob("*") if path.is_file())


def test_the_builder_finds_the_built_plugins_in_the_build_tree(tmp_path):
    """The search covers the build trees of the plug-in builds."""
    root = tmp_path / "repo"
    cmake = root / "build" / "vst3" / "Auralis_AUR_DECLICK_artefacts" / "Release" / "VST3"
    _make_bundle(cmake)
    native = root / "plugins" / "vst3native" / "build"
    native.mkdir(parents=True)
    (native / "Auralis Native.vst3").write_bytes(b"\x00" * 128)

    roots = builder.plugin_search_roots(root)
    assert roots, "the build trees are not searched at all"

    bundles = builder.find_plugin_bundles(root)
    names = {bundle.name for bundle in bundles}
    assert "Auralis De-Click.vst3" in names, names
    assert "Auralis Native.vst3" in names, names

    folder = next(b for b in bundles if b.name == "Auralis De-Click.vst3")
    assert folder.is_folder, "a bundle folder is not recognised as a bundle"


def test_an_empty_bundle_folder_is_not_a_module(tmp_path):
    """The empty folder a failed plug-in build leaves behind is not a module."""
    root = tmp_path / "repo"
    empty = root / "build" / "vst3" / "Artefacts" / "Release" / "VST3" / "Auralis De-Click.vst3"
    (empty / "Contents" / "x86_64-win").mkdir(parents=True)

    assert builder.find_plugin_bundles(root) == [], "an empty folder was taken for a module"


def test_the_plugins_dir_option_overrides_the_search(tmp_path):
    """--plugins-dir replaces the search instead of adding to it."""
    root = tmp_path / "repo"
    _make_bundle(root / "build" / "vst3")

    override = tmp_path / "elsewhere"
    override.mkdir()
    (override / "Auralis Elsewhere.vst3").write_bytes(b"\x00" * 64)

    bundles = builder.find_plugin_bundles(root, override)
    assert [bundle.name for bundle in bundles] == ["Auralis Elsewhere.vst3"]

    # Without the override the build tree is searched.
    assert [b.name for b in builder.find_plugin_bundles(root)] == ["Auralis De-Click.vst3"]

    args = builder.build_parser().parse_args(["--plugins-dir", str(override)])
    assert args.plugins_dir == str(override)
    assert builder.build_parser().parse_args(["--plugins", str(override)]).plugins_dir == (
        str(override)
    )


def test_the_release_manifest_lists_the_plugin_files(tmp_path):
    """The manifest describes the plug-in bundles and the core ABI library."""
    plugins = tmp_path / "plugins-out"
    bundle = _make_bundle(plugins)

    tree = builder.assemble_release(
        ROOT, tmp_path / "full", builder.PROFILES["full"], plugins_dir=plugins
    )
    manifest = builder.build_manifest(tree, profile="full", version="0.1.0a0")
    assert builder.verify_manifest(tree, manifest) == []

    listed = {entry.path for entry in manifest.entries}
    inner = bundle.relative_to(plugins) / "Contents" / "x86_64-win" / bundle.name
    staged = f"{builder.PLUGIN_STAGE_DIR}/{inner.as_posix()}"
    assert staged in listed, sorted(listed)

    # The core C ABI library sits next to the application.
    library = builder.find_abi_library(ROOT)
    if library is not None:
        assert f"{builder.ABI_STAGE_DIR}/{library.name}" in listed, sorted(listed)


def test_the_size_budget_accounts_for_the_plugins(tmp_path):
    """The measured size of the package includes the plug-in bundles."""
    empty = tmp_path / "empty"
    empty.mkdir()
    plugins = tmp_path / "plugins-out"
    bundle = _make_bundle(plugins, size=64 * 1024)

    plain = builder.assemble_release(
        ROOT, tmp_path / "plain", builder.PROFILES["full"], plugins_dir=empty
    )
    loaded = builder.assemble_release(
        ROOT, tmp_path / "loaded", builder.PROFILES["full"], plugins_dir=plugins
    )

    plain_manifest = builder.build_manifest(plain, profile="full", version="0.1.0a0")
    loaded_manifest = builder.build_manifest(loaded, profile="full", version="0.1.0a0")

    assert loaded_manifest.total_size > plain_manifest.total_size
    assert loaded_manifest.total_size - plain_manifest.total_size == _bundle_size(bundle)

    line = builder.budget_report({"full": loaded_manifest})[0]
    assert line.measured == loaded_manifest.total_size
    assert line.budget == 700 * 1024 * 1024
    assert line.ok
    assert "SIZE BUDGET" in builder.format_budget_report([line])


def test_the_release_build_works_without_plugins(tmp_path, capsys):
    """A build without the plug-ins still succeeds and says so."""
    empty = tmp_path / "empty"
    empty.mkdir()

    layer = builder.PluginLayer()
    tree = builder.assemble_release(
        ROOT,
        tmp_path / "full",
        builder.PROFILES["full"],
        plugins_dir=empty,
        layer=layer,
    )

    assert not layer.bundles, "a bundle was staged out of an empty directory"
    report = builder.format_plugin_table(layer)
    assert "the plug-ins were not built" in report, report

    manifest = builder.build_manifest(tree, profile="full", version="0.1.0a0")
    assert builder.verify_manifest(tree, manifest) == []
    staged = tree / builder.PLUGIN_STAGE_DIR
    assert not list(staged.glob("*.vst3")), "a bundle was staged out of an empty directory"

    # The whole command still succeeds.
    code = builder.main(
        [
            "--profile",
            "full",
            "--out",
            str(tmp_path / "dist"),
            "--skip-checks",
            "--no-zip",
            "--no-installer",
            "--plugins-dir",
            str(empty),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "the plug-ins were not built" in printed, printed


def test_the_plugin_table_lists_what_was_staged(tmp_path, capsys):
    """The build prints a table of the plug-in layer it staged."""
    plugins = tmp_path / "plugins-out"
    _make_bundle(plugins, size=32 * 1024)

    code = builder.main(
        [
            "--profile",
            "full",
            "--out",
            str(tmp_path / "dist"),
            "--skip-checks",
            "--no-zip",
            "--no-installer",
            "--plugins-dir",
            str(plugins),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "PLUG-IN LAYER" in printed, printed
    assert "Auralis De-Click.vst3" in printed, printed
    assert builder.PLUGIN_STAGE_DIR in printed, printed
    assert "the plug-ins were not built" not in printed, printed


def test_the_installer_script_mentions_the_plugin_directory(script, installer_text):
    """The installer installs the bundle into the VST3 directory it registers."""
    bundle_entries = [
        entry
        for entry in script.section("Files")
        if "PluginBundleDir" in entry.get("destdir")
    ]
    assert bundle_entries, "nothing is installed into the plug-in directory"

    for entry in bundle_entries:
        source = builder.resolve_iss_path(entry.get("source"), script)
        assert source.name == "stamped", source
        assert "recursesubdirs" in entry.flags(), (
            "the bundle is not copied as a unit, so its layout is flattened"
        )
        assert "createallsubdirs" in entry.flags(), entry.raw

    # The directory the bundles go into is the one the registry points at.
    registered = {
        builder.expand_iss_text(entry.get("valuename"), script).strip('"').upper(): builder
        .expand_iss_text(entry.get("valuedata"), script)
        .strip('"')
        for entry in script.section("Registry")
        if builder.expand_iss_text(entry.get("subkey"), script).endswith("PluginPaths")
    }
    assert registered.get("VST3"), "the VST3 directory is not registered"

    code = installer_text.split("[Code]", 1)[-1]
    assert "PluginPathKey" in code, "the registry scheme is not reused"
    assert "RegisterPluginPaths" in code, "the directory is never registered"
    assert "plugins\\vst3" in installer_text.lower(), "the plug-in directory is not named"


def test_the_release_build_works_without_the_abi_library(tmp_path, monkeypatch):
    """A build without the core C ABI library still succeeds."""
    monkeypatch.setattr(builder, "find_abi_library", lambda root: None)

    layer = builder.PluginLayer()
    tree = builder.assemble_release(
        ROOT, tmp_path / "full", builder.PROFILES["full"], layer=layer
    )

    assert layer.abi is None, "an ABI library was staged out of thin air"
    assert not (tree / builder.ABI_STAGE_DIR).exists(), "the ABI library was staged"

    manifest = builder.build_manifest(tree, profile="full", version="0.1.0a0")
    assert builder.verify_manifest(tree, manifest) == []
    assert not any(entry.path.startswith("lib/") for entry in manifest.entries)


def test_the_slim_package_carries_no_plugin_layer(tmp_path):
    """The slim package has no plug-in directory at all."""
    plugins = tmp_path / "plugins-out"
    _make_bundle(plugins)

    layer = builder.PluginLayer()
    tree = builder.assemble_release(
        ROOT,
        tmp_path / "slim",
        builder.PROFILES["slim"],
        plugins_dir=plugins,
        layer=layer,
    )

    assert not layer.bundles, "the slim package staged a bundle"
    assert not (tree / "plugins").exists(), "the slim package ships the plug-in layer"
    assert not (tree / builder.ABI_STAGE_DIR).exists(), "the slim package ships the ABI library"

    report = builder.format_plugin_table(layer, profile=builder.PROFILES["slim"])
    assert "no plug-in layer" in report, report


# ---------------------------------------------------------------------------
#  Hygiene
# ---------------------------------------------------------------------------
def test_no_third_party_trademark_in_the_packaging_files():
    """The packaging files follow the naming rule of the contract.

    The two words are spelled in pieces on purpose: this file is part of the
    codebase, and the codebase must not contain a third party trademark even
    in the scanner that looks for one.
    """
    forbidden = ("r" + "x", "izo" + "tope")
    files = [
        INSTALLER,
        BUILDER,
        ROOT / "packaging" / "README.md",
        Path(__file__),
    ]
    for path in files:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, f"{path.name} mentions a third party trademark"
