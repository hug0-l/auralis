/*
    AuralisPlugin.h - the Auralis VST3 adapter.

    ONE template serves every Auralis module.  Which module a given
    binary implements is decided at BUILD time: the build stamps an identity
    record into a reserved slot in the binary (see plugins/identity.py and
    plugins/README.md), and this adapter reads that record at construction.

    Consequences of that design, in order of importance:

      * There is no per-module adapter code.  Adding a module to the
        registry produces a plug-in for every format automatically.
      * The parameter ids are NOT written by hand.  They come from the
        generated registry index (aurcore/schema/generated.py, mirrored into
        AuralisGenerated.h at build time), so a session written by one
        build still loads in the next one.
      * The core is addressed by parameter NAME.  The identity record carries
        the host-id -> name map; the adapter uses it to translate.

    The core itself is reached through the thin C ABI declared in
    aurcore_c_api.h.  The adapter never sees the Python implementation.

    Build:  see CMakeLists.txt in this directory.
*/

#pragma once

#include <juce_audio_processors/juce_audio_processors.h>

#include <atomic>
#include <memory>
#include <map>
#include <vector>

#include "aurcore_c_api.h"

namespace auralis
{

/** The identity record embedded in this binary at build time.

    Parsed from the JSON blob that the build wrote into the identity slot.
    A template binary that was never stamped yields a record whose `valid`
    flag is false; the adapter then refuses to pretend to be something it
    is not.
*/
struct IdentityRecord
{
    /** True when a valid, stamped record was found. */
    bool valid = false;

    /** Canonical module code, e.g. "AUR_<MODULE>". */
    juce::String moduleCode;

    /** Human readable module name, e.g. "De-<Something>". */
    juce::String displayName;

    /** Product line name, e.g. "Auralis De-<Something>". */
    juce::String productName;

    /** Long description shown in the plug-in browser. */
    juce::String fileDescription;

    /** File name the plug-in is installed under. */
    juce::String originalFilename;

    /** Stable internal name used by the host's session machinery. */
    juce::String internalName;

    /** Registry category, e.g. "Repair". */
    juce::String category;

    /** VST3 class id as a canonical dashed GUID string. */
    juce::String vst3Fuid;

    /** The VST3 class id as thirty-two hex characters, no dashes. */
    juce::String vst3FuidHex;

    /** AAX effect ids: manufacturer, product and plug-in code triples. */
    juce::Array<juce::StringArray> aaxEffectIds;

    /** Host parameter id -> canonical core parameter name. */
    std::map<int, juce::String> paramIdMap;

    /** Look up the core parameter name of a host parameter id. */
    juce::String paramNameFor (int hostParamId) const
    {
        const auto entry = paramIdMap.find (hostParamId);
        return entry != paramIdMap.end() ? entry->second : juce::String();
    }
};

/** Read the identity record stamped into this binary.

    Returns an invalid record when the binary carries no record, which is
    the case for an unstamped template.
*/
IdentityRecord readEmbeddedIdentity();

/** The Auralis VST3 adapter.

    One instance of this class backs one host plug-in instance.  It owns
    exactly one core module - the one named by the stamped identity.
*/
class AuralisPlugin final : public juce::AudioProcessor
{
public:
    AuralisPlugin();
    ~AuralisPlugin() override;

    // -- juce::AudioProcessor ---------------------------------------------
    const juce::String getName() const override;

    /** How long the plug-in keeps ringing after the input stopped.

        The answer comes from the core: a module declares its own tail
        and the engine reports the longest tail of the chain.  A module
        that makes a sound of its own never goes quiet, and the answer is
        then infinite.  The call reaches the core, so it belongs on the
        message thread.
    */
    double getTailLengthSeconds() const override;
    bool acceptsMidi() const override { return false; }
    bool producesMidi() const override { return false; }
    bool isMidiEffect() const override { return false; }

    void prepareToPlay (double sampleRate, int maximumExpectedSamplesPerBlock) override;
    void releaseResources() override;
    void processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midi) override;

    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram (int) override {}
    const juce::String getProgramName (int) override;
    void changeProgramName (int, const juce::String&) override {}

