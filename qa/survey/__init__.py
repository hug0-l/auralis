"""
qa.survey — the module survey.

The survey answers one question for every one of the 48 modules of the
catalogue:

    what does the module actually do to a signal, and is that good?

Five numbers are measured per module:

``wanted_removed_db``
    How much of the *wanted* part of the signal the module takes away.  For a
    restoration module this has to be close to zero: the job is to remove
    the defect, not the programme.

``unwanted_removed_db``
    How much of the *unwanted* part the module takes away.  This is the job.

``cost_us``
    What one block of 64 samples costs the module, in the microseconds of
    the *reference machine*, against the 1333.3 us realtime budget of such
    a block at 48 kHz.  Every block is timed next to a batch of 1024 point
    transforms of the machine that measures it, so the number does not move
    with the load of the machine — it is the same yardstick the
    performance table uses.

``latency_samples`` / ``block_coherent``
    How long the module delays the signal, and whether the result depends on
    how the audio was chopped into blocks.

``finite`` / ``same_length`` / ``allocator_independent``
    Whether the output is a real signal: finite, as long as the input, and
    the same no matter what the allocator handed out.

The measurement code is measurement only — it never touches ``aurcore`` or
``aurcore.dsp``.  Run it with::

    python qa/survey/survey.py --all
"""
