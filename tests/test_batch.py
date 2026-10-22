"""
tests/test_batch.py — the batch runner, as the user meets it.

The runner takes a chain, runs it over a set of files and writes the
results.  This file checks the four things a user has to be able to trust:

* the file that was written is on the target of the delivery it was made
  for, and the report says so plainly when it is not;
* the file that was written is under the true-peak ceiling of the target,
  and a file that is not is not delivered;
* the runner never writes outside the output directory, never overwrites a
  source file and never leaves a part of a file behind;
* the job can be cancelled while it runs, and a cancelled file is not left
  half written.

The delivery table in this file is the measurement of the runner over the
corpus test clips.  It was produced with

    python -m aurserver.batch --json var/batch/report.json

and the test regenerates the table and checks it.  The guarantees (the
target, the tolerance, the ceiling) are checked tightly; the recorded
numbers are checked with a slack, because the corpus is a golden set that
may be regenerated, and a new measurement is a new table.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from aurserver import batch
from aurserver import batch_formats as bf

#: The project root.
ROOT = Path(__file__).resolve().parents[1]

#: The corpus clips the delivery table was measured over.
CORPUS = ROOT / "corpus" / "audio" / "test"

#: The delivery targets the table covers.
TARGETS = (
    "ebu-r128",
    "ebu-r128-short",
    "atsc-a85",
    "arib-tr-b32",
    "podcast",
    "music-stream",
    "spoken-word",
)

#: The delivery table, measured with ``python -m aurserver.batch``.
#:
#: Every entry is ``(loudness_lufs, error_lu, peak_dbfs, true_peak_dbtp)`` of
#: the file that was written.  ``None`` means the meter has no value for it:
#: the silent clip has no integrated loudness at all.  The error is the
#: loudness of the delivered file minus the target of the delivery.
#:
#: The table was measured again on 2026-10-22, twice over.
#:
#: First the score of the runner changed: it measures the delivered file
#: against the true-peak **ceiling** now and prices the headroom margin
#: instead of refusing a file that lands a hair over the headroom line
#: (``aurserver/batch.py``).  Two rows moved because of it: the ``reverb``
#: clip of ``ebu-r128-short`` came 1.21 LU closer to its target and the one
#: of ``spoken-word`` 0.21 LU.  The rows that miss their target are the same
#: rows as before.
#:
#: Then the Loudness Control module was rewritten again in the same hour
#: (``aurcore/modules/loudness_control.py``, 2026-10-22 06:34 and 06:46),
#: so most of the table moved with it: the delivered loudness of the steady
#: clips is now 0.02 to 0.38 LU below the target instead of 0.05 LU above
#: it, and the ``clicks`` clip is 2.85 LU to 3.10 LU below its target
#: instead of 2.80 LU to 3.05 LU.  The table was written again with
#:
#:     python -m aurserver.batch --out var/w82/delivery --json var/w82/delivery.json
#:
#: and the measurement is in ``var/w82/delivery.json``.
DELIVERY_TABLE: dict[str, dict[str, tuple]] = {
    "ebu-r128": {
        "clicks": (-25.90, -2.90, -1.05, -1.05),
        "clipped": (-23.07, -0.07, -25.19, -25.02),
        "hiss": (-23.07, -0.07, -15.85, -15.85),
        "hum50": (-23.06, -0.06, -21.42, -21.42),
        "music_mix": (-23.38, -0.38, -7.18, -7.18),
        "pink_hiss": (-23.12, -0.12, -12.22, -12.22),
        "pitch_drift": (-23.06, -0.06, -22.31, -22.31),
        "plosive": (-23.12, -0.12, -6.00, -6.00),
        "reverb": (-23.00, 0.00, -2.76, -2.76),
        "rumble": (-23.06, -0.06, -21.47, -21.47),
        "sibilance": (-23.02, -0.02, -14.43, -14.43),
        "silence": (None, None, -120.00, -120.00),
        "stereo_mid": (-23.06, -0.06, -22.31, -22.31),
        "stereo_phase": (-23.06, -0.06, -22.31, -21.29),
        "stereo_side": (-23.06, -0.06, -22.07, -22.06),
        "voice_hiss": (-23.07, -0.07, -14.69, -14.66),
    },
    "ebu-r128-short": {
        "clicks": (-23.01, -3.01, -1.05, -1.05),
        "clipped": (-20.07, -0.07, -22.19, -22.02),
        "hiss": (-20.07, -0.07, -12.85, -12.85),
        "hum50": (-20.06, -0.06, -18.42, -18.42),
        "music_mix": (-20.81, -0.81, -5.05, -5.05),
        "pink_hiss": (-20.12, -0.12, -9.22, -9.22),
        "pitch_drift": (-20.06, -0.06, -19.31, -19.31),
        "plosive": (-20.44, -0.44, -4.43, -4.43),
        "reverb": (-21.29, -1.29, -1.05, -1.05),
        "rumble": (-20.06, -0.06, -18.47, -18.47),
        "sibilance": (-20.02, -0.02, -11.43, -11.43),
        "silence": (None, None, -120.00, -120.00),
        "stereo_mid": (-20.06, -0.06, -19.31, -19.31),
        "stereo_phase": (-20.06, -0.06, -19.31, -18.29),
        "stereo_side": (-20.06, -0.06, -19.07, -19.06),
        "voice_hiss": (-20.07, -0.07, -11.69, -11.66),
    },
    "atsc-a85": {
        "clicks": (-26.85, -2.85, -2.00, -2.00),
        "clipped": (-24.07, -0.07, -26.19, -26.02),
        "hiss": (-24.07, -0.07, -16.85, -16.85),
        "hum50": (-24.06, -0.06, -22.43, -22.43),
        "music_mix": (-24.34, -0.34, -8.18, -8.18),
        "pink_hiss": (-24.12, -0.12, -13.22, -13.22),
        "pitch_drift": (-24.06, -0.06, -23.31, -23.31),
        "plosive": (-24.20, -0.20, -7.35, -7.35),
        "reverb": (-24.00, 0.00, -3.73, -3.73),
        "rumble": (-24.06, -0.06, -22.47, -22.47),
        "sibilance": (-24.02, -0.02, -15.43, -15.43),
        "silence": (None, None, -120.00, -120.00),
        "stereo_mid": (-24.06, -0.06, -23.31, -23.31),
        "stereo_phase": (-24.06, -0.06, -23.31, -22.29),
        "stereo_side": (-24.06, -0.06, -23.06, -23.06),
        "voice_hiss": (-24.07, -0.07, -15.69, -15.66),
    },
    "arib-tr-b32": {
        "clicks": (-26.85, -2.85, -2.00, -2.00),
        "clipped": (-24.07, -0.07, -26.19, -26.02),
        "hiss": (-24.07, -0.07, -16.85, -16.85),
        "hum50": (-24.06, -0.06, -22.43, -22.43),
        "music_mix": (-24.34, -0.34, -8.18, -8.18),
        "pink_hiss": (-24.12, -0.12, -13.22, -13.22),
        "pitch_drift": (-24.06, -0.06, -23.31, -23.31),
        "plosive": (-24.20, -0.20, -7.35, -7.35),
        "reverb": (-24.00, 0.00, -3.73, -3.73),
        "rumble": (-24.06, -0.06, -22.47, -22.47),
        "sibilance": (-24.02, -0.02, -15.43, -15.43),
        "silence": (None, None, -120.00, -120.00),
        "stereo_mid": (-24.06, -0.06, -23.31, -23.31),
        "stereo_phase": (-24.06, -0.06, -23.31, -22.29),
        "stereo_side": (-24.06, -0.06, -23.06, -23.06),
        "voice_hiss": (-24.07, -0.07, -15.69, -15.66),
    },
    "podcast": {
        "clicks": (-19.08, -3.08, -1.05, -1.05),
        "clipped": (-16.07, -0.07, -18.19, -18.02),
        "hiss": (-16.07, -0.07, -8.85, -8.85),
        "hum50": (-16.06, -0.06, -14.42, -14.42),
        "music_mix": (-16.57, -0.57, -1.04, -1.04),
        "pink_hiss": (-16.12, -0.12, -5.22, -5.22),
        "pitch_drift": (-16.06, -0.06, -15.31, -15.31),
        "plosive": (-16.51, -0.51, -1.06, -1.05),
        "reverb": (-21.09, -5.09, -1.98, -1.05),
        "rumble": (-16.06, -0.06, -14.47, -14.47),
        "sibilance": (-16.02, -0.02, -7.43, -7.43),
        "silence": (None, None, -120.00, -120.00),
        "stereo_mid": (-16.06, -0.06, -15.31, -15.31),
        "stereo_phase": (-16.06, -0.06, -15.31, -14.29),
        "stereo_side": (-16.06, -0.06, -15.06, -15.06),
        "voice_hiss": (-16.07, -0.07, -7.69, -7.66),
    },
    "music-stream": {
        "clicks": (-17.10, -3.10, -1.05, -1.05),
        "clipped": (-14.07, -0.07, -16.19, -16.02),
        "hiss": (-14.07, -0.07, -6.85, -6.85),
        "hum50": (-14.06, -0.06, -12.42, -12.42),
        "music_mix": (-14.79, -0.79, -1.11, -1.05),
        "pink_hiss": (-14.12, -0.12, -3.22, -3.22),
        "pitch_drift": (-14.06, -0.06, -13.31, -13.31),
        "plosive": (-14.74, -0.74, -1.21, -1.05),
        "reverb": (-20.17, -6.17, -1.99, -1.05),
        "rumble": (-14.06, -0.06, -12.47, -12.47),
        "sibilance": (-14.58, -0.58, -6.00, -6.00),
        "silence": (None, None, -120.00, -120.00),
        "stereo_mid": (-14.06, -0.06, -13.31, -13.31),
        "stereo_phase": (-14.06, -0.06, -13.31, -12.29),
        "stereo_side": (-14.06, -0.06, -13.06, -13.06),
        "voice_hiss": (-14.38, -0.38, -6.00, -5.98),
    },
    "spoken-word": {
        "clicks": (-22.03, -3.03, -1.05, -1.05),
        "clipped": (-19.07, -0.07, -21.19, -21.02),
        "hiss": (-19.07, -0.07, -11.85, -11.85),
        "hum50": (-19.06, -0.06, -17.42, -17.42),
        "music_mix": (-19.57, -0.57, -4.04, -4.04),
        "pink_hiss": (-19.12, -0.12, -8.22, -8.22),
        "pitch_drift": (-19.06, -0.06, -18.31, -18.31),
        "plosive": (-19.44, -0.44, -3.43, -3.43),
        "reverb": (-21.29, -2.29, -1.05, -1.05),
        "rumble": (-19.06, -0.06, -17.47, -17.47),
        "sibilance": (-19.02, -0.02, -10.43, -10.43),
        "silence": (None, None, -120.00, -120.00),
        "stereo_mid": (-19.06, -0.06, -18.31, -18.31),
        "stereo_phase": (-19.06, -0.06, -18.31, -17.29),
        "stereo_side": (-19.06, -0.06, -18.06, -18.06),
        "voice_hiss": (-19.07, -0.07, -10.69, -10.66),
    },
}

#: The rows of the table that cannot meet their target, and why.
#:
#: ``clicks`` carries a full-scale impulse, so the true-peak ceiling of the
#: target does not allow the gain the target needs.  ``reverb`` has a long
#: decay and the same ceiling stands in the way of the louder targets.
#: ``silence`` has no loudness at all, so no gain can bring it onto a
#: target.  All three are reported as a warning, never as a silent pass.
CANNOT_MEET: dict[str, tuple[str, ...]] = {
    "ebu-r128": ("clicks", "silence"),
    "ebu-r128-short": ("clicks", "reverb", "silence"),
    "atsc-a85": ("clicks", "silence"),
    "arib-tr-b32": ("clicks", "silence"),
    "podcast": ("clicks", "reverb", "silence"),
    "music-stream": ("clicks", "reverb", "silence"),
    "spoken-word": ("clicks", "reverb", "silence"),
}

#: The corrections the runner made, with the numbers before and after.
#:
#: Every entry is ``(loudness before, loudness after, true peak before,
#: true peak after)``.  The runner corrects the file it wrote until the file
#: meets the delivery, and there are two kinds of correction: a file the
#: chain wrote over the true-peak ceiling of the target is attenuated until it
#: is under the ceiling again, and a file that came out quieter than the
#: target is raised until it is on it.
CORRECTIONS: dict[str, dict[str, tuple]] = {
    "ebu-r128": {
        "clicks": (-25.85, -25.90, -1.00, -1.05),
        "reverb": (-25.50, -23.00, -5.27, -2.76),
    },
    "ebu-r128-short": {
        "clicks": (-22.96, -23.01, -1.00, -1.05),
        "reverb": (-22.50, -21.29, -2.27, -1.05),
    },
    "atsc-a85": {
        "reverb": (-26.27, -24.00, -6.00, -3.73),
    },
    "arib-tr-b32": {
        "reverb": (-26.27, -24.00, -6.00, -3.73),
    },
    "podcast": {
        "clicks": (-19.03, -19.08, -1.00, -1.05),
        "plosive": (-16.46, -16.51, -0.99, -1.05),
        "reverb": (-20.11, -21.09, -0.07, -1.05),
    },
    "music-stream": {
        "clicks": (-17.05, -17.10, -1.00, -1.05),
        "music_mix": (-14.68, -14.79, -0.94, -1.05),
        "plosive": (-14.53, -14.74, -0.84, -1.05),
        "reverb": (-19.18, -20.17, -0.06, -1.05),
    },
    "spoken-word": {
        "clicks": (-21.98, -22.03, -1.00, -1.05),
        "reverb": (-21.50, -21.29, -1.26, -1.05),
    },
}

#: How far the regenerated table may differ from the recorded one.  The
#: loudness of a delivered file is set by the target, so it barely moves;
#: the peak follows the material of the clip, which the corpus may change.
LOUDNESS_SLACK_LU = 0.35


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def clip(name: str) -> Path:
    """Return the path of one corpus clip."""
    return CORPUS / f"{name}.wav"


def rows_of(table: list[dict]) -> dict[tuple[str, str], dict]:
    """Index a delivery table by target and clip."""
    return {(row["target"], row["file"]): row for row in table}


def no_temporary_files(folder: Path) -> list[str]:
    """Return the names of the files a runner left half written."""
    return [
        item.name
        for item in folder.rglob("*")
        if item.is_file() and item.name.endswith(".part")
    ]


def is_complete_wav(path: Path, frames: int | None = None) -> bool:
    """True when the file is a readable WAV of the expected length."""
    import soundfile as sf

    try:
        data, _rate = sf.read(str(path), always_2d=True)
    except Exception:
        return False
    if frames is not None and data.shape[0] != frames:
        return False
    return bool(np.all(np.isfinite(data)))


class _StubApp:
    """The little of an application the batch routes need."""

    def __init__(self, roots: list[Path]):
        self.state = SimpleNamespace(batch_roots=list(roots))


class _StubRequest:
    """A request the batch routes can read a JSON body from."""

    def __init__(self, payload: dict, app: _StubApp):
        self._payload = payload
        self.app = app

    async def body(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def start_job(payload: dict, roots: list[Path]) -> str:
    """Start a batch job through the route and return its id."""
    request = _StubRequest(payload, _StubApp(roots))
    answer = asyncio.run(batch.api_start_batch(request))
    return str(answer["job_id"])


def wait_for_job(job_id: str, timeout: float = 120.0) -> batch.BatchJob:
    """Wait for a job to end and return it."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = batch.get_job(job_id)
        if job is not None and job.state in ("done", "error", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"the job {job_id} did not end in time")


