/*
    AuralisVST3.h - the Auralis VST3 adapter, written directly against the
    VST3 SDK (no JUCE).

    WHY THIS EXISTS
    ---------------
    ``plugins/vst3`` holds a JUCE based VST3 adapter.  It needs a JUCE
    checkout AND a JUCE compatible VST3 SDK layout, and the two do not line
    up: JUCE 6 wants the 3.6 era SDK, JUCE 7 and 8 want a 3.7 era layout
    that no SDK tag provides.  The SDK we actually have is 3.8.1, in the new
    split layout (``pluginterfaces/`` next to ``public.sdk/``).

    This adapter therefore talks to the SDK directly.  It implements the
    interfaces a VST3 plug-in needs:

        IPluginFactory    the class factory the host asks for first
        IComponent        the bus layout and the state
        IAudioProcessor   the audio path
        IEditController   the parameter list and the parameter values

    THE THREE OBJECTS
    -----------------
    The format wants the roles in separate objects, and this adapter now
    has them:

        AuralisComponent    the buses, the state and the DSP engine
        AuralisProcessor    the audio path; the component owns it
        AuralisController   the parameter list and the parameter values

    The factory creates the component or the controller, depending on the
    interface the host asks for.  The host holds the two as two separate
    pointers and releases each of them once, so every object is deleted
    when its last reference goes.

    THE PARAMETER VALUES
    --------------------
    The controller holds the values the host sees; the component holds the
    copy the audio thread reads.  The connection point carries the values
    from the first to the second: when the host connects the controller to
    the component, the controller pushes every value over the connection,
    and every later change is pushed as well.

    The component's copy is a plain array of atomics, so the audio thread
    reads it without taking a lock and without asking the core.

    THE TEMPLATE
    ------------
    One binary serves every Auralis module.  Which module a binary
    implements is decided at BUILD time: the build stamps an identity record
    into a reserved slot in the binary (see ``plugins/identity.py`` and
    ``plugins/vst3/build_identity.py``), and this adapter reads that record
    when it starts.

    Consequences:

      * There is no per-module adapter code.  Adding a module to the
        registry produces a plug-in for every format automatically.
      * The parameter ids are NOT written by hand.  They come from the
        generated registry index, mirrored into ``AuralisGenerated.h`` at
        build time, so a session written by one build still loads in the
        next one.
      * The core is addressed by parameter NAME.  The identity record
        carries the host-id -> name map; the adapter uses it to translate.

    THE CORE
    --------
    The DSP core is reached through the thin C ABI declared in
    ``plugins/vst3/aurcore_c_api.h`` and nowhere else.  The adapter never
    sees the core's implementation.

    THE STATE
    ---------
    The state chunk uses the ``auralis.state.v1`` convention that the JUCE
    adapter uses, byte for byte, so a session saved by one adapter loads in
    the other:

        "auralis.state.v1" NUL
        <core chain document> NUL
        <host parameter state> NUL

    Every field is NUL terminated, which is what the JUCE adapter's
    ``MemoryOutputStream::writeString`` writes.

    Build:  see CMakeLists.txt in this directory.
*/

#pragma once

#include "pluginterfaces/base/funknown.h"
#include "pluginterfaces/base/ipluginbase.h"
#include "pluginterfaces/gui/iplugview.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivstmessage.h"
#include "pluginterfaces/vst/ivstparameterchanges.h"
#include "pluginterfaces/vst/ivstunits.h"
#include "pluginterfaces/vst/vsttypes.h"

#include "aurcore_c_api.h"

