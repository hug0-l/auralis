#!/usr/bin/env python3
"""
scripts/build_aax.py - build the Auralis AAX adapter.

WHAT THIS DOES
--------------
The AAX adapter is one template: which module a binary implements is
decided at build time by the identity record that is stamped into the
binary, and the page tables and the parameter list come from the generated
description.  This tool

  1. writes the generated description (AuralisAAXGenerated.h) from
     docs/aax_page_tables.json - the file scripts/aax_tables.py writes
     from the canonical parameter table, so the two plug-in formats cannot
     drift apart;
  2. compiles the SDK's own sources and the adapter into the plug-in;
  3. stamps the identity record of every module into its own copy of the
     template, which is the family the format expects;
  4. runs the probe, which reads the adapter back through the format's own
     interfaces and writes down what it finds.

The SDK is a separate download, so the build is told where it is: the
``--sdk`` switch, the ``AURALIS_AAX_SDK_PATH`` environment variable, or the
copy in the tree, in that order.  The SDK's import library is not part of the
download, so the SDK's own sources are compiled into the plug-in.  That is
what the framework's own client does as well.

USAGE
-----
    python scripts/build_aax.py                       # build the family
    python scripts/build_aax.py --no-stamp            # build the template only
    python scripts/build_aax.py --only AUR_DECLICK    # build one plug-in
    python scripts/build_aax.py --sdk PATH            # build against a copy
    python scripts/build_aax.py generate --out FILE   # write the description
    python scripts/build_aax.py check                 # verify the description

Exit codes: ``0`` success, ``1`` the build failed, ``2`` the tool could not
run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]

#: The directory of the adapter.
SOURCE = ROOT / "plugins" / "aax"

#: The build tree.
BUILD_ROOT = ROOT / "build" / "aax"

#: The generated description, in the machine readable half of the package.
PACKAGE = ROOT / "docs" / "aax_page_tables.json"

#: The generator of the package.  The tool checks that the package is not
#: drifted before it uses it, so a stale package cannot reach the binary.
PACKAGE_TOOL = ROOT / "scripts" / "aax_tables.py"

#: The identity layer, which owns the record that is stamped into a binary.
IDENTITY_MODULE = ROOT / "plugins" / "identity.py"

#: The copy of the SDK that is in the tree.  A copy that was downloaded
#: separately takes precedence over it; see ``sdk_path``.
SDK_IN_TREE = ROOT / "vendor" / "JUCE8" / "modules" / "juce_audio_plugin_client" / "AAX" / "SDK"

#: The environment variable that names a copy of the SDK outside the tree.
SDK_ENVIRONMENT = "AURALIS_AAX_SDK_PATH"

#: The command that runs a command inside the build-tools environment.
MSVC = ROOT / "tools" / "msvc.bat"

#: The channel configurations, and the channels of each of them.  The three
#: of them are the three effect ids of a module.
VARIANTS: tuple[tuple[str, int, int], ...] = (
    ("MONO", 1, 1),
    ("M2ST", 1, 2),
    ("STER", 2, 2),
)

#: The base the AAX parameter id is offset by.  It is the number the
#: application package publishes; see plugins/aax/README.md.
PARAM_ID_BASE = 256

#: The directory the bundles are staged in, relative to the build tree.
#: The format loads a plug-in from a bundle directory, so this is where the
#: shippable form of every plug-in of the family is put together.
BUNDLE_DIR = "bundles"


class BuildError(Exception):
    """Raised when the adapter cannot be built."""


# ---------------------------------------------------------------------------
# the tools
# ---------------------------------------------------------------------------
def tool(name: str) -> Path:
    """Return the path of a build tool.

    The tools are not on the PATH on this machine, so they are looked for
    next to the interpreter and in the local application directory.
    """
    candidates: list[Path] = []

    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        candidates.append(
            Path(local) / "Python" / "pythoncore-3.15-64" / "Scripts" / f"{name}.exe"
        )

    candidates.append(Path(sys.executable).parent / "Scripts" / f"{name}.exe")

    found = shutil.which(name)
    if found:
        candidates.append(Path(found))

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise BuildError(f"the build tool {name!r} was not found")


def run(command: list[str], cwd: Path = ROOT) -> tuple[int, str]:
    """Run a command and return its exit code and its output."""
    result = subprocess.run(command, capture_output=True, text=True, cwd=str(cwd))
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def run_in_build_environment(command: list[str], cwd: Path = ROOT) -> tuple[int, str]:
    """Run a command inside the Visual C++ build-tools environment."""
    if not MSVC.is_file():
        raise BuildError(f"the build-tools wrapper is missing: {MSVC}")

    return run(["cmd", "/c", str(MSVC)] + [str(part) for part in command], cwd)


# ---------------------------------------------------------------------------
# the SDK
# ---------------------------------------------------------------------------
def sdk_path(explicit: str = "") -> Path:
    """Return the SDK the build is made against.

    The SDK is a download, so the path is told to the build: the ``--sdk``
    switch first, the environment variable second, the copy in the tree
    third.  The path is never written down in this file - it is a property
    of the machine, not of the project.
    """
    if explicit.strip():
        return Path(explicit.strip()).resolve()

    from_environment = os.environ.get(SDK_ENVIRONMENT, "").strip()
    if from_environment:
        return Path(from_environment).resolve()

    return SDK_IN_TREE


def sdk_version(path: Path) -> str:
    """Return the version stamp of the SDK at ``path``.

    The stamp is the one the SDK publishes in its own version header: the
    revision of the binary interface and the release stamp of the sources.
    """
    header = path / "Interfaces" / "AAX_Version.h"

    if not header.is_file():
        return "unknown"

    text = header.read_text(encoding="utf-8", errors="replace")

    interface = ""
    revision = ""

    for line in text.splitlines():
        if "AAX_SDK_VERSION" in line and "AAX_SDK_VERSION (" in line:
            interface = line.split("(", 1)[1].split(")")[0].strip()
        if "AAX_SDK_CURRENT_REVISION" in line and "AAX_SDK_CURRENT_REVISION (" in line:
            revision = line.split("(", 1)[1].split(")")[0].strip()

    return f"interface {interface}, revision {revision}"


def check_sdk(path: Path) -> None:
    """Fail when the path does not hold an SDK."""
    if not (path / "Interfaces" / "AAX.h").is_file():
        raise BuildError(
            f"the SDK was not found at {path}\n"
            "Point --sdk, or the environment variable "
            f"{SDK_ENVIRONMENT}, at a copy of the SDK (the directory that "
            "holds Interfaces/ and Libs/)."
        )


# ---------------------------------------------------------------------------
# the generated description
# ---------------------------------------------------------------------------
def _identifier(text: str) -> str:
    """Return a C identifier built from a name."""
    out = "".join(character if character.isalnum() else "_" for character in text)
    if not out or not (out[0].isalpha() or out[0] == "_"):
        out = "a" + out
    return out


def _c_string(text: str) -> str:
    """Return a C string literal."""
    escaped = (
        str(text)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _c_number(value: Any) -> str:
    """Return a C literal for a number."""
    if value is None:
        return "0.0"
    if isinstance(value, bool):
        return "1.0" if value else "0.0"
    if isinstance(value, int):
        return f"{value}.0"
    return repr(float(value))


def load_package() -> dict[str, Any]:
    """Return the machine readable half of the application package."""
    if not PACKAGE.is_file():
        raise BuildError(f"the application package is missing: {PACKAGE}")

    return json.loads(PACKAGE.read_text(encoding="utf-8"))


def check_package() -> list[str]:
    """Return the problems the package generator reports, if any.

    The package has to be a fresh run of its generator, otherwise the
    adapter would be built from a stale description.
    """
    code, output = run([sys.executable, str(PACKAGE_TOOL), "--check"])
    if code == 0:
        return []

    return [line for line in output.splitlines() if line.strip()]


def render_description(package: dict[str, Any]) -> str:
    """Render the generated description.

    The description is plain data: the effect ids, the parameter list and
    the page tables of every module.  It is a pure function of the package,
    so the same package always produces the same header.
    """
    plug_ids: list[str] = list(package["plug_ids"])
    manufacturer: str = package["manufacturer_id"]

    lines: list[str] = []
    lines.append("/*")
    lines.append("    GENERATED FILE, DO NOT EDIT.")
    lines.append("")
    lines.append("    Generator:       scripts/build_aax.py")
    lines.append("    Source of truth: aurcore/schema/params.tsv")
    lines.append("    Through:         scripts/aax_tables.py -> docs/aax_page_tables.json")
    lines.append(f"    Source sha256:   {package['source_sha256']}")
    lines.append("")
    lines.append("    Regenerate with: python scripts/build_aax.py")
    lines.append("*/")
    lines.append("")
    lines.append("#ifndef AURALIS_AAX_GENERATED_H")
    lines.append("#define AURALIS_AAX_GENERATED_H")
    lines.append("")
    lines.append('#include "AuralisAAX.h"')
    lines.append("")

    # The name of the page table resource.  The plug-in declares it to the
    # host with AAX_eResourceType_PageTable, and the host looks for the
    # file under that name in Contents/Resources of the bundle, so the
    # declaration and the bundle cannot disagree about the name.
    page_table = package.get("page_table", {})
    lines.append("/** The name of the page table resource of the plug-in bundle. */")
    lines.append(
        "#define AURALIS_AAX_PAGE_TABLE_RESOURCE \"{}\"".format(
            page_table.get("resource", "")
        )
    )
    lines.append("")

    module_entries: list[str] = []

    for module in package["modules"]:
        tag = _identifier(module["code"])

        # -- the parameters of the module, in registry order ----------------
        #
        # The pages list the controls in the order they are laid out, and
        # the layout is not the registry order, so the parameters are
        # collected and sorted by their id: the id of a parameter is its
        # position inside its own module, and the plug-in has to carry the
        # parameters in that order.
        parameters_by_id: dict[int, dict[str, Any]] = {}

        for control in _controls_of(module):
            parameter_id = int(control["parameter"])
            parameters_by_id.setdefault(parameter_id, control)

        # -- the choices of the parameters --------------------------------
        choice_tables: dict[int, str] = {}

        for parameter_id in sorted(parameters_by_id):
            control = parameters_by_id[parameter_id]
            choices = list(control.get("choices") or [])
            if not choices:
                continue

            name = f"auralisAAXChoices_{tag}_{parameter_id}"
            values = ", ".join(_c_string(choice) for choice in choices)
            lines.append(f"static const char* const {name}[] = {{ {values}, nullptr }};")
            choice_tables[parameter_id] = name

        lines.append("")

        # -- the parameters ------------------------------------------------
        parameters: list[str] = []

        for parameter_id in sorted(parameters_by_id):
            control = parameters_by_id[parameter_id]

            choices = list(control.get("choices") or [])
            choices_expression = choice_tables.get(parameter_id, "nullptr")
            choice_count = len(choices)

            default = control.get("default")

            if control.get("type") == "enum":
                # The default of an enum is the text of the choice; the
                # parameter carries the index of the choice.
                if default in choices:
                    default_value = float(choices.index(default))
                else:
                    default_value = 0.0
            elif control.get("type") == "bool":
                default_value = 1.0 if default else 0.0
            else:
                default_value = float(default if default is not None else 0.0)

            parameters.append(
                "    {{ {id}, {name}, {label}, {type}, {group}, {unit}, "
                "{minimum}, {maximum}, {default}, {choices}, {count}, {help} }},".format(
                    id=parameter_id,
                    name=_c_string(control["name"]),
                    label=_c_string(control.get("label", control["name"])),
                    type=_c_string(control.get("type", "float")),
                    group=_c_string(control.get("group", "")),
                    unit=_c_string(control.get("unit", "")),
                    minimum=_c_number(control.get("minimum")),
                    maximum=_c_number(control.get("maximum")),
                    default=_c_number(default_value),
                    choices=choices_expression,
                    count=choice_count,
                    help=_c_string(control.get("help", "")),
                )
            )

        parameters_name = f"auralisAAXParameters_{tag}"
        lines.append(f"static const AuralisAAXParameter {parameters_name}[] = {{")
        lines.extend(parameters)
        lines.append("};")
        lines.append("")

        # -- the pages -----------------------------------------------------
        page_entries: list[str] = []

        for page in module["pages"]:
            controls: list[str] = []

            for control in page["controls"]:
                controls.append(
                    '    {{ {slot}, {parameter} }},'.format(
                        slot=_c_string(control["slot"]),
                        parameter=int(control["parameter"]),
                    )
                )

            controls_name = f"auralisAAXControls_{tag}_{page['index']}"
            lines.append(f"static const AuralisAAXControl {controls_name}[] = {{")
            lines.extend(controls)
            lines.append("};")
            lines.append("")

            page_entries.append(
                "    {{ {index}, {controls}, {count} }},".format(
                    index=int(page["index"]),
                    controls=controls_name,
                    count=len(controls),
                )
            )

        pages_name = f"auralisAAXPages_{tag}"
        lines.append(f"static const AuralisAAXPage {pages_name}[] = {{")
        lines.extend(page_entries)
        lines.append("};")
        lines.append("")

        # -- the module ----------------------------------------------------
        effect_ids = {entry["plug"]: entry["id"] for entry in module["effect_ids"]}

        plug_values = []
        variant_names = []

        for plug, inputs, outputs in VARIANTS:
            plug_values.append(_c_string(plug))
            variant_names.append(_c_string(effect_ids.get(plug, f"{manufacturer}:"
                                                          f"{module['product_id']}:{plug}")))

        module_entries.append(
            "    {{ {code}, {display}, {category}, {manufacturer}, {product}, "
            "{{ {plugs} }}, {{ {variants} }}, {{ {inputs} }}, {{ {outputs} }}, "
            "{parameters}, {parameter_count}, {pages}, {page_count}, "
            "{bypass_id}, {bypass_label}, {bypass_help} }},".format(
                code=_c_string(module["code"]),
                display=_c_string(module["display_name"]),
                category=_c_string(module["category"]),
                manufacturer=_c_string(manufacturer),
                product=_c_string(module["product_id"]),
                plugs=", ".join(plug_values),
                variants=", ".join(variant_names),
                inputs=", ".join(str(inputs) for _, inputs, _ in VARIANTS),
                outputs=", ".join(str(outputs) for _, _, outputs in VARIANTS),
                parameters=parameters_name,
                parameter_count=len(parameters),
                pages=pages_name,
                page_count=len(page_entries),
                bypass_id=_c_string(module.get("bypass", {}).get("id", "")),
                bypass_label=_c_string(module.get("bypass", {}).get("label", "")),
                bypass_help=_c_string(module.get("bypass", {}).get("help", "")),
            )
        )

    lines.append("static const AuralisAAXModule auralisAAXModules[] = {")
    lines.extend(module_entries)
    lines.append("};")
    lines.append("")
    lines.append(
        "const AuralisAAXFamily auralisAAXFamily = {{ {count}, auralisAAXModules }};".format(
            count=len(module_entries)
        )
    )
    lines.append("")
    lines.append("#endif // AURALIS_AAX_GENERATED_H")
    lines.append("")

    return "\n".join(lines)


def _controls_of(module: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the controls of a module, in the order of their pages.

    The package lists a control on the page it is bound to, and a
    parameter appears on exactly one page, so the list of the controls of
    the module is the list of the controls of its pages.
    """
    controls: list[dict[str, Any]] = []

    for page in module["pages"]:
        controls.extend(page["controls"])

    return controls


