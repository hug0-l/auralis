"""
qa.performance.budgets — the realtime budgets of the Auralis core.

This file is the gate of ``tests/test_performance.py``.  It says how
much a module and a product chain may cost for one block of audio.

It is written by ``qa/performance/make_budgets.py`` from a sweep
(``qa/performance/sweep.py``) and it is committed.  A budget that
were regenerated on every test run would always equal the
measurement and would never catch anything.

The unit is the **transform equivalent**: how many 1024 point
transforms a block may cost.  It is machine independent, so the
verdict does not depend on how busy the machine that runs the
test is.  The realtime budget of a block at 48 kHz is
``block / 48000`` seconds, which is
160.6 transform equivalents
for a 64 sample block on the reference machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The cost of one 1024 point transform on the reference machine,
#: in microseconds.  It is the yardstick every measurement is
#: divided by, so a timing becomes a machine independent number.
REFERENCE_TRANSFORM_US = 8.300

#: The block sizes the guard measures.
BLOCK_SIZES: tuple[int, ...] = (64, 128, 256, 512)

#: The realtime budget of one block, in microseconds.
BUDGET_US: dict[int, float] = {
    64: 1333.3333,
    128: 2666.6667,
    256: 5333.3333,
    512: 10666.6667,
}

#: The realtime budget of one block, in transform equivalents.
BUDGET_TRANSFORMS: dict[int, float] = {
    64: 160.6426,
    128: 321.2851,
    256: 642.5703,
    512: 1285.1406,
}

#: The modules whose realtime cost is being repaired right now.
UNDER_REPAIR: tuple[str, ...] = (
)


@dataclass(frozen=True)
class Budget:
    """The realtime budget of one module or one chain.

    Attributes
    ----------
    code:
        The module code, or the name of the chain.
    kind:
        ``realtime`` — the budget is the realtime budget of the
        block, i.e. the requirement itself.  ``debt`` — the module
        does not fit into the realtime budget today, so the budget
        is the measured cost plus a margin: a ceiling that holds it
        where it is and fails when it gets worse.
    margin:
        The margin of a debt budget, in percent.
    note:
        Why the budget is what it is.
    measured:
        The measured cost per block size, in transform equivalents.
    transforms:
        The ceiling per block size, in transform equivalents.
    """

    code: str
    kind: str = "realtime"
    margin: float = 0.0
    note: str = ""
    measured: dict[int, float] = field(default_factory=dict)
    transforms: dict[int, float] = field(default_factory=dict)

    @property
    def measured_us(self) -> float:
        """The measured cost of a 64 sample block, in reference us."""
        return self.measured.get(64, 0.0) * REFERENCE_TRANSFORM_US

    @property
    def budget_us(self) -> float:
        """The ceiling for a 64 sample block, in reference us."""
        return self.transforms.get(64, 0.0) * REFERENCE_TRANSFORM_US

    @property
    def over_budget(self) -> bool:
        """True when the module does not fit into the realtime budget."""
        return self.kind == "debt"


#: The product chains the guard measures, in signal order.
CHAIN_MODULES: dict[str, tuple[str, ...]] = {
    'dialogue': ('AUR_DEHUM', 'AUR_DECLICK', 'AUR_DEESS', 'AUR_DEPLOSIVE', 'AUR_GAIN'),
    'spectral': ('AUR_DEHUM', 'AUR_SPECTRALDENOISE', 'AUR_DECLICK', 'AUR_DEESS', 'AUR_GAIN'),
}

#: The budget of every module.
MODULE_BUDGETS: dict[str, Budget] = {
    'AUR_SPATIALSUPPRESSOR': Budget(
        code='AUR_SPATIALSUPPRESSOR',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 3.7192,
            128: 4.3624,
            256: 30.4204,
            512: 52.609,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_AMBIENCEMATCH': Budget(
        code='AUR_AMBIENCEMATCH',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 37.5800,
            128: 52.9280,
            256: 54.5470,
            512: 61.5140,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_AZIMUTH': Budget(
        code='AUR_AZIMUTH',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 7.3500,
            128: 6.4980,
            256: 8.7490,
            512: 7.6760,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_BANDRESTORATION': Budget(
        code='AUR_BANDRESTORATION',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 7.6810,
            128: 7.8770,
            256: 9.5250,
            512: 7.2800,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_BREATH': Budget(
        code='AUR_BREATH',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 256 samples',
        measured={
            64: 3.4310,
            128: 3.5840,
            256: 30.8790,
            512: 44.8210,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_CENTEREXTRACT': Budget(
        code='AUR_CENTEREXTRACT',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 256 samples',
        measured={
            64: 0.9500,
            128: 0.8740,
            256: 24.5530,
            512: 32.1540,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_DEBLEED': Budget(
        code='AUR_DEBLEED',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 32.1290,
            128: 32.8860,
            256: 32.1350,
            512: 31.8220,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_DECLICK': Budget(
        code='AUR_DECLICK',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 1.7430,
            128: 1.7490,
            256: 2.0160,
            512: 2.4570,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_DECLIP': Budget(
        code='AUR_DECLIP',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 3.0240,
            128: 3.1110,
            256: 3.2230,
            512: 4.1320,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_DECRACKLE': Budget(
        code='AUR_DECRACKLE',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.7780,
            128: 0.9510,
            256: 0.9390,
            512: 2.0860,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_DEESS': Budget(
        code='AUR_DEESS',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 256 samples',
        measured={
            64: 3.2420,
            128: 3.3310,
            256: 37.1080,
            512: 53.7170,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_DEHUM': Budget(
        code='AUR_DEHUM',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 12.1270,
            128: 12.7870,
            256: 15.9760,
            512: 14.7260,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_DEPLOSIVE': Budget(
        code='AUR_DEPLOSIVE',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 128 samples',
        measured={
            64: 2.4750,
            128: 15.3630,
            256: 24.8000,
            512: 42.9470,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_DEREVERB': Budget(
        code='AUR_DEREVERB',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 512 samples',
        measured={
            64: 1.6980,
            128: 2.3870,
            256: 3.3000,
            512: 47.0090,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_DERUSTLE': Budget(
        code='AUR_DERUSTLE',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 512 samples',
        measured={
            64: 2.3030,
            128: 2.2730,
            256: 2.8280,
            512: 68.0160,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_DEWIND': Budget(
        code='AUR_DEWIND',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 256 samples',
        measured={
            64: 3.6410,
            128: 4.3580,
            256: 26.3540,
            512: 49.8880,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_DECONSTRUCT': Budget(
        code='AUR_DECONSTRUCT',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 512 samples',
        measured={
            64: 4.9060,
            128: 4.7570,
            256: 5.5680,
            512: 183.0050,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_DIALOGUEISOLATE': Budget(
        code='AUR_DIALOGUEISOLATE',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 2.9190,
            128: 3.2470,
            256: 3.5370,
            512: 5.1310,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_GUITARDENOISE': Budget(
        code='AUR_GUITARDENOISE',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 26.4330,
            128: 20.8520,
            256: 53.7000,
            512: 86.7410,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_MOUTHDECLICK': Budget(
        code='AUR_MOUTHDECLICK',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.8710,
            128: 0.8910,
            256: 0.8690,
            512: 1.2910,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_MUSICREBALANCE': Budget(
        code='AUR_MUSICREBALANCE',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 91.1850,
            128: 68.0910,
            256: 114.3640,
            512: 75.6840,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_REPAIRASSISTANT': Budget(
        code='AUR_REPAIRASSISTANT',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.3690,
            128: 0.3410,
            256: 0.3890,
            512: 0.3740,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_SPECTRALDENOISE': Budget(
        code='AUR_SPECTRALDENOISE',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 256 samples',
        measured={
            64: 2.7770,
            128: 10.5480,
            256: 308.7720,
            512: 569.2470,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_SPECTRALEDITOR': Budget(
        code='AUR_SPECTRALEDITOR',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.1990,
            128: 0.2590,
            256: 0.2880,
            512: 0.3320,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_SPECTRALRECOVERY': Budget(
        code='AUR_SPECTRALRECOVERY',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 40.0230,
            128: 39.2550,
            256: 31.6110,
            512: 36.7390,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_SPECTRALREPAIR': Budget(
        code='AUR_SPECTRALREPAIR',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.1750,
            128: 0.2180,
            256: 0.2330,
            512: 0.3470,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_VOICEDENOISE': Budget(
        code='AUR_VOICEDENOISE',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 256 samples',
        measured={
            64: 2.1130,
            128: 2.0930,
            256: 92.4810,
            512: 132.7170,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_VOICEREVERB': Budget(
        code='AUR_VOICEREVERB',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 256 samples',
        measured={
            64: 1.5190,
            128: 1.6540,
            256: 21.7840,
            512: 35.0000,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_WOWFLUTTER': Budget(
        code='AUR_WOWFLUTTER',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 24.2210,
            128: 32.7630,
            256: 33.0900,
            512: 63.1210,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_DITHER': Budget(
        code='AUR_DITHER',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 2.3400,
            128: 2.5450,
            256: 2.9120,
            512: 3.4120,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_EQ': Budget(
        code='AUR_EQ',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 3.1490,
            128: 2.8080,
            256: 3.8410,
            512: 4.6560,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_EQMATCH': Budget(
        code='AUR_EQMATCH',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.2050,
            128: 0.1980,
            256: 0.2270,
            512: 0.1780,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_FADE': Budget(
        code='AUR_FADE',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 63.5110,
            128: 55.0300,
            256: 57.7400,
            512: 50.5500,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_GAIN': Budget(
        code='AUR_GAIN',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.2320,
            128: 0.2520,
            256: 0.4420,
            512: 0.4230,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_INTERPOLATE': Budget(
        code='AUR_INTERPOLATE',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 52.7480,
            128: 65.6840,
            256: 62.4290,
            512: 58.2000,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_LEVELER': Budget(
        code='AUR_LEVELER',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 512 samples',
        measured={
            64: 9.9180,
            128: 22.4050,
            256: 44.2260,
            512: 93.6000,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_LOUDNESS': Budget(
        code='AUR_LOUDNESS',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 512 samples',
        measured={
            64: 9.8590,
            128: 22.5190,
            256: 44.0400,
            512: 91.1240,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_LOUDNESSOPT': Budget(
        code='AUR_LOUDNESSOPT',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 2.8250,
            128: 4.5960,
            256: 9.4470,
            512: 18.0600,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_MIXING': Budget(
        code='AUR_MIXING',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 1.1300,
            128: 1.0950,
            256: 1.2300,
            512: 1.4090,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_NORMALIZE': Budget(
        code='AUR_NORMALIZE',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.6110,
            128: 0.6380,
            256: 0.7150,
            512: 0.8660,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_PHASE': Budget(
        code='AUR_PHASE',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.4130,
            128: 0.3150,
            256: 0.3430,
            512: 0.3390,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_RESAMPLE': Budget(
        code='AUR_RESAMPLE',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 4.5880,
            128: 4.8080,
            256: 7.2970,
            512: 11.8450,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_SIGNALGEN': Budget(
        code='AUR_SIGNALGEN',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 1.3750,
            128: 1.7210,
            256: 2.1420,
            512: 2.5960,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_STREAMPREVIEW': Budget(
        code='AUR_STREAMPREVIEW',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.6720,
            128: 0.6860,
            256: 0.8000,
            512: 0.7710,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_TIMEPITCH': Budget(
        code='AUR_TIMEPITCH',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.4210,
            128: 0.4170,
            256: 0.4480,
            512: 0.4790,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_VARIPITCH': Budget(
        code='AUR_VARIPITCH',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.2750,
            128: 0.2640,
            256: 0.2870,
            512: 0.3000,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_VARTIME': Budget(
        code='AUR_VARTIME',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.2640,
            128: 0.2570,
            256: 0.2940,
            512: 0.2740,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_CLIPCLASSIFIER': Budget(
        code='AUR_CLIPCLASSIFIER',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.7480,
            128: 0.8350,
            256: 1.0480,
            512: 1.3090,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_FINDSIMILAR': Budget(
        code='AUR_FINDSIMILAR',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.1860,
            128: 0.1780,
            256: 0.2090,
            512: 0.2200,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_CONNECT': Budget(
        code='AUR_CONNECT',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 0.1140,
            128: 0.2140,
            256: 0.2800,
            512: 0.2790,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'AUR_MONITOR': Budget(
        code='AUR_MONITOR',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 64 samples',
        measured={
            64: 5.6530,
            128: 6.7900,
            256: 9.0240,
            512: 13.3440,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
}

#: The budget of the product chains.
CHAIN_BUDGETS: dict[str, Budget] = {
    'dialogue': Budget(
        code='dialogue',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 256 samples',
        measured={
            64: 21.8600,
            128: 40.3230,
            256: 88.1060,
            512: 138.5840,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
    'spectral': Budget(
        code='spectral',
        kind='realtime',
        margin=0.0,
        note='fits the realtime budget at 512 samples',
        measured={
            64: 25.5480,
            128: 42.8220,
            256: 375.1980,
            512: 831.1870,
        },
        transforms={
            64: 160.6426,
            128: 321.2851,
            256: 642.5703,
            512: 1285.1406,
        },
    ),
}

#: The modules that allocate inside ``process()`` today.  The
#: guard fails when a module that is not in this list starts to
#: allocate: the number of allocating modules must not grow.
ALLOCATING_MODULES: frozenset[str] = frozenset({
    'AUR_SPATIALSUPPRESSOR',
    'AUR_AMBIENCEMATCH',
    'AUR_AZIMUTH',
    'AUR_BANDRESTORATION',
    'AUR_BREATH',
    'AUR_CENTEREXTRACT',
    'AUR_CLIPCLASSIFIER',
    'AUR_CONNECT',
    'AUR_DEBLEED',
    'AUR_DECLICK',
    'AUR_DECLIP',
    'AUR_DECONSTRUCT',
    'AUR_DECRACKLE',
    'AUR_DEESS',
    'AUR_DEHUM',
    'AUR_DEPLOSIVE',
    'AUR_DEREVERB',
    'AUR_DERUSTLE',
    'AUR_DEWIND',
    'AUR_DIALOGUEISOLATE',
    'AUR_DITHER',
    'AUR_EQ',
    'AUR_EQMATCH',
    'AUR_FADE',
    'AUR_FINDSIMILAR',
    'AUR_GAIN',
    'AUR_GUITARDENOISE',
    'AUR_INTERPOLATE',
    'AUR_LEVELER',
    'AUR_LOUDNESS',
    'AUR_LOUDNESSOPT',
    'AUR_MIXING',
    'AUR_MONITOR',
    'AUR_MOUTHDECLICK',
    'AUR_MUSICREBALANCE',
    'AUR_NORMALIZE',
    'AUR_PHASE',
    'AUR_REPAIRASSISTANT',
    'AUR_RESAMPLE',
    'AUR_SIGNALGEN',
    'AUR_SPECTRALDENOISE',
    'AUR_SPECTRALEDITOR',
    'AUR_SPECTRALRECOVERY',
    'AUR_SPECTRALREPAIR',
    'AUR_STREAMPREVIEW',
    'AUR_TIMEPITCH',
    'AUR_VARIPITCH',
    'AUR_VARTIME',
    'AUR_VOICEDENOISE',
    'AUR_VOICEREVERB',
    'AUR_WOWFLUTTER',
})

#: The allocation baseline of every module: the number of NumPy
#: arrays it creates per block, the bytes of new array data it
#: creates per block and the peak Python churn of one block.
ALLOC_BASELINE: dict[str, tuple[float, float, int]] = {
    'AUR_AMBIENCEMATCH': (42.00, 570960.0, 36755),
    'AUR_AZIMUTH': (9.00, 12576.0, 11232),
    'AUR_BANDRESTORATION': (41.00, 5120.0, 1152),
    'AUR_BREATH': (22.00, 30208.0, 33264),
    'AUR_CENTEREXTRACT': (22.00, 108728.0, 33168),
    'AUR_CLIPCLASSIFIER': (2.00, 0.0, 16),
    'AUR_CONNECT': (2.00, 0.0, 16),
    'AUR_DEBLEED': (39.00, 585968.0, 576),
    'AUR_DECLICK': (12.00, 65024.0, 64016),
    'AUR_DECLIP': (1.00, 0.0, 16),
    'AUR_DECONSTRUCT': (38.00, 87576.0, 95952),
    'AUR_DECRACKLE': (4.00, 37376.0, 36176),
    'AUR_DEESS': (20.00, 25600.0, 31376),
    'AUR_DEHUM': (15.00, 102960.0, 3960),
    'AUR_DEPLOSIVE': (18.50, 30288.0, 7536),
    'AUR_DEREVERB': (21.00, 62976.0, 65968),
    'AUR_DERUSTLE': (13.00, 29184.0, 32112),
    'AUR_DEWIND': (21.00, 30208.0, 33328),
    'AUR_DIALOGUEISOLATE': (14.00, 5176.0, 3984),
    'AUR_DITHER': (12.00, 5120.0, 136),
    'AUR_EQ': (18.00, 0.0, 186),
    'AUR_EQMATCH': (2.00, 0.0, 16),
    'AUR_FADE': (6.00, 769024.0, 48),
    'AUR_FINDSIMILAR': (1.00, 0.0, 16),
    'AUR_GAIN': (2.00, 0.0, 16),
    'AUR_GUITARDENOISE': (68.00, 173380.0, 2146),
    'AUR_INTERPOLATE': (161.00, 653728.0, 1585),
    'AUR_LEVELER': (53.00, 17512.0, 7072),
    'AUR_LOUDNESS': (53.00, 17512.0, 7072),
    'AUR_LOUDNESSOPT': (10.00, 880.0, 240),
    'AUR_MIXING': (6.00, 1056.0, 48),
    'AUR_MONITOR': (20.00, 18016.0, 1206),
    'AUR_MOUTHDECLICK': (4.00, 37376.0, 36176),
    'AUR_MUSICREBALANCE': (48.00, 703294.0, 944),
    'AUR_NORMALIZE': (4.00, 1024.0, 16),
    'AUR_PHASE': (2.00, 0.0, 16),
    'AUR_REPAIRASSISTANT': (3.00, 0.0, 16),
    'AUR_RESAMPLE': (13.00, 6152.0, 3042),
    'AUR_SIGNALGEN': (5.00, 2048.0, 16),
    'AUR_SPECTRALDENOISE': (21.00, 12856.0, 7200),
    'AUR_SPECTRALEDITOR': (2.00, 0.0, 16),
    'AUR_SPECTRALRECOVERY': (27.00, 491752.0, 288),
    'AUR_SPECTRALREPAIR': (2.00, 0.0, 16),
    'AUR_STREAMPREVIEW': (5.00, 5632.0, 3344),
    'AUR_TIMEPITCH': (2.00, 0.0, 16),
    'AUR_VARIPITCH': (2.00, 0.0, 16),
    'AUR_VARTIME': (2.00, 0.0, 16),
    'AUR_VOICEDENOISE': (12.00, 39408.0, 37184),
    'AUR_VOICEREVERB': (12.00, 12800.0, 15600),
    'AUR_WOWFLUTTER': (75.00, 46048.0, 10698),
}

#: The factor the allocation baseline may grow by before the
#: guard complains.
ALLOC_MARGIN = 1.5

