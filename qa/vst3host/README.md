# Auralis VST3 host

An independent, DAW-free harness that loads a built VST3 module and
exercises it.  It exists so that "the plug-in works" can be shown with
evidence instead of with a screenshot of a DAW.

It is a *host*, not a test of the plug-in build: it loads the binary the
plug-in build produced, talks to it over the VST3 interfaces, and reports
what it saw.

---

## What it does

1. **Loads the module.**  The argument is a `.vst3` bundle, a `.vst3.dll`,
   a module file, or a folder that holds one.  On Windows the bundle's
   shared library is opened with `LoadLibraryEx` and `GetPluginFactory` is
   called; elsewhere `dlopen` and `dlsym` do the same job.

   A folder that holds a module may hold more than one file: a module that
   ships a payload next to itself, a second architecture, a readme.  The
   module is the file that **exports the factory entry point**, so the
   host reads the export table out of the file and picks the module that
   way; the name of the bundle and the extension only break a tie.  The
   order of the files in a folder is not a property of the folder, so it
   never decides anything.  The layout that was found - the folder, the
   shape of the bundle and the files that travel with the module - is in
   the report under `layout`.
2. **Enumerates the classes.**  `countClasses()` and `getClassInfo()` for
   every class.  The class id is printed as a canonical GUID, together with
   the name, the category, the cardinality and the class flags.  When the
   factory supports `IPluginFactory2` the second level of information
   (vendor, version, SDK version, sub categories) is read as well.
3. **Instantiates the component and its controller.**  The component is
   created with the `IComponent` IID, the controller with the
   `IEditController` IID of the class the component points at, and the two
   are connected through `IConnectionPoint`.
4. **Asks the audio processor what it can do.**  The number of input and
   output buses, the channel count and the name of every bus, the sample
   precisions the processor accepts, and the sample rates and block sizes it
   accepts (the VST3 interface does not publish those, so they are probed).
5. **Enumerates the parameters.**  `getParameterCount`,
   `getParameterInfo` and `getParamStringByValue` for every parameter.
6. **Drives one parameter.**  A parameter is set to a different value with
   `setParamNormalized` and read back; the change is also handed to the
   processor through `IParameterChanges`, the way a host hands over an
   automation change.
7. **Processes a block.**  `setupProcessing` with a 256 frame stereo
   buffer, then `process()`.  The input is silence, and the output peak,
   RMS and finiteness are reported.  With `--tone` a second block with a
   1 kHz tone is processed as well.
8. **Saves and restores the state.**  `getState` and `setState` on the
   component and on the controller, with the byte counts reported.
9. **Reports.**  A JSON object on stdout, a readable report on stderr.

Steps 3 to 9 above are the ones a host does.  The steps below are the
ones that make the report worth something: they are *measurements*, not
questions, and they are what catches a defect that only shows up once the
plug-in is driven like a host drives it.

## The measurements

### The buses the host uses

A host activates the buses it works with, and the validator of the SDK
activates every bus it finds.  A plug-in only processes an activated bus,
so a host that does not activate them gets silence back and cannot tell
the difference between a plug-in that does nothing and a plug-in that
was never asked to do anything.

The VST3 format says it in so many words:

> Called upon (de-)activating a bus in the host application.  The plug-in
> should only processed an activated bus, the host could provide less
> AudioBusBuffers in the process call if last busses are not activated.
> (`pluginterfaces/vst/ivstcomponent.h`)

A plug-in that takes the specification seriously writes **nothing at all**
when a host forgets.  The host then sees its own sentinel (a peak of
1e30) and 512 unwritten output samples for a 256 frame stereo block, and
the plug-in "does not process audio" for reasons that have nothing to do
with the plug-in.

The host therefore walks this order:

    setupProcessing
    activateBus (true) for every bus: the audio ones and the event ones,
                 in both directions
    setActive (true)
    setProcessing (true)

and the way out is the same list in reverse:

    setProcessing (false)
    setActive (false)
    activateBus (false) for every bus

Every instance the host makes is treated the same way: the second
instance of the agreement measurement activates its buses before it is
set active, and deactivates them when it is let go.