def write_description(out_path: Path, package: dict[str, Any]) -> Path:
    """Write the generated description."""
    text = render_description(package)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8", newline="\n")
    return out_path


# ---------------------------------------------------------------------------
# the build
# ---------------------------------------------------------------------------
def module_codes() -> list[str]:
    """Return the module codes of the family, in canonical order."""
    package = load_package()
    return [module["code"] for module in package["modules"]]


def configure(build_dir: Path, jobs: int, sdk: Path) -> tuple[int, str]:
    """Configure the build tree."""
    command = [
        tool("cmake"), "-S", SOURCE, "-B", build_dir, "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_MAKE_PROGRAM={tool('ninja')}",
        f"-DAURALIS_AAX_SDK_PATH={sdk}",
    ]
    return run_in_build_environment(command)


def build(build_dir: Path, jobs: int) -> tuple[int, str]:
    """Build the adapter."""
    command = [tool("cmake"), "--build", build_dir, "--parallel", str(jobs)]
    return run_in_build_environment(command)


def import_identity():
    """Return the identity layer, which owns the record that is stamped."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from plugins import identity  # noqa: PLC0415 - the owner of the record

    return identity


def import_generator():
    """Return the package generator, which owns the page table document."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import aax_tables  # noqa: PLC0415 - the owner of the document

    return aax_tables