#include <array>
#include <atomic>
#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace auralis
{

// ---------------------------------------------------------------------------
//  the parameters the plug-in owns
// ---------------------------------------------------------------------------
//
//  A module's own parameters are numbered from 0 inside their module, so the
//  two parameters below cannot collide with one of them.  They are the same
//  in every plug-in of the family, so an automation map written for one
//  module works with the next one.

/** The parameter id of the bypass switch.

    The id is the one the reference plug-ins use for their bypass, so a host
    that automates the reference's switch automates ours with the same
    curve.  The flags are the ones the format reserves for it:
    ``kCanAutomate | kIsBypass``.

    The switch is part of the parameter table, so it is part of the state:
    a session that is saved with the module bypassed comes back bypassed.
*/
constexpr int kAuralisBypassParamId = 1652125811;

/** The parameter id of the program change parameter.

    A host's program list drives this parameter: the host sets it to the
    index of the preset the user picked, and the plug-in loads that preset.
    The id is the same in every plug-in of the family.
*/
constexpr int kAuralisProgramParamId = 1652125810;

/** The title of the bypass switch. */
constexpr const char* kAuralisBypassTitle = "Bypass";

/** The title of the program change parameter. */
constexpr const char* kAuralisProgramTitle = "Program";

// ---------------------------------------------------------------------------
//  the identity record
// ---------------------------------------------------------------------------

/** The identity record that the build stamped into this binary.

    Parsed from the JSON blob that the build wrote into the identity slot.
    A template binary that was never stamped yields a record whose `valid`
    flag is false; the adapter then refuses to pretend to be something it
    is not.
*/
struct IdentityRecord
{
    /** True when a valid, stamped record was found. */
    bool valid = false;

    /** Canonical module code, e.g. "AUR_DECLICK". */
    std::string moduleCode;

    /** Human readable module name, e.g. "De-Click". */
    std::string displayName;

    /** Product line name shown to the host, e.g. "Auralis De-Click". */
    std::string productName;

    /** Long description shown in the plug-in file browser. */
    std::string fileDescription;

    /** File name the plug-in is installed under. */
    std::string originalFilename;

    /** Stable internal name used by the host's session machinery. */
    std::string internalName;

    /** Registry category, e.g. "Repair". */
    std::string category;

    /** VST3 class id as a canonical dashed GUID string. */
    std::string vst3Fuid;

    /** The VST3 class id as thirty-two hex characters, no dashes. */
    std::string vst3FuidHex;

    /** AAX effect ids: manufacturer, product and plug-in code triples. */
    std::vector<std::array<std::string, 3>> aaxEffectIds;

    /** Host parameter id -> canonical core parameter name. */
    std::map<int, std::string> paramIdMap;

    /** Look up the core parameter name of a host parameter id. */
    std::string paramNameFor (int hostParamId) const;
};

/** Read the identity record stamped into this binary.

    Returns an invalid record when the binary carries no record, which is
    the case for an unstamped template.
*/
IdentityRecord readEmbeddedIdentity();

/** One preset of the built-in library, as the host sees it.

    The values are the ones the preset file carries, in the shape the file
    carries them: an enum parameter holds the CHOICE STRING, a switch holds
    "1" or "0", and a number holds the number in text.
*/
struct ProgramPreset
{
    std::string name;         //!< the name the host shows
    std::string category;     //!< the category the library files it under
    std::string description;  //!< the sentence the library carries
    std::string moduleCode;   //!< the module the preset belongs to
    bool builtin = false;     //!< true when the preset is one of the shipped ones

    /** The parameter values: the canonical name and the value in text. */
    std::vector<std::pair<std::string, std::string>> values;
};

/** The presets of the built-in library that belong to one module.

    The library is read from disk: the plug-in finds it the way it finds
    the shared runtime, from the directory it was loaded from.  A module
    without a preset gets an empty list, and so does a plug-in that cannot
    find the library at all: an empty list is the honest answer, and the
    host shows an empty program list.

    A preset that names a parameter the module does not have is not sound,
    and it is left out: the library calls such a file damaged.

    The list is read once and kept: the plug-in is loaded and unloaded
    again and again, and the library does not change while it is loaded.
*/
const std::vector<ProgramPreset>& programPresetsForModule (const IdentityRecord& identity);

/** The identity record of this binary, read once and kept.

    The record is deliberately never destroyed: a host unloads the module
    at a moment of its own choosing, and a static that is torn down at that
    moment walks the containers of a module that is already half unloaded.
*/
const IdentityRecord& sharedIdentity();

// ---------------------------------------------------------------------------
//  the host parameters
// ---------------------------------------------------------------------------

/** The kind of a core parameter, which decides how the host sees it. */
enum class ParamKind
{
    Float,
    Int,
    Bool,
    Enum
};

/** One parameter of the stamped module, as the host sees it. */
struct HostParam
{
    int id = 0;                // host parameter id (inside its module)
    std::string name;          // canonical core parameter name
    std::string displayName;   // what the host shows
    std::string unit;          // "dB", "Hz", "ms", "%", ...
    std::string group;         // panel grouping label
    std::string curve;         // "linear", "log" or "taper"
    std::string help;          // the sentence that explains the parameter
    ParamKind kind = ParamKind::Float;
    double minimum = 0.0;
    double maximum = 1.0;
    double defaultValue = 0.0;
    /** The exponent p of the taper law, v = lo + (hi - lo) * n ** p.

        It only means something when the curve is a taper; the straight line
        and the logarithm do not have a shape to carry.
    */
    double curveExponent = 1.0;
    std::vector<std::string> choices;   // enum choices, empty otherwise
    bool automatable = true;
    bool stepped = false;

    /** True when the host has to draw the parameter as a list of choices.

        The format has a flag for it (``kIsList``): a generic editor draws a
        drop-down instead of a slider, and the choices of the parameter are
        the steps of that list.
    */
    bool isList = false;

    /** True for the bypass switch. */
    bool isBypass = false;

    /** True for the program change parameter. */
    bool isProgramChange = false;
};

// ---------------------------------------------------------------------------
//  the presets of the built-in library
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
//  the help text of the parameters
// ---------------------------------------------------------------------------

/** The extension that carries the help text of a parameter.

    WHY AN EXTENSION
    ----------------
    The parameter info of the format carries three texts: the title, the
    short title and the units.  The title is the name of the parameter, the
    short title is what a narrow column shows, and the units are the unit
    the value is in.  A sentence that says what the parameter does fits in
    none of them, and a host that is handed one of them shows the name of
    the parameter where the sentence should be.

    So the plug-in publishes the sentences through an interface of its own.
    A host that knows the interface shows the sentence of every parameter; a
    host that does not know it is no worse off than it was before.
*/
class IParameterHelp : public Steinberg::FUnknown
{
public:
    /** The number of parameters that carry a sentence. */
    virtual Steinberg::int32 PLUGIN_API getParameterHelpCount () = 0;

    /** The sentence of one parameter, by its host parameter id.

        Returns ``kResultFalse`` when the parameter carries no sentence.
    */
    virtual Steinberg::tresult PLUGIN_API getParameterHelp (Steinberg::Vst::ParamID id,
                                                            Steinberg::Vst::String128 help) = 0;

    static const Steinberg::FUID iid;
};

//  The interface id of the extension.  It is declared here, inside the
//  namespace the class lives in, because the macro that declares it builds
//  the name of the identifier from the name of the class.
DECLARE_CLASS_IID (IParameterHelp, 0xA17A9E11, 0x4C2B0D6E, 0x9F31C4A7, 0x2B5E8D30)

// ---------------------------------------------------------------------------
//  the state chunk
// ---------------------------------------------------------------------------

/** The marker both adapters put at the head of the state chunk. */
extern const char* const kAuralisStateMarker;

/** Build the state chunk: the marker, the core chain and the parameter state.

    The three fields are NUL terminated, exactly like the JUCE adapter's
    ``MemoryOutputStream::writeString`` writes them.
*/
std::string encodeStateChunk (const std::string& chainJson, const std::string& parameterState);

/** Split a state chunk back into its two payload fields.

    Returns false when the chunk does not start with the shared marker, in
    which case the caller must leave the plug-in alone.
*/
bool decodeStateChunk (const char* data, size_t size, std::string& chainJson,
                       std::string& parameterState);

/** Build the parameter state as the value tree document the JUCE adapter
    writes, so the two adapters can read each other's sessions.

    The document is an XML element tree whose root carries the state type
    and whose children carry one entry per parameter.  The writer and the
    reader are internal to the adapter: the document is written and read by
    the same binary.
*/
std::string encodeParameterState (const std::vector<HostParam>& params,
                                   const std::vector<double>& naturalValues);

// ---------------------------------------------------------------------------
//  the parameter table
// ---------------------------------------------------------------------------

/** The parameters of one module, with the values that belong to them.

    The component and the controller each own one: the controller publishes
    the table and holds the values the host sees, the component holds the
    copy the audio thread reads.  The connection point keeps the two in
    step.

    The values are atomic, because the host writes them from the user
    interface thread and the audio thread reads them.
*/
class ParameterTable
{
public:
    /** Build the table from the stamped identity.

        The presets of the module are the second half of the table's story:
        a module that has presets gets a program change parameter, so that a
        host's program list has something to drive.
    */
    void build (const IdentityRecord& identity, const std::vector<ProgramPreset>& presets);

    /** The parameters, ordered by their host parameter id. */
    const std::vector<HostParam>& params () const noexcept { return params_; }

    /** The index of a host parameter id, or -1. */
    int indexOfParam (Steinberg::Vst::ParamID id) const;

    /** True when the bypass switch is on. */
    bool isBypassed () const;

    /** The normalised (0..1) value of one parameter. */
    double normalizedValue (Steinberg::Vst::ParamID id) const;

    /** The normalised (0..1) value of the parameter at an index. */
    double normalizedValueAt (size_t index) const;

    /** Store a normalised (0..1) value. */
    void setNormalizedValue (Steinberg::Vst::ParamID id, double value);

    /** The natural (dB, Hz, ms, ...) value of one parameter. */
    double naturalValue (const HostParam& param, double normalized) const;

    /** The normalised (0..1) value of a natural value. */
    double normalizedFromNatural (const HostParam& param, double natural) const;

    /** The parameter state as the shared document. */
    std::string parameterState () const;

    /** Apply a decoded parameter state.

        A parameter the table does not know is ignored, so a state written
        by an adapter that knows more parameters still loads.
    */
    void applyParameterState (const std::map<int, double>& valuesById);

private:
    std::vector<HostParam> params_;
    std::map<int, size_t> paramIndex_;

    /** The current normalised value of every parameter. */
    std::unique_ptr<std::atomic<double>[]> values_;
};

// ---------------------------------------------------------------------------
//  the plug-in objects
// ---------------------------------------------------------------------------

class AuralisProcessor;

/** What the processor and the component that owns it share.

    The processor is an ordinary COM object: the last release deletes it.
    A host may however hold more than one pointer to it and release it more
    than once - the test host takes the processor interface through a smart
    pointer and releases the raw pointer again when it tears the plug-in
    down - and the count then reaches zero while the owner still holds a
    reference.  The owner therefore has to be able to tell whether the
    processor is still there.
*/
struct ProcessorLifetime
{
    /** False once the processor is gone. */
    std::atomic<bool> alive { true };
};

/** The component: the buses, the state and the DSP engine.

    One instance backs one host plug-in instance.  It owns the core engine
    and the processor, and it keeps the copy of the parameter values the
    audio thread reads.
*/
class AuralisComponent final : public Steinberg::Vst::IComponent,
                               public Steinberg::Vst::IConnectionPoint
{
public:
    AuralisComponent();
    ~AuralisComponent();

    // -- FUnknown ----------------------------------------------------------
    Steinberg::tresult PLUGIN_API queryInterface (const Steinberg::TUID iid,
                                                  void** obj) SMTG_OVERRIDE;
    Steinberg::uint32 PLUGIN_API addRef () SMTG_OVERRIDE;
    Steinberg::uint32 PLUGIN_API release () SMTG_OVERRIDE;

    // -- IPluginBase -------------------------------------------------------
    Steinberg::tresult PLUGIN_API initialize (Steinberg::FUnknown* context) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API terminate () SMTG_OVERRIDE;

    // -- IComponent --------------------------------------------------------
    Steinberg::tresult PLUGIN_API getControllerClassId (Steinberg::TUID classId) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API setIoMode (Steinberg::Vst::IoMode mode) SMTG_OVERRIDE;
    Steinberg::int32 PLUGIN_API getBusCount (Steinberg::Vst::MediaType type,
                                             Steinberg::Vst::BusDirection dir) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getBusInfo (Steinberg::Vst::MediaType type,
                                              Steinberg::Vst::BusDirection dir,
                                              Steinberg::int32 index,
                                              Steinberg::Vst::BusInfo& bus) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getRoutingInfo (Steinberg::Vst::RoutingInfo& inInfo,
                                                  Steinberg::Vst::RoutingInfo& outInfo) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API activateBus (Steinberg::Vst::MediaType type,
                                               Steinberg::Vst::BusDirection dir,
                                               Steinberg::int32 index,
                                               Steinberg::TBool state) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API setActive (Steinberg::TBool state) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API setState (Steinberg::IBStream* state) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getState (Steinberg::IBStream* state) SMTG_OVERRIDE;

    // -- IConnectionPoint --------------------------------------------------
    //  The host connects the component with the controller through this
    //  interface.  The controller pushes the parameter values over it.
    Steinberg::tresult PLUGIN_API connect (Steinberg::Vst::IConnectionPoint* other) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API disconnect (Steinberg::Vst::IConnectionPoint* other) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API notify (Steinberg::Vst::IMessage* message) SMTG_OVERRIDE;

    // -- adapter specific --------------------------------------------------
    /** The identity this binary was stamped with. */
    const IdentityRecord& identity () const noexcept { return identity_; }

    /** True when the binary carries a usable identity record. */
    bool isIdentityValid () const noexcept { return identity_.valid; }

private:
    friend class AuralisProcessor;

    // -- the audio side, called by the processor ----------------------------
    Steinberg::tresult setupProcessing (Steinberg::Vst::ProcessSetup& setup);
    Steinberg::tresult process (Steinberg::Vst::ProcessData& data);
    Steinberg::uint32 getLatencySamples () const;
    Steinberg::tresult setBusArrangements (Steinberg::Vst::SpeakerArrangement* inputs,
                                           Steinberg::int32 numIns,
                                           Steinberg::Vst::SpeakerArrangement* outputs,
                                           Steinberg::int32 numOuts);
    Steinberg::tresult getBusArrangement (Steinberg::Vst::BusDirection dir,
                                          Steinberg::int32 index,
                                          Steinberg::Vst::SpeakerArrangement& arr);

    // -- the core engine ---------------------------------------------------
    /** Create the core engine for a sample rate. */
    bool createEngine (double sampleRate);

    /** Release the core engine. */
    void destroyEngine ();

    /** Push every host parameter into the core. */
    void pushAllParams ();

    /** Read every core parameter back out into the host parameters. */
    void pullParamsFromCore ();

    /** Push every parameter value to the controller over the connection.

        The controller holds the values the host sees; after a state has
        been restored they are the ones the audio side works with.
    */
    void pushAllParamsToController ();

    /** The core's chain document, as JSON (empty when there is no engine). */
    std::string serialiseChain () const;

    /** Apply the parameter changes the host sent with a block. */
    void applyParameterChanges (Steinberg::Vst::IParameterChanges* changes);

    // -- members -----------------------------------------------------------
    IdentityRecord identity_;

    /** The parameters and the copy of their values the audio side uses. */
    ParameterTable parameters_;

    /** The processor: the component owns it and hands it to the host. */
    AuralisProcessor* processor_ = nullptr;

    /** What the processor and the component share. */
    std::shared_ptr<ProcessorLifetime> processorLifetime_;

    /** The controller's connection point, if the host connected it. */
    Steinberg::Vst::IConnectionPoint* controllerPoint_ = nullptr;

    /** Guards against pushing parameters while a state is being restored. */
    std::atomic<bool> isRestoringState_ { false };

    /** The core engine; null until the plug-in is set up. */
    AurEngine* engine_ = nullptr;

    /** Sample rate the engine was created with. */
    double engineSampleRate_ = 0.0;

    /** The host sample rate, as the last setupProcessing reported it. */
    double sampleRate_ = 44100.0;

    /** True once the host has reported a sample rate. */
    bool hasSampleRate_ = false;

    /** True once a processing failure has been reported, so that the audio
        thread does not fill the log with the same message. */
    bool hasReportedProcessError_ = false;

    /** A chain document that arrived before the engine existed.

        Hosts routinely restore a session before they activate the
        plug-in, so the chain is kept until there is an engine to restore
        it into.  It is only touched on the message thread.
    */
    std::string pendingChain_;

    Steinberg::int32 refCount_ = 1;
};

/** The processor: the audio path.

    The component owns it and hands it to the host through queryInterface,
    the way a host asks a component for its audio processor.  It is a
    separate object because the host holds the component and the
    controller as two pointers and releases each of them once: a host that
    holds an alias of the component's own interface must not be able to
    walk into freed memory.
*/
class AuralisProcessor final : public Steinberg::Vst::IAudioProcessor
{
public:
    /** The processor of a component.  The component owns it. */
    explicit AuralisProcessor (AuralisComponent* owner);
    ~AuralisProcessor();

    // -- FUnknown ----------------------------------------------------------
    Steinberg::tresult PLUGIN_API queryInterface (const Steinberg::TUID iid,
                                                  void** obj) SMTG_OVERRIDE;
    Steinberg::uint32 PLUGIN_API addRef () SMTG_OVERRIDE;
    Steinberg::uint32 PLUGIN_API release () SMTG_OVERRIDE;

    // -- IAudioProcessor ---------------------------------------------------
    Steinberg::tresult PLUGIN_API canProcessSampleSize (Steinberg::int32 symbolicSampleSize) SMTG_OVERRIDE;
    Steinberg::uint32 PLUGIN_API getLatencySamples () SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API setupProcessing (Steinberg::Vst::ProcessSetup& setup) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API setProcessing (Steinberg::TBool state) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API process (Steinberg::Vst::ProcessData& data) SMTG_OVERRIDE;
    Steinberg::uint32 PLUGIN_API getTailSamples () SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API setBusArrangements (Steinberg::Vst::SpeakerArrangement* inputs,
                                                      Steinberg::int32 numIns,
                                                      Steinberg::Vst::SpeakerArrangement* outputs,
                                                      Steinberg::int32 numOuts) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getBusArrangement (Steinberg::Vst::BusDirection dir,
                                                     Steinberg::int32 index,
                                                     Steinberg::Vst::SpeakerArrangement& arr) SMTG_OVERRIDE;

private:
    /** The component that owns this processor. */
    AuralisComponent* owner_ = nullptr;

    /** What this processor and its owner share. */
    std::shared_ptr<ProcessorLifetime> lifetime_;

    Steinberg::int32 refCount_ = 1;
};

/** The controller: the parameter list and the parameter values.

    One instance backs the parameter side of one host plug-in instance.  It
    holds the values the host sees and pushes them to the component over
    the connection point.
*/
class AuralisController final : public Steinberg::Vst::IEditController,
                                 public Steinberg::Vst::IEditController2,
                                 public Steinberg::Vst::IUnitInfo,
                                 public IParameterHelp,
                                 public Steinberg::Vst::IConnectionPoint
{
public:
    AuralisController();
    ~AuralisController();

    // -- FUnknown ----------------------------------------------------------
    Steinberg::tresult PLUGIN_API queryInterface (const Steinberg::TUID iid,
                                                  void** obj) SMTG_OVERRIDE;
    Steinberg::uint32 PLUGIN_API addRef () SMTG_OVERRIDE;
    Steinberg::uint32 PLUGIN_API release () SMTG_OVERRIDE;

    // -- IPluginBase -------------------------------------------------------
    Steinberg::tresult PLUGIN_API initialize (Steinberg::FUnknown* context) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API terminate () SMTG_OVERRIDE;

    // -- IEditController ---------------------------------------------------
    Steinberg::tresult PLUGIN_API setComponentState (Steinberg::IBStream* state) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getState (Steinberg::IBStream* state) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API setState (Steinberg::IBStream* state) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getParameterCount () SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getParameterInfo (Steinberg::int32 paramIndex,
                                                    Steinberg::Vst::ParameterInfo& info) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getParamStringByValue (Steinberg::Vst::ParamID id,
                                                         Steinberg::Vst::ParamValue valueNormalized,
                                                         Steinberg::Vst::String128 string) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getParamValueByString (Steinberg::Vst::ParamID id,
                                                         Steinberg::Vst::TChar* string,
                                                         Steinberg::Vst::ParamValue& valueNormalized) SMTG_OVERRIDE;
    Steinberg::Vst::ParamValue PLUGIN_API normalizedParamToPlain (Steinberg::Vst::ParamID id,
                                                                  Steinberg::Vst::ParamValue valueNormalized) SMTG_OVERRIDE;
    Steinberg::Vst::ParamValue PLUGIN_API plainParamToNormalized (Steinberg::Vst::ParamID id,
                                                                    Steinberg::Vst::ParamValue plainValue) SMTG_OVERRIDE;
    Steinberg::Vst::ParamValue PLUGIN_API getParamNormalized (Steinberg::Vst::ParamID id) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API setParamNormalized (Steinberg::Vst::ParamID id,
                                                      Steinberg::Vst::ParamValue value) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API setComponentHandler (Steinberg::Vst::IComponentHandler* handler) SMTG_OVERRIDE;
    Steinberg::IPlugView* PLUGIN_API createView (Steinberg::FIDString name) SMTG_OVERRIDE;

    // -- IEditController2 --------------------------------------------------
    Steinberg::tresult PLUGIN_API setKnobMode (Steinberg::Vst::KnobMode mode) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API openHelp (Steinberg::TBool onlyCheck) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API openAboutBox (Steinberg::TBool onlyCheck) SMTG_OVERRIDE;

    // -- IUnitInfo ---------------------------------------------------------
    //  The program list: the presets of the built-in library that belong to
    //  this module.  A host shows them in its program menu, and it loads one
    //  through the program change parameter.
    Steinberg::int32 PLUGIN_API getUnitCount () SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getUnitInfo (Steinberg::int32 unitIndex,
                                                Steinberg::Vst::UnitInfo& info) SMTG_OVERRIDE;
    Steinberg::int32 PLUGIN_API getProgramListCount () SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getProgramListInfo (Steinberg::int32 listIndex,
                                                       Steinberg::Vst::ProgramListInfo& info) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getProgramName (Steinberg::Vst::ProgramListID listId,
                                                  Steinberg::int32 programIndex,
                                                  Steinberg::Vst::String128 name) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getProgramInfo (Steinberg::Vst::ProgramListID listId,
                                                  Steinberg::int32 programIndex,
                                                  Steinberg::Vst::CString attributeId,
                                                  Steinberg::Vst::String128 attributeValue) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API hasProgramPitchNames (Steinberg::Vst::ProgramListID listId,
                                                         Steinberg::int32 programIndex) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getProgramPitchName (Steinberg::Vst::ProgramListID listId,
                                                       Steinberg::int32 programIndex,
                                                       Steinberg::int16 midiPitch,
                                                       Steinberg::Vst::String128 name) SMTG_OVERRIDE;
    Steinberg::Vst::UnitID PLUGIN_API getSelectedUnit () SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API selectUnit (Steinberg::Vst::UnitID unitId) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getUnitByBus (Steinberg::Vst::MediaType type,
                                                Steinberg::Vst::BusDirection dir,
                                                Steinberg::int32 busIndex,
                                                Steinberg::int32 channel,
                                                Steinberg::Vst::UnitID& unitId) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API setUnitProgramData (Steinberg::int32 listOrUnitId,
                                                      Steinberg::int32 programIndex,
                                                      Steinberg::IBStream* data) SMTG_OVERRIDE;

    // -- IParameterHelp ----------------------------------------------------
    Steinberg::int32 PLUGIN_API getParameterHelpCount () SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getParameterHelp (Steinberg::Vst::ParamID id,
                                                    Steinberg::Vst::String128 help) SMTG_OVERRIDE;

    // -- IConnectionPoint --------------------------------------------------
    //  The host connects the controller with the component through this
    //  interface.  The values are pushed over it.
    Steinberg::tresult PLUGIN_API connect (Steinberg::Vst::IConnectionPoint* other) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API disconnect (Steinberg::Vst::IConnectionPoint* other) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API notify (Steinberg::Vst::IMessage* message) SMTG_OVERRIDE;

    // -- adapter specific --------------------------------------------------
    /** The identity this binary was stamped with. */
    const IdentityRecord& identity () const noexcept { return identity_; }

    /** True when the binary carries a usable identity record. */
    bool isIdentityValid () const noexcept { return identity_.valid; }

private:
    /** Push one parameter value to the component. */
    void pushParameter (int paramId, double normalized);

    /** Push every parameter value to the component. */
    void pushAllParams ();

    /** Apply a decoded parameter document to the controller's values. */
    void applyParameterDocument (const std::string& document);

    /** Load one preset of the built-in library into the parameters.

        The values the preset carries are set on the parameters they name,
        and the new values are pushed to the component, so that the audio
        side works with the preset and not with the values it had before.
    */
    bool applyPreset (size_t index);

    // -- members -----------------------------------------------------------
    IdentityRecord identity_;

    /** The parameters and the values the host sees. */
    ParameterTable parameters_;

    /** The presets of the built-in library that belong to this module. */
    std::vector<ProgramPreset> presets_;

    /** The component's connection point, if the host connected it. */
    Steinberg::Vst::IConnectionPoint* componentPoint_ = nullptr;

    /** The host's component handler, if it gave us one. */
    Steinberg::Vst::IComponentHandler* componentHandler_ = nullptr;

    Steinberg::int32 refCount_ = 1;
};

// ---------------------------------------------------------------------------
//  the class factory
// ---------------------------------------------------------------------------

/** The Auralis class factory.

    It publishes exactly one class.  The role of an instance is decided by
    the interface the host asks for: the component for ``IComponent``, the
    controller for ``IEditController``.
*/
class AuralisFactory final : public Steinberg::IPluginFactory2
{
public:
    AuralisFactory ();
    ~AuralisFactory ();

    // -- FUnknown ----------------------------------------------------------
    Steinberg::tresult PLUGIN_API queryInterface (const Steinberg::TUID iid,
                                                  void** obj) SMTG_OVERRIDE;
    Steinberg::uint32 PLUGIN_API addRef () SMTG_OVERRIDE;
    Steinberg::uint32 PLUGIN_API release () SMTG_OVERRIDE;

    // -- IPluginFactory ----------------------------------------------------
    Steinberg::tresult PLUGIN_API getFactoryInfo (Steinberg::PFactoryInfo* info) SMTG_OVERRIDE;
    Steinberg::int32 PLUGIN_API countClasses () SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API getClassInfo (Steinberg::int32 index,
                                                Steinberg::PClassInfo* info) SMTG_OVERRIDE;
    Steinberg::tresult PLUGIN_API createInstance (Steinberg::FIDString cid,
                                                  Steinberg::FIDString iid,
                                                  void** obj) SMTG_OVERRIDE;

    // -- IPluginFactory2 ---------------------------------------------------
    Steinberg::tresult PLUGIN_API getClassInfo2 (Steinberg::int32 index,
                                                 Steinberg::PClassInfo2* info) SMTG_OVERRIDE;

private:
    Steinberg::int32 refCount_ = 1;
};

} // namespace auralis
