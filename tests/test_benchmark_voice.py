"""
tests/test_benchmark_voice.py — the gate of the voice head to head.

The numbers of the voice comparison live in
``qa/benchmark/reference_voice.py``.  A number that cannot be read is a number
that cannot be argued about, so this gate asserts the **properties of the
constants**:

* every constant of the comparison is there, under the name it was recorded
  under, with the value it was recorded with;
* every constant names the **case** it belongs to, the **unit** it is in and
  the **way it was measured**;
* the unit of a constant is the unit of its metric;
* every value is finite;
* the file names no product of anybody.

And it asserts the one thing that is a **contract** rather than a target: the
numbers of **our** side.  A measurement of a reference was taken on somebody
else's build, on somebody else's machine, with somebody else's settings, so
nothing here fails on it — a gate that failed on it would be a gate held by a
machine we do not control.  Our side, on the other hand, is measured live, with
the voice instrument of the project, and it has to hold:

* the voice de-noise removes at least 15 dB of hiss from the voice clip of the
  corpus;
* it moves the wanted voice by at most 1.5 dB (our own rule R1);
* its gain curve is rougher than 1.0 dB (our own rule R3) nowhere;
* its output does not depend on the block size of the host by more than
  -60 dB (our own rule R5);
* the gain it applies does not follow the level of the input: over the 18 dB
  the level of the input moves by, the applied gain moves by at most 0.5 dB
  (our own rule R9);
* the delay it adds is the delay it declares, at every block size of the
  host.

**Every number of ours is measured live and held to the constants file**: the
hiss removed, the voice kept, the chatter, the block coherence, the delay and
the delay against the block size of the host.  The gate is not a copy of the
constants compared with itself: it drives the module of the tree with the
instrument of the project and compares what comes out with what the file
records.

**If those numbers no longer hold, this test fails.**  That is a real
regression of the module, not a test to be loosened: the constants are
re-measured with the same instrument and the comparison in
``docs/VOICE_HEAD_TO_HEAD.md`` is written down.
"""
from __future__ import annotations

import importlib.util
import math
import re
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BENCH = _ROOT / "qa" / "benchmark"
_CONSTANTS = _BENCH / "reference_voice.py"
_DOCUMENT = _ROOT / "docs" / "VOICE_HEAD_TO_HEAD.md"

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aurcore import registry  # noqa: E402
from tools import hygiene_check  # noqa: E402