def stamp_family(build_dir: Path, codes: Sequence[str]) -> list[Path]:
    """Stamp the identity record of every module into its own binary.

    The template is the binary the build produced; every module gets a
    copy of it with its own record in the reserved slot.
    """
    identity = import_identity()

    template = build_dir / "AuralisAAX.dll"
    if not template.is_file():
        template = build_dir / "AuralisAAX.so"
    if not template.is_file():
        template = build_dir / "AuralisAAX.dylib"
    if not template.is_file():
        raise BuildError("the build produced no plug-in to stamp")

    target_dir = build_dir / "plug-ins"
    target_dir.mkdir(parents=True, exist_ok=True)

    stamped: list[Path] = []

    for code in codes:
        record = identity.identity_for(code)
        name = record.display_name.replace(" ", "")
        target = target_dir / f"Auralis {name}.aaxplugin"
        identity.stamp_identity(template, target, record)
        stamped.append(target)

    return stamped


def bundle_name(code: str) -> str:
    """Return the name of the bundle of a module.

    The bundle is a directory, so the name of the module is used: it is
    written in the name of the file inside it as well, which is the
    arrangement the host looks for.
    """
    return code


def stage_bundles(build_dir: Path, codes: Sequence[str],
                  stamped: Sequence[Path]) -> list[Path]:
    """Stage the bundle of every module.

    The format loads a plug-in from a bundle directory, not from a file::

        <name>.aaxplugin\\
            Contents\\
                x64\\<name>.dll                  the plug-in
                Resources\\<page table>.xml      the page table resource

    The page table document is the one the package generator produced: it
    is copied out of the tree into the bundle, so a bundle cannot carry a
    hand-edited copy of it.
    """
    package = load_package()

    page_table = package["page_table"]
    resource = page_table["resource"]
    documents = ROOT / page_table["directory"]

    if not documents.is_dir():
        raise BuildError(f"the page table documents are missing: {documents}")

    target_root = build_dir / BUNDLE_DIR
    staged: list[Path] = []

    for code, binary in zip(codes, stamped):
        name = bundle_name(code)

        bundle = target_root / f"{name}.aaxplugin"
        binaries = bundle / "Contents" / "x64"
        resources = bundle / "Contents" / "Resources"

        binaries.mkdir(parents=True, exist_ok=True)
        resources.mkdir(parents=True, exist_ok=True)

        shutil.copy2(binary, binaries / f"{name}.dll")

        document = documents / f"{code}.xml"
        if not document.is_file():
            raise BuildError(f"the page table document of {code} is missing: {document}")

        shutil.copy2(document, resources / resource)

        staged.append(bundle)

    return staged


