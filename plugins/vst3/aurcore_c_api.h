/*
    aurcore_c_api.h -- the C ABI between a plug-in adapter and the Auralis DSP core.

    THIS HEADER IS THE CONTRACT.
    ---------------------------
    An adapter reads this file and nothing else.  Every entry point below
    states its arguments, its return value, the thread it may be called on,
    whether it may be called before the interpreter is up, and what it leaves
    behind when it fails.  The implementation (plugins/abi/aurcore_abi.cpp) is
    written to match this file, the build derives the library's export table
    from this file, and tests/test_plugin_abi.py checks that all three agree.

    A change to this file is a change to the contract: bump AUR_ABI_VERSION
    and every adapter has to be rebuilt.


    1. THE TWO CLASSES OF CALL
    --------------------------
    The ABI has exactly two classes of entry point, and the difference is not
    a matter of taste: one class may be called from the audio thread, the
    other may not.

    REALTIME calls (marked "thread: audio" below) may be called from the
    audio thread.  They are the calls the adapter makes while the transport
    is running:

        aur_engine_process          run one block of audio
        aur_engine_set_param        set one parameter by name (the core's ramp)
        aur_engine_set_param_ramped set one parameter by name, over a ramp
        aur_engine_set_param_text   set one enum parameter by name
        aur_engine_get_param        read one parameter by name
        aur_engine_set_bypass       bypass one module
        aur_engine_last_error       read the last failure message

    CONTROL calls (marked "thread: message" below) must be called from the
    message thread -- the thread the host calls the plug-in's setup, state and
    teardown methods on -- and never while the audio thread is inside
    aur_engine_process:

        aur_engine_create           create an engine
        aur_engine_add_module       append a module to the chain
        aur_engine_destroy          release an engine
        aur_engine_serialize        write the chain out as a document
        aur_engine_deserialize      read a chain back from a document
        aur_engine_latency_samples  latency of the chain
        aur_engine_tail_samples     tail of the chain
        aur_engine_output_sample_rate  rate the chain's output runs at
        aur_shutdown                end the embedded interpreter

    Three calls are safe from any thread, because they touch no shared state
    and take no lock at all (marked "thread: any"):

        aur_engine_module_count     number of modules in the chain
        aur_engine_free             release a buffer the ABI handed out
        aur_engine_version          version of the core behind this ABI

    The rule the adapter has to keep is therefore short: PARAMETERS AND
    AUDIO MAY BE TOUCHED FROM THE AUDIO THREAD; THE CHAIN AND THE ENGINE
    ITSELF MAY NOT.  A host that asks for the latency, for the state or for
    a chain change does so from the message thread, and the plug-in format
    interfaces say the same thing (the latency is a setup-time question).


    2. THE INTERPRETER LOCK
    ------------------------
    The core is Python, so the ABI embeds an interpreter.  Every call that
    reaches the core takes the interpreter's own lock (the GIL) for the
    duration of that call, and holds the engine's lock while it does so.
    The order is always the same -- engine lock, then interpreter lock -- so
    the two can never deadlock.

    On the audio thread the interpreter lock is uncontended as long as no
    control call is running: the audio thread is then the only thread inside
    the interpreter and the lock costs a few hundred nanoseconds.  A control
    call waits for the engine lock, so it never runs Python code in the
    middle of an audio block; the audio thread can therefore be delayed, at
    worst, by the remainder of one control call that was already running.

    What this buys the audio thread, stated plainly: no unbounded wait, no
    Python exception left behind, no lock held across the block boundary, and
    no allocation on the C side of the audio path (the block buffer belongs
    to the engine and is reused).  What it does not buy: the audio path
    itself is Python, so it is not real-time safe in the strict sense.  That
    is a property of the core, not of this ABI, and no ABI can fix it.


    3. VALUES AND UNITS
    -------------------
    Parameters are addressed BY NAME.  There are no numeric parameter ids in
    the core: a host parameter id is an adapter-side concept, and the adapter
    maps it to a name through the identity record's param_id_map.

    The values are in NATURAL UNITS: dB, Hz, ms, %, semitones.  The core
    never sees a normalised 0..1 knob position; the adapter converts.  Enum
    parameters carry their CHOICE STRING, not an index.

    The audio buffer is PLANAR: `channels` pointers, `frames` samples each.
    The input and the output may be the same pointers (in-place).


    3a. THE RATE
    ------------
    A chain may change the rate.  The resampler converts the signal to
    another rate, and the samples it hands over belong to THAT rate: a
    block of 64 samples at 48 kHz becomes 59 samples at 44.1 kHz, and
    those 59 samples are 59/44100 seconds long, not 59/48000.

    A caller that can change its rate asks for the chain's output as it
    is (output_rate = 0 in aur_engine_process) and reads the rate the
    chain works at from aur_engine_output_sample_rate.  It is then the
    caller's business how many samples it asks for and how it lines the
    result up with the rest of the session.

    A caller that CANNOT change its rate -- a plug-in in a host with a
    fixed bus -- asks for the result at its own rate.  The core converts
    the chain's output back to it with the core's own resampler, so the
    block that comes back has the number of samples the caller asked for
    and the right duration.  The look ahead of that conversion is part of
    the latency the engine reports, because the host has to compensate
    for it.

    The call says how many samples the chain took and how many it made,
    so an adapter never has to guess: aur_engine_process fills the
    AurBlockCounts.


    3b. THE MODE
    ------------
    One parameter name is special: "Processing Mode".  It carries the mode
    the chain works in, and there are exactly two modes:

        "realtime"  the chain is played while somebody waits for it: every
                    module of the chain has to keep inside the budget of a
                    real time block.
        "offline"   the chain is rendered into a file: there is no
                    deadline, so a module may use the window it likes best
                    -- and it has to declare the look ahead that costs.

    The mode is a PARAMETER like every other one: an adapter sets it with
    aur_engine_set_param_text, exactly as it sets any other parameter, and
    the chain follows.  The mode belongs to the chain, not to a module: the
    core hands it to every module of the chain before the chain runs.

    The mode is part of the chain document, so the state of a plug-in
    carries it: a session that was rendered offline and is opened again in a
    real time host does not change its meaning silently.

    A module that uses a longer window in the offline mode delays more, so
    THE LATENCY FOLLOWS THE MODE.  An adapter that reports the latency to
    its host has to report it again when the mode changes: the format asks
    the plug-in for the latency whenever it needs it, and a plug-in that
    changes its latency has to tell the host, so that the host can
    recompute its delay compensation.


    3c. THE RAMP
    -----------
    A parameter change that arrives while the plug-in is playing is applied
    over a short, DECLARED time instead of as a step.  A step is a click:
    the output jumps by the whole change at once, and the jump is heard as
    a tick in the programme.

    The ramp moves the value from the one the parameter had to the one it
    is given, in NATURAL UNITS, over AUR_PARAM_RAMP_MS milliseconds.  The
    value that is read back is the value the parameter is going to have, so
    the automation reaches its target even while the ramp runs, and a change
    that arrives while a ramp runs re-targets the ramp from the value the
    parameter has reached -- the value never jumps.

    A parameter that is STEPPED -- an enum, a switch, a count -- has no
    value between two of its values to move through, so it changes at once.

    The plug-in formats have no field for a ramp, so the plug-in says it in
    the parameter's own description and in its own report.  A ramp that is
    not declared is a lie the user hears as a click.


    4. ERROR HANDLING
    -----------------
    Every call that fails returns a negative AurError and leaves a message
    the host can read:

        - a call on an engine leaves the message on that engine;
        - a call that has no engine (aur_engine_create, aur_shutdown) leaves
          the message on the library, and the host reads it with
          aur_engine_last_error(NULL).

    aur_engine_last_error never returns NULL.  It returns the message of the
    most recent failure, and a later success does not erase it: the host
    reads the message right after the call that failed.

    No call ever leaves a Python exception pending.  The interpreter is
    shared with the program that embeds the library (the application, the
    test suite), and a pending exception would surface there at a moment of
    the host's choosing.  Every entry point therefore clears the interpreter's
    error state before it returns.


    5. THE LIFETIME OF THE LIBRARY
    ------------------------------
    A host unloads the module at a moment of its own choosing.  The library
    is built for that: it can be loaded and unloaded repeatedly in one
    process, and it leaves no finalizer behind that would run during the
    unload.

    The interpreter is ended by an explicit call, aur_shutdown, and only if
    this library started it.  When the library is loaded into a program that
    already runs an interpreter (the application, the test suite), that
    interpreter belongs to that program and aur_shutdown leaves it alone.

    An interpreter can only be started once in a process: a second one
    cannot load the extension modules the core needs.  So a host that
    intends to load the library again in the same process must NOT call
    aur_shutdown -- the interpreter survives, and the next load finds it
    ready.  A host that calls aur_shutdown gets one interpreter lifetime,
    and a later load reports clearly that it cannot start another one
    instead of failing in some way of its own.

    Every engine must be destroyed before aur_shutdown is called.


    6. THE HOME OF THE INTERPRETER
    -------------------------------
    The interpreter needs a standard library to start at all.  Without one
    it stops with "Fatal Python error: Failed to import encodings module"
    and it takes the host down with it, in the middle of the exercise,
    without a report and without a word about the reason.

    The library therefore finds the home of the interpreter ITSELF, from
    where it is running.  It is a shared library, so it knows the directory
    it was loaded from, and the home is the first of these that holds a
    standard library of this interpreter:

    1. the directory the library was loaded from -- the runtime directory of
       the family, which is the home of the interpreter that sits in it;
    2. the directory next to it, for the arrangement where the runtime
       directory is a sibling of the library;
    3. the "Python" directory next to it, for the arrangement where the
       home is a directory of its own;
    4. the PYTHONHOME of the environment, when the package carries no home
       of its own: a deployment can point the plug-in at another
       installation without rebuilding it.

    A home that holds the interpreter library of another version is not the
    home of this library: the standard library of one version is not the
    standard library of another.

    When there is no home, the interpreter is NOT started at all.  The
    failure is reported on the error stream and in the message of the
    library, so the host can print it: a host that runs the plug-in must
    never be taken down by a plug-in that cannot find its interpreter.


    7. THE SUMMARY
    --------------
    entry point                  thread    interpreter        failure
    ---------------------------  --------  -----------------  ------------------------
    aur_engine_create            message   starts it          NULL + library message
    aur_engine_add_module        message   up (engine)        -AUR_ERR_UNKNOWN_MODULE
    aur_engine_set_param         audio     up (engine)        -AUR_ERR_UNKNOWN_PARAMETER
    aur_engine_set_param_ramped  audio     up (engine)        -AUR_ERR_UNKNOWN_PARAMETER
    aur_engine_set_param_text    audio     up (engine)        -AUR_ERR_UNKNOWN_PARAMETER
    aur_engine_get_param         audio     up (engine)        -AUR_ERR_UNKNOWN_PARAMETER
    aur_engine_set_bypass        audio     up (engine)        -AUR_ERR_INTERNAL
    aur_engine_process           audio     up (engine)        -AUR_ERR_INTERNAL
    aur_engine_module_count      any       up (engine)        -AUR_ERR_INVALID_ARGUMENT
    aur_engine_latency_samples   message   up (engine)        -AUR_ERR_INTERNAL
    aur_engine_tail_samples      message   up (engine)        -AUR_ERR_INTERNAL
    aur_engine_output_sample_rate message  up (engine)        -AUR_ERR_INTERNAL
    aur_engine_serialize         message   up (engine)        -AUR_ERR_INTERNAL
    aur_engine_deserialize       message   up (engine)        -AUR_ERR_INTERNAL
    aur_engine_destroy           message   up (engine)        none (void)
    aur_engine_free              any       not needed         none (void)
    aur_engine_last_error        any       not needed         none (never NULL)
    aur_engine_version           any       starts it          "0.0.0" if it cannot start
    aur_shutdown                 message   ends it           none (void)
*/

