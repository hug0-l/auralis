"""
tests/test_journey.py — the product journey, driven against a live server.

The journey itself lives in ``qa/journey/journey.py``: it is a stand-alone
script a person can run against any Auralis server.  This module runs the
same script against a server that is started **in this process** (uvicorn in
a worker thread) and asserts the outcome.

Two properties of the journey are checked here as well:

* every step of the journey *asserts* something — the journey counts its
  assertions and reports them, and this module refuses a journey whose
  steps only call things;
* the numbers of two runs are the same — the journey is deterministic, so a
  CI run may compare the measurements of two builds.

Known defects are marked with ``xfail``.  Such a marker is always **strict**:
a defect that is fixed turns the marker into a failure that has to be
noticed, and the marker is removed.  A soft marker would hide the fix.

    python -m pytest tests/test_journey.py -v
"""
from __future__ import annotations

import ast
import importlib.util
import socket
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
JOURNEY_PATH = ROOT / "qa" / "journey" / "journey.py"


def _load_journey() -> ModuleType:
    """Import ``qa/journey/journey.py`` as a module (it is not a package)."""
    spec = importlib.util.spec_from_file_location("auralis_journey", JOURNEY_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load the journey from {JOURNEY_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


journey = _load_journey()


# ---------------------------------------------------------------------------
# the in-process server
# ---------------------------------------------------------------------------
def _free_port() -> int:
    """Return a port that is free right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class InProcessServer:
    """A uvicorn server running in a thread of this process."""

    def __init__(self, fresh: bool = False) -> None:
        import uvicorn

        if fresh:
            # a second run needs a second application: the chain of the
            # first one is still in it
            from aurserver.app import create_app

            application = create_app()
        else:
            from aurserver.app import app

            application = app

        self.port = _free_port()
        config = uvicorn.Config(
            application, host="127.0.0.1", port=self.port, log_level="warning"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run, name="auralis-journey-server", daemon=True
        )

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 60.0) -> None:
        """Start the server and wait until it answers."""
        self.thread.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                journey.request("GET", self.base_url + "/api/health", timeout=2.0)
                return
            except Exception:  # noqa: BLE001 - the server is still starting
                time.sleep(0.25)
        raise RuntimeError("the in-process server did not become healthy")

    def stop(self) -> None:
        """Ask the server to leave and wait for the thread."""
        self.server.should_exit = True
        self.thread.join(timeout=15)


def _clear_chain(base_url: str) -> None:
    """Empty the chain of the server, so a run starts from a clean bench.

    The engine of the server lives in the module, so a second application in
    the same process finds the chain of the first run.  The journey wants a
    clean workbench, so the chain is emptied over the ordinary route.
    """
    chain = journey.request("GET", base_url + "/api/chain").get("chain", [])
    for index in range(len(chain) - 1, -1, -1):
        journey.request("DELETE", base_url + f"/api/chain/{index}")


@pytest.fixture(scope="module")
def journey_run() -> journey.Journey:
    """Run the whole journey once against an in-process server."""
    server = InProcessServer()
    server.start()
    try:
        _clear_chain(server.base_url)
        return journey.run(server.base_url, verbose=False)
    finally:
        server.stop()


@pytest.fixture(scope="module")
def journey_run_again() -> journey.Journey:
    """Run the journey a second time, to prove that the numbers repeat."""
    server = InProcessServer(fresh=True)
    server.start()
    try:
        _clear_chain(server.base_url)
        return journey.run(server.base_url, verbose=False)
    finally:
        server.stop()


def step_of(run: journey.Journey, name: str) -> dict:
    """Return the recorded step ``name`` of the journey."""
    for entry in run.steps:
        if entry["name"] == name:
            return entry
    raise AssertionError(
        f"the journey has no step named {name!r}; it ran: "
        + ", ".join(entry["name"] for entry in run.steps)
    )


def assert_passed(run: journey.Journey, name: str) -> None:
    """Assert that the journey step ``name`` passed."""
    entry = step_of(run, name)
    assert entry["ok"], f"the journey step {name!r} failed: {entry['detail']}"


#: Every step the journey has to walk through.  A step that is missing is a
#: step of the product that nobody walks any more.
EXPECTED_STEPS = (
    "the workbench answers",
    "the module catalogue",
    "upload the recording",
    "analysis: spectrogram",
    "analysis: the frequency axis",
    "analysis: every row carries its own band",
    "analysis: waveform envelope",
    "analysis: a selection is measured",
    "analysis: the selection leaves the click outside",
    "analysis: the selection is reported",
    "chain: add De-Hum and De-Click",
    "chain: reorder",
    "chain: set a parameter",
    "chain: bypass De-Hum",
    "chain: engage De-Hum again",
    "render: the chain runs",
    "render: a valid WAV of the right length",
    "render: the hum is measurably reduced",
    "render: the programme survives",
    "preset: save",
    "preset: list",
    "preset: the shipped library is there",
    "preset: load and round-trip the values",
    "session: save the document",
    "session: the document lists in the library",
    "session: load the document",
    "undo: the change is reverted",
    "undo: the history is in the document",
    "undo: the chain comes back as it was",
    "compare: the two renders",
    "compare: the hum is gone in one of them",
    "delivery: the catalogue",
    "batch: start the job",
    "batch: the job finished",
    "batch: the output exists",
    "batch: every delivered file has a loudness",
    "batch: the loudness follows the input",
)


# ---------------------------------------------------------------------------
# the journey itself
# ---------------------------------------------------------------------------
def test_the_journey_runs_every_step(journey_run: journey.Journey) -> None:
    """The journey walks through the whole product and records every step."""
    names = [entry["name"] for entry in journey_run.steps]
    assert names, "the journey recorded no step at all"
    for expected in EXPECTED_STEPS:
        assert expected in names, f"the journey never ran the step {expected!r}"


def test_the_journey_passes(journey_run: journey.Journey) -> None:
    """Every step of the journey passes: the journey is the gate."""
    summary = journey_run.summary()
    failures = [
        f"{step['name']}: {step['detail']}" for step in journey_run.steps if not step["ok"]
    ]
    assert summary["ok"], "the journey failed:\n  " + "\n  ".join(failures)


def test_every_step_asserts_something(journey_run: journey.Journey) -> None:
    """A step that only calls something is not a step."""
    for entry in journey_run.steps:
        assert entry["assertions"] >= 1, (
            f"the journey step {entry['name']!r} made no assertion"
        )
    total = sum(entry["assertions"] for entry in journey_run.steps)
    assert total >= len(journey_run.steps), (
        f"the journey made {total} assertions over {len(journey_run.steps)} steps"
    )


def test_the_summary_is_machine_readable(journey_run: journey.Journey) -> None:
    """The summary carries the verdicts in a shape a tool can read."""
    summary = journey_run.summary()
    assert set(summary) == {
        "ok",
        "base_url",
        "passed",
        "failed",
        "total",
        "assertions",
        "steps",
        "measurements",
        "volatile",
    }
    assert summary["total"] == len(journey_run.steps)
    assert summary["passed"] == sum(1 for e in journey_run.steps if e["ok"])
    assert summary["failed"] == summary["total"] - summary["passed"]
    assert summary["assertions"] == sum(e["assertions"] for e in journey_run.steps)
    assert summary["ok"] is (summary["passed"] == summary["total"])
    for entry in summary["steps"]:
        assert set(entry) == {"name", "ok", "detail", "assertions", "measurements"}
        assert isinstance(entry["ok"], bool)
        assert isinstance(entry["name"], str) and entry["name"]
        assert isinstance(entry["assertions"], int) and entry["assertions"] >= 1
        assert isinstance(entry["measurements"], dict)
        for value in entry["measurements"].values():
            assert isinstance(value, (int, float, bool, str))
    # the measurements are the numbers of the run, keyed by the step
    assert set(summary["measurements"]) == {e["name"] for e in summary["steps"]}
    for name, numbers in summary["measurements"].items():
        assert isinstance(numbers, dict)
        assert numbers, f"the step {name!r} reported no measurement"
    # the fields that cannot be the same in two runs are named
    assert summary["volatile"]
    for field, reason in summary["volatile"].items():
        assert isinstance(field, str) and field
        assert isinstance(reason, str) and reason


def test_the_measurements_are_deterministic(
    journey_run: journey.Journey, journey_run_again: journey.Journey
) -> None:
    """Two runs of the same build report the same numbers."""
    first = journey_run.summary()["measurements"]
    second = journey_run_again.summary()["measurements"]
    assert set(first) == set(second), "the two runs walked through different steps"
    for name in first:
        assert first[name] == second[name], (
            f"the step {name!r} reported different numbers in two runs: "
            f"{first[name]} and {second[name]}"
        )


def test_the_workbench_answers(journey_run: journey.Journey) -> None:
    """The server is alive and the module catalogue is complete."""
    assert_passed(journey_run, "the workbench answers")
    assert_passed(journey_run, "the module catalogue")


def test_the_recording_is_uploaded(journey_run: journey.Journey) -> None:
    """The upload comes back with the right format."""
    assert_passed(journey_run, "upload the recording")


def test_the_analysis_shows_the_recording(journey_run: journey.Journey) -> None:
    """The spectrogram and the waveform envelope are present and sane."""
    assert_passed(journey_run, "analysis: spectrogram")
    assert_passed(journey_run, "analysis: waveform envelope")


def test_the_frequency_axis_is_honest(journey_run: journey.Journey) -> None:
    """The programme tone is reported at the frequency it really has.

    The axis used to be off by a factor of 1.5 (a 1 kHz tone was reported
    at 656.2 Hz and the bottom 170 of the 512 rows were copies of each
    other).  The defect is fixed, so this is a plain test: if the axis
    breaks again, the journey fails.
    """
    assert_passed(journey_run, "analysis: the frequency axis")
    assert_passed(journey_run, "analysis: every row carries its own band")


def test_the_analysis_of_a_selection(journey_run: journey.Journey) -> None:
    """A selection is measured as a selection, not as the whole file."""
    assert_passed(journey_run, "analysis: a selection is measured")
    assert_passed(journey_run, "analysis: the selection leaves the click outside")
    assert_passed(journey_run, "analysis: the selection is reported")


def test_the_chain_is_built(journey_run: journey.Journey) -> None:
    """Modules can be added, reordered, tuned and bypassed."""
    assert_passed(journey_run, "chain: add De-Hum and De-Click")
    assert_passed(journey_run, "chain: reorder")
    assert_passed(journey_run, "chain: set a parameter")
    assert_passed(journey_run, "chain: bypass De-Hum")
    assert_passed(journey_run, "chain: engage De-Hum again")


def test_the_render_is_a_valid_wav(journey_run: journey.Journey) -> None:
    """The render is a WAV of the right length and the hum is gone."""
    assert_passed(journey_run, "render: the chain runs")
    assert_passed(journey_run, "render: a valid WAV of the right length")
    assert_passed(journey_run, "render: the hum is measurably reduced")


def test_the_repair_keeps_the_programme(journey_run: journey.Journey) -> None:
    """Removing the hum must not eat the programme.

    De-Click used to cost the 1 kHz programme 8 to 10 dB at a sensitivity of
    5 to 6.  The defect is fixed, so this is a plain test: if the repair
    damages the programme again, the journey fails.
    """
    assert_passed(journey_run, "render: the programme survives")


def test_the_presets_round_trip(journey_run: journey.Journey) -> None:
    """A preset can be saved, listed and loaded back unchanged."""
    assert_passed(journey_run, "preset: save")
    assert_passed(journey_run, "preset: list")
    assert_passed(journey_run, "preset: the shipped library is there")
    assert_passed(journey_run, "preset: load and round-trip the values")


def test_the_session_document_round_trips(journey_run: journey.Journey) -> None:
    """A session document can be saved and loaded back with its chain."""
    assert_passed(journey_run, "session: save the document")
    assert_passed(journey_run, "session: the document lists in the library")
    assert_passed(journey_run, "session: load the document")


def test_the_undo_comes_back(journey_run: journey.Journey) -> None:
    """A change can be taken back: the chain is as it was."""
    assert_passed(journey_run, "undo: the change is reverted")
    assert_passed(journey_run, "undo: the history is in the document")
    assert_passed(journey_run, "undo: the chain comes back as it was")


def test_the_compare_route(journey_run: journey.Journey) -> None:
    """Two chains can be compared over the same recording."""
    assert_passed(journey_run, "compare: the two renders")
    assert_passed(journey_run, "compare: the hum is gone in one of them")


def test_the_delivery_catalogue_is_served(journey_run: journey.Journey) -> None:
    """The delivery catalogue is reachable over HTTP."""
    assert_passed(journey_run, "delivery: the catalogue")


def test_the_batch_job_writes_its_outputs(journey_run: journey.Journey) -> None:
    """A batch job runs to the end and leaves the rendered files behind."""
    assert_passed(journey_run, "batch: start the job")
    assert_passed(journey_run, "batch: the job finished")
    assert_passed(journey_run, "batch: the output exists")


def test_the_batch_reports_the_loudness(journey_run: journey.Journey) -> None:
    """Every delivered file carries its own loudness."""
    assert_passed(journey_run, "batch: every delivered file has a loudness")
    assert_passed(journey_run, "batch: the loudness follows the input")


# ---------------------------------------------------------------------------
# the markers
# ---------------------------------------------------------------------------
def test_the_xfail_markers_are_strict() -> None:
    """A marker in this file has to be strict.

    A soft marker (``strict=False``) hides a fixed defect: the test passes
    and nobody notices that the marker has to go.  A strict marker turns the
    fix into a failure, so the fix cannot pass unnoticed.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if not isinstance(target, ast.Attribute) or target.attr != "xfail":
                continue
            strict = None
            if isinstance(decorator, ast.Call):
                for keyword in decorator.keywords:
                    if keyword.arg == "strict":
                        strict = ast.literal_eval(keyword.value)
            found.append((node.name, strict))
    for name, strict in found:
        assert strict is True, (
            f"the xfail marker of {name} is not strict; a fixed defect would "
            "pass unnoticed"
        )