def check_bundles(build_dir: Path) -> list[str]:
    """Compare the page table documents in the bundles with the generator's.

    The bundle is what ships, so the document inside it has to be the one
    the package generator produced - not a copy that was edited by hand.
    """
    generator = import_generator()
    package = generator.build_package(ROOT)

    problems: list[str] = []

    try:
        published = load_package()["page_table"]["resource"]
    except Exception as exc:  # noqa: BLE001 - report, do not crash
        return [f"the application package does not name the page table resource: {exc}"]

    for module in package.modules:
        bundle = build_dir / BUNDLE_DIR / f"{bundle_name(module.code)}.aaxplugin"
        document = bundle / "Contents" / "Resources" / published

        # The plug-in inside the bundle has to carry the name of the bundle
        # directory: the host reads the file by that name, and a bundle
        # whose two names differ is one the host cannot load.
        binary = bundle / "Contents" / "x64" / f"{bundle_name(module.code)}.dll"
        if not binary.is_file():
            problems.append(f"{binary}: missing")
        else:
            for candidate in (bundle / "Contents" / "x64").glob("*.dll"):
                if candidate.stem != bundle_name(module.code):
                    problems.append(
                        f"{candidate}: does not carry the name of the bundle "
                        f"({bundle_name(module.code)})"
                    )

        if not document.is_file():
            problems.append(f"{document}: missing")
            continue

        expected = generator.render_page_table(package, module.code)

        if document.read_text(encoding="utf-8") != expected:
            problems.append(f"{document}: is not the document the generator produced")

    return problems