#ifndef AURALIS_AURCORE_C_API_H
#define AURALIS_AURCORE_C_API_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------
    The version of this contract.  It changes whenever a signature, a
    return value or a documented behaviour changes, and every adapter has to
    be rebuilt when it does.
   ------------------------------------------------------------------------- */

#define AUR_ABI_VERSION 4

/* -------------------------------------------------------------------------
    The declared ramp of a parameter change, in milliseconds.

    A parameter change that arrives while the plug-in is playing is applied
    over this time instead of as a step.  The value moves in natural units
    from the one the parameter had to the one it is given, and it reaches
    the target at the end of the ramp.

    The number is a DECLARATION, not a detail: the plug-in says it in the
    parameter's own description and in its own report, so the user knows
    that a change is on its way.

    Why this length: the reference product applies a change within 0.17 ms
    -- it does not smooth it at all, it steps.  The product's own preview
    path crosses a change over 22.0 ms and is taken to be good, so 20 ms is
    inside what the product already accepts, and it is short enough that a
    knob still feels immediate.
   ------------------------------------------------------------------------- */

#define AUR_PARAM_RAMP_MS 20.0

/* -------------------------------------------------------------------------
    The tail of a chain that never goes quiet.

    A module that makes a sound of its own -- a generator, a bed of
    ambience -- keeps ringing for as long as it is processed, so the chain
    it is in has an infinite tail.  The value is the one the plug-in
    formats use for it, so an adapter can hand it to the host as it is.
   ------------------------------------------------------------------------- */