# ---------------------------------------------------------------------------
# the delivery table
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def delivery_table(tmp_path_factory) -> list[dict]:
    """Run the delivery chain over the corpus for every target.

    The table is the measurement of the runner: it is produced by the same
    code the job runs, so it describes what the runner delivers.
    """
    out_dir = tmp_path_factory.mktemp("delivery")
    sources = sorted(CORPUS.glob("*.wav"))
    return batch.delivery_report(sources, out_dir=out_dir)


def test_the_table_covers_every_target_and_clip(delivery_table: list[dict]) -> None:
    """The table has one row per target and clip."""
    clips = sorted(path.stem for path in CORPUS.glob("*.wav"))
    assert len(delivery_table) == len(TARGETS) * len(clips)
    for key in TARGETS:
        names = [row["file"] for row in delivery_table if row["target"] == key]
        assert sorted(names) == clips, f"the table is incomplete for {key}"


def test_the_recorded_table_is_complete() -> None:
    """The recorded table holds every target and the same clips for each."""
    assert sorted(DELIVERY_TABLE) == sorted(TARGETS)
    corpus = {path.stem for path in CORPUS.glob("*.wav")}
    for key, rows in DELIVERY_TABLE.items():
        assert set(rows) <= corpus, f"the recorded table holds a clip that is not in the corpus"
        for other, other_rows in DELIVERY_TABLE.items():
            assert set(rows) == set(other_rows), (
                f"the recorded table holds different clips for {key} and {other}"
            )


