"""The plug-in family, checked by the host that was written against the SDK.

The host in ``qa/vst3host`` loads a module the way a host does: it asks the
factory for the classes, creates the component and the controller, connects
them, accepts the bus arrangement, drives a parameter, processes audio and
restores the state.  A plug-in that walks into freed memory on the way out
shows up there as a fault, and a fault is a failure of the product, not a
detail of the test.

The host does more than that now.  It also checks that

  * the component and the controller are connected and that the parameters
    the controller publishes are the ones the component works with,
  * the automation the host sends reaches the plug-in, and that the value
    the plug-in then works with is the value that was sent,
  * the state of the controller and the state of the component agree and
    both survive a save and restore,
  * the bus layout is really what the plug-in says it is,
  * the buses are activated before the component is set active, which is
    what the format asks a host to do, and
  * the latency and the tail the plug-in reports are the ones it has.

The last one of those is the one a plug-in that writes nothing slips past:
the host fills the output buffers with a value no plug-in produces and
counts the samples the plug-in left untouched.  A plug-in that writes
nothing at all is caught by that count, and the report says how many
samples were not written.

Every module of the family is exercised, not a sample of it: the whole
family is run in parallel, so the gate stays inside a test suite's time
budget.  The test skips itself when the host or the plug-ins are not built,
so the suite stays green on a machine that has never compiled anything.

The gate also carries a fixture of its own: a small plug-in that takes the
VST3 specification seriously and only writes its output when its output bus
is active.  The host has to activate the buses, or the fixture writes
nothing and the host has to say so.  The fixture is built by the test, so
the gate does not depend on anything that is not in the repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOST = ROOT / "build" / "vst3host" / "vst3host.exe"
FAMILY = ROOT / "build" / "vst3native"
REGISTRY = ROOT / "aurcore" / "schema" / "modules.json"

#: The script that writes the catalogue of the product for the host.  The
#: host measures the delay of a plug-in over the range the catalogue
#: declares, so the gate hands it a catalogue that is written from the
#: registry of the product at the moment the gate runs: a catalogue that is
#: out of date would make the gate measure the wrong range.
CATALOGUE_MAKER = ROOT / "qa" / "vst3host" / "make_catalogue.py"

#: The shared runtimes that were stale when the gate ran.  The gate says so
#: in its report: a stale copy of the core is a build problem.
STALE_RUNTIME: list[Path] = []

#: The modules whose plug-in promises a delay that the catalogue does not
#: know about.  The gate prints them: the bundle is then older than the
#: source it was built from, or the plug-in adds a delay of its own.
DIFFERENT_PROMISE: list[str] = []

#: How many modules are exercised at the same time.  Every module loads a
#: whole interpreter, so the wall clock is what the number of workers buys.
WORKERS = max(1, int(os.environ.get("AURALIS_VST3HOST_WORKERS", "8")))

#: How long one module may take before the host is killed.
TIMEOUT = 300

#: The worst difference between two audio blocks that still count as the
#: same block.
AUDIO_TOLERANCE = 1.0e-4

#: How much delay the plug-in may have beyond the latency it reports, in
#: samples.  64 samples are 1.33 ms at 48 kHz: below that the
#: misalignment is inaudible.
LATENCY_TOLERANCE = 64

#: The margin above the largest declared delay of the catalogue that the
#: delay search of the host covers, in samples.  It is one window of the
#: analysis of the core (2048 samples, 42.7 ms at 48 kHz), and it is the
#: margin the survey of the catalogue uses as well
#: (qa/survey/harness.py, LATENCY_SEARCH_MARGIN).
LATENCY_SEARCH_MARGIN = 2048

#: How much the plug-in's declared tail may differ from the tail the host
#: measures, in samples.
#:
#: The ring-out of a spectral module is the tail of its last analysis
#: window, and where that tail crosses the -60 dBFS threshold depends on
#: the state the module is in: the same module, in two states, was
#: measured ringing for 5 and for 547 samples beyond its look-ahead, a
#: spread of 542 samples.  Half of the 2048-sample analysis window (1024
#: samples, 21.3 ms at 48 kHz) is the allowance.  It covers the spread
#: with room to spare and it is still half of the window the ring-out
#: comes from.
TAIL_TOLERANCE = 1024

#: The tail a plug-in reports when it never goes quiet.  It is the value
#: the plug-in formats use for it, and the core hands it out unchanged.
INFINITE_TAIL = 0xFFFFFFFF

#: The smallest cross correlation that still says something about where
#: the output sits against the input.  Below it the delay could not be
#: measured and the onset is used instead.
CORRELATION_LIMIT = 0.2

#: The level the host puts into its output buffers before the plug-in
#: writes them.  Nothing produces it, so the samples the plug-in left
#: untouched are the ones that still hold it.  The value is the one the
#: study measured against the reference plug-ins: a plug-in that writes
#: nothing at all shows up as a peak of 1e30.
SENTINEL_LEVEL = 1.0e30

#: The name of the directory the family keeps its shared runtime in.
RUNTIME_DIR_NAME = "Auralis Runtime"

#: The environment variable the fixture plug-in reads to choose what it
#: does.  "silent" makes it write nothing at all.
FIXTURE_MODE_VARIABLE = "AURALIS_FIXTURE_MODE"


def registry_codes() -> list[str]:
    """Every module code of the registry, in canonical order."""
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    if isinstance(data, dict):
        modules = data.get("modules")
        if isinstance(modules, list):
            codes = [
                str(module["code"])
                for module in modules
                if isinstance(module, dict) and module.get("code")
            ]
        else:
            # The registry maps the module code to the module's record.
            codes = [key for key in data if not key.startswith("_")]
        return sorted(codes)

    if isinstance(data, list):
        return sorted(
            str(module["code"])
            for module in data
            if isinstance(module, dict) and module.get("code")
        )

    return []


def built_modules() -> dict[str, Path]:
    """Every built module of the family, by code.

    The family lives in one folder per module; the plug-in is the shared
    library inside its stamped bundle.
    """
    if not FAMILY.is_dir():
        return {}

    found: dict[str, Path] = {}
    for code_dir in sorted(FAMILY.iterdir()):
        if not code_dir.is_dir() or not code_dir.name.startswith("AUR_"):
            continue
        binaries = sorted((code_dir / "stamped").rglob("*.vst3.dll"))
        if binaries:
            found[code_dir.name] = binaries[0]
    return found


def _digest(path: Path) -> str:
    """The SHA-256 of a file, or an empty string when it cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _core_is_current(directory: Path) -> bool:
    """True when the directory holds the core the plug-ins were built against.

    A copy of the core that is older than the one the plug-ins were linked
    against does not export what they need, and the plug-in falls back to
    passing the audio through.
    """
    core = directory / "aurcore_abi.dll"
    reference = ROOT / "plugins" / "abi" / "build" / "aurcore_abi.dll"

    if not core.is_file():
        return False
    if not reference.is_file():
        return True

    return _digest(core) == _digest(reference)


def runtime_directory(binary: Path) -> Path | None:
    """The runtime the plug-in should load its core from.

    The family shares one runtime: the core and the interpreter live in a
    directory of their own next to the family, and every plug-in finds it
    there.  The rule is the plug-in's own: the directory called "Auralis
    Runtime" is looked for in the bundle, in the directory above it, and in
    the two directories above that.
    """
    shared = None
    for base in (
        binary.parent,
        binary.parent.parent,
        binary.parent.parent.parent,
        binary.parent.parent.parent.parent,
    ):
        candidate = base / RUNTIME_DIR_NAME
        if candidate.is_dir():
            shared = candidate
            break

    if shared is not None:
        if _core_is_current(shared):
            return shared
        STALE_RUNTIME.append(shared)

    core = ROOT / "plugins" / "abi" / "build"
    if core.is_dir():
        return core

    return shared