#define AUR_TAIL_INFINITE 4294967295LL

/* -------------------------------------------------------------------------
    What one block of audio did.

    A chain may change the rate: the resampler converts the signal to
    another rate, and the samples it hands over belong to that rate.  The
    adapter has to know how many samples went in and how many came out,
    because the number of samples that comes out is not the number that
    went in when the rate changes.
   ------------------------------------------------------------------------- */

typedef struct
{
    uint32_t consumed;  /* the input samples the chain took */
    uint32_t produced;  /* the output samples the chain made, in the output rate */
} AurBlockCounts;

/* -------------------------------------------------------------------------
    Error codes.  Every failing call returns the NEGATIVE of one of these.
   ------------------------------------------------------------------------- */

typedef enum
{
    AUR_OK = 0,
    AUR_ERR_INVALID_ARGUMENT = 1,   /* an argument was NULL, empty or out of range */
    AUR_ERR_OUT_OF_MEMORY = 2,      /* the allocation for the call failed */
    AUR_ERR_UNKNOWN_MODULE = 3,     /* the module code is not in the registry */
    AUR_ERR_UNKNOWN_PARAMETER = 4,  /* the parameter name is not on that module */
    AUR_ERR_UNKNOWN_ENGINE = 5,     /* the handle does not belong to this library */
    AUR_ERR_UNSUPPORTED = 6,        /* the call cannot be made in this state */
    AUR_ERR_INTERNAL = 7            /* the core raised an exception */
} AurError;

