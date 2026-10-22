"""
aurserver.preset_library — the presets the product ships with.

A program list is only worth publishing when every entry of it names a
**situation**.  The reference products of the study name rooms and
situations ("Default", "Studio", "Concert Hall", "OutDoor", "Ghost",
"Silence"); a list that offers "Algorithm 1" and "Algorithm 2" is a list of
knob positions and not a list of programs.  Every preset in this file
therefore answers two questions:

* **what is the material?**  a dry recording, a noisy take, a close mic, a
  humming desk, a reverberant room, a clipped master, a legacy transfer.
* **what does the user want?**  a clean voice, a level match, a room that
  is gone.

The rules the library keeps
---------------------------

* **The names are canonical.**  A preset names a parameter by the name the
  canonical table gives it, never by a name the parameter had before it was
  renamed.  The parameter store resolves an old name when a preset is
  loaded, so an old preset still lands on the right parameter, but the
  plug-in matches a preset on the canonical names and drops one that
  carries an old name.  The writer therefore writes the canonical name.
* **The values are real.**  Every value is inside the declared range of its
  parameter, in the unit the parameter is declared in (decibels, hertz,
  milliseconds, per cent), and the preset does what its name says.
* **A preset is a whole situation.**  A preset sets every parameter of its
  module, apart from the few the product owns (the mode of the chain and
  the answer of the classifier).  A preset that sets one knob out of
  twenty is a nudge, not a situation.
* **One idea per name.**  A name carries no number, because a number in a
  name is a setting and not a situation, and it names no third party.

The module is data only.  It does not run the DSP and it does not read the
disk: :mod:`aurserver.presets` turns the definitions below into the
documents of the library and writes them out.
"""
from __future__ import annotations

#: The categories the library uses.  A preset sits in the category of the
#: module it belongs to, so the list of the library reads as the list of
#: the product.
REPAIR = "Repair"
UTILITY = "Utility"
MEASUREMENT = "Measurement"
SYSTEM = "System"

#: The parameters a preset does not set, per module.
#:
#: * ``Processing Mode`` is the mode of the chain: the engine writes it
#:   into every module of the chain before the chain runs, so a value in a
#:   preset would be overwritten the moment the module is used.
#: * the result parameters of the classifier are the answer of the module
#:   and not the question: a preset that names them would be writing down
#:   a measurement instead of a setting.
OWNED_PARAMETERS: dict[str, tuple[str, ...]] = {
    "AUR_CLIPCLASSIFIER": ("Detected Class", "Confidence", "Similarity"),
    "AUR_REPAIRASSISTANT": ("Processing Mode",),
    "AUR_SPECTRALDENOISE": ("Processing Mode",),
    "AUR_VOICEDENOISE": ("Processing Mode",),
}

#: The modules whose first program has to be the configuration the module
#: comes with.
#:
#: A host shows the program list in the order the names sort, and it loads
#: a program before it measures the delay of the plug-in.  A program that
#: changes the settings therefore leaves the module in a state the
#: measurement does not know.  A module whose output does not look like
#: its input closely enough for the measurement to survive that is then
#: reported as a plug-in that delays the audio it does not delay, so the
#: first program of such a module is the configuration the module comes
#: with.
#:
#: The De-Bleed is such a module: it subtracts the reference channel from
#: the target, so its output does not look like its input to begin with.
FIRST_PROGRAM_IS_THE_DEFAULT: tuple[str, ...] = ("AUR_DEBLEED",)


def _preset(
    name: str,
    category: str,
    module_code: str,
    display_name: str,
    params: dict,
    description: str,
) -> dict:
    """Build one preset definition.

    The element of the module is not written down here: the canonical
    table names it, and the table is the one place that knows.
    """
    return {
        "name": name,
        "category": category,
        "module_code": module_code,
        "display_name": display_name,
        "params": params,
        "description": description,
    }