The report says what happened, in `buses`:

| field | what it is |
|---|---|
| `buses_activated` | the number of buses the host activated |
| `activation_failures` | the number of buses that refused |
| `activated_before_set_active` | the order the host walked |

### Did the plug-in write its output?

A host does not clear its output buffers: a plug-in that writes nothing
leaves whatever was there, and the host would pass it on.  The host fills
the buffers with a value no plug-in produces (-1e30) and counts the
samples the plug-in left untouched.

The report says it in `processing`:

| field | what it is |
|---|---|
| `output_samples_expected` | the samples the host handed over |
| `written_output_samples` | the samples the plug-in wrote |
| `unwritten_output_samples` | the samples the plug-in left untouched |
| `output_length_ok` | true when the plug-in wrote all of them |
| `unwritten_output_samples_total` | the same count over the whole exercise |

A plug-in that writes nothing shows up as `unwritten_output_samples: 512`
and a peak of 1e30, and the host fails the exercise.

### The message channel

The connection point is the channel the controller uses to talk to the
component, and the host checks that it answers.  The answer is not always
"yes": the interface does not say that a message has to be accepted, the
reference implementation declines every message it does not know
(`kResultFalse`), and a plug-in that does not implement the channel says
`kNotImplemented`.  Neither is a broken channel - the message was
delivered.  Only a call that failed outright means that the channel is
not there, and the raw result is in the report as `notify_result`.

### The connection and the parameters

A host connects the component with the controller and then never looks
at the connection again.  That is a mistake: the parameters the
controller publishes and the parameters the component works with are two
different lists, and nothing but a measurement says that they are the
same list.

The host measures it twice, with the state and with the audio.

**The state.**  The state of a plug-in that has been told a new value
differs from the state of the same plug-in that has not: the difference
is the parameter.  The host runs the same exercise three times, on three
fresh instances of the plug-in, because a plug-in's own state moves on
while it processes:

| run | what the host does | what the plug-in ends up with |
|---|---|---|
| baseline | nothing | the parameter at its default |
| automation | the change is delivered with a block, through `IParameterChanges` | the value the host sent, if the change arrived |
| controller | the controller is told the value | the value the host sent |

Three fresh instances are needed because a plug-in's internal state moves
on while it processes: two runs only compare cleanly when they start from
the same place.

The host then asks three questions:

* did the state after the automated run differ from the state after the
  baseline run?  If not, **the automation the host sent never reached the
  plug-in**.
* is the state after the automated run byte for byte the state after the
  controller was told the same value?  If not, **the value the plug-in
  holds after the automation is not the value the host sent**.
* does the audio after the automated run match the audio after the
  controller was told the same value?  If not, **the plug-in does not
  work with the value it was given**.

Every published parameter is checked the same way: the host moves it and
sees whether the plug-in's state moves with it.  A parameter that the
plug-in publishes but does not carry is a parameter the host's user can
move and the plug-in ignores.

The host also measures which parameters change the sound, with three
blocks of the same signal and the parameter at three different values.
The parameter the witness uses is the one the host can hear most clearly,
because the audio can only confirm the automation for a parameter that
changes the sound.

### The bus layout

The host reads every bus and checks that the plug-in's own description
holds up:

* the bus has channels, a name, the direction that was asked for and the
  media type that was asked for,
* the first bus of a direction is the main bus,
* a bus that does not exist may not be described,
* the speaker arrangement of the bus holds as many channels as the bus
  says it has,
* the plug-in accepts the arrangement it has just published,
* the plug-in claims no event bus it cannot describe.

### The state

The component's state and the controller's state are two different things
in the split shape (the audio state and the parameter state) and one and
the same in the combined shape (one object plays both roles).  The host
finds out which shape it is talking to and checks both:

* moving a parameter moves the controller's state,
* moving a parameter moves the component's state, so the audio side works
  with the value the controller published,
* giving the controller's state back brings the parameters back,
* giving the component's state back and handing it to the controller with
  `setComponentState` brings the parameters back as well, which is how the
  two states agree.

### The latency and the tail