/* -------------------------------------------------------------------------
    The engine handle.  Opaque to the adapter.
   ------------------------------------------------------------------------- */

typedef struct AurEngine AurEngine;

/* =========================================================================
    THE REQUIRED ENTRY POINTS
    ========================================================================= */

/** Create an engine.
    @param sample_rate   samples per second, > 0
    @param channels      channel count, > 0
    @return a new engine, or NULL on failure.

    thread:    message.  Not on the audio thread.
    interpreter: starts it if it is not up yet.
    failure:   returns NULL and leaves the message on the library; read it
               with aur_engine_last_error(NULL).
    lifetime:  the engine must be released with aur_engine_destroy before
               aur_shutdown is called.
 */
AurEngine* aur_engine_create (uint32_t sample_rate, uint32_t channels);

/** Append a module to the end of the chain.
    @param module_code  canonical registry code, e.g. "AUR_DECLICK"
    @return the index of the new module in the chain (0 based), or a
            negative AurError.

    thread:    message.  Not on the audio thread.
    interpreter: up (an engine can only exist when it is up).
    failure:   -AUR_ERR_INVALID_ARGUMENT for a NULL argument,
               -AUR_ERR_UNKNOWN_MODULE for a code that is not in the
               registry, -AUR_ERR_INTERNAL when the core raises.  The message
               is left on the engine.
 */
