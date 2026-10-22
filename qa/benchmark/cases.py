"""
qa.benchmark.cases — the cases of the benchmark.

A case is a signal whose truth is known: the wanted part, the unwanted part
and the signal that is fed to the module are three separate things, so that a
measurement can say what a module removed and what it kept.

Two families of case:

* the **corpus cases** are clips of ``corpus/audio/test``.  The manifest of
  the corpus names the bands that carry nothing but the artefact and the
  partials that carry wanted content.  The benchmark reads them from there,
  so the ground truth of the corpus stays in one place.
* the **voice cases** are the three signals of the voice report: the corpus
  voice clip, the synthetic voice under a white bed and the speech shaped
  signal of the quality report.

Every case names the module of ours that owns it.  The choice is written
down with its reason, so that the benchmark measures the module that is
supposed to win the case and not the module that happens to come first in the
registry.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "Case",
    "CASES",
    "CASE_BY_NAME",
    "CORPUS_CASES",
    "VOICE_CASES",
]


@dataclass(frozen=True)
class Case:
    """One case of the benchmark.

    Attributes
    ----------
    name:
        The name of the case.  For a corpus case it is also the name of the
        clip of the corpus.
    kind:
        ``"corpus"`` or ``"voice"``; it says which instrument measures it.
    module:
        The code of the module of ours that owns the case.
    artefact:
        The names of the bands that carry nothing but the artefact.  For a
        corpus case they are the names the manifest gives its bands; for a
        voice case they are ``"hiss"`` or the name of the artefact of the
        case.
    wanted:
        The names of the partials that carry wanted content, again as the
        manifest names them.  Empty when the case has no wanted content.
    settings:
        The parameter overrides of the module, as ``(name, value)`` pairs.
        Empty means the module is measured with its own defaults, which is
        what a user gets.
    reason:
        Why this module owns this case.
    """

    name: str
    kind: str
    module: str
    artefact: tuple[str, ...] = ()
    wanted: tuple[str, ...] = ()
    settings: tuple[tuple[str, object], ...] = ()
    reason: str = ""

    @property
    def settings_map(self) -> dict:
        """The parameter overrides of the module, as a dictionary."""
        return dict(self.settings)


#: Every case of the benchmark, in the order they are measured.
CASES: tuple[Case, ...] = (
    Case(
        name="hiss",
        kind="corpus",
        module="AUR_SPECTRALDENOISE",
        artefact=("broadband",),
        wanted=(),
        reason=(
            "A steady white bed with nothing wanted in it: the whole clip is "
            "artefact, so the number is the plainest one of the whole "
            "benchmark.  The spectral de-noise owns a stationary broadband "
            "bed."
        ),
    ),
    Case(
        name="pink_hiss",
        kind="corpus",
        module="AUR_SPECTRALDENOISE",
        artefact=("broadband",),
        wanted=(),
        reason=(
            "The same bed with a pink spectrum, so the energy moves with the "
            "frequency: the module has to work over the whole spectrum, not "
            "over one band.  The spectral de-noise owns it."
        ),
    ),
    Case(
        name="hum50",
        kind="corpus",
        module="AUR_DEHUM",
        artefact=("hum", "hum_harmonic"),
        wanted=("tone",),
        reason=(
            "Mains hum with its first harmonic under a 1 kHz programme tone.  "
            "The de-hum owns a periodic low frequency artefact; the tone is "
            "the wanted content that has to survive it."
        ),
    ),
    Case(
        name="rumble",
        kind="corpus",
        module="AUR_DEHUM",
        artefact=("rumble",),
        wanted=("programme",),
        reason=(
            "Low frequency rumble under a 1 kHz programme tone.  The de-hum "
            "owns it as well: the notch bank of the module takes the rumble "
            "away and leaves the tone where it is."
        ),
    ),
    Case(
        name="voice_hiss",
        kind="corpus",
        module="AUR_SPECTRALDENOISE",
        artefact=("hiss",),
        wanted=("f0",),
        reason=(
            "A voice like tone under hiss.  The hiss of the clip lives in the "
            "difference between the two channels, so the corpus reads it "
            "there; the spectral de-noise works on each channel on its own "
            "and is measured with the ruler of the corpus, like everybody "
            "else.  The voice shaped module of ours is measured on the voice "
            "cases below, with the ruler of the voice report."
        ),
    ),
    Case(
        name="corpus_voice",
        kind="voice",
        module="AUR_VOICEDENOISE",
        artefact=("hiss",),
        wanted=("voice",),
        reason=(
            "The voice clip of the corpus: the voice in the middle channel, "
            "the hiss in the difference between the channels.  The voice "
            "de-noise owns a voice under hiss."
        ),
    ),
    Case(
        name="synthetic_voice",
        kind="voice",
        module="AUR_VOICEDENOISE",
        artefact=("hiss",),
        wanted=("voice",),
        reason=(
            "The synthetic voice of the voice report under a white bed 45 dB "
            "down: the case the voice rules are written against.  The voice "
            "de-noise owns it."
        ),
    ),
    Case(
        name="speech_shaped",
        kind="voice",
        module="AUR_VOICEDENOISE",
        artefact=("hiss",),
        wanted=("voice",),
        reason=(
            "The speech shaped signal of the quality report: a harmonic stack "
            "under hiss, the case of the spectral rules.  The voice de-noise "
            "owns it as well."
        ),
    ),
)

#: The cases, keyed by name.
CASE_BY_NAME: dict[str, Case] = {case.name: case for case in CASES}

#: The cases that are clips of the corpus.
CORPUS_CASES: tuple[Case, ...] = tuple(case for case in CASES if case.kind == "corpus")

#: The cases that are measured with the ruler of the voice report.
VOICE_CASES: tuple[Case, ...] = tuple(case for case in CASES if case.kind == "voice")