A host has to know how late the plug-in's output is, or the plug-in sits
behind the rest of the session, and it has to know how long the plug-in
keeps ringing, or the host cuts the tail off and the result clicks.

**The latency.**  The host reads what the plug-in reports, twice, and
measures what it really does with a sweep: the sweep goes in, the
processed sweep comes out, and the cross correlation says where the output
sits against the input.  When the output does not look like the input any
more (the correlation is below 0.2), the host measures the onset instead:
an impulse goes in and the host watches where the output starts.  A
plug-in that delays the audio by more than 64 samples (1.33 ms) beyond
what it admits is a plug-in a host cannot compensate for.

**The range of the search.**  The cross correlation is searched over a
range of lags, and the range has to be wide enough to hold every delay
the product is able to produce.  It is **not** a constant of the host: it
is the largest delay the modules of the catalogue declare, plus a margin
of one analysis window (2048 samples, 42.7 ms at 48 kHz), and it is
never smaller than the delay the plug-in under test reports itself.  With
the catalogue of today that is 4848 + 2048 = **6896 samples (143.7 ms at
48 kHz)**.

The margin is there for three reasons: a plug-in that delays the signal
by more than it declares is still measured inside the search, so the host
finds the delay the plug-in really has instead of the edge of its own
search; a module whose delay depends on its parameters declares the delay
of the state the host measured in, and the search covers the states next
to it as well; and it is the margin the survey of the catalogue uses as
well (`qa/survey/harness.py`, `LATENCY_SEARCH_MARGIN`), so the host and
the survey look at the same range and their numbers can be compared.

A constant of 2048 samples (the width the host used to search) went blind
above 43 ms, and four modules of the family live there: `AUR_LEVELER` and
`AUR_LOUDNESS` declare 4848 samples, `AUR_STREAMPREVIEW` declares 4800 and
`AUR_DIALOGUEISOLATE` declares 4096.  The host reported 2003, 2003, 2010
and 2034 samples for them - the edge of its own search, with a
correlation of 0.0008 - and called the delay unmeasured.  With the bound
of the catalogue the same four modules report 4848, 4848, 4800 and 4096,
with a correlation of 1.000.

**The cost of the wider search.**  The correlation walks the probe once per
lag, so the wider search costs what the wider range costs, and the host
measures it instead of assuming it: the report carries
`search_ms`.  On this machine the search over 2048 lags takes 11 ms and
the search over 6896 lags takes 24 to 25 ms, so the wider search costs
about 14 ms more per module - a fraction of the 390 ms the whole latency
step takes, which is mostly the plug-in's own processing.

**The catalogue.**  The host is a C++ program and the catalogue of the
product is Python, so the catalogue is written to a file of plain text
that the host reads:

    code <TAB> class id <TAB> display name <TAB> declared delay <TAB> tolerance

`qa/vst3host/make_catalogue.py` writes it by asking the product's own
registry for every module: the delay the module declares, and the
tolerance of that delay.  The tolerance rules are the ones the survey
uses (`qa/survey/survey.py`, `latency_tolerance`): a module that declares
a delay must delay the signal by exactly that delay (2 samples, 41.7 us
at 48 kHz), a module that declares none may delay it by no more than a
block (64 samples, 1.33 ms), and a module whose delay is not a constant
carries the tolerance it needs in its own specification
(`qa/survey/specs.py`).

The host finds the catalogue in `qa/vst3host/catalogue.tsv` by walking up
from the executable and from the module, or in the file
`AURALIS_VST3HOST_CATALOGUE` names.  The host matches the module under
test on its **class id**, which is the identity of the module; a plug-in
that is not in the catalogue (the fixture of the gate, a plug-in of
somebody else) is measured against the delay it reports itself.

The report says what the host did in `latency`:

| field | what it is |
|---|---|
| `measured_delay_samples` | the delay the host measured |
| `reported_samples` | the latency the plug-in reports |
| `delay_excess_samples` | the measured delay beyond the reported one |
| `correlation` | how much of the output looks like the input (0 to 1) |
| `correlation_peak` | the same peak, normalised over the whole probe |
| `search_bound_samples` | the largest lag the search looked at |
| `search_margin_samples` | the margin above the largest declared delay |
| `search_ms` | how long the search took |
| `catalogue_loaded` | whether the catalogue was read |
| `catalogue_path` | where it was read from |
| `catalogue_max_latency_samples` | the largest delay the catalogue declares |
| `catalogue_module` | the code of the module under test |
| `catalogue_declared_samples` | the delay the catalogue declares for it |
| `catalogue_delay_error_samples` | the measured delay beyond that one |
| `latency_tolerance_samples` | how far the two may differ |

**The tail.**  A tone goes in, then silence, and the host watches how
long the output keeps going.  A plug-in that delays the signal keeps the
signal going for the length of the delay, so the delay is part of what
the host has to wait for: the ring-out is compared with the reported
latency plus the reported tail.  The spectral modules of the family
analyse the signal in windows of 2048 samples and the edge of such a
window rings for a part of that, so a plug-in may ring for up to 2048
samples (43 ms) more than it reports.  The level of the ring-out is
reported as well, so a reader can see how loud it is.

A plug-in that keeps making a sound of its own after the input stopped
(the generator does) is not a tail a host can wait for: the host tells
the two apart by the correlation between the input's tail and the
output's tail.  Such a plug-in is reported, not failed on, because the
host cannot do anything about it either way.

### The audio path

The host feeds a silence block and a tone block, and it checks that the
samples are finite.  It also checks that the plug-in *wrote* its output: a
host does not clear the output buffers, so the host fills them with a
value no plug-in produces and counts the samples the plug-in left
untouched.  The count is in the report (`unwritten_output_samples`,
`written_output_samples`) and a plug-in that writes nothing is a failure
of the exercise.

The host measures, for every parameter, whether the plug-in changes the
sound at all and whether a change of that parameter changes it.  A plug-in
that hands its input straight back is a plug-in that is not working: when
the plug-in says so in its own log, the host fails the module.  A plug-in
that works but whose effect needs more than the host's first block shows
is only reported.

### What the plug-in says

The plug-in is third party code and it talks: when it cannot find its
core it says so and passes the audio through.  The host keeps its own
report on the real error stream and catches what the plug-in writes, so
the report can say what the plug-in said, and a plug-in that complains
while it does not touch the audio is a failure.

A module that has no console of its own writes its log to the **debug
output of the operating system** (`OutputDebugString`), and that is where
its own diagnostics end up.  The operating system hands those lines to
the one reader in the session, and the host is that reader: it owns the
shared memory and the two events the operating system looks for, and it
reads the lines the module writes while it is exercised.  The lines are
in the report under `plug_in_debug_output`, and the shared memory belongs
to the whole session, so the host keeps only the lines that the process
it runs in wrote.

The buffer holds one line at a time and there is one reader per session,
so when something else in the session is reading the same buffer, some
of the lines are taken before the host sees them.  The count in
`debug_capture` says how many the host got; a run that reports fewer
lines than the module wrote means the lines were shared.

### A module that does not let go

A module that is not ours may not let go of its shared library: the code
that runs when the library is unloaded is third party code, and it may
take its time or never finish.  The host does not wait for ever.  The
unload runs on a thread of its own, the host gives up on it after five
seconds, says so in `faults`, and ends the process the moment the report
is written.

### A module that is not ours

The checks above are the checks the product makes on its own modules.  A
module that is not ours does not have to pass them, and the report is
worth more when it holds the whole picture.  `--report-only` makes the
exercise go on with the steps that can still run when a step fails: the
failure is still a failure (the exit code says so, and the step is
recorded in `steps`), but the report is complete instead of stopping at
the first complaint.

## Output contract

* **stdout** carries exactly one JSON object and nothing else.  The module
  is third party code and may print, so stdout is pointed at stderr while
  the module is loaded and exercised, and restored for the report.
* **stderr** carries the readable report.

The JSON has a stable shape, so a test can assert on it:

```jsonc
{
  "ok": true,
  "host": "auralis-vst3host",
  "exit_code": 0,
  "module":  { "argument": "...", "binary": "..." },
  "layout":  { "folder": "...", "shape": "flat", "companions": ["..."],
               "companion_count": 1 },
  "steps":   [ { "step": "classes", "outcome": "ok" } ],
  "report_only": false,
  "factory": { "vendor": "...", "url": "...", "email": "...", "flags": 16 },
  "class_count": 1,
  "classes": [
    { "index": 0, "class_id": "{...}", "class_id_hex": "...",
      "name": "...", "category": "Audio Module Class", "cardinality": 2147483647,
      "class_flags": 0, "vendor": "...", "version": "...", "sdk_version": "...",
      "sub_categories": "..." }
  ],
  "component": { "created": true, "controller_created": true,
                 "selected_class_index": 0, "selected_class_name": "..." },
  "units":    { "unit_count": 1, "program_list_count": 0, "program_name": "" },
  "connection": { "controller_is_component": false, "notify_accepted": true,
                  "component_handler_set": true },
  "buses":    { "event_buses": 0, "arrangement_accepted": true,
                "problems": [], "problem_count": 0,
                "buses_activated": 2, "activation_failures": 0,
                "activated_before_set_active": true },
  "audio":    { "input_buses": 1, "output_buses": 1, "buses": [ ... ],
                "sample_rates": [ ... ], "block_sizes": [ ... ],
                "can_process_32bit": true, "can_process_64bit": false,
                "latency_samples_before_setup": 0, "tail_samples": 0 },
  "parameters": [ { "id": 0, "title": "...", "units": "...", "step_count": 0,
                    "min_normalized": 0.0, "max_normalized": 1.0,
                    "min_plain": 0.0, "max_plain": 0, "stepped": false,
                    "default_normalized": 0.0, "default_plain": 0.0,
                    "value_normalized": 0.0,
                    "value_plain": 0.0, "value_string": "...", "flags": 1,
                    "text_at_zero": "...", "text_at_one": "...",
                    "text_round_trip_ok": true, "plain_round_trip_ok": true } ],
  "parameter_catalogue": { "count": 5, "problems": [], "problem_count": 0,
                           "text_round_trip_failures": 0,
                           "plain_round_trip_failures": 0,
                           "duplicate_id_failures": 0 },
  "parameter_liveness": { "carrier": "component", "live": 5, "inert": 0,
                           "inert_ids": [], "audible_parameters": 1,
                           "parameter_effects": [ ... ] },
  "parameter_roundtrip": { "performed": true, "id": 0, "title": "...",
                            "before_normalized": 0.0, "target_normalized": 0.25,
                            "after_normalized": 0.25, "read_back_matches": true },
  "agreement": { "performed": true, "probe_id": 4, "probe_title": "...",
                  "probe_value": 1.0,
                  "state_matches_controller": true,
                  "state_matches_baseline": false,
                  "audio_difference_to_controller": 0.0,
                  "audio_difference_to_baseline": 0.65,
                  "probe_is_audible": true },
  "latency":  { "reported_samples": 0, "reported_stable": true,
                "measured_delay_samples": 0, "delay_excess_samples": 0,
                "correlation": 0.99, "onset_samples": 0,
                "tail_reported_samples": 0, "tail_measured_samples": 0,
                "tail_excess_samples": 0 },
  "processing": { "frames": 256, "sample_rate": 48000, "input_peak": 0.0,
                  "output_peak": 0.0, "output_rms": 0.0, "output_finite": true,
                  "output_samples_expected": 512, "written_output_samples": 512,
                  "unwritten_output_samples": 0, "output_length_ok": true,
                  "unwritten_output_samples_total": 0 },
  "state":    { "component_bytes": 655, "component_restored": true,
                "controller_bytes": 244, "controller_restored": true,
                "controller_state": "restored",
                "component_and_controller_identical": false,
                "controller_state_moved_with_the_parameter": true,
                "component_state_moved_with_the_parameter": true,
                "parameter_restored_through_the_controller": true,
                "parameter_restored_through_the_component": true,
                "round_trip_identical": true },
  "timings":  { "classes": 0, "component": 1, "...": 0, "total_ms": 2231 },
  "findings": [ { "kind": "tail_not_reported", "text": "..." } ],
  "plug_in_messages": [ "Auralis: AUR_DECLICK (De-Click) with 5 parameter(s)" ],
  "plug_in_debug_output": [ "Plugin loaded..." ],
  "debug_capture": { "available": true, "line_count": 1 },
  "error": null,
  "faults": []
}
```