int aur_engine_add_module (AurEngine* engine, const char* module_code);

/** Set one parameter BY NAME, in natural units.
    Float, int and bool parameters take the numeric value.  Enum parameters
    take the choice string, e.g. "MB Periodic"; use aur_engine_set_param_text
    for that.

    A change that arrives while the chain is playing is applied over the
    core's ramp: a step is a click, and a click is heard.  A change that
    arrives before the chain has played anything is a change of the setup
    and is applied at once, and so is a change to a chain that renders a
    file.  The value that is read back with aur_engine_get_param is the
    value the parameter is going to have, so the automation reaches its
    target even while the ramp runs.

    Use aur_engine_set_param_ramped to ask for a ramp of a length of your
    own, or for the value at once.
    @return AUR_OK (0) or a negative AurError.

    thread:    audio.  Also allowed on the message thread.
    interpreter: up (an engine can only exist when it is up).
    failure:   -AUR_ERR_INVALID_ARGUMENT for a NULL argument or a module
               index that is not in the chain, -AUR_ERR_UNKNOWN_PARAMETER
               for a name that is not on that module, -AUR_ERR_INTERNAL
               when the core raises.  The message is left on the engine.
 */
int aur_engine_set_param (AurEngine* engine, uint32_t module_index, const char* param_name, double value);

/** Set one parameter BY NAME, in natural units, applied over a ramp.

    The value moves from the one the parameter had to the one given here, in
    natural units, over `ramp_ms` milliseconds.  The value that is read back
    with aur_engine_get_param is the value the parameter is going to have,
    so the automation reaches its target even while the ramp runs.

    A change that arrives while a ramp runs re-targets the ramp from the
    value the parameter has reached: the value never jumps.

    A parameter that is stepped -- an enum, a switch, a count -- has no value
    between two of its values to move through, so it changes at once.

    @param ramp_ms  the length of the ramp in milliseconds.  Zero or less
                    applies the value at once, whatever the chain does.
    @return AUR_OK (0) or a negative AurError.

    thread:    audio.  Also allowed on the message thread.
    interpreter: up (an engine can only exist when it is up).
    failure:   -AUR_ERR_INVALID_ARGUMENT for a NULL argument or a module
               index that is not in the chain, -AUR_ERR_UNKNOWN_PARAMETER
               for a name that is not on that module, -AUR_ERR_INTERNAL
               when the core raises.  The message is left on the engine.
*/
int aur_engine_set_param_ramped (AurEngine* engine, uint32_t module_index,
                                 const char* param_name, double value, double ramp_ms);