#: The shipped library.  One entry per situation, in the order of the
#: canonical table.
BUILTIN_PRESETS: tuple[dict, ...] = (
    # -- De-Click -------------------------------------------------------------
    _preset(
        "Tape Transfer Tick",
        REPAIR,
        "AUR_DECLICK",
        "De-Click",
        {
            "Algorithm": "Single Band",
            "Sensitivity": 3.0,
            "Frequency Skew": 0.0,
            "Click Widening": 0.15,
            "Output Clicks Only": False,
            "Removed Mix": 0.0,
        },
        "Removes the ticks of a tape transfer without touching the hiss "
        "underneath them.",
    ),
    _preset(
        "Digital Dropout",
        REPAIR,
        "AUR_DECLICK",
        "De-Click",
        {
            "Algorithm": "Single Band",
            "Sensitivity": 6.0,
            "Frequency Skew": 0.0,
            "Click Widening": 0.25,
            "Output Clicks Only": False,
            "Removed Mix": 0.0,
        },
        "Repairs the dropouts of a digital file, where the event is loud "
        "and short.",
    ),
    _preset(
        "Periodic Buzz",
        REPAIR,
        "AUR_DECLICK",
        "De-Click",
        {
            "Algorithm": "MB Periodic",
            "Sensitivity": 4.0,
            "Frequency Skew": 0.2,
            "Click Widening": 0.1,
            "Output Clicks Only": False,
            "Removed Mix": 0.0,
        },
        "Removes a buzz that repeats, such as a faulty lamp or a mains "
        "distortion in a power supply.",
    ),
    _preset(
        "Live Low Latency Repair",
        REPAIR,
        "AUR_DECLICK",
        "De-Click",
        {
            "Algorithm": "Low Latency",
            "Sensitivity": 2.0,
            "Frequency Skew": 0.0,
            "Click Widening": 0.0,
            "Output Clicks Only": False,
            "Removed Mix": 0.0,
        },
        "Repairs clicks while the signal is played, with the shortest "
        "delay the module has.",
    ),
    # -- De-Crackle -----------------------------------------------------------
    _preset(
        "Vinyl Crackle",
        REPAIR,
        "AUR_DECRACKLE",
        "De-Crackle",
        {
            "Quality": "Medium",
            "Reduction": 0.6,
            "Amplitude Skew": 0.0,
            "Output Crackle Only": False,
        },
        "Removes the crackle of a record, where the events are small and "
        "everywhere.",
    ),
    _preset(
        "Quiet Crackle",
        REPAIR,
        "AUR_DECRACKLE",
        "De-Crackle",
        {
            "Quality": "High",
            "Reduction": 0.6,
            "Amplitude Skew": 0.4,
            "Output Crackle Only": False,
        },
        "Looks for the quiet ticks a worn record makes under the music.",
    ),
    _preset(
        "Loud Crackle",
        REPAIR,
        "AUR_DECRACKLE",
        "De-Crackle",
        {
            "Quality": "Medium",
            "Reduction": 0.5,
            "Amplitude Skew": -0.4,
            "Output Crackle Only": False,
        },
        "Looks for the loud pops of a scratched record.",
    ),
    # A program that shows the removed signal on its own is a render mode
    # and not a situation: a host that loads it while it measures the
    # plug-in leaves the module in a state the measurement does not know.
    # It is therefore not shipped, and the switch that does it stays where
    # it belongs, on the parameter of the module.
    # -- De-Clip --------------------------------------------------------------
    _preset(
        "Gentle Peak Recovery",
        REPAIR,
        "AUR_DECLIP",
        "De-Clip",
        {
            "Threshold": -0.05,
            "Link Threshold": True,
            "Quality": "Medium",
            "Makeup Gain": 2.5,
            "Post Limiter": True,
        },
        "Rebuilds the peaks that were cut by a limiter, and leaves the "
        "quiet passages alone.",
    ),
    _preset(
        "Hard Clipped Master",
        REPAIR,
        "AUR_DECLIP",
        "De-Clip",
        {
            "Threshold": -0.5,
            "Link Threshold": True,
            "Quality": "High",
            "Makeup Gain": 4.0,
            "Post Limiter": True,
        },
        "Repairs a recording that was cut hard, and puts the level back "
        "where it was.",
    ),
    _preset(
        "Broadcast Safe Restore",
        REPAIR,
        "AUR_DECLIP",
        "De-Clip",
        {
            "Threshold": -0.1,
            "Link Threshold": False,
            "Quality": "Low",
            "Makeup Gain": 2.5,
            "Post Limiter": True,
        },
        "Repairs clipped peaks for a delivery, with a limiter that catches "
        "what the rebuild overshoots.",
    ),
    # -- De-Hum ---------------------------------------------------------------
    _preset(
        "Mains Hum",
        REPAIR,
        "AUR_DEHUM",
        "De-Hum",
        {
            "Mode": "Static",
            "Base Frequency": 50.0,
            "Filter Q": 1.0,
            "Number of Harmonics": 8,
            "Link Harmonics": "All",
            "Harmonic Slope": 0.0,
            "Enable Learning": False,
            "Adaptive": True,
            "Enable Highpass": False,
            "Highpass Frequency": 20.0,
            "Enable Lowpass": False,
            "Lowpass Frequency": 20000.0,
            "Linear Phase Filters": False,
            "Enable DC Offset": False,
            "Dynamic Sensitivity": 0.5,
            "Dynamic Bands": 3,
            "Dynamic Low Cutoff": 20.0,
            "Dynamic High Cutoff": 20000.0,
            "Output Hum Only": False,
            "Removed Mix": 0.0,
        },
        "Removes the hum of a fifty hertz mains and the harmonics above "
        "it.",
    ),
    _preset(
        "Lighting Hum",
        REPAIR,
        "AUR_DEHUM",
        "De-Hum",
        {
            "Mode": "Static",
            "Base Frequency": 60.0,
            "Filter Q": 1.0,
            "Number of Harmonics": 8,
            "Link Harmonics": "All",
            "Harmonic Slope": 0.0,
            "Enable Learning": False,
            "Adaptive": True,
            "Enable Highpass": False,
            "Highpass Frequency": 20.0,
            "Enable Lowpass": False,
            "Lowpass Frequency": 20000.0,
            "Linear Phase Filters": False,
            "Enable DC Offset": False,
            "Dynamic Sensitivity": 0.5,
            "Dynamic Bands": 3,
            "Dynamic Low Cutoff": 20.0,
            "Dynamic High Cutoff": 20000.0,
            "Output Hum Only": False,
            "Removed Mix": 0.0,
        },
        "Removes the hum of a sixty hertz mains, the kind a lighting desk "
        "puts on a recording.",
    ),
    _preset(
        "Drifting Mains Hum",
        REPAIR,
        "AUR_DEHUM",
        "De-Hum",
        {
            "Mode": "Dynamic",
            "Base Frequency": 50.0,
            "Filter Q": 1.0,
            "Number of Harmonics": 8,
            "Link Harmonics": "All",
            "Harmonic Slope": 0.0,
            "Enable Learning": False,
            "Adaptive": True,
            "Enable Highpass": False,
            "Highpass Frequency": 20.0,
            "Enable Lowpass": False,
            "Lowpass Frequency": 20000.0,
            "Linear Phase Filters": False,
            "Enable DC Offset": False,
            "Dynamic Sensitivity": 0.6,
            "Dynamic Bands": 4,
            "Dynamic Low Cutoff": 40.0,
            "Dynamic High Cutoff": 500.0,
            "Output Hum Only": False,
            "Removed Mix": 0.0,
        },
        "Follows a hum that wanders, the kind a generator on another "
        "circuit makes.",
    ),
    _preset(
        "Rumble And Motor Hum",
        REPAIR,
        "AUR_DEHUM",
        "De-Hum",
        {
            "Mode": "Static",
            "Base Frequency": 50.0,
            "Filter Q": 3.0,
            "Number of Harmonics": 2,
            "Link Harmonics": "None",
            "Harmonic Slope": 0.0,
            "Enable Learning": False,
            "Adaptive": True,
            "Enable Highpass": True,
            "Highpass Frequency": 80.0,
            "Enable Lowpass": False,
            "Lowpass Frequency": 20000.0,
            "Linear Phase Filters": True,
            "Enable DC Offset": True,
            "Dynamic Sensitivity": 0.5,
            "Dynamic Bands": 3,
            "Dynamic Low Cutoff": 20.0,
            "Dynamic High Cutoff": 20000.0,
            "Output Hum Only": False,
            "Removed Mix": 0.0,
        },
        "Cleans a rumble-prone take: the motor hum is notched, the low "
        "end is cleared and the offset is taken out.",
    ),
    # -- De-Ess ---------------------------------------------------------------
    _preset(
        "Close Mic Sibilance",
        REPAIR,
        "AUR_DEESS",
        "De-Ess",
        {
            "Mode": "Classic",
            "Threshold": -30.0,
            "Absolute Mode": False,
            "Cutoff Frequency": 6000.0,
            "Spectral Flattening": 0.5,
            "Spectral Tilt": 0.0,
            "Speed": "Fast",
            "Output Ess Only": False,
        },
        "Tames the sibilance of a voice that was recorded close, with a "
        "fast reduction that does not follow the consonants.",
    ),
    _preset(
        "Gentle Sibilance Control",
        REPAIR,
        "AUR_DEESS",
        "De-Ess",
        {
            "Mode": "Spectral",
            "Threshold": -24.0,
            "Absolute Mode": False,
            "Cutoff Frequency": 5500.0,
            "Spectral Flattening": 0.3,
            "Spectral Tilt": 0.0,
            "Speed": "Slow",
            "Output Ess Only": False,
        },
        "Eases the sibilance of a voice, slowly, so the listener never "
        "hears the reduction.",
    ),
    _preset(
        "Hard Sibilance",
        REPAIR,
        "AUR_DEESS",
        "De-Ess",
        {
            "Mode": "Classic",
            "Threshold": -36.0,
            "Absolute Mode": True,
            "Cutoff Frequency": 7000.0,
            "Spectral Flattening": 0.6,
            "Spectral Tilt": 0.2,
            "Speed": "Fast",
            "Output Ess Only": False,
        },
        "Pulls down the hard sibilance of a shout, and weighs the "
        "reduction towards the top of the band.",
    ),
    # -- De-Plosive -----------------------------------------------------------
    _preset(
        "Close Mic Plosives",
        REPAIR,
        "AUR_DEPLOSIVE",
        "De-Plosive",
        {
            "Sensitivity": 3.0,
            "Reduction": 2.5,
            "Frequency Limit": 300.0,
        },
        "Softens the plosive consonants of a voice recorded close, where "
        "the breath of the speaker hits the microphone.",
    ),
    _preset(
        "Gentle Plosive Control",
        REPAIR,
        "AUR_DEPLOSIVE",
        "De-Plosive",
        {
            "Sensitivity": 3.0,
            "Reduction": 2.0,
            "Frequency Limit": 250.0,
        },
        "Eases the plosives of a voice that is already close to balanced.",
    ),
    _preset(
        "Hard Plosive Control",
        REPAIR,
        "AUR_DEPLOSIVE",
        "De-Plosive",
        {
            "Sensitivity": 4.0,
            "Reduction": 3.0,
            "Frequency Limit": 350.0,
        },
        "Pulls down the plosives of a voice that was recorded with the "
        "microphone in front of the mouth.",
    ),
    # -- De-Reverb ------------------------------------------------------------
    _preset(
        "Small Dry Room",
        REPAIR,
        "AUR_DEREVERB",
        "De-Reverb",
        {
            "Reduction": 0.4,
            "Tail Length": 0.4,
            "Artifact Smoothing": 0.5,
            "Band Reduction Low": 1.0,
            "Band Reduction Low-Mid": 1.0,
            "Band Reduction High-Mid": 1.0,
            "Band Reduction High": 1.0,
            "Enhance Dry Signal": False,
            "Output Reverb Only": False,
            "Removed Mix": 0.0,
        },
        "Takes the short reflection of a small, dry room out of a take.",
    ),
    _preset(
        "Live Room",
        REPAIR,
        "AUR_DEREVERB",
        "De-Reverb",
        {
            "Reduction": 0.7,
            "Tail Length": 0.9,
            "Artifact Smoothing": 0.5,
            "Band Reduction Low": 1.0,
            "Band Reduction Low-Mid": 1.0,
            "Band Reduction High-Mid": 1.0,
            "Band Reduction High": 1.0,
            "Enhance Dry Signal": True,
            "Output Reverb Only": False,
            "Removed Mix": 0.0,
        },
        "Takes the room of a live recording out of the take, and gives the "
        "dry voice back the energy the room took from it.",
    ),
    _preset(
        "Large Hall",
        REPAIR,
        "AUR_DEREVERB",
        "De-Reverb",
        {
            "Reduction": 0.85,
            "Tail Length": 2.5,
            "Artifact Smoothing": 0.6,
            "Band Reduction Low": 1.0,
            "Band Reduction Low-Mid": 1.0,
            "Band Reduction High-Mid": 1.0,
            "Band Reduction High": 1.0,
            "Enhance Dry Signal": False,
            "Output Reverb Only": False,
            "Removed Mix": 0.0,
        },
        "Takes the long tail of a hall out of a recording.",
    ),
    _preset(
        "Boomy Room",
        REPAIR,
        "AUR_DEREVERB",
        "De-Reverb",
        {
            "Reduction": 0.6,
            "Tail Length": 0.6,
            "Artifact Smoothing": 0.5,
            "Band Reduction Low": 1.0,
            "Band Reduction Low-Mid": 0.9,
            "Band Reduction High-Mid": 0.5,
            "Band Reduction High": 0.4,
            "Enhance Dry Signal": False,
            "Output Reverb Only": False,
            "Removed Mix": 0.0,
        },
        "Takes the boom of a small room out of a take and leaves the air "
        "around the voice alone.",
    ),
    # -- De-Rustle ------------------------------------------------------------
    _preset(
        "Clothing Rustle",
        REPAIR,
        "AUR_DERUSTLE",
        "De-Rustle",
        {
            "Reduction": 0.6,
            "Ambience Preservation": 0.4,
            "Separation Algorithm": "Joint",
        },
        "Removes the rustle of a moving presenter, with the two channels "
        "read together.",
    ),
    _preset(
        "Gentle Rustle Removal",
        REPAIR,
        "AUR_DERUSTLE",
        "De-Rustle",
        {
            "Reduction": 0.35,
            "Ambience Preservation": 0.7,
            "Separation Algorithm": "Channel Independent",
        },
        "Eases the rustle of a take and keeps the room around the voice.",
    ),
    _preset(
        "Heavy Handling Noise",
        REPAIR,
        "AUR_DERUSTLE",
        "De-Rustle",
        {
            "Reduction": 0.8,
            "Ambience Preservation": 0.2,
            "Separation Algorithm": "Advanced Joint",
        },
        "Removes the noise of a jacket and a chair, the kind a take with "
        "a lot of handling carries.",
    ),
    # -- De-Wind --------------------------------------------------------------
    _preset(
        "OutDoor Take",
        REPAIR,
        "AUR_DEWIND",
        "De-Wind",
        {
            "Reduction": 0.7,
            "Crossover Frequency": 500.0,
            "Fundamental Recovery": 0.5,
            "Artifact Smoothing": 0.5,
            "Removed Mix": 0.0,
        },
        "Removes the rumble of a take that was made in the wind, and puts "
        "the low end of the voice back.",
    ),
    _preset(
        "Gentle Wind Removal",
        REPAIR,
        "AUR_DEWIND",
        "De-Wind",
        {
            "Reduction": 0.4,
            "Crossover Frequency": 400.0,
            "Fundamental Recovery": 0.6,
            "Artifact Smoothing": 0.6,
            "Removed Mix": 0.0,
        },
        "Eases the wind of a take that was sheltered but not still.",
    ),
    _preset(
        "Strong Wind On A Mic",
        REPAIR,
        "AUR_DEWIND",
        "De-Wind",
        {
            "Reduction": 0.9,
            "Crossover Frequency": 700.0,
            "Fundamental Recovery": 0.4,
            "Artifact Smoothing": 0.5,
            "Removed Mix": 0.0,
        },
        "Removes the wind of a take that was made in a strong wind, where "
        "the rumble sits high.",
    ),
    # -- De-Bleed -------------------------------------------------------------
    # The first program of the module is the configuration the module comes
    # with.  A host shows the program list in the order the names sort and
    # it loads a program before it measures the delay of the plug-in; a
    # program that changes the settings leaves the module in a state the
    # measurement does not know, and the De-Bleed - whose output does not
    # look like its input closely enough for the measurement to survive
    # that - is then reported as a plug-in that delays the audio it does
    # not delay.
    _preset(
        "Constant Bleed In The Take",
        REPAIR,
        "AUR_DEBLEED",
        "De-Bleed",
        {
            "Reduction": 0.5,
            "Artifact Smoothing": 0.5,
        },
        "A leak that never stops, such as a headset or a talkback that is "
        "open all the time, taken out of the take at the rate the module "
        "works at.",
    ),
    _preset(
        "Gentle Bleed Removal",
        REPAIR,
        "AUR_DEBLEED",
        "De-Bleed",
        {
            "Reduction": 0.3,
            "Artifact Smoothing": 0.7,
        },
        "Eases the bleed of a second source out of the first track.",
    ),
    _preset(
        "Headphone Bleed Into The Boom Track",
        REPAIR,
        "AUR_DEBLEED",
        "De-Bleed",
        {
            "Reduction": 0.6,
            "Artifact Smoothing": 0.5,
        },
        "Takes the headphone of the presenter out of the boom track, with "
        "the headset on the second channel.",
    ),
    _preset(
        "Talkback In The Room Track",
        REPAIR,
        "AUR_DEBLEED",
        "De-Bleed",
        {
            "Reduction": 0.75,
            "Artifact Smoothing": 0.4,
        },
        "Takes the talkback of the floor out of the room track.",
    ),
    # -- Breath Control -------------------------------------------------------
    _preset(
        "Visible Breath Between Lines",
        REPAIR,
        "AUR_BREATH",
        "Breath Control",
        {
            "Mode": "Gain Mode",
            "Gain": -9.0,
            "Target Level": -30.0,
            "Sensitivity": 0.5,
            "Output Breaths Only": False,
            "Removed Mix": 0.0,
        },
        "Pulls the breaths down that a listener hears between the lines.",
    ),
    _preset(
        "Subtle Breath",
        REPAIR,
        "AUR_BREATH",
        "Breath Control",
        {
            "Mode": "Gain Mode",
            "Gain": -4.0,
            "Target Level": -30.0,
            "Sensitivity": 0.35,
            "Output Breaths Only": False,
            "Removed Mix": 0.0,
        },
        "Eases the breaths of a take that is already close to balanced.",
    ),
    _preset(
        "Aggressive Breath Removal",
        REPAIR,
        "AUR_BREATH",
        "Breath Control",
        {
            "Mode": "Gain Mode",
            "Gain": -14.0,
            "Target Level": -30.0,
            "Sensitivity": 0.7,
            "Output Breaths Only": False,
            "Removed Mix": 0.0,
        },
        "Pulls the breaths of a close take down hard, for a voice that has "
        "to sit over a bed.",
    ),
    _preset(
        "Breath Down To A Target",
        REPAIR,
        "AUR_BREATH",
        "Breath Control",
        {
            "Mode": "Target Mode",
            "Gain": -9.0,
            "Target Level": -40.0,
            "Sensitivity": 0.5,
            "Output Breaths Only": False,
            "Removed Mix": 0.0,
        },
        "Brings every breath down to the same level, so the quiet ones "
        "disappear and the loud ones are only eased.",
    ),
    # -- Mouth De-Click --------------------------------------------------------
    _preset(
        "Lip And Tooth Noise",
        REPAIR,
        "AUR_MOUTHDECLICK",
        "Mouth De-Click",
        {
            "Sensitivity": 3.0,
            "Frequency Skew": 0.0,
            "Click Widening": 0.1,
            "Output Clicks Only": False,
        },
        "Removes the small clicks a lip and a tooth make in a close take.",
    ),
    _preset(
        "Gentle Mouth Noise",
        REPAIR,
        "AUR_MOUTHDECLICK",
        "Mouth De-Click",
        {
            "Sensitivity": 2.0,
            "Frequency Skew": 0.0,
            "Click Widening": 0.0,
            "Output Clicks Only": False,
        },
        "Eases the mouth noise of a take that is already clean.",
    ),
    _preset(
        "Hard Mouth Noise",
        REPAIR,
        "AUR_MOUTHDECLICK",
        "Mouth De-Click",
        {
            "Sensitivity": 5.0,
            "Frequency Skew": 0.2,
            "Click Widening": 0.2,
            "Output Clicks Only": False,
        },
        "Removes the mouth noise of a take that was recorded very close.",
    ),
    # -- Voice De-Noise --------------------------------------------------------
    _preset(
        "Noisy Location Take",
        REPAIR,
        "AUR_VOICEDENOISE",
        "Voice De-Noise",
        {
            "Optimize For": "Dialogue",
            "Filter Type": "Surgical",
            "Adaptive Mode": True,
            "Threshold": -18.0,
            "Threshold 1": -40.0,
            "Threshold 2": -50.0,
            "Threshold 3": -60.0,
            "Threshold 4": -70.0,
            "Threshold 5": -80.0,
            "Threshold 6": -90.0,
            "Reduction": 15.0,
            "Learn": False,
            "Removed Mix": 0.0,
        },
        "Cleans a voice that was recorded on location, where the wind and "
        "the traffic sit under the words.",
    ),
    _preset(
        "Studio Voice Clean",
        REPAIR,
        "AUR_VOICEDENOISE",
        "Voice De-Noise",
        {
            "Optimize For": "Dialogue",
            "Filter Type": "Gentle",
            "Adaptive Mode": True,
            "Threshold": -10.0,
            "Threshold 1": -30.0,
            "Threshold 2": -40.0,
            "Threshold 3": -50.0,
            "Threshold 4": -60.0,
            "Threshold 5": -70.0,
            "Threshold 6": -80.0,
            "Reduction": 8.0,
            "Learn": False,
            "Removed Mix": 0.0,
        },
        "Keeps the texture of a voice that was recorded in a studio and "
        "only needs the room taken out of it.",
    ),
    _preset(
        "Broadcast Voice Cleanup",
        REPAIR,
        "AUR_VOICEDENOISE",
        "Voice De-Noise",
        {
            "Optimize For": "Dialogue",
            "Filter Type": "Surgical",
            "Adaptive Mode": True,
            "Threshold": -15.0,
            "Threshold 1": -40.0,
            "Threshold 2": -50.0,
            "Threshold 3": -60.0,
            "Threshold 4": -70.0,
            "Threshold 5": -80.0,
            "Threshold 6": -90.0,
            "Reduction": 18.0,
            "Learn": False,
            "Removed Mix": 0.0,
        },
        "The deepest clean-up a voice takes for a broadcast, with the "
        "noise floor pulled down as far as the module allows.",
    ),
    _preset(
        "Music With A Noisy Floor",
        REPAIR,
        "AUR_VOICEDENOISE",
        "Voice De-Noise",
        {
            "Optimize For": "Music",
            "Filter Type": "Gentle",
            "Adaptive Mode": True,
            "Threshold": -12.0,
            "Threshold 1": -35.0,
            "Threshold 2": -45.0,
            "Threshold 3": -55.0,
            "Threshold 4": -65.0,
            "Threshold 5": -75.0,
            "Threshold 6": -85.0,
            "Reduction": 10.0,
            "Learn": False,
            "Removed Mix": 0.0,
        },
        "Takes the hiss out of a recording of music without touching the "
        "spectrum of the instruments.",
    ),
    # -- Spectral De-Noise -----------------------------------------------------
    _preset(
        "Tape Hiss",
        REPAIR,
        "AUR_SPECTRALDENOISE",
        "Spectral De-Noise",
        {
            "Algorithm": "Advanced",
            "Quality": "C",
            "FFT Size": 20.0,
            "Multi Resolution": True,
            "Learning Time": 2.0,
            "Adaptive": True,
            "Noise Threshold": 0.0,
            "Tonal Threshold": 0.0,
            "Noise Reduction": 12.0,
            "Tonal Reduction": 12.0,
            "Artifact Control": 0.5,
            "Smoothing": 0.5,
            "Knee": 0.5,
            "Release": 100.0,
            "Noise Floor Synthesis": 0.5,
            "Masking": 0.5,
            "Enhancement": 0.5,
            "Whitening": 0.5,
            "Output Noise Only": False,
        },
        "Takes the hiss of a tape transfer down, and leaves the programme "
        "alone.",
    ),
    _preset(
        "Steady Room Noise",
        REPAIR,
        "AUR_SPECTRALDENOISE",
        "Spectral De-Noise",
        {
            "Algorithm": "Advanced",
            "Quality": "B",
            "FFT Size": 10.0,
            "Multi Resolution": True,
            "Learning Time": 1.0,
            "Adaptive": True,
            "Noise Threshold": 0.0,
            "Tonal Threshold": 0.0,
            "Noise Reduction": 9.0,
            "Tonal Reduction": 9.0,
            "Artifact Control": 0.5,
            "Smoothing": 0.5,
            "Knee": 0.5,
            "Release": 80.0,
            "Noise Floor Synthesis": 0.4,
            "Masking": 0.5,
            "Enhancement": 0.4,
            "Whitening": 0.4,
            "Output Noise Only": False,
        },
        "Takes the noise of a room down while the take is running, for a "
        "room that is not silent enough to learn from.",
    ),
    _preset(
        "Digital Noise Floor",
        REPAIR,
        "AUR_SPECTRALDENOISE",
        "Spectral De-Noise",
        {
            "Algorithm": "Simple",
            "Quality": "D",
            "FFT Size": 30.0,
            "Multi Resolution": False,
            "Learning Time": 3.0,
            "Adaptive": True,
            "Noise Threshold": 3.0,
            "Tonal Threshold": 3.0,
            "Noise Reduction": 18.0,
            "Tonal Reduction": 10.0,
            "Artifact Control": 0.6,
            "Smoothing": 0.4,
            "Knee": 0.6,
            "Release": 150.0,
            "Noise Floor Synthesis": 0.6,
            "Masking": 0.6,
            "Enhancement": 0.3,
            "Whitening": 0.3,
            "Output Noise Only": False,
        },
        "Takes the floor of a digital file down, where the noise is even "
        "and carries no musical information.",
    ),
    _preset(
        "Aggressive Cleanup",
        REPAIR,
        "AUR_SPECTRALDENOISE",
        "Spectral De-Noise",
        {
            "Algorithm": "Extreme",
            "Quality": "D",
            "FFT Size": 40.0,
            "Multi Resolution": True,
            "Learning Time": 4.0,
            "Adaptive": True,
            "Noise Threshold": -3.0,
            "Tonal Threshold": -3.0,
            "Noise Reduction": 24.0,
            "Tonal Reduction": 18.0,
            "Artifact Control": 0.7,
            "Smoothing": 0.4,
            "Knee": 0.4,
            "Release": 200.0,
            "Noise Floor Synthesis": 0.7,
            "Masking": 0.4,
            "Enhancement": 0.2,
            "Whitening": 0.2,
            "Output Noise Only": False,
        },
        "The deepest clean-up the module has, for a file that has to be "
        "usable and is badly damaged.",
    ),
    # -- Spectral Repair -------------------------------------------------------
    _preset(
        "Attenuate A Noise Band",
        REPAIR,
        "AUR_SPECTRALREPAIR",
        "Spectral Repair",
        {
            "Mode": "Attenuate",
            "Reduction": 0.8,
            "Region Length": 0.25,
            "Direction": "2D",
            "Multi Resolution": True,
            "Before Weighting": 0.5,
            "After Weighting": 0.5,
        },
        "Pulls a band of noise down inside a region that has been marked.",
    ),
    _preset(
        "Replace A Dropout",
        REPAIR,
        "AUR_SPECTRALREPAIR",
        "Spectral Repair",
        {
            "Mode": "Replace",
            "Reduction": 0.7,
            "Region Length": 0.1,
            "Direction": "2D",
            "Multi Resolution": True,
            "Before Weighting": 0.5,
            "After Weighting": 0.5,
        },
        "Fills a dropout by rebuilding what the material around it says "
        "was there.",
    ),
    _preset(
        "Cut A Region Out",
        REPAIR,
        "AUR_SPECTRALREPAIR",
        "Spectral Repair",
        {
            "Mode": "Attenuate",
            "Reduction": 0.9,
            "Region Length": 0.05,
            "Direction": "Horizontal",
            "Multi Resolution": False,
            "Before Weighting": 0.0,
            "After Weighting": 0.0,
        },
        "Cuts a short region out with a hard edge at both ends, for a "
        "fault that has to go without a fade.",
    ),
    _preset(
        "Rebuild A Missing Partial",
        REPAIR,
        "AUR_SPECTRALREPAIR",
        "Spectral Repair",
        {
            "Mode": "Partials",
            "Reduction": 0.6,
            "Region Length": 0.2,
            "Direction": "Vertical",
            "Multi Resolution": True,
            "Before Weighting": 0.5,
            "After Weighting": 0.5,
        },
        "Rebuilds a partial that was lost, using the partials around it.",
    ),
    # -- Spectral Recovery -----------------------------------------------------
    _preset(
        "Restore A Lost Low Band",
        REPAIR,
        "AUR_SPECTRALRECOVERY",
        "Spectral Recovery",
        {
            "Spectral Patching": True,
            "Low Gain": -6.0,
            "Low Cutoff": 200.0,
            "High Cutoff": 4000.0,
            "High Gain": 0.0,
        },
        "Regenerates the band below a recording that was cut off at the "
        "bottom.",
    ),
    _preset(
        "Gentle Low End Restore",
        REPAIR,
        "AUR_SPECTRALRECOVERY",
        "Spectral Recovery",
        {
            "Spectral Patching": True,
            "Low Gain": -3.0,
            "Low Cutoff": 150.0,
            "High Cutoff": 6000.0,
            "High Gain": 0.0,
        },
        "Eases a recording whose low end is thin, without a hard edge at "
        "the cut.",
    ),
    _preset(
        "Restore A Narrowed Top",
        REPAIR,
        "AUR_SPECTRALRECOVERY",
        "Spectral Recovery",
        {
            "Spectral Patching": True,
            "Low Gain": 0.0,
            "Low Cutoff": 200.0,
            "High Cutoff": 6000.0,
            "High Gain": 2.0,
        },
        "Regenerates the top of a recording that was narrowed on the way "
        "in.",
    ),
    _preset(
        "Leave The Spectrum Alone",
        REPAIR,
        "AUR_SPECTRALRECOVERY",
        "Spectral Recovery",
        {
            "Spectral Patching": False,
            "Low Gain": 0.0,
            "Low Cutoff": 200.0,
            "High Cutoff": 4000.0,
            "High Gain": 0.0,
        },
        "Keeps the spectrum of the recording as it is, and regenerates "
        "nothing.",
    ),
    # -- Guitar De-Noise -------------------------------------------------------
    _preset(
        "Amp Buzz",
        REPAIR,
        "AUR_GUITARDENOISE",
        "Guitar De-Noise",
        {
            "Amp Sensitivity": 0.7,
            "Amp Resolution": 256,
            "Squeak Sensitivity": 0.5,
            "Squeak Reduction": 0.5,
            "Squeak Duration": "Short",
            "Pick Sensitivity": 0.5,
            "Pick Reduction": 0.5,
            "Pick Attack": 0.5,
        },
        "Notches the buzz and the rumble an amplifier makes when it is "
        "left on the floor.",
    ),
    _preset(
        "String Squeak",
        REPAIR,
        "AUR_GUITARDENOISE",
        "Guitar De-Noise",
        {
            "Amp Sensitivity": 0.5,
            "Amp Resolution": 128,
            "Squeak Sensitivity": 0.7,
            "Squeak Reduction": 0.6,
            "Squeak Duration": "Long",
            "Pick Sensitivity": 0.5,
            "Pick Reduction": 0.5,
            "Pick Attack": 0.5,
        },
        "Removes the squeak a string makes when it is bent and let go.",
    ),
    _preset(
        "Pick Attack Noise",
        REPAIR,
        "AUR_GUITARDENOISE",
        "Guitar De-Noise",
        {
            "Amp Sensitivity": 0.5,
            "Amp Resolution": 128,
            "Squeak Sensitivity": 0.5,
            "Squeak Reduction": 0.5,
            "Squeak Duration": "Short",
            "Pick Sensitivity": 0.7,
            "Pick Reduction": 0.4,
            "Pick Attack": 0.6,
        },
        "Softens the noise of a pick that hits a string hard.",
    ),
    _preset(
        "Clean Amp Take",
        REPAIR,
        "AUR_GUITARDENOISE",
        "Guitar De-Noise",
        {
            "Amp Sensitivity": 0.4,
            "Amp Resolution": 128,
            "Squeak Sensitivity": 0.3,
            "Squeak Reduction": 0.3,
            "Squeak Duration": "Short",
            "Pick Sensitivity": 0.3,
            "Pick Reduction": 0.3,
            "Pick Attack": 0.4,
        },
        "Leaves a clean take alone, and only catches what is clearly a "
        "fault.",
    ),
    # -- Deconstruct -----------------------------------------------------------
    _preset(
        "Pull Out The Tonal Part",
        REPAIR,
        "AUR_DECONSTRUCT",
        "Deconstruct",
        {
            "Tonal Gain": 2.4,
            "Noisy Gain": -12.0,
            "Separate Transients": True,
            "Transient Gain": 0.0,
            "Balance": "Tonal",
            "Artifact Smoothing": 0.5,
        },
        "Keeps the harmonic part of a recording and takes the noise floor "
        "away from it.",
    ),
    _preset(
        "Pull Out The Noise Floor",
        REPAIR,
        "AUR_DECONSTRUCT",
        "Deconstruct",
        {
            "Tonal Gain": -6.0,
            "Noisy Gain": 2.4,
            "Separate Transients": True,
            "Transient Gain": 0.0,
            "Balance": "Noisy",
            "Artifact Smoothing": 0.5,
        },
        "Keeps the steady broadband part of a recording and takes the "
        "harmonics away from it.",
    ),
    _preset(
        "Keep The Drums",
        REPAIR,
        "AUR_DECONSTRUCT",
        "Deconstruct",
        {
            "Tonal Gain": 0.0,
            "Noisy Gain": -6.0,
            "Separate Transients": True,
            "Transient Gain": 2.4,
            "Balance": "Noisy",
            "Artifact Smoothing": 0.6,
        },
        "Pulls the percussive part of a mix forward, for a drum sound that "
        "is lost in the other instruments.",
    ),
    _preset(
        "Split Without Chatter",
        REPAIR,
        "AUR_DECONSTRUCT",
        "Deconstruct",
        {
            "Tonal Gain": 0.0,
            "Noisy Gain": 0.0,
            "Separate Transients": True,
            "Transient Gain": 0.0,
            "Balance": "Noisy",
            "Artifact Smoothing": 0.8,
        },
        "Splits a recording into its parts without the masks chattering, "
        "for a split that is going to be used as material.",
    ),
    # -- Dialogue Isolate ------------------------------------------------------
    _preset(
        "Voice Forward",
        REPAIR,
        "AUR_DIALOGUEISOLATE",
        "Dialogue Isolate",
        {
            "Voice Gain": 3.0,
            "Reverb Gain": -12.0,
            "Noise Gain": -12.0,
            "Sensitivity": 0.5,
            "Quality": "Good",
        },
        "Brings the voice of a recording forward and pushes the room and "
        "the noise back.",
    ),
    _preset(
        "Voice Only",
        REPAIR,
        "AUR_DIALOGUEISOLATE",
        "Dialogue Isolate",
        {
            "Voice Gain": 4.8,
            "Reverb Gain": -24.0,
            "Noise Gain": -24.0,
            "Sensitivity": 0.6,
            "Quality": "Best",
        },
        "Leaves nothing but the voice of a recording, for a stem that has "
        "to be clean.",
    ),
    _preset(
        "Keep The Room",
        REPAIR,
        "AUR_DIALOGUEISOLATE",
        "Dialogue Isolate",
        {
            "Voice Gain": 0.0,
            "Reverb Gain": 3.0,
            "Noise Gain": -6.0,
            "Sensitivity": 0.5,
            "Quality": "Good",
        },
        "Brings the room of a recording forward and pushes the voice and "
        "the noise back.",
    ),
    # -- Music Rebalance -------------------------------------------------------
    _preset(
        "Vocal Forward",
        REPAIR,
        "AUR_MUSICREBALANCE",
        "Music Rebalance",
        {
            "Vocal Gain": 3.0,
            "Bass Gain": 0.0,
            "Drums Gain": 0.0,
            "Other Gain": 0.0,
            "Vocal Mute": False,
            "Bass Mute": False,
            "Drums Mute": False,
            "Other Mute": False,
            "Quality": "Good",
        },
        "Brings the voice of a mix forward, for a mix where the words are "
        "hard to follow.",
    ),
    _preset(
        "Drums Forward",
        REPAIR,
        "AUR_MUSICREBALANCE",
        "Music Rebalance",
        {
            "Vocal Gain": 0.0,
            "Bass Gain": 0.0,
            "Drums Gain": 3.0,
            "Other Gain": 0.0,
            "Vocal Mute": False,
            "Bass Mute": False,
            "Drums Mute": False,
            "Other Mute": False,
            "Quality": "Good",
        },
        "Brings the drums of a mix forward, for a mix where the beat is "
        "lost.",
    ),
    _preset(
        "Vocal Muted",
        REPAIR,
        "AUR_MUSICREBALANCE",
        "Music Rebalance",
        {
            "Vocal Gain": 0.0,
            "Bass Gain": 0.0,
            "Drums Gain": 0.0,
            "Other Gain": 0.0,
            "Vocal Mute": True,
            "Bass Mute": False,
            "Drums Mute": False,
            "Other Mute": False,
            "Quality": "Good",
        },
        "Takes the voice out of a mix, for a backing track that has to be "
        "played over.",
    ),
    _preset(
        "Bass Light",
        REPAIR,
        "AUR_MUSICREBALANCE",
        "Music Rebalance",
        {
            "Vocal Gain": 0.0,
            "Bass Gain": -3.0,
            "Drums Gain": 0.0,
            "Other Gain": 0.0,
            "Vocal Mute": False,
            "Bass Mute": False,
            "Drums Mute": False,
            "Other Mute": False,
            "Quality": "Better",
        },
        "Eases the bass of a mix that is too heavy on small speakers.",
    ),
    # -- Ambience Match --------------------------------------------------------
    _preset(
        "Living Room Tone",
        REPAIR,
        "AUR_AMBIENCEMATCH",
        "Ambience Match",
        {
            "Ambience Type": "Complex",
            "Ambience Threshold": -60.0,
            "Movement": 0.5,
            "Randomness": 0.5,
            "Gain": 0.0,
            "Output Ambience Only": False,
        },
        "Puts a living room tone under a recording that was made in a "
        "dead room.",
    ),
    _preset(
        "Static Room Bed",
        REPAIR,
        "AUR_AMBIENCEMATCH",
        "Ambience Match",
        {
            "Ambience Type": "Static",
            "Ambience Threshold": -60.0,
            "Movement": 0.2,
            "Randomness": 0.2,
            "Gain": 0.0,
            "Output Ambience Only": False,
        },
        "Puts a steady bed under a recording, for a room that is a booth "
        "and not a place.",
    ),
    _preset(
        "Open Air Ambience",
        REPAIR,
        "AUR_AMBIENCEMATCH",
        "Ambience Match",
        {
            "Ambience Type": "Complex",
            "Ambience Threshold": -50.0,
            "Movement": 0.7,
            "Randomness": 0.6,
            "Gain": 2.0,
            "Output Ambience Only": False,
        },
        "Puts the air of an open place under a recording, for a scene that "
        "was meant to be outdoors.",
    ),
    # The program that shows the matched room on its own is a render mode
    # and not a situation, for the same reason as the one of the De-Crackle.
    # -- Repair Assistant ------------------------------------------------------
    _preset(
        "Rough Location Take",
        REPAIR,
        "AUR_REPAIRASSISTANT",
        "Repair Assistant",
        {
            "Mode": "Voice",
            "Noise Reduction": 0.5,
            "Reverb Reduction": 0.4,
            "Hum Reduction": 0.5,
            "Click Reduction": 0.4,
            "De-Ess Reduction": 0.2,
            "De-Clip Reduction": 0.0,
        },
        "One pass over a take that was recorded on location, where the "
        "wind, the hum and the room are all in the way.",
    ),
    _preset(
        "Hum And Hiss",
        REPAIR,
        "AUR_REPAIRASSISTANT",
        "Repair Assistant",
        {
            "Mode": "Music",
            "Noise Reduction": 0.6,
            "Reverb Reduction": 0.0,
            "Hum Reduction": 0.8,
            "Click Reduction": 0.0,
            "De-Ess Reduction": 0.0,
            "De-Clip Reduction": 0.0,
        },
        "Cleans a recording that carries a hum and a hiss, and nothing "
        "else is wrong with it.",
    ),
    _preset(
        "Clipped And Noisy Master",
        REPAIR,
        "AUR_REPAIRASSISTANT",
        "Repair Assistant",
        {
            "Mode": "Music",
            "Noise Reduction": 0.5,
            "Reverb Reduction": 0.0,
            "Hum Reduction": 0.0,
            "Click Reduction": 0.0,
            "De-Ess Reduction": 0.0,
            "De-Clip Reduction": 0.6,
        },
        "Repairs a master that was cut and is noisy at the same time.",
    ),
    _preset(
        "Sibilant Close Mic",
        REPAIR,
        "AUR_REPAIRASSISTANT",
        "Repair Assistant",
        {
            "Mode": "Voice",
            "Noise Reduction": 0.0,
            "Reverb Reduction": 0.0,
            "Hum Reduction": 0.0,
            "Click Reduction": 0.0,
            "De-Ess Reduction": 0.6,
            "De-Clip Reduction": 0.0,
        },
        "Tames the sibilance of a voice that was recorded close, and "
        "leaves the rest of the take alone.",
    ),
    # -- Center Extract --------------------------------------------------------
    _preset(
        "Keep The Centre",
        REPAIR,
        "AUR_CENTEREXTRACT",
        "Center Extract",
        {
            "Algorithm": "True Phase",
            "Reduction": 1.0,
            "Artifact Smoothing": 0.5,
            "Dry Mix": 0.0,
        },
        "Keeps only what sits in the middle of the image, for a stem that "
        "has to be the centre and nothing else.",
    ),
    _preset(
        "Gentle Centre Keep",
        REPAIR,
        "AUR_CENTEREXTRACT",
        "Center Extract",
        {
            "Algorithm": "True Phase",
            "Reduction": 0.6,
            "Artifact Smoothing": 0.6,
            "Dry Mix": 20.0,
        },
        "Eases the sides of an image away and leaves a little of the "
        "original in, so the result does not sound thin.",
    ),
    _preset(
        "Pull The Centre Out",
        REPAIR,
        "AUR_CENTEREXTRACT",
        "Center Extract",
        {
            "Algorithm": "Pseudo Pan",
            "Reduction": 0.8,
            "Artifact Smoothing": 0.5,
            "Dry Mix": 0.0,
        },
        "Pulls the centre out of a wide recording, with the pan position of "
        "the two channels as the measure.",
    ),
    # -- Azimuth ---------------------------------------------------------------
    _preset(
        "Right Channel Quiet",
        REPAIR,
        "AUR_AZIMUTH",
        "Azimuth",
        {
            "Gain": -3.0,
            "Delay": 0.0,
            "Adaptive Matching": False,
        },
        "Lifts the right channel of a recording that sits low on the "
        "right.",
    ),
    _preset(
        "Right Channel Late",
        REPAIR,
        "AUR_AZIMUTH",
        "Azimuth",
        {
            "Gain": 0.0,
            "Delay": 2.0,
            "Adaptive Matching": False,
        },
        "Pulls the right channel of a recording back in time, for a pair "
        "of microphones that were not quite together.",
    ),
    _preset(
        "Measured Alignment",
        REPAIR,
        "AUR_AZIMUTH",
        "Azimuth",
        {
            "Gain": 0.0,
            "Delay": 0.0,
            "Adaptive Matching": True,
        },
        "Measures the level and the time offset of the two channels and "
        "corrects them.",
    ),
    # -- Wow & Flutter ---------------------------------------------------------
    _preset(
        "Tape Wow",
        REPAIR,
        "AUR_WOWFLUTTER",
        "Wow & Flutter",
        {
            "Wow": 1.5,
            "Flutter": 20.0,
            "Sensitivity": 0.6,
            "Center Global Pitch": 440.0,
        },
        "Straightens the slow drift of a tape that was played on a worn "
        "machine.",
    ),
    _preset(
        "Turntable Flutter",
        REPAIR,
        "AUR_WOWFLUTTER",
        "Wow & Flutter",
        {
            "Wow": 0.8,
            "Flutter": 30.0,
            "Sensitivity": 0.7,
            "Center Global Pitch": 440.0,
        },
        "Straightens the fast flutter of a record that was played on a "
        "turntable with a tired bearing.",
    ),
    _preset(
        "Gentle Drift Correction",
        REPAIR,
        "AUR_WOWFLUTTER",
        "Wow & Flutter",
        {
            "Wow": 0.5,
            "Flutter": 12.0,
            "Sensitivity": 0.3,
            "Center Global Pitch": 442.0,
        },
        "Eases the drift of a recording without flattening its character.",
    ),
    # -- Spectral Editor --------------------------------------------------------
    _preset(
        "Attenuate A Region",
        REPAIR,
        "AUR_SPECTRALEDITOR",
        "Spectral Editor",
        {
            "Mode": "Attenuate",
            "Reduction": 0.8,
            "Region Length": 0.25,
            "Direction": "2D",
            "Multi Resolution": True,
            "Before Weighting": 0.5,
            "After Weighting": 0.5,
        },
        "Pulls a region of a spectrogram down, for a fault that covers a "
        "patch of the picture.",
    ),
    _preset(
        "Replace A Region",
        REPAIR,
        "AUR_SPECTRALEDITOR",
        "Spectral Editor",
        {
            "Mode": "Replace",
            "Reduction": 0.7,
            "Region Length": 0.1,
            "Direction": "2D",
            "Multi Resolution": True,
            "Before Weighting": 0.5,
            "After Weighting": 0.5,
        },
        "Rebuilds a region of a spectrogram from the material around it.",
    ),
    _preset(
        "Hard Edge Repair",
        REPAIR,
        "AUR_SPECTRALEDITOR",
        "Spectral Editor",
        {
            "Mode": "Attenuate",
            "Reduction": 0.9,
            "Region Length": 0.05,
            "Direction": "Horizontal",
            "Multi Resolution": False,
            "Before Weighting": 0.0,
            "After Weighting": 0.0,
        },
        "Cuts a region out with a hard edge at both ends, for a fault that "
        "has to go without a fade.",
    ),
    _preset(
        "Fill A Missing Partial",
        REPAIR,
        "AUR_SPECTRALEDITOR",
        "Spectral Editor",
        {
            "Mode": "Partials",
            "Reduction": 0.6,
            "Region Length": 0.2,
            "Direction": "Vertical",
            "Multi Resolution": True,
            "Before Weighting": 0.5,
            "After Weighting": 0.5,
        },
        "Rebuilds a partial that was lost, using the partials around it.",
    ),
    # -- Band Restoration ------------------------------------------------------
    _preset(
        "Restore A Worn High Band",
        REPAIR,
        "AUR_BANDRESTORATION",
        "Band Restoration",
        {
            "Bands": 4,
            "Low Crossover": 200.0,
            "Mid Crossover": 2000.0,
            "High Crossover": 10000.0,
            "Phase Linearity": "Efficient",
            "Restoration Depth": 20.0,
            "Noise Threshold": 6.0,
            "High Frequency Transparency": 60.0,
            "Broadband Mix": 20.0,
            "Band 1 Mix": 0.0,
            "Band 1 Solo": False,
            "Band 1 Mute": False,
            "Band 1 Bypass": False,
            "Band 2 Mix": 0.0,
            "Band 2 Solo": False,
            "Band 2 Mute": False,
            "Band 2 Bypass": False,
            "Band 3 Mix": 0.0,
            "Band 3 Solo": False,
            "Band 3 Mute": False,
            "Band 3 Bypass": False,
            "Band 4 Mix": 0.0,
            "Band 4 Solo": False,
            "Band 4 Mute": False,
            "Band 4 Bypass": False,
        },
        "Brings back the high band of a recording that was worn thin, and "
        "leaves the other bands alone.",
    ),
    _preset(
        "Gentle Band Restore",
        REPAIR,
        "AUR_BANDRESTORATION",
        "Band Restoration",
        {
            "Bands": 4,
            "Low Crossover": 200.0,
            "Mid Crossover": 2000.0,
            "High Crossover": 10000.0,
            "Phase Linearity": "Efficient",
            "Restoration Depth": 8.0,
            "Noise Threshold": 12.0,
            "High Frequency Transparency": 100.0,
            "Broadband Mix": 30.0,
            "Band 1 Mix": 0.0,
            "Band 1 Solo": False,
            "Band 1 Mute": False,
            "Band 1 Bypass": False,
            "Band 2 Mix": 0.0,
            "Band 2 Solo": False,
            "Band 2 Mute": False,
            "Band 2 Bypass": False,
            "Band 3 Mix": 0.0,
            "Band 3 Solo": False,
            "Band 3 Mute": False,
            "Band 3 Bypass": False,
            "Band 4 Mix": 0.0,
            "Band 4 Solo": False,
            "Band 4 Mute": False,
            "Band 4 Bypass": False,
        },
        "Eases the restoration of a recording that is only slightly worn, "
        "and keeps most of the original signal.",
    ),
    _preset(
        "Deep Band Rescue",
        REPAIR,
        "AUR_BANDRESTORATION",
        "Band Restoration",
        {
            "Bands": 4,
            "Low Crossover": 200.0,
            "Mid Crossover": 2000.0,
            "High Crossover": 10000.0,
            "Phase Linearity": "Linear Phase",
            "Restoration Depth": 35.0,
            "Noise Threshold": 3.0,
            "High Frequency Transparency": 40.0,
            "Broadband Mix": 0.0,
            "Band 1 Mix": 0.0,
            "Band 1 Solo": False,
            "Band 1 Mute": False,
            "Band 1 Bypass": False,
            "Band 2 Mix": 0.0,
            "Band 2 Solo": False,
            "Band 2 Mute": False,
            "Band 2 Bypass": False,
            "Band 3 Mix": 0.0,
            "Band 3 Solo": False,
            "Band 3 Mute": False,
            "Band 3 Bypass": False,
            "Band 4 Mix": 0.0,
            "Band 4 Solo": False,
            "Band 4 Mute": False,
            "Band 4 Bypass": False,
        },
        "The deepest rescue the module has, for a recording whose bands "
        "have fallen far below the noise.",
    ),
    _preset(
        "Low Band Rescue",
        REPAIR,
        "AUR_BANDRESTORATION",
        "Band Restoration",
        {
            "Bands": 2,
            "Low Crossover": 150.0,
            "Mid Crossover": 1500.0,
            "High Crossover": 8000.0,
            "Phase Linearity": "Efficient",
            "Restoration Depth": 25.0,
            "Noise Threshold": 6.0,
            "High Frequency Transparency": 100.0,
            "Broadband Mix": 10.0,
            "Band 1 Mix": 0.0,
            "Band 1 Solo": False,
            "Band 1 Mute": False,
            "Band 1 Bypass": False,
            "Band 2 Mix": 0.0,
            "Band 2 Solo": False,
            "Band 2 Mute": False,
            "Band 2 Bypass": False,
            "Band 3 Mix": 0.0,
            "Band 3 Solo": False,
            "Band 3 Mute": False,
            "Band 3 Bypass": False,
            "Band 4 Mix": 0.0,
            "Band 4 Solo": False,
            "Band 4 Mute": False,
            "Band 4 Bypass": False,
        },
        "Brings back the bottom of a recording whose low band was cut off, "
        "and leaves the top of it alone.",
    ),
    # -- Voice Reverb ----------------------------------------------------------
    _preset(
        "Room On A Voice",
        REPAIR,
        "AUR_VOICEREVERB",
        "Voice Reverb",
        {
            "Reduction": 12.0,
            "Tail Length": 1.0,
            "Voice Band Low": 80.0,
            "Voice Band High": 12000.0,
            "Sensitivity": 0.5,
            "Output Reverb Only": False,
        },
        "Takes the room off a voice that was recorded with the microphone "
        "too far away.",
    ),
    _preset(
        "Small Booth",
        REPAIR,
        "AUR_VOICEREVERB",
        "Voice Reverb",
        {
            "Reduction": 8.0,
            "Tail Length": 0.4,
            "Voice Band Low": 100.0,
            "Voice Band High": 12000.0,
            "Sensitivity": 0.5,
            "Output Reverb Only": False,
        },
        "Takes the short reflection of a booth off a voice, without "
        "touching the room it was really in.",
    ),
    _preset(
        "Large Hall Voice",
        REPAIR,
        "AUR_VOICEREVERB",
        "Voice Reverb",
        {
            "Reduction": 18.0,
            "Tail Length": 2.0,
            "Voice Band Low": 80.0,
            "Voice Band High": 12000.0,
            "Sensitivity": 0.6,
            "Output Reverb Only": False,
        },
        "Takes the long tail of a hall off a voice, for a recording made in "
        "a space far too big for the words.",
    ),
    # -- Spatial Suppressor ----------------------------------------------------
    _preset(
        "Tighten A Wide Image",
        REPAIR,
        "AUR_SPATIALSUPPRESSOR",
        "Spatial Suppressor",
        {
            "Reduction": 0.7,
            "Artifact Smoothing": 0.5,
        },
        "Pulls the sides of a wide recording in, for a mix that is wider "
        "than the room it is played in.",
    ),
    _preset(
        "Gentle Narrowing",
        REPAIR,
        "AUR_SPATIALSUPPRESSOR",
        "Spatial Suppressor",
        {
            "Reduction": 0.3,
            "Artifact Smoothing": 0.7,
        },
        "Eases the width of a recording down, with a wide soft edge so "
        "nothing chatters.",
    ),
    _preset(
        "Full Narrowing",
        REPAIR,
        "AUR_SPATIALSUPPRESSOR",
        "Spatial Suppressor",
        {
            "Reduction": 1.0,
            "Artifact Smoothing": 0.4,
        },
        "Takes everything the two channels do not share out of a "
        "recording.",
    ),
    # -- Leveler ---------------------------------------------------------------
    _preset(
        "Level Dialogue Takes",
        UTILITY,
        "AUR_LEVELER",
        "Leveler",
        {
            "Optimize For": "Dialogue",
            "Target Level": -23.0,
            "Responsiveness": 0.5,
            "Preserve Dynamics": 0.5,
            "Ess Reduction": 0.0,
            "Limiter": -1.0,
        },
        "Evens out the loudness of a set of dialogue takes so that they "
        "sit together.",
    ),
    _preset(
        "Even Out A Music Take",
        UTILITY,
        "AUR_LEVELER",
        "Leveler",
        {
            "Optimize For": "Music",
            "Target Level": -18.0,
            "Responsiveness": 0.3,
            "Preserve Dynamics": 0.8,
            "Ess Reduction": 0.0,
            "Limiter": -1.0,
        },
        "Evens out a music take that was recorded with the level moving, "
        "and keeps the dynamics of the performance.",
    ),
    _preset(
        "Slow Level Ride",
        UTILITY,
        "AUR_LEVELER",
        "Leveler",
        {
            "Optimize For": "Dialogue",
            "Target Level": -23.0,
            "Responsiveness": 0.2,
            "Preserve Dynamics": 0.9,
            "Ess Reduction": 0.0,
            "Limiter": -1.0,
        },
        "Rides the level of a take slowly, so the listener never hears the "
        "gain move.",
    ),
    _preset(
        "Tame Sibilance While Levelling",
        UTILITY,
        "AUR_LEVELER",
        "Leveler",
        {
            "Optimize For": "Dialogue",
            "Target Level": -23.0,
            "Responsiveness": 0.5,
            "Preserve Dynamics": 0.5,
            "Ess Reduction": 4.0,
            "Limiter": -1.0,
        },
        "Levels a voice and takes the edge off its sibilance in one pass.",
    ),
    # -- Loudness Control ------------------------------------------------------
    _preset(
        "Broadcast Delivery",
        UTILITY,
        "AUR_LOUDNESS",
        "Loudness Control",
        {
            "Target Loudness": -23.0,
            "Tolerance": 0.5,
            "Short Term Loudness": -23.0,
            "Momentary Loudness": -23.0,
            "True Peak": -1.0,
            "Standard": "EBU R128",
        },
        "Brings a programme to the loudness a broadcast expects, with a "
        "true peak ceiling.",
    ),
    _preset(
        "Streaming Delivery",
        UTILITY,
        "AUR_LOUDNESS",
        "Loudness Control",
        {
            "Target Loudness": -16.0,
            "Tolerance": 0.5,
            "Short Term Loudness": -16.0,
            "Momentary Loudness": -16.0,
            "True Peak": -1.0,
            "Standard": "AES TD1006.1.17-10",
        },
        "Brings a programme to the loudness a streaming service plays "
        "back, and leaves the room for the service to normalise it.",
    ),
    _preset(
        "Podcast Delivery",
        UTILITY,
        "AUR_LOUDNESS",
        "Loudness Control",
        {
            "Target Loudness": -16.0,
            "Tolerance": 1.0,
            "Short Term Loudness": -16.0,
            "Momentary Loudness": -16.0,
            "True Peak": -1.0,
            "Standard": "AES TD1006.1.17-10",
        },
        "Brings a spoken programme to a level a listener can hear on a "
        "phone, and tolerates a wider spread.",
    ),
    _preset(
        "Cinema Master",
        UTILITY,
        "AUR_LOUDNESS",
        "Loudness Control",
        {
            "Target Loudness": -24.0,
            "Tolerance": 0.5,
            "Short Term Loudness": -24.0,
            "Momentary Loudness": -24.0,
            "True Peak": -2.0,
            "Standard": "ATSC A/85",
        },
        "Brings a master to the loudness a cinema expects, with the "
        "headroom it needs.",
    ),
    # -- Loudness Optimize -----------------------------------------------------
    _preset(
        "Lift Quiet Passages",
        UTILITY,
        "AUR_LOUDNESSOPT",
        "Loudness Optimize",
        {
            "Boost": 6.0,
            "Threshold": -18.0,
            "Ratio": 3.0,
            "Speed": 200.0,
        },
        "Brings the quiet passages of a recording up towards the rest of "
        "it.",
    ),
    _preset(
        "Gentle Lift",
        UTILITY,
        "AUR_LOUDNESSOPT",
        "Loudness Optimize",
        {
            "Boost": 3.0,
            "Threshold": -24.0,
            "Ratio": 2.0,
            "Speed": 300.0,
        },
        "Lifts the quiet passages of a recording a little, and slowly.",
    ),
    _preset(
        "Strong Lift",
        UTILITY,
        "AUR_LOUDNESSOPT",
        "Loudness Optimize",
        {
            "Boost": 10.0,
            "Threshold": -12.0,
            "Ratio": 6.0,
            "Speed": 80.0,
        },
        "Lifts the quiet passages of a recording hard, for a file that has "
        "to be heard everywhere.",
    ),
    # -- EQ --------------------------------------------------------------------
    _preset(
        "Telephone Voice",
        UTILITY,
        "AUR_EQ",
        "EQ",
        {
            "Type": "Analog IIR",
            "Band 1 Frequency": 300.0,
            "Band 1 Gain": -18.0,
            "Band 1 Q": 1.0,
            "Band 2 Frequency": 1800.0,
            "Band 2 Gain": 3.6,
            "Band 2 Q": 1.2,
            "Band 3 Frequency": 3200.0,
            "Band 3 Gain": -10.0,
            "Band 3 Q": 1.5,
            "Band 4 Frequency": 7000.0,
            "Band 4 Gain": -6.0,
            "Band 4 Q": 1.0,
            "Output Gain": 0.0,
        },
        "Makes a voice sound as if it were spoken through a telephone, for "
        "a scene that is told on the phone.",
    ),
    _preset(
        "Warm Voice",
        UTILITY,
        "AUR_EQ",
        "EQ",
        {
            "Type": "Analog IIR",
            "Band 1 Frequency": 120.0,
            "Band 1 Gain": -4.0,
            "Band 1 Q": 1.0,
            "Band 2 Frequency": 300.0,
            "Band 2 Gain": -2.0,
            "Band 2 Q": 1.0,
            "Band 3 Frequency": 4000.0,
            "Band 3 Gain": 2.0,
            "Band 3 Q": 1.0,
            "Band 4 Frequency": 10000.0,
            "Band 4 Gain": 0.0,
            "Band 4 Q": 1.0,
            "Output Gain": 0.0,
        },
        "Takes the edge off a voice that was recorded close, without "
        "making it dull.",
    ),
    _preset(
        "Dull Recording Lift",
        UTILITY,
        "AUR_EQ",
        "EQ",
        {
            "Type": "Analog IIR",
            "Band 1 Frequency": 80.0,
            "Band 1 Gain": -3.0,
            "Band 1 Q": 1.0,
            "Band 2 Frequency": 400.0,
            "Band 2 Gain": -2.0,
            "Band 2 Q": 1.0,
            "Band 3 Frequency": 3500.0,
            "Band 3 Gain": 3.5,
            "Band 3 Q": 1.2,
            "Band 4 Frequency": 11000.0,
            "Band 4 Gain": 2.0,
            "Band 4 Q": 1.0,
            "Output Gain": 0.0,
        },
        "Lifts a recording that came in dull, and clears the mud under it.",
    ),
    _preset(
        "Boxy Room",
        UTILITY,
        "AUR_EQ",
        "EQ",
        {
            "Type": "Analog IIR",
            "Band 1 Frequency": 300.0,
            "Band 1 Gain": -4.0,
            "Band 1 Q": 1.2,
            "Band 2 Frequency": 800.0,
            "Band 2 Gain": -3.0,
            "Band 2 Q": 1.2,
            "Band 3 Frequency": 1500.0,
            "Band 3 Gain": 1.5,
            "Band 3 Q": 1.0,
            "Band 4 Frequency": 8000.0,
            "Band 4 Gain": 0.0,
            "Band 4 Q": 1.0,
            "Output Gain": 0.0,
        },
        "Takes the box of a small room out of a voice that was recorded in "
        "one.",
    ),
    # -- EQ Match --------------------------------------------------------------
    _preset(
        "Match A Reference",
        UTILITY,
        "AUR_EQMATCH",
        "EQ Match",
        {
            "Amount": 1.0,
        },
        "Applies the whole difference between the recording and the "
        "reference it was compared with.",
    ),
    _preset(
        "Halfway Match",
        UTILITY,
        "AUR_EQMATCH",
        "EQ Match",
        {
            "Amount": 0.5,
        },
        "Applies half of the difference between the recording and the "
        "reference it was compared with.",
    ),
    _preset(
        "Subtle Match",
        UTILITY,
        "AUR_EQMATCH",
        "EQ Match",
        {
            "Amount": 0.25,
        },
        "Nudges the recording towards the reference without going all the "
        "way.",
    ),
    # -- Gain ------------------------------------------------------------------
    _preset(
        "Trim Down Six Decibels",
        UTILITY,
        "AUR_GAIN",
        "Gain",
        {
            "Gain": -6.0,
        },
        "Turns a recording down by six decibels, for a headroom that is "
        "not there.",
    ),
    _preset(
        "Trim Down Three Decibels",
        UTILITY,
        "AUR_GAIN",
        "Gain",
        {
            "Gain": -3.0,
        },
        "Turns a recording down by three decibels, for a mix that is "
        "slightly too loud.",
    ),
    _preset(
        "Trim Up Three Decibels",
        UTILITY,
        "AUR_GAIN",
        "Gain",
        {
            "Gain": 3.0,
        },
        "Turns a recording up by three decibels, for a mix that is "
        "slightly too quiet.",
    ),
    # -- Fade ------------------------------------------------------------------
    _preset(
        "Short Crossfade",
        UTILITY,
        "AUR_FADE",
        "Fade",
        {
            "Fade Type": "Equal Power",
            "Fade Length": 1.0,
        },
        "A one second crossfade with an even power across the join.",
    ),
    _preset(
        "Long Fade",
        UTILITY,
        "AUR_FADE",
        "Fade",
        {
            "Fade Type": "Log",
            "Fade Length": 5.0,
        },
        "A long fade, for the start or the end of a piece.",
    ),
    _preset(
        "Straight Fade",
        UTILITY,
        "AUR_FADE",
        "Fade",
        {
            "Fade Type": "Linear",
            "Fade Length": 2.0,
        },
        "A fade on a straight line, for a join that has to be obvious.",
    ),
    _preset(
        "Smooth Fade",
        UTILITY,
        "AUR_FADE",
        "Fade",
        {
            "Fade Type": "Cosine",
            "Fade Length": 3.0,
        },
        "A smooth fade, for a join that should not be heard at all.",
    ),
    # -- Normalize -------------------------------------------------------------
    _preset(
        "Peak At Minus One",
        UTILITY,
        "AUR_NORMALIZE",
        "Normalize",
        {
            "Target Peak Level": -1.0,
        },
        "Sets the highest peak of a finished file to one decibel below "
        "full scale.",
    ),
    _preset(
        "Peak At Minus Three",
        UTILITY,
        "AUR_NORMALIZE",
        "Normalize",
        {
            "Target Peak Level": -3.0,
        },
        "Sets the highest peak of a finished file to three decibels below "
        "full scale.",
    ),
    _preset(
        "Peak At Minus Six",
        UTILITY,
        "AUR_NORMALIZE",
        "Normalize",
        {
            "Target Peak Level": -6.0,
        },
        "Sets the highest peak of a finished file to six decibels below "
        "full scale, for a file that still has to be worked on.",
    ),
    # -- Phase -----------------------------------------------------------------
    _preset(
        "Channels Aligned",
        UTILITY,
        "AUR_PHASE",
        "Phase",
        {
            "Rotation": 0.0,
            "Adaptive Rotation": True,
        },
        "Measures the phase of the two channels and brings the right one "
        "into line with the left.",
    ),
    _preset(
        "Inverted Right Channel",
        UTILITY,
        "AUR_PHASE",
        "Phase",
        {
            "Rotation": 180.0,
            "Adaptive Rotation": False,
        },
        "Turns the right channel upside down, for a pair that was wired "
        "the wrong way round.",
    ),
    _preset(
        "Quarter Turn",
        UTILITY,
        "AUR_PHASE",
        "Phase",
        {
            "Rotation": 90.0,
            "Adaptive Rotation": False,
        },
        "Turns the right channel a quarter of a cycle against the left.",
    ),
    # -- Mixing ----------------------------------------------------------------
    _preset(
        "Left Heavier",
        UTILITY,
        "AUR_MIXING",
        "Mixing",
        {
            "Left Output Mix": 115.0,
            "Right Output Mix": 100.0,
        },
        "Puts more of the signal on the left, for a mix that leans to the "
        "left.",
    ),
    _preset(
        "Right Heavier",
        UTILITY,
        "AUR_MIXING",
        "Mixing",
        {
            "Left Output Mix": 100.0,
            "Right Output Mix": 115.0,
        },
        "Puts more of the signal on the right, for a mix that leans to the "
        "right.",
    ),
    _preset(
        "Both Slightly Hot",
        UTILITY,
        "AUR_MIXING",
        "Mixing",
        {
            "Left Output Mix": 105.0,
            "Right Output Mix": 105.0,
        },
        "Lifts both outputs a little, for a mix that is a shade too quiet.",
    ),
    # -- Dither ----------------------------------------------------------------
    _preset(
        "Sixteen Bit Delivery",
        UTILITY,
        "AUR_DITHER",
        "Dither",
        {
            "Bit Depth": "16",
            "Noise Shaping": "None",
            "Dither Amount": "Medium",
            "Auto Blanking": "If Quantized",
        },
        "Quantises a file to sixteen bits and dithers it, for a delivery "
        "that is meant to be compact.",
    ),
    _preset(
        "Twenty Four Bit Master",
        UTILITY,
        "AUR_DITHER",
        "Dither",
        {
            "Bit Depth": "24",
            "Noise Shaping": "None",
            "Dither Amount": "None",
            "Auto Blanking": "Off",
        },
        "Keeps a master at twenty four bits and adds nothing to it.",
    ),
    _preset(
        "Eight Bit Archive",
        UTILITY,
        "AUR_DITHER",
        "Dither",
        {
            "Bit Depth": "8",
            "Noise Shaping": "Shape 4",
            "Dither Amount": "High",
            "Auto Blanking": "If Quantized",
        },
        "Quantises a file to eight bits with a shaped dither, for an "
        "archive that has to be small.",
    ),
    _preset(
        "Twenty Bit Print Master",
        UTILITY,
        "AUR_DITHER",
        "Dither",
        {
            "Bit Depth": "20",
            "Noise Shaping": "Shape 2",
            "Dither Amount": "Low",
            "Auto Blanking": "Off",
        },
        "Quantises a master to twenty bits with a light shaped dither.",
    ),
    # -- Resample --------------------------------------------------------------
    _preset(
        "Studio Rate",
        UTILITY,
        "AUR_RESAMPLE",
        "Resample",
        {
            "Sample Rate": "48000",
            "Filter Steepness": 0.5,
            "Pre Ringing": 0.0,
            "Post Limiter": False,
        },
        "Converts a file to the rate a studio works at.",
    ),
    _preset(
        "Archive Rate",
        UTILITY,
        "AUR_RESAMPLE",
        "Resample",
        {
            "Sample Rate": "44100",
            "Filter Steepness": 0.7,
            "Pre Ringing": 0.3,
            "Post Limiter": False,
        },
        "Converts a file to the rate an archive keeps, with a filter that "
        "keeps the top of the spectrum.",
    ),
    _preset(
        "Broadcast Rate",
        UTILITY,
        "AUR_RESAMPLE",
        "Resample",
        {
            "Sample Rate": "32000",
            "Filter Steepness": 1.0,
            "Pre Ringing": 0.0,
            "Post Limiter": True,
        },
        "Converts a file down to the rate a broadcast carries, with a "
        "limiter that catches what the conversion pushes over.",
    ),
    _preset(
        "High Resolution Rate",
        UTILITY,
        "AUR_RESAMPLE",
        "Resample",
        {
            "Sample Rate": "192000",
            "Filter Steepness": 1.0,
            "Pre Ringing": 0.5,
            "Post Limiter": False,
        },
        "Converts a file up to a high resolution rate, for a master that "
        "is going to be worked on again.",
    ),
    # -- Time & Pitch ----------------------------------------------------------
    _preset(
        "Slow To Half Speed",
        UTILITY,
        "AUR_TIMEPITCH",
        "Time & Pitch",
        {
            "Algorithm": "Radius",
            "Stretch Ratio": 2.0,
            "Pitch Shift": 0.0,
            "Adaptive Window": 37.0,
            "Transient Sensitivity": 1.0,
            "Phase Coherence": 0.5,
            "Shift Formants": False,
        },
        "Stretches a recording to twice its length, and leaves the pitch "
        "where it was.",
    ),
    _preset(
        "Tighten A Loop",
        UTILITY,
        "AUR_TIMEPITCH",
        "Time & Pitch",
        {
            "Algorithm": "Radius RT",
            "Stretch Ratio": 0.5,
            "Pitch Shift": 0.0,
            "Adaptive Window": 60.0,
            "Transient Sensitivity": 1.2,
            "Phase Coherence": 0.6,
            "Shift Formants": False,
        },
        "Shortens a recording to half its length, for a loop that has to "
        "be tight.",
    ),
    _preset(
        "Drop The Voice A Third",
        UTILITY,
        "AUR_TIMEPITCH",
        "Time & Pitch",
        {
            "Algorithm": "Radius",
            "Stretch Ratio": 1.0,
            "Pitch Shift": -3.0,
            "Adaptive Window": 37.0,
            "Transient Sensitivity": 1.0,
            "Phase Coherence": 0.5,
            "Shift Formants": True,
        },
        "Drops a voice a third, and keeps the character of the voice with "
        "it.",
    ),
    _preset(
        "Keep The Attack",
        UTILITY,
        "AUR_TIMEPITCH",
        "Time & Pitch",
        {
            "Algorithm": "Solo",
            "Stretch Ratio": 1.0,
            "Pitch Shift": 0.0,
            "Adaptive Window": 20.0,
            "Transient Sensitivity": 1.8,
            "Phase Coherence": 0.4,
            "Shift Formants": False,
        },
        "Keeps the attack of a recording sharp, for a drum sound that is "
        "going to be played with.",
    ),
    # -- Variable Pitch --------------------------------------------------------
    _preset(
        "Drop A Third",
        UTILITY,
        "AUR_VARIPITCH",
        "Variable Pitch",
        {
            "Contour": -3.0,
            "Smoothing": 0.5,
            "Preserve Time": True,
            "Phase Coherence": 0.5,
        },
        "Moves a recording a third down, without changing how long it "
        "lasts.",
    ),
    _preset(
        "Up A Fifth",
        UTILITY,
        "AUR_VARIPITCH",
        "Variable Pitch",
        {
            "Contour": 7.0,
            "Smoothing": 0.5,
            "Preserve Time": True,
            "Phase Coherence": 0.5,
        },
        "Moves a recording a fifth up, without changing how long it lasts.",
    ),
    _preset(
        "Subtle Drift",
        UTILITY,
        "AUR_VARIPITCH",
        "Variable Pitch",
        {
            "Contour": -1.0,
            "Smoothing": 0.7,
            "Preserve Time": True,
            "Phase Coherence": 0.6,
        },
        "Moves a recording down a little, slowly, for a part that should "
        "not sit still.",
    ),
    # -- Variable Time ---------------------------------------------------------
    _preset(
        "Half Speed",
        UTILITY,
        "AUR_VARTIME",
        "Variable Time",
        {
            "Ratio": 2.0,
            "Smoothing": 0.5,
            "Phase Coherence": 0.5,
        },
        "Stretches a recording to twice its length.",
    ),
    _preset(
        "Double Speed",
        UTILITY,
        "AUR_VARTIME",
        "Variable Time",
        {
            "Ratio": 0.5,
            "Smoothing": 0.5,
            "Phase Coherence": 0.5,
        },
        "Shortens a recording to half its length.",
    ),
    _preset(
        "Slow And Smooth",
        UTILITY,
        "AUR_VARTIME",
        "Variable Time",
        {
            "Ratio": 1.5,
            "Smoothing": 0.8,
            "Phase Coherence": 0.6,
        },
        "Stretches a recording and lets the peaks slide, for a slow take "
        "that must not sound stretched.",
    ),
    # -- Interpolate -----------------------------------------------------------
    _preset(
        "High Order Repair",
        UTILITY,
        "AUR_INTERPOLATE",
        "Interpolate",
        {
            "Quality": 8,
        },
        "Rebuilds damaged samples with the highest order the module has, "
        "for the few samples that are left.",
    ),
    _preset(
        "Medium Order Repair",
        UTILITY,
        "AUR_INTERPOLATE",
        "Interpolate",
        {
            "Quality": 4,
        },
        "Rebuilds damaged samples with a middle order, for a file with a "
        "few gaps in it.",
    ),
    _preset(
        "Fast Repair",
        UTILITY,
        "AUR_INTERPOLATE",
        "Interpolate",
        {
            "Quality": 2,
        },
        "Rebuilds damaged samples with the lowest order, for a repair that "
        "has to be quick.",
    ),
    # -- Signal Generator ------------------------------------------------------
    _preset(
        "Sine Tone",
        UTILITY,
        "AUR_SIGNALGEN",
        "Signal Generator",
        {
            "Tone Shape": "Sine",
            "Frequency": 440.0,
            "Amplitude": -18.0,
            "Noise Type": "Pink",
            "Level": -60.0,
        },
        "A steady sine at concert pitch, the tone a chain is checked with.",
    ),
    _preset(
        "Alignment Tone",
        UTILITY,
        "AUR_SIGNALGEN",
        "Signal Generator",
        {
            "Tone Shape": "Sine",
            "Frequency": 1000.0,
            "Amplitude": -18.0,
            "Noise Type": "Pink",
            "Level": -60.0,
        },
        "A steady tone at one kilohertz, for a level check on a meter.",
    ),
    _preset(
        "Pink Noise",
        UTILITY,
        "AUR_SIGNALGEN",
        "Signal Generator",
        {
            "Tone Shape": "Sine",
            "Frequency": 440.0,
            "Amplitude": -96.0,
            "Noise Type": "Pink",
            "Level": -24.0,
        },
        "Pink noise, for a level check that has to cover the whole "
        "spectrum.",
    ),
    _preset(
        "Brown Noise",
        UTILITY,
        "AUR_SIGNALGEN",
        "Signal Generator",
        {
            "Tone Shape": "Sine",
            "Frequency": 440.0,
            "Amplitude": -96.0,
            "Noise Type": "Brown",
            "Level": -18.0,
        },
        "Brown noise, for a check of the low end of a chain.",
    ),
    # -- Streaming Preview -----------------------------------------------------
    _preset(
        "Lossless Preview",
        UTILITY,
        "AUR_STREAMPREVIEW",
        "Streaming Preview",
        {
            "Playback Loudness": -16.0,
            "Apply Positive Gain": True,
            "Codec": "Lossless",
            "Quality": "High",
        },
        "A preview that is played back as it is, so what is heard is what "
        "is in the file.",
    ),
    _preset(
        "Low Bitrate Preview",
        UTILITY,
        "AUR_STREAMPREVIEW",
        "Streaming Preview",
        {
            "Playback Loudness": -16.0,
            "Apply Positive Gain": True,
            "Codec": "MP3",
            "Quality": "Low",
        },
        "A preview that is squeezed hard, so the damage a low bitrate does "
        "can be heard.",
    ),
    _preset(
        "Voice Preview",
        UTILITY,
        "AUR_STREAMPREVIEW",
        "Streaming Preview",
        {
            "Playback Loudness": -19.0,
            "Apply Positive Gain": False,
            "Codec": "AAC",
            "Quality": "Medium",
        },
        "A preview of a voice at the level a phone would play it.",
    ),
    # -- Find Similar ----------------------------------------------------------
    _preset(
        "Strict Match",
        MEASUREMENT,
        "AUR_FINDSIMILAR",
        "Find Similar",
        {
            "Similarity": 0.9,
        },
        "Reports only the regions that are almost the same, for a long "
        "recording with a few duplicates.",
    ),
    _preset(
        "Loose Match",
        MEASUREMENT,
        "AUR_FINDSIMILAR",
        "Find Similar",
        {
            "Similarity": 0.6,
        },
        "Reports the regions that are alike, for a recording with a "
        "repeating bed.",
    ),
    _preset(
        "Any Match",
        MEASUREMENT,
        "AUR_FINDSIMILAR",
        "Find Similar",
        {
            "Similarity": 0.3,
        },
        "Reports every region that is in the slightest way alike, for a "
        "search that has to find something.",
    ),
    # -- Clip Classifier -------------------------------------------------------
    _preset(
        "Short Look",
        MEASUREMENT,
        "AUR_CLIPCLASSIFIER",
        "Clip Classifier",
        {
            "Highlight Match": False,
            "Analysis Length": 1.0,
        },
        "Looks at the first second of a clip and says what it thinks the "
        "source is.",
    ),
    _preset(
        "Four Second Look",
        MEASUREMENT,
        "AUR_CLIPCLASSIFIER",
        "Clip Classifier",
        {
            "Highlight Match": False,
            "Analysis Length": 4.0,
        },
        "Looks at the first four seconds of a clip, which is enough for a "
        "steady passage.",
    ),
    _preset(
        "Long Look With The Match Flagged",
        MEASUREMENT,
        "AUR_CLIPCLASSIFIER",
        "Clip Classifier",
        {
            "Highlight Match": True,
            "Analysis Length": 20.0,
        },
        "Looks at a long stretch of a clip and flags the source it "
        "believes is in it.",
    ),
    # -- Connect ---------------------------------------------------------------
    _preset(
        "Send Every Block",
        SYSTEM,
        "AUR_CONNECT",
        "Connect",
        {
            "Auto Send": True,
            "Keep Source": True,
        },
        "Hands every block to the host on its own, and keeps the source "
        "material in the timeline.",
    ),
    _preset(
        "Send By Hand",
        SYSTEM,
        "AUR_CONNECT",
        "Connect",
        {
            "Auto Send": False,
            "Keep Source": True,
        },
        "Leaves the sending to the user, and keeps the source material in "
        "the timeline.",
    ),
    _preset(
        "Replace The Source",
        SYSTEM,
        "AUR_CONNECT",
        "Connect",
        {
            "Auto Send": True,
            "Keep Source": False,
        },
        "Hands every block to the host and takes the source material out "
        "of the timeline.",
    ),
    # -- Monitor ---------------------------------------------------------------
    _preset(
        "Loudness Metering",
        SYSTEM,
        "AUR_MONITOR",
        "Monitor",
        {
            "Monitor Loudness": True,
        },
        "Runs the loudness measurement while the audio plays, for a "
        "session where the level has to be watched.",
    ),
    _preset(
        "Plain Monitoring",
        SYSTEM,
        "AUR_MONITOR",
        "Monitor",
        {
            "Monitor Loudness": False,
        },
        "Plays the audio without measuring it, for a session where the "
        "level is not in question.",
    ),
)