`faults` is empty unless the module itself faulted (see below).
`findings` holds the things the host saw that are not failures: a tail
that is not reported, a delay that could not be measured, a plug-in that
does not touch the audio in the host's window.  `plug_in_messages` holds
what the plug-in wrote to its error stream, `plug_in_debug_output` what
it wrote to the debug output of the operating system.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | everything worked |
| 1 | the module could not be loaded or instantiated (this includes "no module given") |
| 2 | a step of the exercise failed |

The exit code is about the exercise, not about the module's manners: a
module that faults while it is being torn down is reported in `faults` and
in the readable report, but it does not change the exit code, because the
steps above all succeeded.

## A module that faults

The module is third party code.  A test host that dies with an access
violation cannot report anything, so the calls that are allowed to fault
are wrapped: the fault is recorded, the report still gets written, and the
host exits with the code of the exercise.  On Windows the faulting address
is printed as well, with the module it belongs to.

## Dependencies of the module

An Auralis plug-in links against the core's C ABI shared library, which
links against the Python runtime.  The host looks for them in this order:

1. the module's own directory,
2. every directory inside the `.vst3` bundle that holds a shared library
   (a bundle may ship its runtime in a sub folder),
3. the directories named by `AURALIS_VST3HOST_DEPS`, separated by the
   platform's path separator (`;` on Windows, `:` elsewhere).

The libraries found in those directories are loaded before the module
itself, so the module's imports are satisfied.  `run_host.py` also points
`AURALIS_VST3HOST_DEPS` at the repository's `plugins/abi/build` and at
the Python runtime, so that a module built without a staged runtime still
loads.

---

## Building

The host is one translation unit plus the two VST3 SDK source files that
have to exist for the interface identifiers and the in-memory stream, and
the measuring instruments in `measurements.cpp`.  The SDK is used from its
headers; no field offset is guessed anywhere.

The SDK is expected in the *new* layout, the directory that holds
`pluginterfaces/` and `public.sdk/`:

```
vendor/vst3sdk/            <- the default
    pluginterfaces/base/...
    pluginterfaces/vst/...
    public.sdk/source/common/...
```

```powershell
cmd /c "tools\msvc.bat cmake -S qa\vst3host -B build\vst3host -G Ninja -DCMAKE_BUILD_TYPE=Release"
cmd /c "tools\msvc.bat cmake --build build\vst3host"
```

`cmake` and `ninja` are taken from `PATH` or from
`%LOCALAPPDATA%\Python\pythoncore-3.15-64\Scripts`.  The VST3 SDK path can
be given explicitly:

```
-D AURALIS_VST3_SDK_PATH=<repo>/vendor/vst3sdk
```

## Running

```powershell
# the short way: build the host, find a module, run the host against it
python qa\vst3host\run_host.py

# a specific module
python qa\vst3host\run_host.py "build\vst3\Auralis_AUR_DECLICK_artefacts\Release\VST3\Auralis De-Click.vst3"

# the whole family, in parallel, with the time it took
python qa\vst3host\run_host.py --all --tone

# the host on its own
build\vst3host\vst3host.exe "build\vst3\Auralis_AUR_DECLICK_artefacts\Release\VST3\Auralis De-Click.vst3"

# with the extra tone pass, and with the JSON kept
build\vst3host\vst3host.exe "path\to\module.vst3" --tone
python qa\vst3host\run_host.py --keep-json qa\vst3host\last_run.json

# a module that is not ours: the report is complete even when a step fails
build\vst3host\vst3host.exe "path\to\module.vst3" --report-only --tone

# the delay is searched over 2048 samples instead of over the bound of the
# catalogue: what the host reports when its search is too narrow
build\vst3host\vst3host.exe "path\to\module.vst3" --search-bound 2048

# the catalogue of the product is read from somewhere else
$env:AURALIS_VST3HOST_CATALOGUE = "some\where\catalogue.tsv"
```