def test_the_recorded_table_agrees_with_the_catalogue() -> None:
    """Every recorded row belongs to a target of the catalogue."""
    for key, rows in DELIVERY_TABLE.items():
        target = bf.get_target(key)
        for name, (loud, error, _peak, _true_peak) in rows.items():
            if loud is None:
                assert error is None, f"{key}/{name}: a silent file has no error"
                continue
            assert abs((loud - target.integrated_lufs) - error) < 0.02, (
                f"{key}/{name}: the recorded error does not match the recorded loudness"
            )


def test_the_recorded_table_agrees_with_the_exception_list() -> None:
    """The rows that miss their target are the rows that are named as such."""
    for key, rows in DELIVERY_TABLE.items():
        target = bf.get_target(key)
        outside = {
            name
            for name, (loud, error, _peak, _true_peak) in rows.items()
            if loud is None or abs(error) > target.tolerance_lu
        }
        assert outside == set(CANNOT_MEET[key]), (
            f"{key}: the rows that miss the target are {sorted(outside)}, "
            f"the recorded exception list says {sorted(CANNOT_MEET[key])}"
        )


def test_the_recorded_corrections_agree_with_the_table() -> None:
    """A correction ends at the numbers the table records.

    A correction brings the delivered file closer to the delivery: a file
    that was over the true-peak ceiling of the target is attenuated until it
    is under the ceiling again, and a file that was quieter than the target
    is raised until it is on it.
    """
    for key, corrections in CORRECTIONS.items():
        target = bf.get_target(key)
        for name, (loud_before, loud_after, peak_before, peak_after) in corrections.items():
            loud, _error, _peak, true_peak = DELIVERY_TABLE[key][name]
            assert abs(loud_after - loud) < 0.01, (
                f"{key}/{name}: the correction ends at {loud_after:.2f} LUFS, "
                f"the table says {loud:.2f} LUFS"
            )
            assert abs(peak_after - true_peak) < 0.01, (
                f"{key}/{name}: the correction ends at {peak_after:.2f} dBTP, "
                f"the table says {true_peak:.2f} dBTP"
            )
            if peak_after < peak_before:
                # an attenuation: the file was over the ceiling of the target
                assert loud_before - loud_after > -0.01, (
                    f"{key}/{name}: the correction did not lower the loudness"
                )
            else:
                # a boost: the file was quieter than the target
                assert loud_after - loud_before > 0.0, (
                    f"{key}/{name}: the correction did not raise the loudness"
                )
                assert peak_after <= target.true_peak_dbtp, (
                    f"{key}/{name}: the correction put the file over the ceiling"
                )


