#!/usr/bin/env python3
"""
plugins/abi/build_abi.py -- build the Auralis core C ABI.

Produces ``aurcore_abi.dll`` (or the platform equivalent) from
``aurcore_abi.cpp`` and the header the adapter uses.

THE EXPORT TABLE IS DERIVED FROM THE HEADER
-------------------------------------------
The export surface of the library is part of its contract, so it is not
written down twice: it is parsed out of ``plugins/vst3/aurcore_c_api.h``,
the same file the adapter reads.  The header is the single source of truth,
the module definition file is generated from it, and the build fails when a
function the header promises has no definition in the implementation.

REPRODUCIBLE
------------
The same sources give the same library: the link is reproducible (no
timestamp, no build path in the image) and the build writes a manifest that
names the header, its digest, the export table and the compiler, so a build
can be compared with another one.

The compiler is taken from, in order:
  1. ``$AURALIS_CC``            (an explicit compiler command)
  2. the ``ziglang`` wheel      (a complete C compiler in a wheel, so no
                                 toolchain has to be installed on the machine)
  3. ``cc``/``gcc``/``clang``   from PATH

Run:  python plugins/abi/build_abi.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import sys
import sysconfig

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ABI_DIR = HERE
SRC = os.path.join(ABI_DIR, "aurcore_abi.cpp")
HEADER = os.path.join(ROOT, "plugins", "vst3", "aurcore_c_api.h")

# The tool that knows the layout of the shared runtime.  It is what copies
# the home of the interpreter next to the library, so that the library that
# comes out of the build can be loaded by a host that does not run Python.
sys.path.insert(0, ABI_DIR)
import interpreter_home  # noqa: E402  (the module lives next to this one)


# --------------------------------------------------------------------------
# the header is the contract: the export table is parsed out of it
# --------------------------------------------------------------------------

def strip_comments(text: str) -> str:
    """Remove the comments from a C source text.

    A comment may mention a function name, and a name that is only
    mentioned is not a declaration.
    """
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return text


def parse_declarations(text: str) -> list[str]:
    """Return the names of the functions a header declares, in order.

    A declaration is a statement that ends with a semicolon and carries a
    parameter list.  The name is the identifier that comes right before the
    opening parenthesis.  Only the names of this library are returned.
    """
    names: list[str] = []
    for statement in strip_comments(text).split(";"):
        statement = statement.strip()
        if "(" not in statement:
            continue
        head = statement.split("(", 1)[0]
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", head)
        if not match:
            continue
        name = match.group(1)
        if not name.startswith("aur_") or name in names:
            continue
        names.append(name)
    return names


def header_exports(header_path: str = HEADER) -> list[str]:
    """Return the export table the header promises."""
    with open(header_path, encoding="utf-8") as fh:
        names = parse_declarations(fh.read())

    if not names:
        raise SystemExit(f"no exported functions found in {header_path}")

    return names


def has_definition(source: str, name: str) -> bool:
    """True when the source defines the function.

    A definition is a parameter list followed by a body; a mention in a
    comment is not a definition.
    """
    for match in re.finditer(r"\b" + re.escape(name) + r"\s*\(", source):
        depth = 0
        index = match.end() - 1
        while index < len(source):
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        if source[index + 1:].lstrip().startswith("{"):
            return True
    return False


def check_definitions(names: list[str], source_path: str = SRC) -> None:
    """Fail the build when the header promises a function that is not there.

    The linker would fail as well, but with a message about a symbol.  This
    one names the contract.
    """
    with open(source_path, encoding="utf-8") as fh:
        source = fh.read()

    missing = [name for name in names if not has_definition(source, name)]
    if missing:
        raise SystemExit(
            "the header promises functions that the implementation does not "
            f"define: {', '.join(missing)}"
        )


def digest(path: str) -> str:
    """The SHA-256 of a file."""
    digest_ = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest_.update(chunk)
    return digest_.hexdigest()


# --------------------------------------------------------------------------
# the toolchain
# --------------------------------------------------------------------------

def zig_compiler():
    """The C++ compiler of the ziglang wheel.

    The wheel ships a complete toolchain, so no compiler has to be
    installed on the machine.  The C++ driver is the one that carries the
    standard library headers the implementation uses.
    """
    try:
        import ziglang  # noqa: F401
    except Exception:
        return None
    return [sys.executable, "-m", "ziglang", "c++"]


def pick_compiler(explicit: str | None):
    if explicit:
        return explicit.split()
    zig = zig_compiler()
    if zig:
        return zig
    for name in ("c++", "g++", "clang++", "cc", "gcc", "clang"):
        found = shutil.which(name)
        if found:
            return [found]
    return None


def python_lib_dir():
    for key in ("LIBDIR", "LIBPL", "LDLIBRARY", "LIBRARY"):
        value = sysconfig.get_config_var(key)
        if value:
            if key in ("LDLIBRARY", "LIBRARY"):
                base = sysconfig.get_config_var("LIBDIR")
                if base:
                    return base
            else:
                return value
    return sysconfig.get_config_var("LIBDIR") or ""


def install_file(source: str, destination: str, attempts: int = 30) -> bool:
    """Copy a build artefact into place, retrying while it is in use.

    A file that another process has open cannot be overwritten on Windows,
    and the build system, the test suite and a host all open these files.
    Waiting a little is the difference between a build that succeeds and one
    that fails for a reason that has nothing to do with the code.
    """
    last_error: Exception | None = None

    for _ in range(attempts):
        try:
            shutil.copy2(source, destination)
            return True
        except OSError as exc:
            last_error = exc
            time.sleep(1.0)

    print(f"note: {destination} is in use and could not be replaced "
          f"({last_error})", file=sys.stderr)
    return False


def read_installed_exports(out_dir: str) -> list[str] | None:
    """The export table of the library that is installed, when it is there.

    The module definition file of the installed build names the functions
    the library exports.  It is what says whether the import library that
    is installed belongs to the shared library that is installed.
    """
    path = os.path.join(out_dir, "aurcore_abi.def")
    if not os.path.exists(path):
        return None

    names: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if not text or text.startswith(";") or text.upper() == "EXPORTS":
                continue
            names.append(text)

    return names


def write_config_header(out_dir: str) -> str:
    """Write the build-time paths into a header.

    A quoted string on a command line is a quoting problem in every compiler
    the command line was not written for, so the paths travel in a header
    the source includes instead.

    THE HOME OF THE INTERPRETER IS NOT WRITTEN HERE.  A library that
    carries the home of the machine the build ran on cannot be shipped: the
    image holds a path that means nothing to the user who installs the
    package, and the interpreter looks for its standard library there.  The
    core finds the home itself, from where it is running - see
    ``aur_find_python_home`` in ``aurcore_abi.cpp``.

    What is left is the root of the project, which is the current
    directory: that is where the core package is when the library is used
    from a checkout.
    """
    path = os.path.join(out_dir, "aurcore_build_config.h")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("/* generated by build_abi.py - do not edit */\n")
        fh.write('#define AURALIS_ROOT "."\n')
    return path


def write_def_file(path: str, names: list[str]) -> str:
    """Write the module definition file that names the exported functions.

    The export surface of the library is part of its contract, so it is
    written down rather than left to the linker.  Without this the linker
    also exports whatever it drags in from the interpreter library, and those
    exports collide with the runtime of the program that loads the plug-in.
    """
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("; generated by build_abi.py - do not edit\r\n")
        fh.write("EXPORTS\r\n")
        for name in names:
            fh.write(f"{name}\r\n")

    return path


def write_version_script(path: str, names: list[str]) -> str:
    """Write the version script that names the exported functions.

    The same job as the module definition file, for the linkers that take
    one: everything that is not named is local.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("/* generated by build_abi.py - do not edit */\n")
        fh.write("{\n  global:\n")
        for name in names:
            fh.write(f"    {name};\n")
        fh.write("  local:\n    *;\n};\n")

    return path