`--search-bound` is a diagnostic: the default is the bound the catalogue
gives (the largest delay the product declares plus the margin), and the
option is what makes the cost of the wider search measurable with one
binary, and what shows what the host reports when its search is too
narrow.

`AURALIS_VST3HOST_CATALOGUE` names the file the catalogue is read from.
Without it the host looks for `qa/vst3host/catalogue.tsv` in the tree the
host and the module live in.

`run_host.py` looks for modules under `build/` and `plugins/vst3/build/`,
and skips a `.vst3` bundle that does not hold a binary yet.  When no module
has been built it still builds the host and runs it without an argument, so
that the "no module given" error is shown.

The gate that runs the family is `tests/test_vst3_host.py`: it runs the
host against every module of the family in parallel and asserts on the
reports.

## What the host does not do

* It does not open the editor.  A plug-in that has no editor is a normal
  plug-in, and the parameters are enough to tell whether the module works.
* It does not judge the audio.  It reports the peak, the RMS and whether
  the samples are finite, and it measures the delay and the tail; whether
  the result is *good* is a question for the corpus tests.
* It does not play a whole piece of music.  The signals it uses are a
  tone, a click, a sweep and a mixed signal, which is what a measurement
  needs.

## The gate

`tests/test_vst3_host.py` runs the host against every module of the
family, in parallel, and asserts on the reports.  The family is 48
modules; the whole run takes about 17 seconds with 8 workers and about
15 seconds with 16, so it fits in a test suite's time budget.  The number
of workers can be set with `AURALIS_VST3HOST_WORKERS`.

The gate skips itself when the host or the family is not built, and it
never leaves a host process behind: a host that takes longer than the
timeout is killed, and the processes the gate itself started are compared
before and after the run.

The gate carries a **fixture of its own**: a small VST3 plug-in that takes
the specification seriously and only writes its output when its output bus
is active.  It is built by the test (the source is in the test file), so
the gate does not depend on anything that is not in the repository.  Two
tests use it:

* the host has to activate the buses, or the fixture writes nothing and
  the exercise fails, and
* a fixture that is told to write nothing at all has to be caught: the
  report has to say `unwritten_output_samples: 512` with a peak of 1e30,
  and the gate has to fail on that report.

Both are held to the same check the family is held to, so the gate
proves that it would catch a plug-in that does not process audio.

The gate writes the catalogue of the product itself, into a temporary
file, and hands that file to the host (`AURALIS_VST3HOST_CATALOGUE`), so
the range the host searches the delay in is the range the product
declares at the moment the gate runs.  It then holds the host to two
things:

* the range it searched has to hold every delay the catalogue declares
  (the largest declared delay plus the margin), and
* the delay it measured has to be the delay the module declares, within
  the tolerance of the module.

The second one is the rule of the survey (`qa/survey/specs.py`): a module
that declares a delay has to delay the signal by exactly that delay, a
module that declares none may delay it by no more than a block, and a
module whose delay is not a constant carries the tolerance it needs in
its own specification.  The plug-in under test is the module with the
adapter of the family around it, and the adapter may add a delay of its
own - the converter of the resampler does - so the exactness of the
module's own declaration is only checked when the plug-in promises what
the module declares.  When the two differ the gate says so: the bundle is
then older than the source it was built from, or the plug-in adds a delay
of its own.

## Files

| File | What it is |
|------|------------|
| `main.cpp` | the host |
| `measurements.cpp` | the measuring instruments: the hash, the peak, the onset, the tail, the cross correlation and the test signals |
| `CMakeLists.txt` | the build |
| `run_host.py` | the wrapper: build, find a module, run |
| `make_catalogue.py` | writes the catalogue of the product that the host reads |
| `catalogue.tsv` | the catalogue: the delay every module declares and the tolerance of that delay (generated) |
| `README.md` | this file |