def _load(name: str, path: Path):
    """Load a module by path, under a name of its own."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


constants = _load("aur_qa_benchmark_reference_voice", _CONSTANTS)
cases_module = _load("aur_qa_benchmark_cases", _BENCH / "cases.py")
harness_module = _load("aur_qa_benchmark_harness", _BENCH / "harness.py")
voice_harness = _load("aur_qa_qualityvoice_harness", _ROOT / "qa" / "qualityvoice" / "harness.py")

# ---------------------------------------------------------------------------
# the numbers of the comparison
#
# The name of every constant and the value it carries.  A constant that is
# added, renamed or given another value has to change this table with it: the
# table is what says what was measured.
# ---------------------------------------------------------------------------
EXPECTED: dict[str, float] = {
    # our side, the voice clip of the corpus
    "OUR_CORPUS_VOICE_REMOVED_DB": 29.65,
    "OUR_CORPUS_VOICE_KEPT_DB": 0.06,
    "OUR_CORPUS_VOICE_CHATTER_DB": 0.03,
    "OUR_CORPUS_VOICE_BLOCK_COHERENCE_DB": -221.9,
    "OUR_CORPUS_VOICE_DELAY_SAMPLES": 2047,
    # our side, the synthetic voice
    "OUR_SYNTHETIC_VOICE_REMOVED_DB": 29.69,
    "OUR_SYNTHETIC_VOICE_KEPT_DB": 0.18,
    "OUR_SYNTHETIC_VOICE_CHATTER_DB": 0.05,
    "OUR_SYNTHETIC_VOICE_BLOCK_COHERENCE_DB": -218.2,
    "OUR_SYNTHETIC_VOICE_DELAY_SAMPLES": 2047,
    # our side, the speech shaped signal
    "OUR_SPEECH_SHAPED_REMOVED_DB": 29.73,
    "OUR_SPEECH_SHAPED_KEPT_DB": 0.12,
    "OUR_SPEECH_SHAPED_CHATTER_DB": 0.03,
    "OUR_SPEECH_SHAPED_BLOCK_COHERENCE_DB": -213.9,
    "OUR_SPEECH_SHAPED_DELAY_SAMPLES": 2047,
    # the reference, the setting that holds the voice budget
    "REFERENCE_CORPUS_VOICE_REMOVED_DB": 0.09,
    "REFERENCE_CORPUS_VOICE_KEPT_DB": 0.15,
    "REFERENCE_CORPUS_VOICE_CHATTER_DB": 0.00,
    "REFERENCE_CORPUS_VOICE_BLOCK_COHERENCE_DB": -42.4,
    "REFERENCE_CORPUS_VOICE_DELAY_SAMPLES": 2238,
    "REFERENCE_SYNTHETIC_VOICE_REMOVED_DB": 8.94,
    "REFERENCE_SYNTHETIC_VOICE_KEPT_DB": 0.04,
    "REFERENCE_SYNTHETIC_VOICE_CHATTER_DB": 0.45,
    "REFERENCE_SYNTHETIC_VOICE_BLOCK_COHERENCE_DB": -60.8,
    "REFERENCE_SYNTHETIC_VOICE_DELAY_SAMPLES": 2238,
    "REFERENCE_SPEECH_SHAPED_REMOVED_DB": 0.05,
    "REFERENCE_SPEECH_SHAPED_KEPT_DB": 0.20,
    "REFERENCE_SPEECH_SHAPED_CHATTER_DB": 0.00,
    "REFERENCE_SPEECH_SHAPED_BLOCK_COHERENCE_DB": -136.2,
    "REFERENCE_SPEECH_SHAPED_DELAY_SAMPLES": 2238,
    # the reference, the most aggressive setting
    "REFERENCE_CORPUS_VOICE_AGGRESSIVE_REMOVED_DB": 23.87,
    "REFERENCE_CORPUS_VOICE_AGGRESSIVE_KEPT_DB": 15.83,
    "REFERENCE_CORPUS_VOICE_AGGRESSIVE_CHATTER_DB": 0.89,
    "REFERENCE_SYNTHETIC_VOICE_AGGRESSIVE_REMOVED_DB": 8.92,
    "REFERENCE_SYNTHETIC_VOICE_AGGRESSIVE_KEPT_DB": 0.04,
    "REFERENCE_SYNTHETIC_VOICE_AGGRESSIVE_CHATTER_DB": 0.45,
    "REFERENCE_SPEECH_SHAPED_AGGRESSIVE_REMOVED_DB": 35.73,
    "REFERENCE_SPEECH_SHAPED_AGGRESSIVE_KEPT_DB": 16.59,
    "REFERENCE_SPEECH_SHAPED_AGGRESSIVE_CHATTER_DB": 0.89,
    # the reference, the sweep of the voice clip of the corpus
    "REFERENCE_CORPUS_VOICE_DEFAULT_REMOVED_DB": 0.00,
    "REFERENCE_CORPUS_VOICE_DEFAULT_KEPT_DB": 0.01,
    "REFERENCE_CORPUS_VOICE_MILD_REMOVED_DB": 3.35,
    "REFERENCE_CORPUS_VOICE_MILD_KEPT_DB": 3.26,
    "REFERENCE_CORPUS_VOICE_MEDIUM_REMOVED_DB": 9.69,
    "REFERENCE_CORPUS_VOICE_MEDIUM_KEPT_DB": 9.03,
    "REFERENCE_CORPUS_VOICE_STEM_ALONE_REMOVED_DB": 23.45,
    "REFERENCE_CORPUS_VOICE_STEM_ALONE_KEPT_DB": 16.28,
    # the delay against the block size of the host
    "REFERENCE_DELAY_SAMPLES_BLOCK_64": 1534,
    "REFERENCE_DELAY_SAMPLES_BLOCK_256": 1470,
    "REFERENCE_DELAY_SAMPLES_BLOCK_1024": 2238,
    "REFERENCE_DELAY_SAMPLES_BLOCK_4096": 5310,
    "OUR_DELAY_SAMPLES_BLOCK_64": 2047,
    "OUR_DELAY_SAMPLES_BLOCK_256": 2047,
    "OUR_DELAY_SAMPLES_BLOCK_1024": 2047,
    "OUR_DELAY_SAMPLES_BLOCK_4096": 2047,
    # the stereo image
    "REFERENCE_SIDE_CHANNEL_RMS_IN": 0.0186,
    "REFERENCE_SIDE_CHANNEL_RMS_OUT": 0.0,
}

#: The names that are measurements of our own module.  They carry their own
#: series, so that a reader can tell which side of the comparison they are on.
OURS = tuple(name for name in EXPECTED if name.startswith("OUR_"))

#: The budgets our own module is held to, on the corpus voice clip.  They are
#: the rules of the voice instrument (R1 the voice, R3 the chatter, R5 the
#: block coherence, R9 the level) and the removal the study measured.
OUR_MIN_REMOVED_DB = 15.0
OUR_MAX_KEPT_DB = 1.5
OUR_MAX_CHATTER_DB = 1.0
OUR_MAX_BLOCK_COHERENCE_DB = -60.0
OUR_MAX_LEVEL_SPREAD_DB = 0.5

#: The delay the module declares and the delay it adds, in samples.
OUR_DELAY_SAMPLES = 2047

#: The tolerance of the comparison between a recorded number and a live
#: measurement of it.  The numbers are recorded to two decimals, so this is
#: the rounding and a little more.
TOLERANCE_DB = 0.1

#: The tolerance of the comparison for the block coherence.  That number sits
#: at the floor of the arithmetic of the module -- it is the difference
#: between two renders that should be the same -- so it is held loosely: what
#: matters is that it stays far below the budget, and the budget is asserted
#: on its own.
TOLERANCE_BLOCK_COHERENCE_DB = 2.0


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------
def test_the_constants_are_importable():
    """The file has to be a module a reader can import."""
    assert constants.VOICES, "there is no constant at all"
    assert constants.METRICS, "there is no metric at all"


def test_the_file_names_no_product():
    """No trademark of anybody may appear in the file.

    The gate of the repository is used to check the gate of the constants, so
    the two can never disagree about what a banned name is.
    """
    report = hygiene_check.Report(root=str(_ROOT))
    hygiene_check.check_text(
        "qa/benchmark/reference_voice.py",
        _CONSTANTS.read_text(encoding="utf-8"),
        report,
    )
    assert not report.findings, "\n".join(f.format() for f in report.findings)


def test_the_document_names_no_product():
    """The same rule holds for the note that reads the constants."""
    report = hygiene_check.Report(root=str(_ROOT))
    hygiene_check.check_text(
        "docs/VOICE_HEAD_TO_HEAD.md",
        _DOCUMENT.read_text(encoding="utf-8"),
        report,
    )
    assert not report.findings, "\n".join(f.format() for f in report.findings)


def test_the_file_names_no_file_of_a_foreign_product():
    """The file and the note may not carry the name of a file of somebody else.

    The gate above hunts the names of products; this one hunts the names of
    the files a foreign product ships in, because a file name is a name too.
    """
    pattern = re.compile(r"\.(vst3|fxp|aaxplugin|component|plugin)\b", re.IGNORECASE)
    for path in (_CONSTANTS, _DOCUMENT):
        text = path.read_text(encoding="utf-8")
        assert not pattern.search(text), path


# ---------------------------------------------------------------------------
# the constants
# ---------------------------------------------------------------------------
def test_every_constant_of_the_comparison_is_there():
    """A constant that is missing is a number that was measured and lost."""
    assert constants.VOICE_BY_NAME.keys() == EXPECTED.keys()
    for name, value in EXPECTED.items():
        assert getattr(constants, name) == value, name


def test_every_number_carries_its_case_its_unit_and_its_method():
    """A number without those three cannot be read, and cannot be replaced."""
    for voice in constants.VOICES:
        assert voice.name, voice
        assert voice.case, voice.name
        assert voice.unit, voice.name
        assert voice.method, voice.name
        assert voice.series in constants.SERIES, voice.name


def test_the_unit_of_a_number_is_the_unit_of_its_metric():
    """The unit is carried twice on purpose, and the two have to agree."""
    for voice in constants.VOICES:
        assert voice.metric in constants.METRIC_BY_NAME, voice.name
        rule = constants.METRIC_BY_NAME[voice.metric]
        assert voice.unit == rule.unit, voice.name


def test_every_value_is_finite():
    """A number that is not finite cannot be compared with anything."""
    for voice in constants.VOICES:
        assert math.isfinite(float(voice.value)), voice.name
    for name, value in EXPECTED.items():
        assert math.isfinite(float(value)), name


def test_the_table_of_the_constants_and_the_table_here_agree():
    """The value in the table is the value in the record of the number."""
    for voice in constants.VOICES:
        assert EXPECTED[voice.name] == float(voice.value), voice.name


def test_a_number_of_ours_is_filed_under_its_own_series():
    """A number of ours must never be mistaken for a number of a reference."""
    for name in OURS:
        assert constants.VOICE_BY_NAME[name].series == "ours", name
    for voice in constants.VOICES:
        if voice.series != "ours":
            assert voice.name not in OURS, voice.name
    assert "ours" in constants.SERIES


def test_the_case_of_a_number_is_one_the_file_declares():
    """A number has to belong to a case, and the cases are written down."""
    for voice in constants.VOICES:
        assert voice.case in constants.CASES, voice.name


def test_every_case_of_the_comparison_can_be_named():
    """The vocabulary of the file has to cover the cases of the benchmark."""
    for case in cases_module.CASES:
        if case.kind != "voice":
            continue
        assert case.name in constants.CASES, case.name


def test_the_reference_series_is_the_series_of_the_benchmark():
    """The constants file and the benchmark have to speak of the same series."""
    benchmark = _load("aur_qa_benchmark_reference", _BENCH / "reference.py")
    assert "stem_separation" in benchmark.SERIES
    assert "stem_separation" in constants.SERIES


# ---------------------------------------------------------------------------
# the tree the numbers describe
# ---------------------------------------------------------------------------
def test_the_tree_the_numbers_describe_is_recorded():
    """A number without the tree it was measured on cannot be read later."""
    for name in (
        "TREE_SHA256_VOICE_DE_NOISE",
        "TREE_SHA256_PARAMS",
        "TREE_SHA256_PARAMS_TABLE",
        "STUDY_TREE_SHA256_VOICE_DE_NOISE",
        "STUDY_TREE_SHA256_PARAMS",
    ):
        digest = getattr(constants, name)
        assert re.fullmatch(r"[0-9a-f]{64}", digest), name
    assert constants.TREE_MEASURED_AT, "the moment of the measurement is not recorded"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", constants.TREE_MEASURED_AT)
    assert constants.TREE_MOVED_SINCE_THE_STUDY is True
    assert constants.NUMBERS_HOLD_ON_THE_CURRENT_TREE is True


def test_the_numbers_moved_with_the_module_and_the_file_says_so():
    """The module was rewritten, the numbers moved with it, and the file says so.

    The numbers of ours are not the numbers of the study any more: the module
    was rewritten (the ratio domain) and the numbers were taken again on the
    new tree.  Only the numbers of ours moved; the numbers of the reference
    are the study's and do not depend on our tree.
    """
    assert constants.NUMBERS_SAME_AS_THE_STUDY is False
    assert constants.THE_NUMBERS_MOVED_WITH_THE_MODULE is True
    assert constants.REMOVAL_GAINED_BY_THE_REWRITE_DB > 0.0
    assert constants.LEVEL_SPREAD_AFTER_THE_REWRITE_DB < 1.0
    assert constants.LEVEL_SPREAD_BEFORE_THE_REWRITE_DB > 1.0


def test_every_revision_the_numbers_were_measured_on_is_recorded():
    """The module was rewritten while the numbers were being confirmed.

    The numbers of ours were taken again on every revision of the module that
    the tree went through.  They came out the same on the first six; the
    rewrite moved them, and the numbers of this file are the ones of the last
    revision.  The revisions are written down, so that a reader can tell
    which tree a number belongs to.
    """
    revisions = constants.TREE_SHA256_VOICE_DE_NOISE_REVISIONS
    assert len(revisions) == constants.TREE_REVISIONS_MEASURED
    assert constants.TREE_REVISIONS_MEASURED >= 2
    for digest in revisions:
        assert re.fullmatch(r"[0-9a-f]{64}", digest), digest
    assert revisions[0] == constants.STUDY_TREE_SHA256_VOICE_DE_NOISE
    assert revisions[-1] == constants.TREE_SHA256_VOICE_DE_NOISE
    assert len(set(revisions)) == len(revisions), "the same tree is listed twice"


def test_the_method_of_a_number_of_ours_names_the_tree_and_the_moment():
    """A number of ours has to say which module it was measured on.

    The method string of a number of ours carries the sha256 of the module
    and the moment of the measurement, so that a reader can tell which tree
    the number belongs to and how old it is.
    """
    for voice in constants.VOICES:
        if voice.series != "ours":
            continue
        assert constants.TREE_SHA256_VOICE_DE_NOISE in voice.method, voice.name
        assert constants.TREE_MEASURED_AT in voice.method, voice.name


def test_the_shape_of_the_two_sides_is_recorded():
    """The two facts that are not numbers still have to be written down."""
    assert constants.REFERENCE_OUTPUT_IS_DUAL_MONO is True
    assert constants.REFERENCE_DELAY_DEPENDS_ON_BLOCK_SIZE is True
    assert constants.OUR_DELAY_DEPENDS_ON_BLOCK_SIZE is False
    assert constants.BAND_RESTORATION_VOICE_REACHABLE is False


# ---------------------------------------------------------------------------
# our side, measured live
# ---------------------------------------------------------------------------
_MEASURED: dict[str, dict] = {}

#: The block sizes the delay of ours is measured against.
BLOCK_SIZES: tuple[int, ...] = (64, 256, 1024, 4096)


def _case(case_name: str):
    """The case of the benchmark, at the setting the numbers were taken at."""
    base = cases_module.CASE_BY_NAME[case_name]
    settings = dict(base.settings)
    settings["Reduction"] = 30.0
    return cases_module.Case(
        name=base.name,
        kind=base.kind,
        module=base.module,
        artefact=base.artefact,
        wanted=base.wanted,
        settings=tuple(sorted(settings.items())),
        reason=base.reason,
    )


def _factory(case, channels: int):
    """A fresh instance of the module of the case, with its setting.

    The module is built through the registry of the core, so the gate drives
    the module a host would drive.
    """

    def make():
        module = registry.create(case.module, int(harness_module.SR), int(channels))
        for key, value in case.settings_map.items():
            if module.params.has(key):
                module.params.set(key, value)
        return module

    return make


def _voice_case(case):
    """The case of the benchmark as a case of the voice instrument.

    The signal is the one the benchmark builds for the case, with the two
    bands it reads the numbers in.
    """
    signal, _voice_band, hiss_band, _stereo = harness_module._voice_case_signal(case)
    data = np.asarray(signal, dtype=np.float64)
    return voice_harness.Case(
        name=case.name,
        x=data,
        voice=data,
        background=np.zeros_like(data),
    ), hiss_band


def _measure(case_name: str) -> dict:
    """Measure our voice module on a voice case, with the project's instrument.

    The measurement is the one of the benchmark: the voice instrument of the
    project measures the removal, the movement of the wanted content, the
    chatter of the gain and the delay.  The block coherence and the spread of
    the applied gain over the level of the input are measured with the same
    instrument, on the same signal.  The module runs at the reduction the
    numbers of the comparison were taken at.
    """
    if case_name in _MEASURED:
        return _MEASURED[case_name]
    case = _case(case_name)
    numbers = dict(harness_module.measure_voice_case(case))

    vcase, hiss_band = _voice_case(case)
    channels = int(vcase.x.shape[1])
    factory = _factory(case, channels)

    coherence = voice_harness.measure_block_coherence(
        vcase, factory, block_size=4096
    )
    numbers["block_coherence_db"] = float(coherence["block_rms_diff_db"])

    spread = voice_harness.measure_level_spread(
        vcase, factory(), dict(case.settings_map), band=hiss_band
    )
    numbers["level_spread_db"] = float(spread["level_spread_db"])

    _MEASURED[case_name] = numbers
    return numbers


def _measure_delay_against_the_block_size(block: int) -> int:
    """The delay our module adds, when the host chops the stream into blocks.

    The signal is the voice clip of the corpus, fed to the module in blocks of
    this size, and the delay is read with the voice instrument of the project.
    """
    case = _case("corpus_voice")
    signal = np.asarray(
        harness_module._voice_case_signal(case)[0], dtype=np.float64
    )
    module = _factory(case, int(signal.shape[1]))()
    pieces = []
    for start in range(0, int(signal.shape[0]), int(block)):
        chunk = signal[start : start + int(block)]
        piece = np.asarray(module.process(chunk), dtype=np.float64)
        pieces.append(piece[:, None] if piece.ndim == 1 else piece)
    rendered = np.concatenate(pieces, axis=0)[: signal.shape[0]]
    return int(voice_harness.measure_latency(signal, rendered))


@pytest.mark.parametrize(
    "case_name", ["corpus_voice", "synthetic_voice", "speech_shaped"]
)
def test_our_side_removes_the_hiss_without_touching_the_voice(case_name: str):
    """The gate of the comparison: our side, on the three voice cases.

    The voice de-noise has to take at least 15 dB of hiss away while it moves
    the wanted voice by at most 1.5 dB and its gain curve by at most 1.0 dB.
    A module that no longer does that is a regression, and the numbers of
    ``qa/benchmark/reference_voice.py`` have to be measured again.
    """
    numbers = _measure(case_name)
    assert numbers["removed_db"] >= OUR_MIN_REMOVED_DB, (
        "%s: the voice de-noise removes %.2f dB of hiss, the gate asks for "
        "%.1f dB" % (case_name, numbers["removed_db"], OUR_MIN_REMOVED_DB)
    )
    assert numbers["kept_db"] <= OUR_MAX_KEPT_DB, (
        "%s: the voice de-noise moves the wanted voice by %.2f dB, the budget "
        "is %.1f dB" % (case_name, numbers["kept_db"], OUR_MAX_KEPT_DB)
    )
    assert numbers["chatter_db"] <= OUR_MAX_CHATTER_DB, (
        "%s: the gain curve of the voice de-noise is %.2f dB rough, the budget "
        "is %.1f dB" % (case_name, numbers["chatter_db"], OUR_MAX_CHATTER_DB)
    )


@pytest.mark.parametrize(
    "case_name", ["corpus_voice", "synthetic_voice", "speech_shaped"]
)
def test_our_side_is_block_coherent(case_name: str):
    """Rule R5: the output may not depend on the block size of the host.

    The block coherence is measured with the voice instrument of the project:
    the same signal in one piece and in blocks of 4096 samples, and the rms
    difference of the two renders.  Our budget is -60 dB.
    """
    numbers = _measure(case_name)
    assert numbers["block_coherence_db"] <= OUR_MAX_BLOCK_COHERENCE_DB, (
        "%s: the output of the voice de-noise moves by %.2f dB when the host "
        "chops the stream into blocks, the budget is %.1f dB"
        % (case_name, numbers["block_coherence_db"], OUR_MAX_BLOCK_COHERENCE_DB)
    )


@pytest.mark.parametrize(
    "case_name", ["corpus_voice", "synthetic_voice", "speech_shaped"]
)
def test_our_side_does_not_follow_the_level_of_the_input(case_name: str):
    """Rule R9: the applied gain may not depend on the level of the input.

    The same material is played at five levels under a fixed setting and the
    gain the module applies in the band that carries the noise is measured
    frame by frame.  The level of the input moves by 18 dB; the applied gain
    may move by at most 0.5 dB.  This is the rule the rewrite of the module
    (the ratio domain) delivered.
    """
    numbers = _measure(case_name)
    assert numbers["level_spread_db"] <= OUR_MAX_LEVEL_SPREAD_DB, (
        "%s: the applied gain of the voice de-noise moves by %.3f dB while the "
        "level of the input moves by 18 dB, the budget is %.1f dB"
        % (case_name, numbers["level_spread_db"], OUR_MAX_LEVEL_SPREAD_DB)
    )


@pytest.mark.parametrize(
    ("case_name", "prefix"),
    [
        ("corpus_voice", "OUR_CORPUS_VOICE"),
        ("synthetic_voice", "OUR_SYNTHETIC_VOICE"),
        ("speech_shaped", "OUR_SPEECH_SHAPED"),
    ],
)
def test_the_numbers_of_ours_still_hold(case_name: str, prefix: str):
    """The numbers of ours in the constants file are the numbers we measure.

    The numbers were taken again on the tree the constants file names, and
    they came out the same.  If the module has moved since, this fails: the
    comparison in ``docs/VOICE_HEAD_TO_HEAD.md`` has to be measured again.
    """
    numbers = _measure(case_name)
    assert abs(numbers["removed_db"] - EXPECTED[prefix + "_REMOVED_DB"]) <= TOLERANCE_DB, (
        "%s: the hiss removed is %.2f dB, the constants file says %.2f dB"
        % (case_name, numbers["removed_db"], EXPECTED[prefix + "_REMOVED_DB"])
    )
    assert abs(numbers["kept_db"] - EXPECTED[prefix + "_KEPT_DB"]) <= TOLERANCE_DB, (
        "%s: the voice moves %.2f dB, the constants file says %.2f dB"
        % (case_name, numbers["kept_db"], EXPECTED[prefix + "_KEPT_DB"])
    )
    assert abs(numbers["chatter_db"] - EXPECTED[prefix + "_CHATTER_DB"]) <= TOLERANCE_DB, (
        "%s: the chatter is %.2f dB, the constants file says %.2f dB"
        % (case_name, numbers["chatter_db"], EXPECTED[prefix + "_CHATTER_DB"])
    )
    assert abs(
        numbers["block_coherence_db"] - EXPECTED[prefix + "_BLOCK_COHERENCE_DB"]
    ) <= TOLERANCE_BLOCK_COHERENCE_DB, (
        "%s: the block coherence is %.2f dB, the constants file says %.2f dB"
        % (case_name, numbers["block_coherence_db"],
           EXPECTED[prefix + "_BLOCK_COHERENCE_DB"])
    )
    assert int(numbers["latency_samples"]) == EXPECTED[prefix + "_DELAY_SAMPLES"], case_name


@pytest.mark.parametrize("block", BLOCK_SIZES)
def test_the_delay_of_ours_does_not_follow_the_block_size(block: int):
    """The delay our module adds is the delay it declares, at every block size.

    The reference's delay follows the block size of the host; ours does not.
    The gate measures it live, block by block, with the voice instrument of
    the project.
    """
    measured = _measure_delay_against_the_block_size(block)
    assert measured == OUR_DELAY_SAMPLES, (
        "a host block of %d samples: the voice de-noise adds %d samples of "
        "delay, it declares %d"
        % (block, measured, OUR_DELAY_SAMPLES)
    )
    assert getattr(constants, "OUR_DELAY_SAMPLES_BLOCK_%d" % block) == measured, block


# ---------------------------------------------------------------------------
# the note and the constants must not drift apart
# ---------------------------------------------------------------------------
#: The constants the note has to carry.  The list is read from the constants
#: file, so the note is held to the numbers of the file and not to a copy of
#: them that can rot.
DOCUMENT_NUMBERS: tuple[str, ...] = (
    # our side, the three cases
    "OUR_CORPUS_VOICE_REMOVED_DB",
    "OUR_CORPUS_VOICE_KEPT_DB",
    "OUR_CORPUS_VOICE_CHATTER_DB",
    "OUR_CORPUS_VOICE_BLOCK_COHERENCE_DB",
    "OUR_CORPUS_VOICE_DELAY_SAMPLES",
    "OUR_SYNTHETIC_VOICE_REMOVED_DB",
    "OUR_SYNTHETIC_VOICE_KEPT_DB",
    "OUR_SYNTHETIC_VOICE_CHATTER_DB",
    "OUR_SYNTHETIC_VOICE_BLOCK_COHERENCE_DB",
    "OUR_SYNTHETIC_VOICE_DELAY_SAMPLES",
    "OUR_SPEECH_SHAPED_REMOVED_DB",
    "OUR_SPEECH_SHAPED_KEPT_DB",
    "OUR_SPEECH_SHAPED_CHATTER_DB",
    "OUR_SPEECH_SHAPED_BLOCK_COHERENCE_DB",
    "OUR_SPEECH_SHAPED_DELAY_SAMPLES",
    # the reference, the setting that holds the voice budget
    "REFERENCE_CORPUS_VOICE_REMOVED_DB",
    "REFERENCE_CORPUS_VOICE_KEPT_DB",
    "REFERENCE_CORPUS_VOICE_CHATTER_DB",
    "REFERENCE_CORPUS_VOICE_BLOCK_COHERENCE_DB",
    "REFERENCE_CORPUS_VOICE_DELAY_SAMPLES",
    "REFERENCE_SYNTHETIC_VOICE_REMOVED_DB",
    "REFERENCE_SYNTHETIC_VOICE_KEPT_DB",
    "REFERENCE_SYNTHETIC_VOICE_CHATTER_DB",
    "REFERENCE_SYNTHETIC_VOICE_BLOCK_COHERENCE_DB",
    "REFERENCE_SYNTHETIC_VOICE_DELAY_SAMPLES",
    "REFERENCE_SPEECH_SHAPED_REMOVED_DB",
    "REFERENCE_SPEECH_SHAPED_KEPT_DB",
    "REFERENCE_SPEECH_SHAPED_CHATTER_DB",
    "REFERENCE_SPEECH_SHAPED_BLOCK_COHERENCE_DB",
    "REFERENCE_SPEECH_SHAPED_DELAY_SAMPLES",
    # the reference, the most aggressive setting
    "REFERENCE_CORPUS_VOICE_AGGRESSIVE_REMOVED_DB",
    "REFERENCE_CORPUS_VOICE_AGGRESSIVE_KEPT_DB",
    "REFERENCE_SPEECH_SHAPED_AGGRESSIVE_REMOVED_DB",
    "REFERENCE_SPEECH_SHAPED_AGGRESSIVE_KEPT_DB",
    # the delay against the block size of the host
    "REFERENCE_DELAY_SAMPLES_BLOCK_64",
    "REFERENCE_DELAY_SAMPLES_BLOCK_256",
    "REFERENCE_DELAY_SAMPLES_BLOCK_4096",
    "OUR_DELAY_SAMPLES_BLOCK_64",
    "OUR_DELAY_SAMPLES_BLOCK_4096",
    # the stereo image
    "REFERENCE_SIDE_CHANNEL_RMS_IN",
)


def _printed(value) -> str:
    """A number as the note writes it."""
    if isinstance(value, int):
        return "%d" % value
    text = ("%.4f" % float(value)).rstrip("0")
    if text.endswith("."):
        text += "0"
    return text


def test_the_document_carries_the_table():
    """The note has to carry the numbers of the comparison.

    The numbers are read from the constants file, so the note and the file
    cannot drift apart: when a number moves, the note has to move with it.
    """
    text = _DOCUMENT.read_text(encoding="utf-8")
    for name in DOCUMENT_NUMBERS:
        number = _printed(getattr(constants, name))
        assert number in text, "%s (%s) is not in the note" % (name, number)


def test_the_document_names_the_tree_the_numbers_were_measured_on():
    """The note has to say which tree the numbers of ours belong to."""
    text = _DOCUMENT.read_text(encoding="utf-8")
    assert constants.TREE_SHA256_VOICE_DE_NOISE[:8] in text


def test_the_document_says_what_we_do_not_know():
    """A head to head is only worth as much as its edges."""
    text = _DOCUMENT.read_text(encoding="utf-8").lower()
    assert "we do not know" in text
    assert "separat" in text
    assert "stem" in text