def test_the_runner_reproduces_the_recorded_loudness(delivery_table: list[dict]) -> None:
    """The runner delivers the loudness the table records.

    The loudness of a delivered file is set by the target of the delivery,
    so it hardly moves.  The peak follows the material of the clip, and the
    corpus is a golden set that may be regenerated, so the peak is only
    checked against the ceiling and not against the recorded number.
    """
    rows = rows_of(delivery_table)
    for key, recorded in DELIVERY_TABLE.items():
        for name, (loud, error, _peak, _true_peak) in recorded.items():
            row = rows[(key, name)]
            assert row["status"] == "delivered", f"{key}/{name}: {row['error']}"
            if loud is None:
                assert row["loudness_lufs"] is None
                assert row["error_lu"] is None
                continue
            assert row["loudness_lufs"] is not None
            assert abs(row["loudness_lufs"] - loud) < LOUDNESS_SLACK_LU, (
                f"{key}/{name}: the delivered loudness is {row['loudness_lufs']:.2f} LUFS, "
                f"the table says {loud:.2f} LUFS; the corpus changed, re-measure with "
                f"python -m aurserver.batch"
            )
            assert abs(row["error_lu"] - error) < LOUDNESS_SLACK_LU, (
                f"{key}/{name}: the delivered error is {row['error_lu']:+.2f} LU, "
                f"the table says {error:+.2f} LU"
            )


def test_the_delivered_loudness_is_on_the_target(delivery_table: list[dict]) -> None:
    """A delivered file is on its target, or the report says why it is not."""
    for row in delivery_table:
        key = row["target"]
        target = bf.get_target(key)
        if row["within_tolerance"] is True:
            assert abs(row["error_lu"]) <= target.tolerance_lu, (
                f"{key}/{row['file']}: the file is marked as on the target but the "
                f"error is {row['error_lu']:+.2f} LU"
            )
            continue
        # The file is not on the target: the report has to say so.
        assert row["warnings"], (
            f"{key}/{row['file']}: the delivered loudness is {row['loudness_lufs']} LUFS "
            f"and the target is {target.integrated_lufs:.1f} LUFS, but the report is silent"
        )
        joined = " ".join(row["warnings"]).lower()
        assert "cannot" in joined or "no measurable loudness" in joined, (
            f"{key}/{row['file']}: the warning does not explain the failure: {row['warnings']}"
        )


def test_a_file_that_meets_its_target_carries_no_warning(
    delivery_table: list[dict],
) -> None:
    """A file that is on its target is not reported as a problem."""
    for row in delivery_table:
        if row["within_tolerance"] is not True:
            continue
        assert not row["warnings"], (
            f"{row['target']}/{row['file']}: an unexpected warning: {row['warnings']}"
        )


def test_the_delivered_true_peak_is_under_the_ceiling(delivery_table: list[dict]) -> None:
    """No delivered file is over the true-peak ceiling of its target."""
    for row in delivery_table:
        target = bf.get_target(row["target"])
        assert row["true_peak_dbtp"] <= target.true_peak_dbtp, (
            f"{row['target']}/{row['file']}: the delivered true peak is "
            f"{row['true_peak_dbtp']:.2f} dBTP, the ceiling is "
            f"{target.true_peak_dbtp:.1f} dBTP"
        )


def test_the_peak_of_the_delivered_file_is_under_the_ceiling(
    delivery_table: list[dict],
) -> None:
    """No delivered file is over the ceiling, by true peak or by sample peak."""
    for row in delivery_table:
        target = bf.get_target(row["target"])
        assert row["true_peak_dbtp"] <= target.true_peak_dbtp, (
            f"{row['target']}/{row['file']}: the delivered true peak is "
            f"{row['true_peak_dbtp']:.2f} dBTP, the ceiling is "
            f"{target.true_peak_dbtp:.1f} dBTP"
        )
        assert row["peak_dbfs"] <= target.true_peak_dbtp, (
            f"{row['target']}/{row['file']}: the delivered sample peak is "
            f"{row['peak_dbfs']:.2f} dBFS, the ceiling is "
            f"{target.true_peak_dbtp:.1f} dBTP"
        )


def test_the_true_peak_meter_finds_an_inter_sample_peak() -> None:
    """The true peak of a file is never under its sample peak."""
    from aurcore.dsp.loudness import true_peak

    rate = 48000
    # 16 kHz at 48 kHz: three samples to a cycle, so the crest of the wave
    # falls between two samples and the samples never show it.
    t = np.arange(rate) / rate
    wave = np.sin(2 * np.pi * 16000 * t) * 0.5
    sample_peak_dbfs = 20.0 * np.log10(float(np.max(np.abs(wave))))
    measured = float(true_peak(wave[:, None], rate))
    assert measured > sample_peak_dbfs + 1.0, (
        f"the true peak is {measured:.2f} dBTP, the sample peak is "
        f"{sample_peak_dbfs:.2f} dBFS: the meter does not see the crest"
    )


def test_a_file_that_was_over_the_ceiling_is_corrected(delivery_table: list[dict]) -> None:
    """A file the chain wrote over the ceiling is delivered under it."""
    for row in delivery_table:
        target = bf.get_target(row["target"])
        before = row["before"] or {}
        after = row["after"] or {}
        if before.get("true_peak_dbtp") is None:
            continue
        if before["true_peak_dbtp"] <= target.true_peak_dbtp + 0.06:
            continue
        assert after["true_peak_dbtp"] <= target.true_peak_dbtp, (
            f"{row['target']}/{row['file']}: the delivered file is still over the ceiling"
        )
        assert row["gain_db"] < 0.0, (
            f"{row['target']}/{row['file']}: the correction was not an attenuation"
        )