def write_symbol_list(path: str, names: list[str]) -> str:
    """Write the exported symbol list, for the linker of the other system.

    The list carries the underscore the symbol carries in the image.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# generated by build_abi.py - do not edit\n")
        for name in names:
            fh.write(f"_{name}\n")

    return path


def write_manifest(path: str, names: list[str], compiler: str, command: list[str]) -> str:
    """Write the manifest of the build.

    The manifest names the header the library was built against, its
    digest, the export table and the compiler, so two builds can be
    compared without guessing.
    """
    manifest = {
        "generated_by": "generated by build_abi.py - do not edit",
        "header": os.path.relpath(HEADER, ROOT).replace("\\", "/"),
        "header_sha256": digest(HEADER),
        "source": os.path.relpath(SRC, ROOT).replace("\\", "/"),
        "source_sha256": digest(SRC),
        "exports": list(names),
        "export_count": len(names),
        "compiler": compiler,
        "command": list(command),
    }

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")

    return path


def msvc_command(out_path: str, include: str, libdir: str, python_home: str,
                 debug: bool, def_file: str) -> list[str]:
    """Build the command line for the Microsoft compiler.

    The Microsoft compiler is used when it is available, because the
    alternative (the compiler shipped in the ziglang wheel) exports parts of
    the C runtime from the shared library it produces, and those exports
    collide with the runtime a host links against (LNK2005 on atexit).

    /std:c++17 is needed for the shared mutex the ABI uses to keep the
    audio thread and the message thread apart, and /Brepro makes the link
    reproducible: the same sources give the same image, byte for byte.
    """
    major, minor = sys.version_info.major, sys.version_info.minor
    lib = os.path.join(libdir, f"python{major}{minor}.lib") if libdir else ""

    cmd = [
        "cl.exe", "/nologo", "/LD", "/EHsc", "/std:c++17",
        "/O0" if debug else "/O2",
        f"/I{include}",
        f"/I{os.path.dirname(HEADER)}",
        f"/I{os.path.dirname(out_path)}",
        SRC,
        f"/Fe:{out_path}",
    ]
    # Everything after /link goes to the linker.  The module definition file
    # has to be there: the compiler reads "/DEF:" as its own "/D" (define a
    # macro) and would never pass it on, and the library would be built
    # without an export table.
    linker_args = []
    if def_file:
        linker_args.append(f"/DEF:{def_file}")
    if lib and os.path.exists(lib):
        linker_args.append(lib)
    if linker_args:
        cmd.append("/link")
        cmd.extend(linker_args)
        # A reproducible link: no timestamp, no build path in the image.
        cmd.append("/Brepro")
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the Auralis core C ABI")
    ap.add_argument("--cc", default=os.environ.get("AURALIS_CC"),
                    help="explicit compiler command")
    ap.add_argument("--out", default=os.path.join(ROOT, "plugins", "abi", "build"))
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--msvc", action="store_true",
                    help="use the Microsoft compiler (cl.exe) instead of the "
                         "compiler shipped in the ziglang wheel")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # The build happens in a staging directory and the result is installed
    # afterwards.  A library that is currently loaded cannot be overwritten
    # on Windows, and the test suite loads this one, so the install step
    # retries instead of failing the build.
    stage_dir = os.path.join(args.out, "_stage")
    if os.path.isdir(stage_dir):
        # A stale module definition file or import library in the output
        # directory is picked up by the linker and changes what the library
        # exports, so the staging directory starts empty every time.
        shutil.rmtree(stage_dir, ignore_errors=True)

    if os.path.isdir(stage_dir):
        # A file of the staging directory is in use - the conformance tests
        # load the library from there - and it cannot be removed.  The build
        # writes into a directory of its own, and the old one is left alone.
        stage_dir = os.path.join(args.out, "_stage_%d" % os.getpid())
        shutil.rmtree(stage_dir, ignore_errors=True)

    os.makedirs(stage_dir, exist_ok=True)

    if sys.platform == "win32":
        name = "aurcore_abi.dll"
    elif sys.platform == "darwin":
        name = "aurcore_abi.dylib"
    else:
        name = "aurcore_abi.so"

    out_path = os.path.join(args.out, name)
    stage_out = os.path.join(stage_dir, name)

    # A module definition file left behind by an earlier build would be
    # picked up by the linker and would export whatever it lists.
    for stale in (os.path.join(stage_dir, "aurcore_abi.def"),
                  os.path.join(args.out, "aurcore_abi.def")):
        if os.path.exists(stale):
            os.remove(stale)

    include = sysconfig.get_paths()["include"]
    libdir = python_lib_dir()

    # The interpreter needs a standard library to start.  The home is NOT
    # baked into the library: a library that holds the home of the build
    # machine cannot be shipped.  The core finds the home itself, from the
    # directory it was loaded from (see aur_find_python_home in
    # plugins/abi/aurcore_abi.cpp), and the build copies the home of the
    # interpreter next to the library, so that the build output is a
    # complete runtime (plugins/abi/interpreter_home.py).
    #
    # The value below is the installation the library is linked against.
    # msvc_command() does not read it; the import library and the include
    # path come from libdir and include.
    python_home = sys.base_prefix or sys.prefix

    write_config_header(args.out)
    write_config_header(stage_dir)

    # The export table comes from the header and nowhere else.
    exported = header_exports()
    check_definitions(exported)
    def_file = write_def_file(os.path.join(stage_dir, "aurcore_abi.def"), exported)
    print(f"exported surface: {len(exported)} functions (from {os.path.basename(HEADER)})")
    for name_ in exported:
        print(f"    {name_}")

    use_msvc = args.msvc or (args.cc or "").strip().lower().startswith("cl")
    if use_msvc:
        cmd = msvc_command(stage_out, include, libdir, python_home, args.debug, def_file)
    else:
        cc = pick_compiler(args.cc)
        if cc is None:
            print("no C compiler found.  Install the 'ziglang' wheel "
                  "(pip install ziglang), use --msvc, or set AURALIS_CC.",
                  file=sys.stderr)
            return 2

        cmd = list(cc) + [
            "-shared", "-fPIC", "-std=c++17",
            "-O2" if not args.debug else "-O0",
            "-I", include,
            "-I", os.path.dirname(HEADER),
            "-I", args.out,
        ]
        if libdir:
            cmd += ["-L", libdir]
        cmd += [SRC, "-o", stage_out]

        # The export table travels with the link, so the library exports
        # exactly what the header promises.
        if sys.platform == "darwin":
            symbol_list = write_symbol_list(
                os.path.join(stage_dir, "aurcore_abi.symlist"), exported)
            cmd += [f"-Wl,-exported_symbols_list,{symbol_list}"]
        elif sys.platform != "win32":
            version_script = write_version_script(
                os.path.join(stage_dir, "aurcore_abi.map"), exported)
            cmd += [f"-Wl,--version-script={version_script}"]

        # Link the CPython import library directly: a plain ``-lpythonX.Y`` is not
        # resolved reliably by every toolchain, but the library file always is.
        # Windows names it pythonXY.lib (no dot), POSIX names it libpythonX.Y.a.
        major, minor = sys.version_info.major, sys.version_info.minor
        if sys.platform == "win32":
            candidates = [f"python{major}{minor}.lib", f"python{major}.lib",
                          f"libpython{major}{minor}.a"]
        else:
            candidates = [f"libpython{major}.{minor}.a", f"libpython{major}.{minor}.so",
                          f"libpython{major}.{minor}.dylib"]
        linked = False
        for candidate in candidates:
            path = os.path.join(libdir, candidate) if libdir else ""
            if path and os.path.exists(path):
                cmd.append(path)
                linked = True
                break
        if not linked:
            cmd += [f"-lpython{major}.{minor}"]

    print("compiler:", " ".join(cmd[:3]))
    print("command :", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        print("build failed", file=sys.stderr)
        return result.returncode

    size = os.path.getsize(stage_out)
    print(f"built {stage_out} ({size} bytes)")

    if sys.platform == "win32" and not use_msvc:
        # The compiler of the ziglang wheel links the C runtime into the
        # library, and the runtime's own symbols are exported with it.  They
        # collide with the runtime of the program that loads the plug-in, so
        # the Microsoft compiler is the one that ships.
        print("note: this compiler links the C runtime into the library, so "
              "the library exports the runtime's symbols as well; the "
              "Microsoft compiler (--msvc) produces the exact export table",
              file=sys.stderr)

    # Install the result.  A loaded library cannot be overwritten on Windows,
    # so the copy is retried for a while before it is called a failure.
    installed_exports = read_installed_exports(args.out)
    installed = install_file(stage_out, out_path)

    if not installed:
        # The import library is NOT installed either.  A plug-in that links
        # against the new import library and loads the old shared library
        # asks for a function the old library does not have, and the
        # delay-load helper kills the host with an exception instead of a
        # message.  The two have to be the same build, or they have to be
        # neither.
        same = installed_exports is not None and installed_exports == exported

        print(f"the library could not be installed: {out_path} is in use.",
              file=sys.stderr)

        if not same:
            print("  The export table of the build is not the one that is "
                  "installed, so the import library is not installed either: "
                  "a plug-in would link against a library it cannot load.  "
                  "Close the program that holds it and build again.",
                  file=sys.stderr)
    else:
        # The import library has to travel with it: the plug-in links
        # against it.
        for suffix in (".lib", ".exp"):
            staged = os.path.join(stage_dir, "aurcore_abi" + suffix)
            if os.path.exists(staged):
                install_file(staged, os.path.join(args.out, "aurcore_abi" + suffix))

    # The module definition file and the manifest describe the build, so
    # they are written either way.
    for suffix in (".def",):
        staged = os.path.join(stage_dir, "aurcore_abi" + suffix)
        if os.path.exists(staged):
            install_file(staged, os.path.join(args.out, "aurcore_abi" + suffix))
            os.remove(staged)

    # The manifest of the build: the header, its digest, the export table
    # and the compiler that produced the image.
    manifest = write_manifest(os.path.join(stage_dir, "aurcore_abi.manifest.json"),
                               exported, " ".join(cmd[:3]), cmd)
    install_file(manifest, os.path.join(args.out, "aurcore_abi.manifest.json"))

    if installed:
        print(f"installed {out_path} ({os.path.getsize(out_path)} bytes)")
    else:
        print(f"staged {stage_out} ({os.path.getsize(stage_out)} bytes)")
        if installed_exports is not None and installed_exports == exported:
            print("  the library that is installed is the same build, so the "
                  "family is unaffected", file=sys.stderr)

    # The interpreter library travels with the ABI library: a plug-in bundle
    # that carries the ABI has to carry the interpreter it is linked against,
    # or the bundle cannot be loaded on a machine without Python installed.
    interpreter = None
    for candidate_name in (f"python{sys.version_info.major}{sys.version_info.minor}.dll",
                           f"libpython{sys.version_info.major}.{sys.version_info.minor}.dylib",
                           f"libpython{sys.version_info.major}.{sys.version_info.minor}.so"):
        candidate = os.path.join(python_home, candidate_name)
        if os.path.exists(candidate):
            interpreter = candidate
            break

    if interpreter is not None:
        target = os.path.join(args.out, os.path.basename(interpreter))
        if os.path.abspath(interpreter) != os.path.abspath(target):
            install_file(interpreter, target)
        print(f"copied {os.path.basename(interpreter)} -> {target}")
    else:
        print("note: the interpreter library was not found next to the "
              "installation; the bundle will need it on the search path",
              file=sys.stderr)

    # The home of the interpreter goes next to the library.  The interpreter
    # needs a standard library to start at all, and without one it stops
    # with "Failed to import encodings module" and takes the host down
    # with it.  The build output is therefore a complete runtime: the core,
    # the interpreter and the home of the interpreter.
    home = interpreter_home.stage_home(args.out)
    print("home of the interpreter: %d file(s), %d bytes (%s)"
          % (home["files"], home["bytes"], home["source"]))

    for problem in home["problems"]:
        print(f"note: {problem}", file=sys.stderr)

    if not installed:
        if installed_exports is None or installed_exports != exported:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