/** Run one block of audio.

    The input is `frames` samples per channel, at the engine's rate.  The
    output is the chain's output, and a chain that changes the rate
    (the resampler) makes a different number of samples: they belong to
    the rate the chain works at, and the call says how many there are.

    @param input            planar input, `channels` pointers of `frames`
                            samples
    @param output           planar output, `channels` pointers of at least
                            `output_capacity` samples (may alias `input`
                            for in-place processing).  The call writes into
                            all of them: the samples the chain did not make
                            are silenced.
    @param frames           the number of input samples
    @param output_capacity  the number of samples the output planes hold
    @param output_rate      the rate the result is wanted at.  Zero means
                            the rate the chain works at.  Any other value
                            asks the core to convert the chain's output to
                            it, which is what a caller with a fixed rate
                            needs: the block that comes back then has the
                            number of samples the caller asked for.
    @param counts           receives the number of input samples the chain
                            took and the number of output samples it
                            made.  May be NULL when the caller does not
                            need them.
    @return AUR_OK (0) or a negative AurError.

    The number of samples that is written into the output planes is the
    smaller of the number the chain made and `output_capacity`; the rest
    of the planes is filled with silence, so the host never plays stale
    audio.

    thread:    audio.  This is the call the transport makes.
    interpreter: up (an engine can only exist when it is up).
    failure:   -AUR_ERR_INVALID_ARGUMENT for a NULL argument or a frame count
               of zero, -AUR_ERR_OUT_OF_MEMORY when the block cannot be
               allocated, -AUR_ERR_INTERNAL when the core raises.  The
               message is left on the engine, and the output planes are
               filled with silence, so the host never plays stale audio.
 */
int aur_engine_process (AurEngine* engine, const float* const* input, float* const* output,
                        uint32_t frames, uint32_t output_capacity, uint32_t output_rate,
                        AurBlockCounts* counts);

/** Destroy an engine.  Passing NULL is a no-op.

    thread:    message.  Not on the audio thread.
    interpreter: up.  Every engine has to be destroyed before aur_shutdown.
    failure:   none; the call returns nothing.
 */
void aur_engine_destroy (AurEngine* engine);

/** End the embedded interpreter, if this library started it.

    A plug-in calls this when its last object goes: a host unloads the
    module at a moment of its own choosing, and the interpreter's own exit
    handler then runs during that unload, where it walks structures that are
    already being torn down.  Ending the interpreter while everything is
    still alive leaves that handler nothing to do.

    The call is explicit because the same library is used inside a program
    that embeds it (the application, the test suite): there the interpreter
    belongs to that program, and ending it would take the program down with
    it.  So the library only ends an interpreter it started itself, and
    leaves the host's alone.

    thread:    message.  Not on the audio thread, and not while an engine
               is alive.
    interpreter: this is the call that ends it.
    failure:   none; the call returns nothing.  When the interpreter cannot
               be started in the first place, the message is on the library
               and aur_engine_last_error(NULL) reads it.
 */
void aur_shutdown (void);

/* =========================================================================
    THE EXTENSIONS.  A plug-in needs them; the core API cannot answer them
    on its own (the host wants a latency figure, the host wants to save and
    restore a session, the user wants to see why something failed).
    ========================================================================= */

/** Set one parameter BY NAME from text.
    Used for enum parameters, whose value is the choice string.

    thread:    audio.  Also allowed on the message thread.
    interpreter: up.
    failure:   -AUR_ERR_INVALID_ARGUMENT for a NULL argument or an unknown
               module index, -AUR_ERR_UNKNOWN_PARAMETER for a name that is
               not on that module or a choice it does not have,
               -AUR_ERR_INTERNAL when the core raises.  The message is left
               on the engine.
 */
int aur_engine_set_param_text (AurEngine* engine, uint32_t module_index, const char* param_name, const char* text);

/** Read one parameter BY NAME.
    @param out  receives the value; numeric for float/int/bool, and the
                choice string for enum parameters (at most `out_size` bytes,
                always NUL terminated).
    @return AUR_OK (0) or a negative AurError.

    thread:    audio.  Also allowed on the message thread.
    interpreter: up.
    failure:   -AUR_ERR_INVALID_ARGUMENT for a NULL argument, a zero
               `out_size` or an unknown module index,
               -AUR_ERR_UNKNOWN_PARAMETER for a name that is not on that
               module, -AUR_ERR_INTERNAL when the core raises.  The message
               is left on the engine and `out` is set to the empty string.
 */