def test_the_loudness_of_a_file_that_cannot_meet_its_target_is_reported() -> None:
    """A file that misses its target carries the number it missed it by."""
    for key, names in CANNOT_MEET.items():
        for name in names:
            loud, error, _peak, _true_peak = DELIVERY_TABLE[key][name]
            if name == "silence":
                assert loud is None, f"{key}/{name}: a silent file has no loudness"
                assert error is None
                continue
            assert loud is not None
            assert error < 0.0, (
                f"{key}/{name}: the file is loud, not quiet: the error is {error:+.2f} LU"
            )


# ---------------------------------------------------------------------------
# the loudness of a delivered file
# ---------------------------------------------------------------------------
def test_the_loudness_is_measured_on_the_file_that_was_written(tmp_path: Path) -> None:
    """The number in the report is the number of the file on disk."""
    source = clip("hiss")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    spec = batch.DeliverySpec.from_target("ebu-r128")
    result = batch._process_file(source, [], out_dir, "wav", 24, spec, threading.Event())
    assert result["status"] == "delivered"
    delivered = Path(str(result["output"]))
    assert delivered.is_file()
    measured = bf.measure_delivery(delivered)
    assert measured.loudness_lufs == pytest.approx(result["loudness_lufs"], abs=1e-9)
    assert measured.true_peak_dbtp == pytest.approx(result["true_peak_dbtp"], abs=1e-9)
    assert measured.peak_dbfs == pytest.approx(result["peak_dbfs"], abs=1e-9)


def test_a_chain_without_a_loudness_stage_is_brought_onto_the_target(
    tmp_path: Path,
) -> None:
    """The runner corrects the delivered file until it is on the target."""
    source = clip("hiss")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    spec = batch.DeliverySpec.from_target("ebu-r128")
    result = batch._process_file(
        source,
        [{"code": "AUR_DEHUM", "enabled": True, "params": {}}],
        out_dir,
        "wav",
        24,
        spec,
        threading.Event(),
    )
    assert result["status"] == "delivered"
    assert result["within_tolerance"] is True
    assert abs(result["loudness_error_lu"]) <= spec.tolerance_lu
    # the file the chain wrote was far below the target
    assert result["before"]["loudness_lufs"] < spec.integrated_lufs - 10.0
    assert result["gain_db"] > 10.0
    assert not result["warnings"]


def test_a_chain_that_carries_a_loudness_stage_names_its_own_target() -> None:
    """The runner reads the delivery out of a chain that carries one."""
    steps = bf.augment_chain([], "music-stream")
    spec = batch.spec_from_chain(steps)
    assert spec is not None
    target = bf.get_target("music-stream")
    assert spec.integrated_lufs == target.integrated_lufs
    assert spec.tolerance_lu == target.tolerance_lu
    assert spec.true_peak_dbtp == target.true_peak_dbtp
    assert batch.spec_from_chain([{"code": "AUR_DEHUM", "params": {}}]) is None


def test_a_chain_without_a_loudness_stage_names_no_target() -> None:
    """A chain that says nothing about the delivery is measured only."""
    assert batch.spec_from_chain([{"code": "AUR_DEHUM", "params": {}}]) is None
    assert batch.spec_from_chain([]) is None
    assert batch.spec_from_chain(None) is None


def test_the_delivery_stages_are_not_added_twice() -> None:
    """A chain that already carries a loudness stage keeps it."""
    steps = bf.augment_chain([], "ebu-r128")
    twice = batch.add_delivery_stages(steps, "ebu-r128")
    codes = [step["code"] for step in twice]
    assert codes.count(bf.LOUDNESS_MODULE) == 1
    assert codes.count(bf.DITHER_MODULE) == 1


# ---------------------------------------------------------------------------
# the true peak
# ---------------------------------------------------------------------------
def test_a_file_over_the_ceiling_is_not_delivered(tmp_path: Path, monkeypatch) -> None:
    """A file that clips is worse than a file that is quiet."""
    source = clip("hiss")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    spec = batch.DeliverySpec.from_target("ebu-r128")

    def over_the_ceiling(path):
        return bf.DeliveryMeasurement(
            loudness_lufs=-23.0,
            peak_dbfs=-0.2,
            true_peak_dbtp=0.5,
            sample_peak=0.97,
        )

    monkeypatch.setattr(batch, "measure_delivery", over_the_ceiling)
    result = batch._process_file(source, [], out_dir, "wav", 24, spec, threading.Event())
    assert result["status"] == "refused"
    assert result["output"] is None
    assert result["error"] and "peak of" in result["error"]
    assert "ceiling" in result["error"]
    assert result["warnings"]
    assert list(out_dir.iterdir()) == [], "the refused file was left behind"


def test_the_ceiling_of_the_target_is_never_exceeded(tmp_path: Path) -> None:
    """The loud targets keep the peak of the delivered file under the ceiling."""
    source = clip("plosive")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    spec = batch.DeliverySpec.from_target("podcast")
    steps = bf.augment_chain([], spec.key)
    result = batch._process_file(source, steps, out_dir, "wav", 16, spec, threading.Event())
    assert result["status"] == "delivered"
    assert result["true_peak_dbtp"] <= spec.true_peak_dbtp
    assert result["within_tolerance"] is True


# ---------------------------------------------------------------------------
# the margin between the delivered file and the ceiling
# ---------------------------------------------------------------------------
def test_the_headroom_line_is_the_ceiling_less_the_margin() -> None:
    """The line the runner aims at is the ceiling less the margin."""
    assert bf.headroom_line_dbtp(-1.0) == pytest.approx(-1.0 - bf.PEAK_MARGIN_DB)
    assert bf.headroom_line_dbtp(-2.0) == pytest.approx(-2.0 - bf.PEAK_MARGIN_DB)
    assert bf.headroom_line_dbtp(-1.0, margin_db=0.25) == pytest.approx(-1.25)


def test_a_file_a_hair_under_the_ceiling_is_delivered(
    tmp_path: Path, monkeypatch
) -> None:
    """A file that is under the ceiling is legal, hair or not.

    The runner keeps a margin under the ceiling, so that the delivered
    master is not written on the line, but the margin is a preference and
    not a limit: a file that is a hair under the ceiling is delivered.
    """
    source = clip("hiss")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    spec = batch.DeliverySpec.from_target("ebu-r128")

    def a_hair_under_the_ceiling(path):
        return bf.DeliveryMeasurement(
            loudness_lufs=-23.0,
            peak_dbfs=-1.5,
            true_peak_dbtp=spec.true_peak_dbtp - 1e-6,
            sample_peak=0.89,
        )

    monkeypatch.setattr(batch, "measure_delivery", a_hair_under_the_ceiling)
    result = batch._process_file(source, [], out_dir, "wav", 24, spec, threading.Event())
    assert result["status"] == "delivered", result["error"]
    assert result["output"] and Path(str(result["output"])).is_file()
    assert result["ceiling_peak_dbtp"] == pytest.approx(spec.true_peak_dbtp - 1e-6)
    assert not result["warnings"]