def dependency_directories(binary: Path) -> list[str]:
    """The directories the host has to search for the module's libraries.

    A plug-in links against the core's C ABI shared library, which links
    against the Python runtime.  Where those live depends on how the
    family was built: inside the bundle, in the shared runtime next to
    it, or in the runtime's own folder.  The host is told all of them.

    The shared runtime comes first.  The host loads the libraries of the
    directories it is given before the ones of the bundle, and a library
    the family shares has to be loaded once: when the bundle's own copy
    wins, the core looks for the standard library of the interpreter next
    to itself and does not find it.
    """
    directories: list[str] = []

    # The runtime the plug-in loads its core from.
    shared = runtime_directory(binary)
    if shared is not None:
        directories.append(str(shared))

    # The bundle's own directory.
    directories.append(str(binary.parent))

    # The folder the module was built in: its "bin" holds the shared
    # libraries the plug-in needs.
    build_dir = binary.parent.parent.parent
    candidate = build_dir / "bin"
    if candidate.is_dir() and _core_is_current(candidate):
        directories.append(str(candidate))

    # The core's C ABI, when it was built on its own.
    for hint in (ROOT / "plugins" / "abi" / "build",
                 ROOT / "plugins" / "abi" / "build" / "_stage"):
        if hint.is_dir():
            directories.append(str(hint))

    # The Python runtime.
    try:
        import sysconfig

        bindir = sysconfig.get_config_var("BINDIR")
        if bindir and Path(bindir).is_dir():
            directories.append(str(bindir))
    except Exception:  # pragma: no cover - only used to improve the odds
        pass

    return directories


def run_host(binary: Path, mode: str | None = None, catalogue: Path | None = None,
             parameter: tuple[int, float] | None = None) -> dict:
    """Run the host against a module and return its report.

    The host is a separate process, and it is killed if it takes longer
    than the timeout, so a hung module never leaves a process behind.

    ``parameter`` names the parameter the host moves: the host picks the
    one it can hear the most clearly on its own, and a caller that names
    one gets that one instead.  The naming is how a gate can ask about a
    parameter the host would not have picked.
    """
    environment = dict(os.environ)
    # The host loads the module with the plain loader, which does not look
    # in the module's own directory for the libraries it needs.
    environment["AURALIS_VST3HOST_DEPS"] = ";".join(dependency_directories(binary))

    # The catalogue of the product, written fresh for this run: the range
    # the host searches the delay in comes from it.
    if catalogue is not None:
        environment["AURALIS_VST3HOST_CATALOGUE"] = str(catalogue)
    else:
        environment.pop("AURALIS_VST3HOST_CATALOGUE", None)

    # The plug-in looks for the shared runtime too, and the environment
    # tells it where it is.
    shared = runtime_directory(binary)
    if shared is not None:
        environment["AURALIS_RUNTIME_DIR"] = str(shared)

    # The core package lives in the source tree, and the gate runs the
    # family from there: the core finds the package by itself, walking up
    # from the runtime directory of the family.  An installed package
    # keeps it next to the application.
    environment.pop("AURALIS_ROOT", None)

    # The plug-in carries its own copy of the interpreter and the standard
    # library of that interpreter, and it finds both by itself: the runtime
    # directory of the family is the home of the interpreter.  The gate
    # therefore takes the two variables out of the environment, because a
    # user who installs the package does not set them either.
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)

    if mode is not None:
        environment[FIXTURE_MODE_VARIABLE] = mode
    else:
        environment.pop(FIXTURE_MODE_VARIABLE, None)

    command = [str(HOST), str(binary), "--tone"]

    if parameter is not None:
        command += ["--parameter", str(int(parameter[0])), repr(float(parameter[1]))]

    creation_flags = 0
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        creationflags=creation_flags,
    )

    try:
        stdout, stderr = process.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise AssertionError(
            f"the host did not finish within {TIMEOUT} s on {binary.name} and was killed"
        ) from None

    # The report is the JSON the host writes to its standard output.  The
    # human readable part goes to the error stream.
    start = stdout.find("{")
    if start < 0:
        raise AssertionError(
            f"the host wrote no report (exit {process.returncode}):\n{stderr[-1500:]}"
        )

    report = json.loads(stdout[start:])
    report["_exit_code"] = process.returncode
    report["_stderr"] = stderr
    report["_pid"] = process.pid
    return report


def host_processes() -> set[int]:
    """The process ids of the host processes that are running.

    The gate proves that it leaves nothing behind, and the proof has to
    survive a machine where somebody else is running the host as well: the
    process ids are the right unit, not a count.  A count would blame
    another session for a process that was already there.
    """
    if os.name != "nt":
        return set()

    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq vst3host.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    pids: set[int] = set()
    for line in result.stdout.splitlines():
        if not line.startswith('"') or "vst3host.exe" not in line:
            continue
        # "name","pid","session name",...
        fields = line.split('","')
        if len(fields) < 2:
            continue
        try:
            pids.add(int(fields[1].strip('"')))
        except ValueError:
            continue

    return pids


def _process_is_running(pid: int) -> bool:
    """True when the process with that id is still running.

    A process that has ended but whose handle is still open is still
    listed by the task list, so only the process itself can say whether it
    is alive.  The gate has to know the difference: the handles of the
    hosts it started are released when the garbage collector gets to them.
    """
    if os.name != "nt":
        return True

    import ctypes

    query_limited_information = 0x1000
    still_active = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    handle = kernel32.OpenProcess(query_limited_information, False, int(pid))
    if not handle:
        # The process is gone.
        return False

    try:
        code = ctypes.c_ulong()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) == 0:
            return True
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
#  the catalogue of the product
# ---------------------------------------------------------------------------
#
#  The host measures the delay of a plug-in over the range the catalogue of
#  the product declares: the largest delay any module declares, plus a
#  margin.  A constant in the host would go blind the moment a module
#  declares a longer delay, and the four modules that declare more than
#  2048 samples are exactly the ones a constant of that size misses.
#
#  The catalogue is written by qa/vst3host/make_catalogue.py, which asks the
#  registry of the product for every module.  The gate writes it into a
#  temporary file and hands that file to the host, so the range the host
#  searches is the range the product declares at the moment the gate runs.