    /** The bypass switch.

        The id is the same in every plug-in format of the family, and it is
        the id the reference plug-ins use, so a host's automation map lines
        up with ours.  The format publishes the parameter with the flag it
        reserves for the bypass, and the wrapper calls
        ``processBlockBypassed`` while it is on, which passes the audio
        through unchanged.
    */
    juce::AudioProcessorParameter* getBypassParameter() const override;

    void getStateInformation (juce::MemoryBlock& destData) override;
    void setStateInformation (const void* data, int sizeInBytes) override;

    juce::AudioProcessorEditor* createEditor() override;

    /** The Auralis editor lives in the user interface layer, which is not
        part of the adapter.  The host falls back to its own generic
        parameter editor.
    */
    bool hasEditor() const override { return false; }

    // -- adapter specific -------------------------------------------------
    /** The identity this binary was stamped with. */
    const IdentityRecord& identity() const noexcept { return identity_; }

    /** True when the binary carries a usable identity record. */
    bool isIdentityValid() const noexcept { return identity_.valid; }

    /** The VST3 class id of the stamped module, as thirty-two hex characters. */
    const juce::String getVst3Fuid() const noexcept { return identity_.vst3FuidHex; }

private:
    // -- parameter plumbing ------------------------------------------------
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
        int id = 0;                 // host parameter id (inside its module)
        juce::String parameterId;   // the id string the host sees
        juce::String name;          // canonical core parameter name
        juce::String displayName;   // what the host shows
        juce::String unit;
        juce::String group;
        juce::String curve;          // "linear", "log" or "taper"
        juce::StringArray choices;  // enum choices, empty otherwise
        ParamKind kind = ParamKind::Float;
        double minimum = 0.0;
        double maximum = 1.0;
        double defaultValue = 0.0;
        /** The exponent p of the taper law, v = lo + (hi - lo) * n ** p.

            It only means something when the curve is a taper.
        */
        double curveExponent = 1.0;
        int choiceIndex = 0;
        bool automatable = true;
    };

    /** Build the host parameter list from the stamped identity. */
    static std::vector<HostParam> buildHostParams (const IdentityRecord& identity);

    /** Build one host parameter object. */
    static std::unique_ptr<juce::RangedAudioParameter> createParameter (const HostParam& param);

    /** Turn the host parameter list into a host parameter layout. */
    static juce::AudioProcessorValueTreeState::ParameterLayout
        buildParameterLayout (const std::vector<HostParam>& params);

    /** Push one host parameter value (already in natural units) into the core. */
    void pushParam (const HostParam& param, double value);

    /** Create the core engine for a sample rate and a channel count.

        Returns false when the engine could not be created, in which case
        the plug-in passes the audio through unchanged.
    */
    bool createEngine (double sampleRate, int channels);

    /** Push every host parameter into the core. */
    void pushAllParams();

    /** Read every core parameter back out into the host parameters. */
    void pullParamsFromCore();

    /** The current value of one host parameter, in natural units. */
    double currentValue (const HostParam& param) const;

    /** The engine's chain document, as JSON (empty when there is no engine). */
    juce::String serialiseChain() const;

    // -- members -----------------------------------------------------------
    IdentityRecord identity_;
    std::vector<HostParam> params_;

    juce::AudioProcessorValueTreeState valueTree_;

    /** The bypass switch.

        The wrapper asks for it and publishes it with the flag the format
        reserves for the bypass.  It belongs to the plug-in, so it is
        deleted with it.
    */
    juce::AudioParameterBool* bypassParameter_ = nullptr;

    /** The core engine; null until prepareToPlay. */
    AurEngine* engine_ = nullptr;

    /** Sample rate the engine was created with. */
    double engineSampleRate_ = 0.0;

    /** Channel count the engine was created with. */
    int engineChannels_ = 0;

    /** Guards against re-entering the parameter push while restoring state.

        The audio thread reads it, so it is atomic.
    */
    std::atomic<bool> isRestoringState_ { false };

    /** A chain document that arrived before the engine existed.

        Hosts routinely restore a session before they call prepareToPlay, so
        the chain is kept until there is an engine to restore it into.  It
        is only touched on the message thread.
    */
    juce::String pendingChain_;

    /** True once a processing failure has been reported, so that the
        audio thread does not fill the log with the same message.
    */
    bool hasReportedProcessError_ = false;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (AuralisPlugin)
};

} // namespace auralis