def test_a_file_over_the_ceiling_is_not_delivered(
    tmp_path: Path, monkeypatch
) -> None:
    """A file that is over the ceiling is refused.

    The meter of the core and the meter of the delivery do not estimate the
    inter-sample peak in exactly the same way, so a file has to be over the
    ceiling by more than the slack of the meter before it is called a
    violation.
    """
    source = clip("hiss")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    spec = batch.DeliverySpec.from_target("ebu-r128")

    def over_the_ceiling(path):
        return bf.DeliveryMeasurement(
            loudness_lufs=-23.0,
            peak_dbfs=-1.5,
            true_peak_dbtp=spec.true_peak_dbtp + 0.06,
            sample_peak=0.89,
        )

    monkeypatch.setattr(batch, "measure_delivery", over_the_ceiling)
    result = batch._process_file(source, [], out_dir, "wav", 24, spec, threading.Event())
    assert result["status"] == "refused"
    assert result["output"] is None
    assert result["error"] and "ceiling" in result["error"]
    assert list(out_dir.iterdir()) == [], "the refused file was left behind"


def test_a_file_that_keeps_no_headroom_carries_the_note(
    tmp_path: Path, monkeypatch
) -> None:
    """A file that sits on the ceiling is reported, not passed on in silence."""
    source = clip("hiss")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    spec = batch.DeliverySpec.from_target("ebu-r128")

    def on_the_ceiling(path):
        return bf.DeliveryMeasurement(
            loudness_lufs=-23.0,
            peak_dbfs=-1.5,
            true_peak_dbtp=spec.true_peak_dbtp,
            sample_peak=0.89,
        )

    monkeypatch.setattr(batch, "measure_delivery", on_the_ceiling)
    result = batch._process_file(source, [], out_dir, "wav", 24, spec, threading.Event())
    assert result["status"] == "delivered", result["error"]
    joined = " ".join(result["notes"]).lower()
    assert "headroom" in joined, result["notes"]
    assert "margin" in joined, result["notes"]


def test_the_score_measures_the_file_against_the_ceiling() -> None:
    """The score of a file is decided by the ceiling and not by the margin."""
    spec = batch.DeliverySpec.from_target("ebu-r128")
    line = bf.headroom_line_dbtp(spec.true_peak_dbtp)
    # a hair over the headroom line, but under the ceiling
    legal = bf.DeliveryMeasurement(
        loudness_lufs=-23.0,
        peak_dbfs=-1.5,
        true_peak_dbtp=line + 1.1e-6,
        sample_peak=0.89,
    )
    over = bf.DeliveryMeasurement(
        loudness_lufs=-23.0,
        peak_dbfs=-1.5,
        true_peak_dbtp=spec.true_peak_dbtp + 0.01,
        sample_peak=0.89,
    )
    assert batch._delivery_score(legal, spec)[0] == 0.0, (
        "a file that is under the ceiling is not over it"
    )
    assert batch._delivery_score(over, spec)[0] > 0.0


def test_a_correction_a_hair_over_the_headroom_line_is_accepted() -> None:
    """The margin is a price and not a wall.

    A correction that lands a hair over the headroom line is legal: the
    file it writes is under the ceiling.  The score has to accept it, or
    the target loses the loudness the correction buys — and a rounding
    error of a millionth of a decibel costs the target a decibel.
    """
    spec = batch.DeliverySpec.from_target("ebu-r128")
    line = bf.headroom_line_dbtp(spec.true_peak_dbtp)
    before = bf.DeliveryMeasurement(
        loudness_lufs=-25.0,
        peak_dbfs=-5.0,
        true_peak_dbtp=-5.0,
        sample_peak=0.56,
    )
    after = bf.DeliveryMeasurement(
        loudness_lufs=-23.0,
        peak_dbfs=line + 1.1e-6,
        true_peak_dbtp=line + 1.1e-6,
        sample_peak=0.89,
    )
    assert batch._delivery_score(after, spec) < batch._delivery_score(before, spec), (
        "the correction is legal and it brings the file onto the target: it has to be kept"
    )


def test_a_correction_that_buys_less_than_the_margin_is_not_kept() -> None:
    """A correction that buys less than the margin is not worth a write."""
    spec = batch.DeliverySpec.from_target("ebu-r128")
    line = bf.headroom_line_dbtp(spec.true_peak_dbtp)
    # the file is 0.01 LU off its target and keeps its headroom
    before = bf.DeliveryMeasurement(
        loudness_lufs=-23.01,
        peak_dbfs=-3.0,
        true_peak_dbtp=-3.0,
        sample_peak=0.7,
    )
    # the correction puts the file on the target but takes the headroom
    after = bf.DeliveryMeasurement(
        loudness_lufs=-23.0,
        peak_dbfs=line + 1.1e-6,
        true_peak_dbtp=line + 1.1e-6,
        sample_peak=0.89,
    )
    assert batch._delivery_score(after, spec) > batch._delivery_score(before, spec), (
        "a hair of accuracy is not worth the headroom the runner promises"
    )


# ---------------------------------------------------------------------------
# the safety of the runner
# ---------------------------------------------------------------------------
def test_the_output_stays_inside_the_output_directory(tmp_path: Path) -> None:
    """The runner writes into the output directory and nowhere else."""
    source = clip("hiss")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    spec = batch.DeliverySpec.from_target("ebu-r128")
    result = batch._process_file(source, [], out_dir, "wav", 24, spec, threading.Event())
    delivered = Path(str(result["output"])).resolve()
    assert delivered.parent == out_dir.resolve()
    assert delivered.is_file()
    assert no_temporary_files(out_dir) == []


def test_a_source_file_is_never_overwritten(tmp_path: Path) -> None:
    """The output directory may be the directory of the source."""
    work = tmp_path / "work"
    work.mkdir()
    source = work / "hiss.wav"
    source.write_bytes(clip("hiss").read_bytes())
    before = source.read_bytes()

    spec = batch.DeliverySpec.from_target("ebu-r128")
    result = batch._process_file(source, [], work, "wav", 24, spec, threading.Event())
    assert result["status"] == "delivered"
    assert source.read_bytes() == before, "the source file was overwritten"
    assert Path(str(result["output"])).resolve() != source.resolve()


