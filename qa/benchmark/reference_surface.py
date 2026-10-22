"""
qa.benchmark.reference_surface — the surfaces of the reference plug-ins, as
plain constants.

Measured with our own VST3 host: the reference plug-ins were loaded into it,
their controllers were asked for every parameter, and the value each
parameter shows was sampled at 41 points across its travel.  The curves below
are the fits of those samples, scored by the largest error they make.  Our own
surface is measured by the same harness, so the numbers here are the line our
modules are held to.

Nothing in this file names a third party.  The two references are described by
their shape:

``reference A``
    the one that separates a recording into three stems and gives each of
    them a gain and three switches (17 parameters);
``reference B``
    the one that picks an algorithm and gives it four bands (22 parameters).

The surfaces are a **target, not a contract**, for the same reason the signal
numbers are: they were measured on somebody else's build.  A control that is
coarser than the line is reported, not failed.
"""
from __future__ import annotations

__all__ = [
    "REFERENCE_GAIN_MIN_DB",
    "REFERENCE_GAIN_MAX_DB",
    "REFERENCE_GAIN_LAW_EXPONENT",
    "REFERENCE_GAIN_LAW_MAX_ERROR_DB",
    "REFERENCE_GAIN_UNITY_TRAVEL",
    "REFERENCE_GAIN_DB_PER_PERCENT",
    "REFERENCE_CUTOFF_MIN_HZ",
    "REFERENCE_CUTOFF_MAX_HZ",
    "REFERENCE_CUTOFF_LAW_EXPONENT",
    "REFERENCE_CUTOFF_LAW_MAX_ERROR_HZ",
    "REFERENCE_CUTOFF_OCTAVES_PER_PERCENT_LOW",
    "REFERENCE_CUTOFF_OCTAVES_PER_PERCENT_HIGH",
    "REFERENCE_BAND_MIX_MAX_ERROR_DB",
    "REFERENCE_BAND_MIX_DB_PER_PERCENT",
    "REFERENCE_A_PARAMETER_COUNT",
    "REFERENCE_B_PARAMETER_COUNT",
    "REFERENCE_A_AUTOMATABLE",
    "REFERENCE_B_AUTOMATABLE",
    "REFERENCE_A_PARAMETERS_WITH_UNITS",
    "REFERENCE_B_PARAMETERS_WITH_UNITS",
    "REFERENCE_A_PROGRAM_COUNT",
    "REFERENCE_B_PROGRAM_COUNT",
    "OUR_MODULE_COUNT",
    "OUR_PARAMETER_COUNT",
    "OUR_PARAMETERS_WITH_UNITS",
    "OUR_LOG_CURVES",
    "OUR_HELP_STRINGS",
    "OUR_BYPASS_PARAMETERS",
    "OUR_PROGRAM_LISTS",
    "OUR_AUTOMATABLE",
    "OUR_LARGEST_PLUG_IN_SURFACE",
    "TARGET_DB_PER_PERCENT_OF_TRAVEL",
    "TARGET_OCTAVES_PER_PERCENT_OF_TRAVEL",
    "TAPER_EXPONENT_60_TO_12",
    "TAPER_EXPONENT_24_TO_4_8",
    "TAPER_EXPONENT_18_TO_3_6",
    "TAPER_EXPONENT_12_TO_2_4",
    "TAPER_DB_PER_PERCENT",
    "OUR_LINEAR_HZ_OCTAVES_PER_PERCENT_AT_100HZ",
    "OUR_LINEAR_HZ_OCTAVES_PER_PERCENT_AT_10KHZ",
    "OUR_GAIN_UNITY_TRAVEL",
    "OUR_LOW_GAIN_UNITY_TRAVEL",
    "OUR_MAKEUP_GAIN_UNITY_TRAVEL",
]


# -- the reference gain control ------------------------------------------
#
# range -60.0 ... +12.0 dB, unity in the middle of the travel, the law
#     dB(n) = lo + (hi - lo) * n^0.264
# the exponent that puts unity in the middle of a range:
#     p = log2((-lo) / (hi - lo))
# the resolution at unity, in dB per 1 % of the travel, for a span B below
# unity:
#     0.00528 * B
#
REFERENCE_GAIN_MIN_DB = -60.0
REFERENCE_GAIN_MAX_DB = 12.0
REFERENCE_GAIN_LAW_EXPONENT = 0.264
REFERENCE_GAIN_LAW_MAX_ERROR_DB = 0.11
REFERENCE_GAIN_UNITY_TRAVEL = 0.5
REFERENCE_GAIN_DB_PER_PERCENT = 0.316

