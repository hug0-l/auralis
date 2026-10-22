"""
qa.performance.model_budgets — the cost budgets of the shipped models.

This file is the gate of ``tests/test_model_budget.py``.  It says
what a shipped model may cost for one block of audio.

It is written by ``qa/performance/make_model_budgets.py`` from a
sweep (``qa/performance/sweep_models.py``) and it is committed.  A
budget that were regenerated on every test run would always equal
the measurement and would never catch anything.

The unit is the **transform equivalent**: how many 1024 point
transforms a block may cost.  It is machine independent, so the
verdict does not depend on how busy the machine that runs the
test is.  The realtime budget of a block at 48 kHz is
``block / 48000`` seconds, which is
160.6 transform equivalents
for a 64 sample block on the reference machine.

The rule of the product is written down here as well: a model
belongs to the **offline** path.  The rule is a policy and not a
technical limit — the hand written modules are exact,
deterministic and cheaper, so they stay the default in the real
time path.  The measurement below shows that every model we
ship would fit into the real time budget as well, and the gate
keeps that true.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The cost of one 1024 point transform on the reference machine,
#: in microseconds.  It is the yardstick every measurement is
#: divided by, so a timing becomes a machine independent number.
REFERENCE_TRANSFORM_US = 8.300

#: The block size the budget is quoted at.
BLOCK = 64

#: The realtime budget of one block, in microseconds.
BUDGET_US = 1333.3333

#: The realtime budget of one block, in transform equivalents.
BUDGET_TRANSFORMS = 160.6426

#: The rule of the product: a model is an offline capability.
#:
#: The rule is a policy and not a technical limit.  The hand
#: written modules are exact, deterministic and cheaper, so they
#: stay the default in the real time path; a model is offered
#: where the extra quality is worth the wait.  The measurement
#: shows that every model we ship fits into the real time budget
#: as well, so the rule is a choice.  The gate keeps it a choice:
#: a model that grows past the budget fails the test.
MODEL_RULE = 'offline'

#: The budgets a model can be held to.
#:
#: ``"block"`` — the realtime budget of a 64 sample block: the
#: model is cheap enough to run inside a real time host.
#: ``"speed"`` — the offline budget: the model is asked once per
#: sample, or the like, and it is held to the speed of the
#: offline path instead of the budget of a block.
BUDGET_KINDS = ('block', 'speed')

#: The offline budget: how many times the real time the offline
#: path may take.  A render may take as long as the file plays
#: (1.0), and twice as long (2.0) before the gate complains.
#:
#: It is a budget of the *path*, not of a module: a file render
#: has no deadline, so there is no block to hold it to.  What it
#: must not become is a render nobody waits for.
OFFLINE_SPEED_CEILING = 2.0

#: The factor the live measurement may exceed the recorded one
#: by before the gate complains.  The machine is shared and the
#: calibration of a session resolves the load to about a factor of
#: two, so the live check must not fail on noise.  A model that
#: got twice as expensive is still caught.
LIVE_MARGIN = 1.0


@dataclass(frozen=True)
class ModelBudget:
    """The cost budget of one shipped model.

    Attributes
    ----------
    name:
        The name the model publishes.
    problem:
        The problem the model solves.
    version:
        The version of the model.
    variant:
        The variant inside the family.
    parameters:
        The number of values the weights hold.
    kind:
        The rule the model is held to.  ``offline`` — a model
        is an offline capability; the rule is a policy of the
        product and not a technical limit.
    budget:
        The budget the model is held to.  ``block`` — the
        realtime budget of a 64 sample block.  ``speed`` — the
        offline budget: the speed of the offline path.
    speed_factor:
        How many times the real time the model took for it.
    speed_ceiling:
        The ceiling of the speed budget, in times the real time.
    note:
        Why the budget is what it is.
    seconds:
        The length of the audio the cost was measured over.
    realtime_factor:
        How many times the real time the model took for it.
    measured:
        The measured cost per block size, in transform equivalents.
    transforms:
        The ceiling per block size, in transform equivalents.
    """

    name: str
    problem: str = ""
    version: str = ""
    variant: str = ""
    parameters: int = 0
    kind: str = "offline"
    budget: str = "block"
    speed_factor: float = 0.0
    speed_ceiling: float = 0.0
    note: str = ""
    seconds: float = 0.0
    realtime_factor: float = 0.0
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
    def share(self) -> float:
        """How much of the realtime budget the model takes, in %."""
        budget = self.budget_us
        return self.measured_us / budget * 100.0 if budget else 0.0

    @property
    def over_budget(self) -> bool:
        """True when the model does not fit inside its budget."""
        if self.budget == "speed":
            return self.speed_factor > self.speed_ceiling
        return self.measured_us > self.budget_us


#: The budget of every shipped model.
MODEL_BUDGETS: dict[str, ModelBudget] = {
    'ambience_match': ModelBudget(
        name='ambience_match',
        problem='ambience',
        version='2.0.0',
        variant='small',
        parameters=9872,
        kind='offline',
        budget='block',
        speed_factor=0.0116,
        speed_ceiling=0.0,
        note=(
            '1.2 % of the 64 sample budget (15.7 us of 1333.3 us); 8 rounds of 10 s of audio, '
            'fastest round wins; the 8 rounds spread 1.9 to 2.8 transforms '
        ),
        seconds=10.0,
        realtime_factor=0.0116,
        measured={
            64: 1.8869,
        },
        transforms={
            64: 160.6426,
        },
    ),
    'voice_denoise_mask': ModelBudget(
        name='voice_denoise_mask',
        problem='voice',
        version='1.1.0',
        variant='base',
        parameters=18608,
        kind='offline',
        budget='block',
        speed_factor=0.0089,
        speed_ceiling=0.0,
        note=(
            '0.9 % of the 64 sample budget (11.8 us of 1333.3 us); 8 rounds of 10 s of audio, '
            'fastest round wins; the 8 rounds spread 1.4 to 2.0 transforms '
        ),
        seconds=10.0,
        realtime_factor=0.0089,
        measured={
            64: 1.4220,
        },
        transforms={
            64: 160.6426,
        },
    ),
    'voice_enhance': ModelBudget(
        name='voice_enhance',
        problem='voice',
        version='2.0.0',
        variant='small',
        parameters=6800,
        kind='offline',
        budget='block',
        speed_factor=0.0079,
        speed_ceiling=0.0,
        note=(
            '0.8 % of the 64 sample budget (10.7 us of 1333.3 us); 8 rounds of 10 s of audio, '
            'fastest round wins; the 8 rounds spread 1.3 to 1.8 transforms '
        ),
        seconds=10.0,
        realtime_factor=0.0079,
        measured={
            64: 1.2921,
        },
        transforms={
            64: 160.6426,
        },
    ),
    'voice_reverb_reduce': ModelBudget(
        name='voice_reverb_reduce',
        problem='voice_reverb',
        version='2.0.0',
        variant='small',
        parameters=12944,
        kind='offline',
        budget='block',
        speed_factor=0.0084,
        speed_ceiling=0.0,
        note=(
            '0.7 % of the 64 sample budget (9.9 us of 1333.3 us); 8 rounds of 10 s of audio, '
            'fastest round wins; the 8 rounds spread 1.2 to 1.8 transforms '
        ),
        seconds=10.0,
        realtime_factor=0.0084,
        measured={
            64: 1.1975,
        },
        transforms={
            64: 160.6426,
        },
    ),
    'voice_sample_mask': ModelBudget(
        name='voice_sample_mask',
        problem='voice',
        version='1.0.0',
        variant='sample',
        parameters=6520,
        kind='offline',
        budget='speed',
        speed_factor=0.7951,
        speed_ceiling=2.0,
        note=(
            '0.80 x the real time and 72.5 % of the 64 sample budget: too close to the edge to '
            'be measured reliably on a shared machine, so the model is held to the offline '
            'budget: a render may take at most 2 times the length of the audio; the 8 rounds '
            'spread 116.5 to 161.9 transforms '
        ),
        seconds=10.0,
        realtime_factor=0.7951,
        measured={
            64: 116.4975,
        },
        transforms={
            64: 160.6426,
        },
    ),
    'voice_sample_memory': ModelBudget(
        name='voice_sample_memory',
        problem='voice',
        version='1.0.0',
        variant='sample',
        parameters=7008,
        kind='offline',
        budget='speed',
        speed_factor=1.1833,
        speed_ceiling=2.0,
        note=(
            '1.18 x the real time and 102.3 % of the 64 sample budget: too close to the edge '
            'to be measured reliably on a shared machine, so the model is held to the offline '
            'budget: a render may take at most 2 times the length of the audio; the 8 rounds '
            'spread 164.3 to 241.6 transforms '
        ),
        seconds=10.0,
        realtime_factor=1.1833,
        measured={
            64: 164.3455,
        },
        transforms={
            64: 160.6426,
        },
    ),
}

#: The raw cost of a model per 64 sample block on the machine
#: that took the measurement, in microseconds.  It is kept next
#: to the table so that the wall clock number of the session is
#: not lost in the conversion, and so that a reader can compare
#: it with a reading of their own machine.
SESSION_US: dict[str, float] = {
    'ambience_match': 15.4727,
    'voice_denoise_mask': 11.8739,
    'voice_enhance': 10.5956,
    'voice_reverb_reduce': 11.1969,
    'voice_sample_mask': 1060.1272,
    'voice_sample_memory': 1577.7167,
}

#: The yardstick of the session that took the measurement, in
#: microseconds per 1024 point transform.  It is the speed of the
#: machine that ran the sweep; the requirement is quoted in the
#: fixed reference above, not in this one.
SESSION_TRANSFORM_US = 6.5000