def test_a_source_of_the_batch_is_never_overwritten(tmp_path: Path) -> None:
    """No output of a batch is one of the sources of that batch."""
    work = tmp_path / "work"
    work.mkdir()
    first = work / "take.wav"
    first.write_bytes(clip("hiss").read_bytes())
    second = work / "take2.wav"
    second.write_bytes(clip("hum50").read_bytes())
    spec = batch.DeliverySpec.from_target("ebu-r128")

    first_result = batch._process_file(
        first, [], work, "wav", 24, spec, threading.Event(), reserved=[first, second]
    )
    second_result = batch._process_file(
        second, [], work, "wav", 24, spec, threading.Event(), reserved=[first, second]
    )
    for source in (first, second):
        assert Path(str(first_result["output"])).resolve() != source.resolve()
        assert Path(str(second_result["output"])).resolve() != source.resolve()


def test_the_output_name_can_not_leave_the_output_directory(tmp_path: Path) -> None:
    """A source whose name would escape never produces a path outside."""
    work = tmp_path / "work"
    work.mkdir()
    tricky = work / "..wav"
    tricky.write_bytes(clip("hiss").read_bytes())
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    candidate = batch.output_path_for(tricky, out_dir, "wav")
    assert candidate.parent.resolve() == out_dir.resolve()
    assert ".." not in candidate.name


def test_a_file_that_was_not_fully_written_is_removed(tmp_path: Path, monkeypatch) -> None:
    """A write that fails leaves nothing behind."""
    import soundfile as sf

    source = clip("hiss")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    spec = batch.DeliverySpec.from_target("ebu-r128")

    def failing_write(*args, **kwargs):
        raise OSError("the disk is full")

    monkeypatch.setattr(sf, "write", failing_write)
    result = batch._process_file(source, [], out_dir, "wav", 24, spec, threading.Event())
    assert result["status"] == "failed"
    assert result["error"]
    assert result["output"] is None
    assert list(out_dir.iterdir()) == [], "a part of a file was left behind"


def test_the_output_of_a_job_stays_inside_its_output_directory(tmp_path: Path) -> None:
    """Every file of a finished job is inside the output directory."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sources = [clip("hiss"), clip("hum50")]
    job_id = start_job(
        {
            "paths": [str(item) for item in sources],
            "out_dir": str(out_dir),
            "target": "ebu-r128",
        },
        [tmp_path, CORPUS],
    )
    job = wait_for_job(job_id)
    assert job.state == "done"
    assert job.done == 2
    for result in job.results:
        delivered = Path(str(result["output"])).resolve()
        assert delivered.parent == out_dir.resolve()
        assert is_complete_wav(delivered)
    assert no_temporary_files(out_dir) == []


def test_a_path_outside_the_roots_is_refused(tmp_path: Path) -> None:
    """The runner refuses a file it is not allowed to read."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as info:
        batch.resolve_input_path(str(tmp_path / "elsewhere.wav"), [tmp_path / "allowed"])
    assert info.value.status_code == 400


# ---------------------------------------------------------------------------
# cancelling a job
# ---------------------------------------------------------------------------
def test_a_job_can_be_cancelled_while_it_runs(tmp_path: Path, monkeypatch) -> None:
    """The job stops when it is asked to, and what it wrote stays whole."""
    original = batch.build_chain_engine

    class _SlowEngine:
        """A chain that takes its time."""

        def __init__(self, inner):
            self._inner = inner

        def process(self, samples):
            time.sleep(0.4)
            return self._inner.process(samples)

    def slow_build(chain, sample_rate, channels=2):
        return _SlowEngine(original(chain, sample_rate, channels))

    monkeypatch.setattr(batch, "build_chain_engine", slow_build)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sources = [clip("hiss"), clip("hum50"), clip("sibilance"), clip("pink_hiss")]
    job_id = start_job(
        {
            "paths": [str(item) for item in sources],
            "out_dir": str(out_dir),
            "target": "ebu-r128",
        },
        [tmp_path, CORPUS],
    )

    deadline = time.time() + 60.0
    job = None
    while time.time() < deadline:
        job = batch.get_job(job_id)
        if job is not None and job.results:
            break
        time.sleep(0.05)
    assert job is not None and job.results, "the job never delivered a file"

    asyncio.run(batch.api_batch_cancel(job_id))
    job = wait_for_job(job_id)
    assert job.state == "cancelled"
    assert job.cancelled is True
    assert len(job.results) < len(sources), "the job did not stop early"

    # every file that is in the output directory is a whole file
    for item in out_dir.iterdir():
        assert item.is_file()
        assert is_complete_wav(item), f"{item.name} is not a whole file"
    assert no_temporary_files(out_dir) == []


def test_a_cancelled_file_is_not_left_half_written(tmp_path: Path, monkeypatch) -> None:
    """A cancel that lands while the file is written removes the file."""
    source = clip("hiss")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    spec = batch.DeliverySpec.from_target("ebu-r128")
    cancel = threading.Event()

    original_stage = batch.stage_audio

    def stage_and_cancel(path, samples, sample_rate, fmt, bit_depth):
        staged, note = original_stage(path, samples, sample_rate, fmt, bit_depth)
        cancel.set()
        return staged, note

    monkeypatch.setattr(batch, "stage_audio", stage_and_cancel)
    result = batch._process_file(source, [], out_dir, "wav", 24, spec, cancel)
    assert result["status"] == "cancelled"
    assert result["output"] is None
    assert list(out_dir.iterdir()) == [], "a part of a file was left behind"