int aur_engine_get_param (AurEngine* engine, uint32_t module_index, const char* param_name, char* out, size_t out_size);

/** Bypass one module in the chain.  `bypass` is 0 or non-zero.
    A bypassed module is transparent: the audio passes through it bit for
    bit.

    thread:    audio.  Also allowed on the message thread.
    interpreter: up.
    failure:   -AUR_ERR_INVALID_ARGUMENT for a NULL engine or an unknown
               module index, -AUR_ERR_INTERNAL when the core raises.  The
               message is left on the engine.
 */
int aur_engine_set_bypass (AurEngine* engine, uint32_t module_index, int bypass);

/** Number of modules currently in the chain.
    @return the count (>= 0) or a negative AurError.

    thread:    any.  The count is kept by the library, so the call takes no
               lock and cannot block.
    interpreter: up (an engine can only exist when it is up).
    failure:   -AUR_ERR_INVALID_ARGUMENT for a NULL engine.  The message is
               left on the library, because there is no engine to leave it on.
 */
int aur_engine_module_count (const AurEngine* engine);

/** Latency of the chain in samples, as reported by the modules.
    A module whose latency depends on its parameters (the look ahead of the
    low latency de-clicker, for instance) reports the current value, so this
    is a question about the chain as it is right now.

    The figure is in the samples of the ENGINE's own rate, which is the
    rate the caller works at: a delay of 45 samples at 44.1 kHz is 49
    samples at 48 kHz, and a host that works at 48 kHz has to be told 49.
    When the chain changes the rate and the caller asks for the result at
    the engine's rate, the look ahead of the conversion back is part of it
    as well.

    @return the latency in samples (>= 0) or a negative AurError.

    thread:    message.  Not on the audio thread: the answer comes from the
               core, and asking the core means running Python.
    interpreter: up.
    failure:   -AUR_ERR_INVALID_ARGUMENT for a NULL engine,
               -AUR_ERR_INTERNAL when the core raises.  The message is left
               on the engine.
 */
int64_t aur_engine_latency_samples (const AurEngine* engine);

/** The tail of the chain in samples: how long the chain keeps ringing
    after the input stopped.

    A tail is a duration and not a position.  It says how much longer the
    chain is audible than its latency, so the time from the input
    stopping until the output of the chain is quiet is the latency plus
    the tail, and a host waits for both.

    The tail of a chain is the LONGEST tail of its modules and not the sum
    of them: the modules of a chain are clocked together, block by block,
    and a host that chains two plug-ins waits for the longer of the two
    tails and not for their sum.  In a chain of two series stages the
    second stage's input stops only when the first stage's output stops,
    so the tail of a chain is a duration for the host and not a
    prediction of the last sample of a series graph.

    A module that makes a sound of its own -- a generator, a bed of
    ambience -- never goes quiet.  The chain then has an infinite tail and
    the answer is AUR_TAIL_INFINITE: a host has to keep such a chain
    running for as long as it is switched on, and a host that stops the
    stream cuts the sound off.

    The tail is counted in the samples of the ENGINE's own rate, like the
    latency: a tail of 600 samples at 44.1 kHz is 653 samples at 48 kHz,
    and a host that works at 48 kHz waits for 653.

    @return the tail in samples (>= 0), AUR_TAIL_INFINITE when the chain
            never goes quiet, or a negative AurError.

    thread:    message.  Not on the audio thread: the answer comes from
               the core, and asking the core means running Python.
    interpreter: up.
    failure:   -AUR_ERR_INVALID_ARGUMENT for a NULL engine,
               -AUR_ERR_INTERNAL when the core raises.  The message is left
               on the engine.
 */
