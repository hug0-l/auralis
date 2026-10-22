"""
tests/test_learned_stage.py — the learned restoration of a file render.

Three things are tested here:

* the **mix decision**: a model that is asked about the mix of the channels
  gives every channel the same gain, so the image does not move;
* the **named run**: a model of the library runs over a whole signal by
  name, and a name the library does not hold is a refusal;
* the **render stage**: ``POST /api/render`` takes the name of a model and
  runs it over the rendered file, and the answer says which model ran.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurcore import models


# ---------------------------------------------------------------------------
# the helpers
# ---------------------------------------------------------------------------
def _stereo(rate: int = 48000, seconds: float = 0.5) -> np.ndarray:
    """Return a stereo signal with a tone and a hiss."""
    count = int(rate * seconds)
    t = np.arange(count) / float(rate)
    left = 0.2 * np.sin(2.0 * np.pi * 440.0 * t)
    right = 0.1 * np.sin(2.0 * np.pi * 440.0 * t + 0.3)
    hiss = np.random.default_rng(7).standard_normal((count, 2)) * 0.01
    return np.stack([left, right], axis=1) + hiss


def _library():
    """Return the shipped library."""
    return models.open_library()


# ---------------------------------------------------------------------------
# the mix decision
# ---------------------------------------------------------------------------
def test_the_mix_decides_once_for_every_channel():
    """Every channel gets the same gain, so the image does not move.

    The model is asked about the **mix** of the channels.  The gain it
    produces is applied to every channel, so the difference between the
    channels is the difference between the channels of the input: nothing
    the model does moves the image.
    """
    library = _library()
    model = library.load("voice_enhance")
    signal = _stereo()
    out = models.run_offline_mix(model, signal)

    assert out.shape == signal.shape
    assert np.all(np.isfinite(out))
    # The relationship of the two channels is the one of the input: the
    # model scales both by the same gain.
    residual = out[:, 0] - out[:, 1]
    original = signal[:, 0] - signal[:, 1]
    # The difference is not scaled to zero: the model is a per band gain,
    # and the two channels do not hold the same bands.
    correlation = float(np.corrcoef(residual, original)[0, 1])
    assert correlation > 0.9, (
        f"the image moved: the correlation of the channels dropped to {correlation:.3f}"
    )


def test_the_mix_costs_one_channel():
    """The mix is asked once: the cost does not grow with the channels."""
    library = _library()
    model = library.load("voice_enhance")
    mono = _stereo()[:, :1]
    stereo = _stereo()
    one = models.run_offline_mix(model, mono)
    two = models.run_offline_mix(model, stereo)
    assert one.shape[0] == two.shape[0]
    assert two.shape[1] == 2


# ---------------------------------------------------------------------------
# the named run
# ---------------------------------------------------------------------------
def test_a_named_model_runs_over_a_whole_signal():
    """A model of the library runs by name."""
    signal = _stereo()
    out, model = models.run_named("voice_enhance", signal)
    assert out.shape == signal.shape
    assert np.all(np.isfinite(out))
    assert model.manifest.name == "voice_enhance"


def test_a_name_the_library_does_not_hold_is_a_refusal():
    """A render that was asked for a model and did not get one is refused."""
    with pytest.raises(models.ModelError):
        models.run_named("no_such_model", _stereo())


# ---------------------------------------------------------------------------
# the render stage
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    """Return a client of the server."""
    from fastapi.testclient import TestClient

    from aurserver.app import app

    return TestClient(app, raise_server_exceptions=False)


def _wav_bytes(signal: np.ndarray, rate: int = 48000) -> bytes:
    """Return a WAV file of a signal."""
    import io

    import soundfile as sf

    buffer = io.BytesIO()
    sf.write(buffer, signal.astype(np.float32), rate, subtype="PCM_16", format="WAV")
    return buffer.getvalue()


def test_the_render_takes_a_learned_model(client) -> None:
    """The render runs a model of the library over the rendered file."""
    signal = _stereo(seconds=0.5)
    upload = client.post(
        "/api/upload", content=_wav_bytes(signal), headers={"Content-Type": "audio/wav"}
    )
    assert upload.status_code == 200, upload.text
    asset_id = upload.json()["asset_id"]

    answer = client.post(
        "/api/render",
        json={"asset_id": asset_id, "chain": [], "start": 0.0, "end": 0.5,
              "learned": "voice_sample_mask"},
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert "asset_id" in body

    codes = [entry.get("code") for entry in body.get("models", [])]
    assert "AUR_LEARNED" in codes, body
    stage = next(
        entry for entry in body["models"] if entry.get("code") == "AUR_LEARNED"
    )
    assert stage["model"]["name"] == "voice_sample_mask"
    assert stage["model"]["hop"] == 1, stage["model"]


def test_the_render_refuses_a_model_it_does_not_hold(client) -> None:
    """A name the library does not hold is a refusal, not a silent fall back."""
    signal = _stereo(seconds=0.5)
    upload = client.post(
        "/api/upload", content=_wav_bytes(signal), headers={"Content-Type": "audio/wav"}
    )
    asset_id = upload.json()["asset_id"]
    answer = client.post(
        "/api/render",
        json={"asset_id": asset_id, "chain": [], "start": 0.0, "end": 0.5,
              "learned": "no_such_model"},
    )
    assert answer.status_code == 400, answer.text
    assert "no_such_model" in answer.text