def test_a_job_that_was_cancelled_before_it_started_delivers_nothing(
    tmp_path: Path,
) -> None:
    """A job that is cancelled before the first file writes nothing."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    job = batch.BatchJob(job_id="test")
    job.cancel()
    assert job.cancel_requested() is True
    spec = batch.DeliverySpec.from_target("ebu-r128")
    result = batch._process_file(
        clip("hiss"), [], out_dir, "wav", 24, spec, job.cancel_event
    )
    assert result["status"] == "cancelled"
    assert list(out_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# the job as the API reports it
# ---------------------------------------------------------------------------
def test_the_job_reports_the_delivery_of_every_file(tmp_path: Path) -> None:
    """Every result carries the measurement of the file that was written."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sources = [clip("hiss"), clip("clicks")]
    job_id = start_job(
        {
            "paths": [str(item) for item in sources],
            "out_dir": str(out_dir),
            "target": "ebu-r128",
        },
        [tmp_path, CORPUS],
    )
    job = wait_for_job(job_id)
    assert job.state == "done"
    assert job.total == 2
    assert len(job.results) == 2
    for result in job.results:
        assert result["status"] == "delivered"
        assert result["loudness_lufs"] is not None
        assert result["true_peak_dbtp"] is not None
        assert result["peak_dbfs"] is not None
        assert result["target_lufs"] == -23.0
        assert result["tolerance_lu"] == 0.5
        assert result["true_peak_ceiling_dbtp"] == -1.0
        assert result["within_tolerance"] in (True, False)

    quiet = [r for r in job.results if r["within_tolerance"] is False]
    assert len(quiet) == 1, "the click file cannot meet the target"
    assert quiet[0]["warnings"], "the job did not report the target it could not meet"
    assert job.warnings, "the job did not carry the warning"


def test_the_job_carries_the_warnings_of_its_files(tmp_path: Path) -> None:
    """The job result names the file and the reason."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    job_id = start_job(
        {
            "paths": [str(clip("clicks"))],
            "out_dir": str(out_dir),
            "target": "ebu-r128",
        },
        [tmp_path, CORPUS],
    )
    job = wait_for_job(job_id)
    assert job.state == "done"
    assert job.warnings
    joined = " ".join(job.warnings).lower()
    assert "clicks" in joined
    assert "cannot be met" in joined or "not allow" in joined


def test_an_unknown_target_is_refused(tmp_path: Path) -> None:
    """A target that is not in the catalogue is refused by name."""
    from fastapi import HTTPException

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(HTTPException) as info:
        start_job(
            {
                "paths": [str(clip("hiss"))],
                "out_dir": str(out_dir),
                "target": "no-such-target",
            },
            [tmp_path, CORPUS],
        )
    assert info.value.status_code == 400
    assert "no-such-target" in str(info.value.detail)
    assert list(out_dir.iterdir()) == []


def test_a_job_without_a_target_only_measures(tmp_path: Path) -> None:
    """A job that names no delivery measures the file and says nothing else."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    job_id = start_job(
        {
            "paths": [str(clip("hiss"))],
            "out_dir": str(out_dir),
            "chain": [{"code": "AUR_DEHUM", "enabled": True, "params": {}}],
        },
        [tmp_path, CORPUS],
    )
    job = wait_for_job(job_id)
    assert job.state == "done"
    result = job.results[0]
    assert result["status"] == "delivered"
    assert result["loudness_lufs"] is not None
    assert result["within_tolerance"] is None
    assert result["target_lufs"] is None
    assert not job.warnings


def test_a_job_with_a_loudness_stage_measures_against_it(tmp_path: Path) -> None:
    """A chain that carries a loudness stage names the delivery to meet."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    steps = bf.augment_chain([], "music-stream")
    job_id = start_job(
        {
            "paths": [str(clip("hiss"))],
            "out_dir": str(out_dir),
            "chain": steps,
        },
        [tmp_path, CORPUS],
    )
    job = wait_for_job(job_id)
    assert job.state == "done"
    result = job.results[0]
    assert result["status"] == "delivered"
    assert result["target_lufs"] == -14.0
    assert result["tolerance_lu"] == 1.0
    assert result["true_peak_ceiling_dbtp"] == -1.0
    assert result["within_tolerance"] is True
    assert abs(result["loudness_error_lu"]) <= 1.0


def test_a_job_that_normalises_the_loudness_brings_the_file_onto_the_level(
    tmp_path: Path,
) -> None:
    """The plain loudness normalisation still works and is measured."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    job_id = start_job(
        {
            "paths": [str(clip("hiss"))],
            "out_dir": str(out_dir),
            "chain": [{"code": "AUR_DEHUM", "enabled": True, "params": {}}],
            "loudness_normalise": True,
            "target_lufs": -20.0,
        },
        [tmp_path, CORPUS],
    )
    job = wait_for_job(job_id)
    assert job.state == "done"
    result = job.results[0]
    assert result["status"] == "delivered"
    assert result["target_lufs"] == -20.0
    assert result["within_tolerance"] is True
    assert abs(result["loudness_error_lu"]) <= 0.5
    assert not job.warnings


# ---------------------------------------------------------------------------
# the measurement helpers
# ---------------------------------------------------------------------------
def test_the_measurement_of_a_missing_file_is_unknown(tmp_path: Path) -> None:
    """A file that cannot be read carries no measurement."""
    measurement = bf.measure_delivery(tmp_path / "nothing.wav")
    assert measurement.loudness_lufs is None
    assert measurement.peak_dbfs is None
    assert measurement.true_peak_dbtp is None


def test_the_measurement_of_a_silent_file_has_no_loudness(tmp_path: Path) -> None:
    """Silence has no integrated loudness, but it has a peak."""
    measurement = bf.measure_delivery(clip("silence"))
    assert measurement.loudness_lufs is None
    assert measurement.peak_dbfs is not None
    assert measurement.peak_dbfs <= bf.LEVEL_FLOOR_DB + 0.01


def test_the_gain_is_bounded_by_the_true_peak_ceiling() -> None:
    """The plan never asks for a gain the ceiling does not allow."""
    measured = bf.DeliveryMeasurement(
        loudness_lufs=-30.0,
        peak_dbfs=-1.0,
        true_peak_dbtp=-1.0,
        sample_peak=0.89,
    )
    plan = bf.plan_gain(measured, -14.0, -1.0)
    assert plan.wanted_db == 16.0
    assert plan.limited_by_peak is True
    assert plan.gain_db <= plan.headroom_db
    assert plan.gain_db < 0.0, "the plan asks for a gain that would clip the file"


def test_the_gain_reaches_the_target_when_the_peak_allows_it() -> None:
    """The plan asks for the gain the target needs when there is room."""
    measured = bf.DeliveryMeasurement(
        loudness_lufs=-30.0,
        peak_dbfs=-20.0,
        true_peak_dbtp=-20.0,
        sample_peak=0.1,
    )
    plan = bf.plan_gain(measured, -14.0, -1.0)
    assert plan.limited_by_peak is False
    assert plan.gain_db == pytest.approx(16.0)