int64_t aur_engine_tail_samples (const AurEngine* engine);

/** The rate the chain's output runs at.

    It is the rate the engine was created with, unless a module of the
    chain changes it: the resampler converts the signal to the rate it is
    given, and the samples it hands over belong to that rate.  A caller
    that cannot change its rate asks for the result at its own rate (see
    aur_engine_process), and the core converts the chain's output back to
    it; a caller that can change its rate reads the rate here and asks
    for the chain's output as it is (output_rate = 0).

    @return the rate in samples per second (> 0) or a negative AurError.

    thread:    message.  Not on the audio thread: the answer comes from the
               core, and asking the core means running Python.
    interpreter: up.
    failure:   -AUR_ERR_INVALID_ARGUMENT for a NULL engine,
               -AUR_ERR_INTERNAL when the core raises.  The message is left
               on the engine.
 */
int64_t aur_engine_output_sample_rate (const AurEngine* engine);

/** Serialise the chain to the "auralis.chain" JSON document described in
    CONTRACT.md section 6.3.
    @param out  receives a NUL terminated buffer that the caller must hand
                back to aur_engine_free.  It is set to NULL first, so a
                failed call leaves nothing to free.
    @return the length of the buffer (not counting the NUL), or a negative
            AurError.

    The document carries the mode of the chain (see section 3b), so the
    state of the plug-in carries it too.

    thread:    message.  Not on the audio thread.
    interpreter: up.
    failure:   -AUR_ERR_INVALID_ARGUMENT for a NULL engine or a NULL `out`,
               -AUR_ERR_OUT_OF_MEMORY when the buffer cannot be allocated,
               -AUR_ERR_INTERNAL when the core raises.  The message is left
               on the engine.
 */
int64_t aur_engine_serialize (const AurEngine* engine, char** out);

/** Restore a chain from an "auralis.chain" JSON document.
    The chain is replaced by the document: the modules the document names,
    in the order it names them, with the parameters and the state it carries.
    A document that cannot be parsed leaves the chain as it was.

    The mode of the document is restored with it: a chain that was written
    in the offline mode comes back in the offline mode.

    thread:    message.  Not on the audio thread.
    interpreter: up.
    failure:   -AUR_ERR_INVALID_ARGUMENT for a NULL engine or a NULL
               document, -AUR_ERR_INTERNAL when the document cannot be read
               or the core raises.  The message is left on the engine.
 */
int aur_engine_deserialize (AurEngine* engine, const char* chain_json);

/** Free a buffer returned by aur_engine_serialize.  Passing NULL is a no-op.

    thread:    any.
    interpreter: not needed.
    failure:   none; the call returns nothing.
 */
void aur_engine_free (char* text);

/** The message describing the most recent failure.
    @param engine  the engine whose message is wanted, or NULL for the
                   message of the library itself (the one left by a call that
                   had no engine: aur_engine_create, aur_shutdown).
    @return the message, never NULL.  It is the empty string when nothing
            has failed yet.  The pointer stays valid until the next call to
            aur_engine_last_error on the same thread.

    thread:    any.  The call takes no lock on the engine, so it is safe to
               call while the audio thread is processing.
    interpreter: not needed.
    failure:   none; the call returns nothing.
 */
const char* aur_engine_last_error (const AurEngine* engine);

/** Version of the core behind this ABI, e.g. "1.0.0".  Never NULL.
    The version is that of the core package, so it changes with the core and
    not with the ABI.

    thread:    any.
    interpreter: starts it if it is not up yet, so the first call after the
                library was loaded reports the real version.
    failure:   none; the call returns "0.0.0" when the interpreter could not
               be started, and leaves the message on the library.
 */
const char* aur_engine_version (void);

#ifdef __cplusplus
}
#endif

#endif /* AURALIS_AURCORE_C_API_H */