# -- the reference frequency control -------------------------------------
#
# range 100.0 ... 10000.0 Hz, the law
#     Hz(n) = 100.24 + 9900.36 * n^3.334
# 0.0209 to 0.0847 octaves per 1 % of the travel
#
REFERENCE_CUTOFF_MIN_HZ = 100.0
REFERENCE_CUTOFF_MAX_HZ = 10000.0
REFERENCE_CUTOFF_LAW_EXPONENT = 3.334
REFERENCE_CUTOFF_LAW_MAX_ERROR_HZ = 0.60
REFERENCE_CUTOFF_OCTAVES_PER_PERCENT_LOW = 0.0209
REFERENCE_CUTOFF_OCTAVES_PER_PERCENT_HIGH = 0.0847

# -- the reference band mix ----------------------------------------------
#
# range -100.00 ... +100.00 dB, straight, 2.000 dB per 1 % of the travel
#
REFERENCE_BAND_MIX_MAX_ERROR_DB = 0.0
REFERENCE_BAND_MIX_DB_PER_PERCENT = 2.000

# -- the size of the reference surfaces ----------------------------------
#
REFERENCE_A_PARAMETER_COUNT = 17
REFERENCE_B_PARAMETER_COUNT = 22
REFERENCE_A_AUTOMATABLE = 5
REFERENCE_B_AUTOMATABLE = 21
REFERENCE_A_PARAMETERS_WITH_UNITS = 0
REFERENCE_B_PARAMETERS_WITH_UNITS = 0
REFERENCE_A_PROGRAM_COUNT = 6
REFERENCE_B_PROGRAM_COUNT = 1

# -- our own surface, measured the same way ------------------------------
#
OUR_MODULE_COUNT = 49
OUR_PARAMETER_COUNT = 284
OUR_PARAMETERS_WITH_UNITS = 105
OUR_LOG_CURVES = 25
OUR_HELP_STRINGS = 284
OUR_BYPASS_PARAMETERS = 49
OUR_PROGRAM_LISTS = 49
OUR_AUTOMATABLE = 284
OUR_LARGEST_PLUG_IN_SURFACE = 25

# -- the line to hold to -------------------------------------------------
#
# the resolution a control must have at unity:
#   a gain control    0.316 dB per 1 % of the travel
#   a frequency       0.0997 octaves per 1 % of the travel
#     (0.0997 = log2(20000/20) / 100, the log law over 20 ... 20000 Hz)
#
TARGET_DB_PER_PERCENT_OF_TRAVEL = 0.316
TARGET_OCTAVES_PER_PERCENT_OF_TRAVEL = 0.0997

# -- the taper exponents that carry our own ranges to that line -----------
#
#   p = log2((-lo) / (hi - lo))
#
TAPER_EXPONENT_60_TO_12 = 0.264
TAPER_EXPONENT_24_TO_4_8 = 0.263
TAPER_EXPONENT_18_TO_3_6 = 0.263
TAPER_EXPONENT_12_TO_2_4 = 0.263

# -- the resolution that follows from the span below unity ----------------
#
#   dB per 1 % of travel = 0.00528 * B
#
TAPER_DB_PER_PERCENT = 0.00528

# -- what a control must not be ------------------------------------------
#
# a frequency control that is linear in hertz moves 1.58 octaves per 1 %
# of travel at 100 Hz; the reference moves 0.02.  This is the number the
# equaliser's band frequencies and the generator's frequency have to beat.
#
OUR_LINEAR_HZ_OCTAVES_PER_PERCENT_AT_100HZ = 1.584

# the same control at the top of its range, for the ratio:
OUR_LINEAR_HZ_OCTAVES_PER_PERCENT_AT_10KHZ = 0.0285

# where unity sits in the travel of the gain controls:
OUR_GAIN_UNITY_TRAVEL = 0.501        # the gain module, -60 ... +12 dB,
                                       # tapered, so unity is in the middle
OUR_LOW_GAIN_UNITY_TRAVEL = 0.501    # the spectral recovery low gain,
                                       # tapered, so unity is in the middle
OUR_MAKEUP_GAIN_UNITY_TRAVEL = 0.333  # the de-clip makeup gain, -12 ... +24 dB,
                                       # straight, because two thirds of its
                                       # range is above unity