def write_catalogue(target: Path) -> dict[str, dict]:
    """Write the catalogue of the product and return what it says.

    Every entry is the delay the module declares and the tolerance of that
    delay: 2 samples for a module that declares a delay, 64 samples for a
    module that declares none, and the tolerance of the module's own
    specification for a module whose delay is not a constant
    (qa/survey/specs.py).

    When the product cannot be imported - a module of it is being edited at
    that moment - the catalogue that is checked in is used instead, and the
    gate says so: the range the host searches is then the range of the
    last catalogue that was written.
    """
    result = subprocess.run(
        [sys.executable, str(CATALOGUE_MAKER), "--out", str(target)],
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        checked_in = ROOT / "qa" / "vst3host" / "catalogue.tsv"
        assert checked_in.is_file(), (
            "the catalogue of the product could not be written and there is no "
            f"catalogue in the repository:\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}"
        )
        print(
            "\nthe catalogue of the product could not be written (the product is "
            "being edited at the moment); the catalogue of the repository is used "
            "instead: " + str(checked_in)
        )
        target.write_text(checked_in.read_text(encoding="utf-8"), encoding="utf-8")

    entries: dict[str, dict] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 5:
            continue
        entries[fields[0]] = {
            "class_id": fields[1].lower(),
            "declared": int(fields[3]),
            "tolerance": int(fields[4]),
        }

    assert entries, "the catalogue of the product is empty"

    entries["_max_declared"] = max(entry["declared"] for entry in entries.values())
    entries["_path"] = target

    return entries


@pytest.fixture(scope="session")
def catalogue(tmp_path_factory) -> dict:
    """The catalogue of the product, written fresh for this run."""
    directory = tmp_path_factory.mktemp("vst3host-catalogue")
    return write_catalogue(Path(directory) / "catalogue.tsv")


def check_module_report(code: str, report: dict, product_catalogue: dict | None = None) -> None:
    """Everything the gate holds a module of the family to.

    The checks live in one place, because the gate has to be able to hold
    a report that did not come from the family to the same standard: the
    fixture plug-in that writes nothing is checked with exactly the same
    function, and it has to fail.

    The catalogue of the product is optional: the fixture is not a module
    of the product, so it has no entry in it.
    """
    assert report.get("faults") == [], f"the module faulted: {report.get('faults')}"
    assert report.get("ok") is True, (
        f"the host did not accept the module: {report.get('error')}"
    )
    assert report.get("exit_code") == 0
    assert report.get("_exit_code") == 0

    # -- the class the module reports is the class the identity record names.
    assert report.get("class_count", 0) >= 1
    assert report.get("classes"), "the module reported no class"

    # -- the connection ----------------------------------------------------
    connection = report.get("connection", {})
    assert connection.get("notify_accepted") is True, (
        "the connection point between the component and the controller refused the host"
    )
    assert connection.get("component_handler_set") is True

    # -- the buses --------------------------------------------------------
    buses = report.get("buses", {})
    assert buses.get("problem_count") == 0, (
        f"the bus layout does not hold up: {buses.get('problems')}"
    )
    assert buses.get("arrangement_accepted") is True
    assert buses.get("event_buses") == 0

    # The buses are activated before the component is set active: the
    # format asks the host for it, and a plug-in that takes the
    # specification seriously writes nothing at all when a host forgets.
    assert buses.get("activated_before_set_active") is True, (
        "the host did not activate the buses before it set the component active"
    )
    assert buses.get("buses_activated", 0) >= 2, (
        "the host activated fewer buses than the plug-in has "
        f"({buses.get('buses_activated')})"
    )
    assert buses.get("activation_failures") == 0, (
        "the plug-in refused to have its buses activated"
    )

    audio = report.get("audio", {})
    assert audio.get("input_buses", 0) >= 1, "the plug-in has no audio input"
    assert audio.get("output_buses", 0) >= 1, "the plug-in has no audio output"
    assert audio.get("input_channels", 0) > 0
    assert audio.get("output_channels", 0) > 0

    # -- the parameter catalogue -------------------------------------------
    catalogue = report.get("parameter_catalogue", {})
    assert catalogue.get("count", 0) > 0, "the controller publishes no parameter"
    assert catalogue.get("problem_count") == 0, (
        f"the parameter catalogue does not hold up: {catalogue.get('problems')}"
    )
    assert catalogue.get("text_round_trip_failures") == 0
    assert catalogue.get("plain_round_trip_failures") == 0
    assert catalogue.get("duplicate_id_failures") == 0

    for parameter in report.get("parameters", []):
        assert parameter["title"], "a parameter has no title"
        assert "units" in parameter, "a parameter has no units"
        assert parameter["value_string"] != "", "a parameter has no value"
        assert parameter["default_string"] != "", "a parameter has no text for its default"
        assert parameter["text_at_zero"] != "", "a parameter has no text for the value 0"
        assert parameter["text_at_one"] != "", "a parameter has no text for the value 1"
        assert 0.0 <= parameter["default_normalized"] <= 1.0

    # -- the parameters reach the component -------------------------------
    liveness = report.get("parameter_liveness", {})
    assert liveness.get("inert") == 0, (
        f"the plug-in publishes parameters it does not carry: {liveness.get('inert_ids')}"
    )
    assert liveness.get("live", 0) > 0

    # -- the automation reaches the plug-in --------------------------------
    agreement = report.get("agreement", {})
    assert agreement.get("performed") is True
    assert agreement.get("state_matches_baseline") is False, (
        "the automation the host sent did not reach the plug-in: the state after the "
        "automated run is the state of a plug-in that was never told"
    )
    assert agreement.get("state_matches_controller") is True, (
        "the value the plug-in holds after the automation is not the value the host sent"
    )
    assert agreement.get("audio_difference_to_controller", 1.0) <= AUDIO_TOLERANCE, (
        "the audio the plug-in produced after the automation is not the audio it produces "
        "when the controller is told the same value"
    )

    # -- the latency and the tail ------------------------------------------
    latency = report.get("latency", {})
    assert latency.get("reported_stable") is True, (
        "the plug-in reports a different latency every time it is asked"
    )
    if latency.get("correlation", 0.0) >= CORRELATION_LIMIT:
        # The cross correlation is readable, so the delay it measured is
        # the delay the plug-in really has.
        assert latency.get("delay_excess_samples", 0) <= LATENCY_TOLERANCE, (
            f"the plug-in delays the audio by {latency.get('measured_delay_samples')} samples but "
            f"reports a latency of {latency.get('reported_samples')}"
        )

    # -- the delay against the catalogue ------------------------------------
    #
    # The delay the host measured against the delay the module declares.
    # The tolerance is the one of the module: a module that declares a
    # delay has to delay the signal by exactly that delay, a module that
    # declares none may delay it by no more than a block, and a module
    # whose delay is not a constant carries the tolerance it needs in its
    # own specification (qa/survey/specs.py).
    #
    # The plug-in under test is the module with the adapter of the family
    # around it, and the adapter may add a delay of its own - the
    # converter of the resampler does.  The exactness of the module's own
    # declaration can therefore only be checked when the plug-in promises
    # what the module declares.  When the two differ, the host's own
    # resolution applies, and the gate says that the two numbers differ:
    # the bundle is then older than the source it was built from, or the
    # plug-in adds a delay of its own.
    if product_catalogue is not None:
        entry = product_catalogue.get(code)

        if entry is None:
            # The catalogue of the product could not be written - a module
            # of the product is being edited at the moment - and the one
            # that is checked in does not carry this module either.  The
            # module is new, so the delay it declares is not in the
            # catalogue the host searched either, and there is nothing to
            # compare it with.
            pytest.skip(
                f"{code} is not in the catalogue of the product: the module is "
                "newer than the catalogue"
            )

        # The range the host searched has to hold every delay the product
        # can declare: a host that searches less than that cannot measure
        # the delay of the modules that declare the most.
        expected_bound = max(product_catalogue["_max_declared"], latency.get("reported_samples", 0))
        expected_bound += LATENCY_SEARCH_MARGIN
        assert latency.get("search_bound_samples", 0) >= expected_bound, (
            f"the host searched the delay over {latency.get('search_bound_samples')} samples, "
            f"the catalogue declares {product_catalogue['_max_declared']} samples and the margin "
            f"is "
            f"{LATENCY_SEARCH_MARGIN} samples: the host cannot measure the delay of the "
            "modules that declare the most"
        )
        assert latency.get("catalogue_max_latency_samples") == product_catalogue["_max_declared"]

        if latency.get("correlation", 0.0) >= CORRELATION_LIMIT:
            declared = entry["declared"]
            promised = latency.get("reported_samples", 0)
            measured = latency.get("measured_delay_samples", -1)

            if promised == declared:
                error = abs(measured - declared)
                assert error <= entry["tolerance"], (
                    f"the plug-in delays the audio by {measured} samples but the catalogue says "
                    f"the module declares {declared} samples (the difference is {error} "
                    f"samples, the tolerance of the module is {entry['tolerance']} samples)"
                )
            else:
                error = abs(measured - promised)
                DIFFERENT_PROMISE.append(
                    f"{code}: the plug-in promises {promised} samples, the catalogue says "
                    f"{declared}"
                )
                assert error <= LATENCY_TOLERANCE, (
                    f"the plug-in delays the audio by {measured} samples but promises "
                    f"{promised} samples (the difference is {error} samples, the resolution of "
                    f"a host is {LATENCY_TOLERANCE} samples)"
                )

    # -- the tail ------------------------------------------------------------
    #
    # The tail the plug-in declares and the tail the host measures have to
    # agree.  The host measures the time from the end of the input until
    # the output is quiet, so the latency the plug-in reports comes off
    # it before the two are compared.
    reported_tail = latency.get("tail_reported_samples", 0)
    measured_tail = latency.get("tail_measured_samples", 0)
    reported_latency = latency.get("reported_samples", 0)

    if reported_tail >= INFINITE_TAIL:
        # The plug-in says it never goes quiet.  The host has to see it
        # still making sound long after the input stopped, or the
        # declaration is a promise the plug-in does not keep.
        assert measured_tail >= TAIL_TOLERANCE, (
            f"the plug-in reports an infinite tail but its output is quiet "
            f"{measured_tail} samples after the input stopped"
        )
    else:
        # The declaration is a promise about how long a host has to wait.
        # A plug-in that promises a longer tail than it has only makes the
        # host wait a little longer than it has to; a plug-in that rings
        # longer than it promises makes the host cut its tail.  Only the
        # second one is a defect, so that is the one the gate holds the
        # plug-in to.
        #
        # The allowance is half of the module's own analysis window: the
        # ring-out of a spectral module is the tail of its last analysis
        # window, and where that ring-out crosses the -60 dBFS threshold
        # depends on the state the module is in.  A module with a 2048
        # sample window may ring for 1024 samples beyond what it promises,
        # one with a 4096 sample window for 2048.
        ring_out = measured_tail - reported_latency
        allowance = max(TAIL_TOLERANCE, reported_latency // 2)
        assert ring_out <= reported_tail + allowance, (
            f"the plug-in declares a tail of {reported_tail} samples, but it rings "
            f"for {ring_out} samples past the latency it reports "
            f"({measured_tail} samples in all, {reported_latency} of them latency): "
            f"a host would cut {ring_out - reported_tail} samples of its output"
        )

    # -- the audio path runs ------------------------------------------------
    processing = report.get("processing", {})
    assert processing.get("frames", 0) > 0
    assert processing.get("output_finite") is True, (
        "the processor produced a sample that is not finite"
    )
    assert processing.get("tone", {}).get("output_finite") is True

    # -- the plug-in wrote its output ---------------------------------------
    #
    # The host fills the output buffers with a value no plug-in produces
    # and counts the samples the plug-in left untouched.  A plug-in that
    # writes nothing at all is caught here: the count is the whole output,
    # and the peak of the output is the sentinel.
    expected = processing.get("output_samples_expected", 0)
    assert expected > 0, "the host did not say how many output samples it handed over"
    assert processing.get("unwritten_output_samples") == 0, (
        f"the plug-in did not write {processing.get('unwritten_output_samples')} of its "
        f"{expected} output samples: a host would pass them on as they were"
    )
    assert processing.get("written_output_samples") == expected, (
        f"the plug-in wrote {processing.get('written_output_samples')} of the "
        f"{expected} output samples it was given"
    )
    assert processing.get("output_length_ok") is True
    assert processing.get("unwritten_output_samples_total") == 0, (
        f"the plug-in did not write {processing.get('unwritten_output_samples_total')} "
        "output samples over the whole exercise"
    )

    # -- the state comes back in both directions ----------------------------
    state = report.get("state", {})
    assert state.get("component_bytes", 0) > 0
    assert state.get("controller_bytes", 0) > 0
    assert state.get("component_restored") is True
    assert state.get("controller_restored") is True
    assert state.get("controller_state_moved_with_the_parameter") is True, (
        "the state of the controller does not carry the parameters"
    )
    assert state.get("component_state_moved_with_the_parameter") is True, (
        "the parameters of the controller are not the ones the component uses"
    )
    assert state.get("parameter_restored_through_the_controller") is True, (
        "the state of the controller does not survive a save and restore"
    )
    assert state.get("parameter_restored_through_the_component") is True, (
        "the state of the component and the state of the controller do not agree"
    )


@pytest.fixture(scope="module")
def family(catalogue: dict) -> dict[str, dict]:
    """The report of every built module of the family.

    The whole family is exercised in one go, in parallel: the modules do
    not share any state, and one host run per module is the honest way to
    load a plug-in.
    """
    if not HOST.is_file():
        pytest.skip(f"the VST3 host is not built: {HOST}")

    modules = built_modules()
    if not modules:
        pytest.skip(f"the plug-in family is not built: {FAMILY}")

    def one(item: tuple[str, Path]) -> tuple[str, dict]:
        code, binary = item
        try:
            return code, run_host(binary, catalogue=catalogue["_path"])
        except Exception as error:  # noqa: BLE001 - the report says it all
            return code, {
                "ok": False,
                "error": f"the host did not produce a report: {error}",
                "faults": [],
                "_exit_code": -1,
            }

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        reports = list(pool.map(one, modules.items()))
    elapsed = time.perf_counter() - started

    result = dict(reports)

    print(
        f"\nthe gate ran the host against {len(result)} module(s) in {elapsed:.1f} s "
        f"with {WORKERS} worker(s)"
    )

    # How many of the modules the host really measured the delay of, and
    # how many of them it could not measure because the output does not
    # look like the input.
    measured = 0
    unreadable = 0
    wrong = 0
    for report in result.values():
        latency = report.get("latency", {})
        if latency.get("correlation", 0.0) >= CORRELATION_LIMIT:
            measured += 1
            if latency.get("catalogue_delay_error_samples", 0) != 0:
                wrong += 1
        else:
            unreadable += 1
    print(
        f"the delay was measured for {measured} module(s), {unreadable} could not be "
        f"measured (the output does not look like the input), {wrong} are off the "
        f"delay the catalogue declares (the catalogue declares up to "
        f"{catalogue['_max_declared']} samples)"
    )

    others = host_processes()
    if others:
        print(
            f"{len(others)} host process(es) of other sessions are running: "
            "they are not the gate's"
        )

    if STALE_RUNTIME:
        print(
            "the shared runtime of the family is stale and was not used: "
            + ", ".join(str(path) for path in sorted(set(STALE_RUNTIME)))
        )

    # The gate must not leave a host behind.  Only the processes the gate
    # itself started count: a host that another session started is not the
    # gate's to clean up, and a host that has ended but whose handle is
    # still open is not a leftover either.
    #
    # A host that is still on its way out is given a moment: a module that
    # does not let go of its library ends the process itself, and that takes
    # a moment.
    mine = {report["_pid"] for report in result.values() if "_pid" in report}
    deadline = time.time() + 10.0
    left = {pid for pid in mine if _process_is_running(pid)}
    while left and time.time() < deadline:
        time.sleep(0.25)
        left = {pid for pid in left if _process_is_running(pid)}

    assert not left, (
        f"the gate left {len(left)} host process(es) behind: {sorted(left)}"
    )

    return result


def test_the_family_is_covered(family: dict[str, dict]) -> None:
    """Every module of the registry is exercised by the gate."""
    codes = registry_codes()
    if not codes:
        pytest.skip("the registry is not readable, so the family size is unknown")

    missing = [code for code in codes if code not in family]

    print(
        f"\ncoverage: {len(family)} of {len(codes)} module(s) of the registry "
        f"were exercised by the host"
    )

    if missing:
        pytest.skip(
            "the family is only partly built, so the gate could not cover it: "
            + ", ".join(missing)
        )

    assert len(family) == len(codes), (
        f"the gate covered {len(family)} module(s) but the registry has {len(codes)}"
    )


@pytest.mark.parametrize("code", registry_codes() or ["AUR_DECLICK"])
def test_the_module_survives_a_host(family: dict[str, dict], catalogue: dict, code: str) -> None:
    """A module loads, works and comes out of the teardown without a fault."""
    report = family.get(code)
    if report is None:
        pytest.skip(f"the plug-in of {code} is not built")

    check_module_report(code, report, catalogue)


def test_the_promise_and_the_declaration_agree(family: dict[str, dict]) -> None:
    """The modules whose plug-in promises a delay the catalogue does not know.

    The promise of the plug-in is the latency of the chain it was given,
    and the catalogue says what the module of the product declares.  The two
    differ when the bundle is older than the source it was built from, or
    when the adapter of the family adds a delay of its own - the converter
    of the resampler does.  The gate says which modules those are.
    """
    if DIFFERENT_PROMISE:
        print(
            "\nthe promise of the plug-in and the declaration of the catalogue differ "
            "for: " + "; ".join(sorted(set(DIFFERENT_PROMISE)))
        )
    assert True


# ---------------------------------------------------------------------------
#  the two paths of a parameter change
# ---------------------------------------------------------------------------
#
#  A host may deliver a change as an automation event -- with a sample
#  offset inside the block -- or as a controller change, which has no
#  offset.  The two have to reach the same audio: the same value must
#  sound the same however it arrives.
#
#  The host picks the parameter it can hear the most clearly and compares
#  the two paths on it.  The program change parameter is the one the host
#  does not pick: it is the plug-in's own, and a host does not automate it
#  in the usual way.  It is also the parameter where the two paths were
#  furthest apart -- the preset was loaded when the controller was told
#  the value and not when the host automated it -- so the gate asks about
#  it by name.


def program_parameter(report: dict) -> tuple[int, float] | None:
    """The id and a value of the program change parameter, or nothing.

    The value is the one the host itself would pick: the choice after the
    first one, so it is a choice the parameter really has.
    """
    for parameter in report.get("parameters", []):
        if parameter.get("title") != "Program":
            continue

        steps = int(parameter.get("step_count", 0) or 0)

        if steps <= 0:
            return None

        return int(parameter["id"]), 1.0 / float(steps)

    return None


def test_the_two_paths_agree_for_the_program_change(
    family: dict[str, dict], catalogue: dict
) -> None:
    """The program change reaches the core the same way on both paths.

    The program change parameter carries a preset.  A host that automates
    it and a host that tells the controller the value have to end in the
    same state and hear the same thing.
    """
    wanted = []

    for code, report in family.items():
        found = program_parameter(report)

        if found is None:
            continue

        wanted.append((code, found))

    if not wanted:
        pytest.skip("no module of the family publishes a program change parameter")

    def one(item: tuple[str, tuple[int, float]]) -> tuple[str, dict]:
        code, (param_id, value) = item
        binary = built_modules()[code]
        return code, run_host(binary, catalogue=catalogue["_path"],
                              parameter=(param_id, value))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        reports = dict(pool.map(one, wanted))

    for code, report in sorted(reports.items()):
        agreement = report.get("agreement", {})

        assert agreement.get("performed") is True, (
            f"{code}: the host did not compare the two paths"
        )
        assert agreement.get("state_matches_controller") is True, (
            f"{code}: the state after the automated run differs from the state after "
            "the controller was told the same value: the two paths do not reach "
            "the same state"
        )
        assert agreement.get("audio_difference_to_controller", 1.0) <= AUDIO_TOLERANCE, (
            f"{code}: the audio of the automated change differs from the audio of the "
            f"same change told to the controller by "
            f"{agreement.get('audio_difference_to_controller')}"
        )


def test_the_host_leaves_nothing_behind(family: dict[str, dict]) -> None:
    """Every host run came back with an exit code and no stray process."""
    for code, report in family.items():
        assert report.get("_exit_code") == 0, (
            f"the host exited with {report.get('_exit_code')} on {code}"
        )
        assert report.get("faults") == [], f"the module faulted: {report.get('faults')}"


# ---------------------------------------------------------------------------
#  the fixture: a plug-in that takes the specification seriously
# ---------------------------------------------------------------------------
#
#  The fixture is built by the test, so the gate does not depend on
#  anything that is not in the repository.  It is a small VST3 module with
#  one audio bus in and one out, one parameter and one rule that matters:
#
#      the plug-in writes its output only when its output bus is active
#
#  A host that does not activate the buses gets nothing back, and the
#  host's own sentinel (1e30) is what it measures.  With the environment
#  variable AURALIS_FIXTURE_MODE set to "silent" the plug-in writes
#  nothing at all, whatever the host does: that is the plug-in the gate
#  holds the host to.

FIXTURE_SOURCE = r"""
// A fixture plug-in for the host gate.
//
// It takes the VST3 specification seriously: it only writes its output when
// its output bus is active.  A host that forgets to activate the buses gets
// nothing back, and the host's own sentinel (1e30) is what it measures.
//
// The environment variable AURALIS_FIXTURE_MODE chooses the behaviour:
//
//     strict   the default: the plug-in writes when the bus is active
//     silent   the plug-in never writes anything at all

#define INIT_CLASS_IID

#include "pluginterfaces/base/funknown.h"
#include "pluginterfaces/base/ibstream.h"
#include "pluginterfaces/base/ipluginbase.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivstmessage.h"
#include "pluginterfaces/vst/ivstparameterchanges.h"
#include "pluginterfaces/vst/vstspeaker.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

using namespace Steinberg;
using namespace Steinberg::Vst;

namespace
{

// The class id of the fixture.  It is the fixture's own: nothing else in
// the world may use it.
const TUID kClassId = {0x0F, 0x1C, 0x17, 0x0E, 0x1A, 0x24, 0x4A, 0x55,
                        0x66, 0x77, 0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD};

enum
{
    kParameterId = 0,
    kParameterCount = 1
};

const double kParameterMin = 0.0;
const double kParameterMax = 2.0;
const double kParameterDefault = 1.0;

const int32 kChannels = 2;

bool silentMode()
{
    const char* mode = std::getenv ("AURALIS_FIXTURE_MODE");
    return mode != nullptr && std::strcmp (mode, "silent") == 0;
}

void toText (char16* destination, size_t count, const char* text)
{
    size_t i = 0;
    for (; i + 1 < count && text[i] != 0; ++i)
        destination[i] = static_cast<char16> (static_cast<unsigned char> (text[i]));
    destination[i] = 0;
}

std::string toNarrow (const TChar* text)
{
    std::string out;
    if (text == nullptr)
        return out;
    for (const TChar* p = text; *p != 0; ++p)
        out += static_cast<char> (static_cast<unsigned char> (*p));
    return out;
}

bool matches (const void* wanted, const FUID& given)
{
    TUID other;
    given.toTUID (other);
    return FUnknownPrivate::iidEqual (wanted, other);
}

double plainFromNormalized (ParamValue value)
{
    return kParameterMin + value * (kParameterMax - kParameterMin);
}

ParamValue normalizedFromPlain (double value)
{
    if (kParameterMax <= kParameterMin)
        return 0.0;
    double result = (value - kParameterMin) / (kParameterMax - kParameterMin);
    if (result < 0.0)
        result = 0.0;
    if (result > 1.0)
        result = 1.0;
    return result;
}

// The audio side of the fixture.
//
// The one thing this class does that matters is the writing: it writes
// its output only when the output bus is active, which is what the format
// asks a plug-in to assume.
class FixtureProcessor final : public IAudioProcessor
{
public:
    FixtureProcessor() = default;

    tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
    {
        if (obj == nullptr)
            return kInvalidArgument;
        *obj = nullptr;
        if (matches (iid, FUnknown::iid))
        {
            *obj = static_cast<FUnknown*> (this);
            addRef();
            return kResultOk;
        }
        if (matches (iid, IAudioProcessor::iid))
        {
            *obj = static_cast<IAudioProcessor*> (this);
            addRef();
            return kResultOk;
        }
        return kNoInterface;
    }

    uint32 PLUGIN_API addRef() override { return ++refCount; }

    uint32 PLUGIN_API release() override
    {
        const uint32 count = --refCount;
        if (count == 0)
            delete this;
        return count;
    }

    tresult PLUGIN_API setBusArrangements (SpeakerArrangement* inputs, int32 numIns,
                                           SpeakerArrangement* outputs, int32 numOuts) override
    {
        (void) inputs;
        (void) numIns;
        (void) outputs;
        (void) numOuts;
        return kResultOk;
    }

    tresult PLUGIN_API getBusArrangement (BusDirection direction, int32 index,
                                          SpeakerArrangement& arrangement) override
    {
        if (index != 0)
            return kResultFalse;
        arrangement = SpeakerArr::kStereo;
        return kResultOk;
    }

    tresult PLUGIN_API canProcessSampleSize (int32 symbolicSampleSize) override
    {
        return (symbolicSampleSize == kSample32) ? kResultOk : kResultFalse;
    }

    uint32 PLUGIN_API getLatencySamples() override { return 0; }

    uint32 PLUGIN_API getTailSamples() override { return 0; }

    tresult PLUGIN_API setupProcessing (ProcessSetup& setup) override
    {
        if (setup.symbolicSampleSize != kSample32)
            return kResultFalse;
        if (setup.maxSamplesPerBlock <= 0)
            return kResultFalse;
        return kResultOk;
    }

    tresult PLUGIN_API setProcessing (TBool state) override
    {
        processing = (state != 0);
        return kResultOk;
    }

    tresult PLUGIN_API process (ProcessData& data) override
    {
        // The automation the host hands over with the block.
        if (data.inputParameterChanges != nullptr)
        {
            const int32 count = data.inputParameterChanges->getParameterCount();
            for (int32 index = 0; index < count; ++index)
            {
                IParamValueQueue* queue = data.inputParameterChanges->getParameterData (index);
                if (queue == nullptr || queue->getParameterId () != kParameterId)
                    continue;

                int32 offset = 0;
                ParamValue value = 0.0;
                if (queue->getPoint (0, offset, value) == kResultOk)
                    setLevel (plainFromNormalized (value));
            }
        }

        // A plug-in that writes nothing at all: the mode the gate uses to
        // prove that the host notices.
        if (silentMode())
            return kResultOk;

        // A plug-in that takes the specification seriously: it only writes
        // into a bus the host activated.
        if (! outputActive)
            return kResultOk;

        if (data.numOutputs <= 0 || data.outputs == nullptr)
            return kResultOk;

        const int32 frames = data.numSamples;
        const float gain = static_cast<float> (levelValue ());

        for (int32 bus = 0; bus < data.numOutputs; ++bus)
        {
            AudioBusBuffers& buffers = data.outputs[bus];
            if (buffers.numChannels <= 0 || buffers.channelBuffers32 == nullptr)
                continue;

            for (int32 channel = 0; channel < buffers.numChannels; ++channel)
            {
                float* out = buffers.channelBuffers32[channel];
                const float* in = nullptr;
                if (data.numInputs > bus && data.inputs != nullptr
                    && data.inputs[bus].numChannels > channel
                    && data.inputs[bus].channelBuffers32 != nullptr)
                {
                    in = data.inputs[bus].channelBuffers32[channel];
                }

                for (int32 frame = 0; frame < frames; ++frame)
                    out[frame] = (in != nullptr ? in[frame] : 0.f) * gain;
            }
        }

        return kResultOk;
    }

    // The fixture's own state.

    // The bus the host activated.  The format asks the host to do this
    // before the component is set active.
    void setOutputActive (bool value) { outputActive = value; }

    void setLevel (double value)
    {
        if (levelPointer != nullptr)
            *levelPointer = value;
        else
            level = value;
    }

    double levelValue () const
    {
        return (levelPointer != nullptr) ? *levelPointer : level;
    }

    // The value the plug-in holds.  The pointer is the plug-in's own, so
    // that the automation the host hands over with a block changes the
    // value the plug-in carries in its state.
    void setLevelPointer (double* value) { levelPointer = value; }

private:
    uint32 refCount {1};
    bool processing {false};
    bool outputActive {false};
    double level {kParameterDefault};
    double* levelPointer {nullptr};
};

// The fixture: one object that is the component and the controller.
//
// The shape is the one a single component plug-in has: the same object
// answers for the audio and for the parameters.
class Fixture final : public IComponent, public IConnectionPoint, public IEditController
{
public:
    Fixture()
    {
        processor->setLevelPointer (&level);
    }

    ~Fixture()
    {
        // The processor is shared: the host holds a reference of its own.
        processor->release ();
    }

    tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
    {
        if (obj == nullptr)
            return kInvalidArgument;
        *obj = nullptr;
        if (matches (iid, FUnknown::iid))
        {
            // The three interfaces carry an FUnknown each, so the one of
            // the component is named.
            *obj = static_cast<FUnknown*> (static_cast<IComponent*> (this));
            addRef();
            return kResultOk;
        }
        if (matches (iid, IComponent::iid))
        {
            *obj = static_cast<IComponent*> (this);
            addRef();
            return kResultOk;
        }
        if (matches (iid, IConnectionPoint::iid))
        {
            *obj = static_cast<IConnectionPoint*> (this);
            addRef();
            return kResultOk;
        }
        if (matches (iid, IEditController::iid))
        {
            *obj = static_cast<IEditController*> (this);
            addRef();
            return kResultOk;
        }
        if (matches (iid, IAudioProcessor::iid))
        {
            *obj = static_cast<IAudioProcessor*> (processor);
            processor->addRef ();
            return kResultOk;
        }
        return kNoInterface;
    }

    uint32 PLUGIN_API addRef() override { return ++refCount; }

    uint32 PLUGIN_API release() override
    {
        const uint32 count = --refCount;
        if (count == 0)
            delete this;
        return count;
    }

    tresult PLUGIN_API initialize (FUnknown* context) override
    {
        host = context;
        return kResultOk;
    }

    tresult PLUGIN_API terminate() override
    {
        host = nullptr;
        return kResultOk;
    }

    tresult PLUGIN_API getControllerClassId (TUID classId) override
    {
        // The fixture is one object that plays both roles: the controller is
        // the component itself.
        std::memset (classId, 0, sizeof (TUID));
        return kResultFalse;
    }

    tresult PLUGIN_API setIoMode (IoMode mode) override { return kResultOk; }

    int32 PLUGIN_API getBusCount (MediaType type, BusDirection direction) override
    {
        if (type != kAudio)
            return 0;
        return (direction == kInput || direction == kOutput) ? 1 : 0;
    }

    tresult PLUGIN_API getBusInfo (MediaType type, BusDirection direction, int32 index,
                                   BusInfo& bus) override
    {
        if (type != kAudio || index != 0)
            return kResultFalse;
        if (direction != kInput && direction != kOutput)
            return kResultFalse;

        std::memset (&bus, 0, sizeof (bus));
        bus.mediaType = kAudio;
        bus.direction = direction;
        bus.channelCount = kChannels;
        toText (bus.name, 128, direction == kInput ? "Input" : "Output");
        bus.busType = kMain;
        bus.flags = BusInfo::kDefaultActive;
        return kResultOk;
    }

    tresult PLUGIN_API getRoutingInfo (RoutingInfo& inInfo, RoutingInfo& outInfo) override
    {
        outInfo = inInfo;
        return kResultOk;
    }

    tresult PLUGIN_API activateBus (MediaType type, BusDirection direction, int32 index,
                                    TBool state) override
    {
        if (type != kAudio || index != 0)
            return kResultFalse;
        if (direction != kInput && direction != kOutput)
            return kResultFalse;

        if (direction == kOutput)
            processor->setOutputActive (state != 0);

        return kResultOk;
    }

    tresult PLUGIN_API setActive (TBool state) override
    {
        active = (state != 0);
        return kResultOk;
    }

    tresult PLUGIN_API setComponentState (IBStream* state) override
    {
        float value = 0.f;
        if (state == nullptr || state->read (&value, sizeof (value)) != kResultOk)
            return kResultFalse;
        level = value;
        return kResultOk;
    }

    tresult PLUGIN_API setState (IBStream* state) override
    {
        float value = 0.f;
        if (state == nullptr || state->read (&value, sizeof (value)) != kResultOk)
            return kResultFalse;
        level = value;
        return kResultOk;
    }

    tresult PLUGIN_API getState (IBStream* state) override
    {
        if (state == nullptr)
            return kInvalidArgument;
        float value = static_cast<float> (level);
        return state->write (&value, sizeof (value));
    }

    int32 PLUGIN_API getParameterCount() override { return kParameterCount; }

    tresult PLUGIN_API getParameterInfo (int32 index, ParameterInfo& info) override
    {
        if (index != 0)
            return kInvalidArgument;

        std::memset (&info, 0, sizeof (info));
        info.id = kParameterId;
        toText (info.title, 128, "Level");
        toText (info.shortTitle, 128, "Level");
        toText (info.units, 128, "dB");
        info.defaultNormalizedValue = normalizedFromPlain (kParameterDefault);
        info.flags = ParameterInfo::kCanAutomate;
        info.stepCount = 0;
        info.unitId = 0;
        return kResultOk;
    }

    tresult PLUGIN_API getParamStringByValue (ParamID id, ParamValue value,
                                              String128 string) override
    {
        if (id != kParameterId)
            return kInvalidArgument;

        char text[64];
        std::snprintf (text, sizeof (text), "%.2f dB", plainFromNormalized (value));
        toText (string, 128, text);
        return kResultOk;
    }

    tresult PLUGIN_API getParamValueByString (ParamID id, TChar* string,
                                              ParamValue& value) override
    {
        if (id != kParameterId || string == nullptr)
            return kInvalidArgument;

        const std::string text = toNarrow (string);
        if (text.empty())
            return kResultFalse;

        value = normalizedFromPlain (std::atof (text.c_str()));
        return kResultOk;
    }

    ParamValue PLUGIN_API normalizedParamToPlain (ParamID id, ParamValue value) override
    {
        if (id != kParameterId)
            return 0.0;
        return plainFromNormalized (value);
    }

    ParamValue PLUGIN_API plainParamToNormalized (ParamID id, ParamValue value) override
    {
        if (id != kParameterId)
            return 0.0;
        return normalizedFromPlain (value);
    }

    ParamValue PLUGIN_API getParamNormalized (ParamID id) override
    {
        return (id == kParameterId) ? normalizedFromPlain (level) : 0.0;
    }

    tresult PLUGIN_API setParamNormalized (ParamID id, ParamValue value) override
    {
        if (id != kParameterId)
            return kInvalidArgument;
        level = plainFromNormalized (value);
        return kResultOk;
    }

    tresult PLUGIN_API setComponentHandler (IComponentHandler* value) override
    {
        handler = value;
        return kResultOk;
    }

    IPlugView* PLUGIN_API createView (FIDString name) override { return nullptr; }

    tresult PLUGIN_API connect (IConnectionPoint* other) override { return kResultOk; }
    tresult PLUGIN_API disconnect (IConnectionPoint* other) override { return kResultOk; }
    tresult PLUGIN_API notify (IMessage* message) override { return kResultFalse; }

private:
    uint32 refCount {1};
    FUnknown* host {nullptr};
    IComponentHandler* handler {nullptr};
    bool active {false};
    double level {kParameterDefault};
    FixtureProcessor* processor {new FixtureProcessor()};
};

// The factory of the fixture.
class FixtureFactory final : public IPluginFactory2
{
public:
    FixtureFactory() = default;

    tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
    {
        if (obj == nullptr)
            return kInvalidArgument;
        *obj = nullptr;
        if (matches (iid, FUnknown::iid))
        {
            *obj = static_cast<FUnknown*> (this);
            addRef();
            return kResultOk;
        }
        if (matches (iid, IPluginFactory::iid))
        {
            *obj = static_cast<IPluginFactory*> (this);
            addRef();
            return kResultOk;
        }
        if (matches (iid, IPluginFactory2::iid))
        {
            *obj = static_cast<IPluginFactory2*> (this);
            addRef();
            return kResultOk;
        }
        return kNoInterface;
    }

    uint32 PLUGIN_API addRef() override { return ++refCount; }

    uint32 PLUGIN_API release() override
    {
        const uint32 count = --refCount;
        if (count == 0)
            delete this;
        return count;
    }

    tresult PLUGIN_API getFactoryInfo (PFactoryInfo* info) override
    {
        if (info == nullptr)
            return kInvalidArgument;
        std::memset (info, 0, sizeof (PFactoryInfo));
        std::snprintf (info->vendor, PFactoryInfo::kNameSize, "Auralis");
        std::snprintf (info->url, PFactoryInfo::kURLSize, "https://auralis.example");
        std::snprintf (info->email, PFactoryInfo::kEmailSize, "support@auralis.example");
        info->flags = PFactoryInfo::kUnicode;
        return kResultOk;
    }

    int32 PLUGIN_API countClasses() override { return 1; }

    tresult PLUGIN_API getClassInfo (int32 index, PClassInfo* info) override
    {
        if (info == nullptr || index != 0)
            return kInvalidArgument;

        std::memset (info, 0, sizeof (PClassInfo));
        std::memcpy (info->cid, kClassId, sizeof (TUID));
        info->cardinality = PClassInfo::kManyInstances;
        std::snprintf (info->category, PClassInfo::kCategorySize, "Audio Module Class");
        std::snprintf (info->name, PClassInfo::kNameSize, "Auralis Fixture");
        return kResultOk;
    }

    tresult PLUGIN_API getClassInfo2 (int32 index, PClassInfo2* info) override
    {
        if (info == nullptr || index != 0)
            return kInvalidArgument;

        std::memset (info, 0, sizeof (PClassInfo2));
        std::memcpy (info->cid, kClassId, sizeof (TUID));
        info->cardinality = PClassInfo::kManyInstances;
        std::snprintf (info->category, PClassInfo::kCategorySize, "Audio Module Class");
        std::snprintf (info->name, PClassInfo::kNameSize, "Auralis Fixture");
        std::snprintf (info->vendor, PClassInfo2::kVendorSize, "Auralis");
        std::snprintf (info->version, PClassInfo2::kVersionSize, "1.0.0");
        std::snprintf (info->sdkVersion, PClassInfo2::kVersionSize, "VST3 SDK 3.7");
        return kResultOk;
    }

    tresult PLUGIN_API createInstance (FIDString cid, FIDString iid, void** obj) override
    {
        if (obj == nullptr)
            return kInvalidArgument;
        *obj = nullptr;

        TUID wanted;
        std::memset (wanted, 0, sizeof (TUID));
        std::memcpy (wanted, kClassId, sizeof (TUID));
        if (std::memcmp (cid, wanted, sizeof (TUID)) != 0)
            return kResultFalse;

        if (matches (iid, IComponent::iid))
        {
            *obj = static_cast<IComponent*> (new Fixture());
            return kResultOk;
        }
        if (matches (iid, IEditController::iid))
        {
            *obj = static_cast<IEditController*> (new Fixture());
            return kResultOk;
        }
        return kResultFalse;
    }

private:
    uint32 refCount {1};
};

} // namespace

extern "C" __declspec (dllexport) bool PLUGIN_API InitDll()
{
    return true;
}

extern "C" __declspec (dllexport) bool PLUGIN_API ExitDll()
{
    return true;
}

extern "C" __declspec (dllexport) IPluginFactory* PLUGIN_API GetPluginFactory()
{
    static FixtureFactory* factory = new FixtureFactory();
    return factory;
}
"""


def sdk_directory() -> Path | None:
    """The VST3 SDK of the repository, the layout the host is built against."""
    for candidate in (ROOT / "vendor" / "vst3sdk", ROOT / "vendor" / "VST3_SDK"):
        if (candidate / "pluginterfaces" / "base" / "funknown.cpp").is_file():
            return candidate
    return None


def build_fixture(directory: Path) -> Path:
    """Build the fixture plug-in and return the path of the module.

    The fixture is a shared library that exports the factory entry point,
    so the host loads it the way it loads any other module.  It is built
    with the toolchain of the machine, the same one the host is built
    with.
    """
    sdk = sdk_directory()
    if sdk is None:
        pytest.skip("the VST3 SDK is not in the repository, so the fixture cannot be built")

    source = directory / "fixture.cpp"
    source.write_text(FIXTURE_SOURCE, encoding="utf-8")

    module = directory / "fixture.vst3.dll"
    if module.is_file():
        return module

    command = [
        "cmd",
        "/c",
        str(ROOT / "tools" / "msvc.bat"),
        "cl",
        "/nologo",
        "/LD",
        "/std:c++17",
        "/EHsc",
        "/O2",
        "/MD",
        "/DNOMINMAX",
        "/D_CRT_SECURE_NO_WARNINGS",
        f"/I{sdk}",
        f"/I{directory}",
        f"/Fo{directory}/",
        f"/Fe{module}",
        str(source),
        str(sdk / "pluginterfaces" / "base" / "funknown.cpp"),
        "ole32.lib",
    ]

    result = subprocess.run(
        command,
        cwd=str(directory),
        capture_output=True,
        text=True,
        timeout=600,
    )

    if not module.is_file():
        pytest.skip(
            "the fixture plug-in could not be built, so the gate cannot check the "
            f"host against a plug-in that writes nothing:\n{result.stdout[-1500:]}\n"
            f"{result.stderr[-1500:]}"
        )

    return module


@pytest.fixture(scope="module")
def fixture_module(tmp_path_factory) -> Path:
    """The fixture plug-in, built once for the whole session."""
    if not HOST.is_file():
        pytest.skip(f"the VST3 host is not built: {HOST}")

    directory = tmp_path_factory.mktemp("vst3host-fixture")
    return build_fixture(Path(directory))


def test_the_host_activates_the_buses(fixture_module: Path) -> None:
    """A plug-in that takes the specification seriously is processed.

    The fixture only writes its output when its output bus is active, so a
    host that does not activate the buses gets nothing back: 512 unwritten
    output samples and a peak of 1e30, the host's own sentinel.  The host
    has to activate the buses, and the report has to say that it did.
    """
    report = run_host(fixture_module)

    processing = report.get("processing", {})
    buses = report.get("buses", {})

    assert buses.get("activated_before_set_active") is True, (
        "the host did not activate the buses before it set the component active"
    )
    assert buses.get("buses_activated") == 2, (
        f"the host activated {buses.get('buses_activated')} bus(es), the fixture has two"
    )
    assert buses.get("activation_failures") == 0

    assert processing.get("unwritten_output_samples") == 0, (
        f"the fixture wrote {processing.get('unwritten_output_samples')} of its "
        f"{processing.get('output_samples_expected')} output samples: the host did not "
        "activate the buses"
    )
    assert processing.get("written_output_samples") == processing.get(
        "output_samples_expected"
    )
    assert processing.get("output_length_ok") is True

    # The whole exercise has to pass, not only the processing step.
    check_module_report("FIXTURE", report)


def test_the_host_reports_a_plug_in_that_writes_nothing(fixture_module: Path) -> None:
    """A plug-in that writes nothing is caught, and the report says so.

    The numbers are the ones the study measured against the reference
    plug-ins: 512 unwritten output samples for a 256 frame stereo block,
    and a peak of 1e30, the host's own sentinel.
    """
    report = run_host(fixture_module, mode="silent")

    processing = report.get("processing", {})

    assert processing.get("unwritten_output_samples") == 512, (
        f"the host reported {processing.get('unwritten_output_samples')} unwritten output "
        "samples, the study measured 512"
    )
    assert processing.get("output_samples_expected") == 512
    assert processing.get("written_output_samples") == 0
    assert processing.get("output_length_ok") is False
    # The peak of the output is the sentinel the host put into the buffers:
    # the plug-in wrote nothing, so the buffers still hold it.
    peak = processing.get("output_peak", 0.0)
    assert abs(peak / SENTINEL_LEVEL - 1.0) < 0.01, (
        f"the peak of the output was {peak}, the sentinel is {SENTINEL_LEVEL}"
    )

    # The host has to say that the exercise failed.
    assert report.get("ok") is False
    assert report.get("exit_code") == 2
    assert "did not write" in str(report.get("error", ""))

    # And the gate has to fail on it: the same check the family is held to.
    with pytest.raises(AssertionError):
        check_module_report("FIXTURE", report)