def run_probe(build_dir: Path, module: str, out_path: Path,
              identity_path: Path | None = None) -> tuple[int, str]:
    """Run the probe and write its report."""
    probe = build_dir / "aaxprobe.exe"
    if not probe.is_file():
        probe = build_dir / "aaxprobe"

    if not probe.is_file():
        return 1, "the build produced no probe"

    command = [str(probe), "--module", module, "--out", str(out_path)]

    if identity_path is not None and identity_path.is_file():
        command += ["--identity", str(identity_path)]

    return run(command)


def command_generate(args: argparse.Namespace) -> int:
    """Write the generated description."""
    problems = check_package()
    if problems:
        print("build_aax: the application package drifted:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    package = load_package()
    out = Path(args.out) if args.out else BUILD_ROOT / "generated" / "AuralisAAXGenerated.h"
    path = write_description(out, package)

    print(f"{path}  {len(package['modules'])} modules, "
          f"{package['totals']['parameters']} parameters, "
          f"{package['totals']['pages']} pages")
    return 0


def command_check(args: argparse.Namespace) -> int:
    """Verify that the generated description is a fresh run of the generator."""
    problems = check_package()
    if problems:
        print("build_aax: the application package drifted:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    package = load_package()
    expected = render_description(package)
    out = Path(args.out) if args.out else BUILD_ROOT / "generated" / "AuralisAAXGenerated.h"

    if not out.is_file():
        print(f"build_aax: {out} does not exist", file=sys.stderr)
        return 1

    if out.read_text(encoding="utf-8") != expected:
        print(f"build_aax: {out} drifted from a fresh run of the generator", file=sys.stderr)
        return 1

    print(f"build_aax: {out} is up to date")

    # The bundles: the page table document inside them has to be the one
    # the generator produced, not a copy that was edited by hand.
    bundles = check_bundles(BUILD_ROOT)
    if bundles:
        print("build_aax: the bundles are not what the generator produced:",
              file=sys.stderr)
        for problem in bundles:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(f"build_aax: the bundles in {BUILD_ROOT / BUNDLE_DIR} are up to date")
    return 0


def command_build(args: argparse.Namespace) -> int:
    """Build the adapter, stamp the family and run the probe."""
    build_dir = BUILD_ROOT
    build_dir.mkdir(parents=True, exist_ok=True)

    sdk = sdk_path(args.sdk)
    check_sdk(sdk)

    started = time.time()

    print(f"the SDK: {sdk}  ({sdk_version(sdk)})")

    code, output = configure(build_dir, args.jobs, sdk)
    if code != 0:
        print("build_aax: the configure failed:", file=sys.stderr)
        print(output[-4000:], file=sys.stderr)
        return 1

    code, output = build(build_dir, args.jobs)
    if code != 0:
        print("build_aax: the build failed:", file=sys.stderr)
        print(output[-4000:], file=sys.stderr)
        return 1

    elapsed = time.time() - started

    binary = build_dir / "AuralisAAX.dll"
    if not binary.is_file():
        binary = build_dir / "AuralisAAX.so"
    if not binary.is_file():
        binary = build_dir / "AuralisAAX.dylib"

    if binary.is_file():
        size = binary.stat().st_size
        print(f"the plug-in: {binary.relative_to(ROOT)}  {size} bytes  "
              f"({size / 1024.0:.1f} kB)  built in {elapsed:.1f}s")
    else:
        print(f"the build took {elapsed:.1f}s")

    if not args.no_stamp:
        codes = module_codes()
        if args.only:
            wanted = {code.strip() for code in args.only.split(",") if code.strip()}
            codes = [code for code in codes if code in wanted]

        stamped = stamp_family(build_dir, codes)
        print(f"stamped {len(stamped)} plug-in(s) into {stamped[0].parent.relative_to(ROOT)}")

        # The bundle of every module: the plug-in and the page table
        # document the host reads out of it.
        bundles = stage_bundles(build_dir, codes, stamped)
        print(f"staged {len(bundles)} bundle(s) into {(bundles[0].parent).relative_to(ROOT)}")

    module = args.module or module_codes()[0]
    report = build_dir / "aax_description.json"

    identity_path = None

    try:
        record = import_identity().identity_for(module)
    except Exception:  # noqa: BLE001 - the record is optional here
        record = None

    if record is not None:
        for candidate in sorted((build_dir / "plug-ins").glob("*.aaxplugin")):
            if candidate.stem.replace(" ", "") == record.display_name.replace(" ", ""):
                identity_path = candidate
                break

    code, output = run_probe(build_dir, module, report, identity_path)

    if code != 0:
        print("build_aax: the probe failed:", file=sys.stderr)
        print(output[-2000:], file=sys.stderr)
        return 1

    print(f"the report: {report.relative_to(ROOT)}  ({module})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        prog="build_aax",
        description="Build the Auralis AAX adapter against the SDK in the tree.",
    )
    parser.add_argument("--out", default="", help="where the generated description is written")
    parser.add_argument("--jobs", type=int, default=2, help="compiler jobs per build")
    parser.add_argument("--module", default="", help="the module the probe reports on")
    parser.add_argument("--only", default="",
                        help="a comma separated list of module codes to stamp")
    parser.add_argument("--no-stamp", action="store_true", help="do not stamp the family")
    parser.add_argument("--sdk", default="", help="the SDK the plug-in is built against")

    subparsers = parser.add_subparsers(dest="command")

    generate = subparsers.add_parser("generate", help="write the generated description")
    generate.add_argument("--out", default="", help="where the description is written")
    generate.set_defaults(handler=command_generate)

    check = subparsers.add_parser("check", help="verify the generated description")
    check.add_argument("--out", default="", help="the description that is checked")
    check.set_defaults(handler=command_check)

    build = subparsers.add_parser("build", help="build the adapter (the default)")
    build.add_argument("--out", default="", help="unused by the build")
    build.add_argument("--jobs", type=int, default=2, help="compiler jobs per build")
    build.add_argument("--module", default="", help="the module the probe reports on")
    build.add_argument("--only", default="", help="a comma separated list of module codes to stamp")
    build.add_argument("--no-stamp", action="store_true", help="do not stamp the family")
    build.add_argument("--sdk", default="", help="the SDK the plug-in is built against")
    build.set_defaults(handler=command_build)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the tool."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    handler = getattr(args, "handler", None)
    if handler is None:
        args.handler = command_build
        handler = command_build

    try:
        return handler(args)
    except BuildError as exc:
        print(f"build_aax: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
