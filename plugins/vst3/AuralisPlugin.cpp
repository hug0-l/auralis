/*
    AuralisPlugin.cpp - the Auralis VST3 adapter.

    See AuralisPlugin.h for the architecture.  The short version:

      * this file is ONE template; it implements any Auralis module;
      * the module is chosen by the identity record that the build
        stamped into the reserved slot below;
      * the parameter table comes from AuralisGenerated.h, which the build
        generates from the canonical registry;
      * the core is reached through the C ABI in aurcore_c_api.h and is
        addressed by parameter NAME.

    Nothing in this file knows the name of a module, a parameter or a
    preset.  Adding a module to the registry therefore produces a plug-in
    for every format without touching this file.
*/

#include <memory>
#include <string>
#include "AuralisPlugin.h"

#include <algorithm>
#include <cmath>
#include <limits>

// The generated parameter table.  It is written by
// plugins/vst3/build_identity.py at build time; the __has_include guard
// keeps this file readable (and compilable) outside a build tree.
#if __has_include ("AuralisGenerated.h")
 #include "AuralisGenerated.h"
 #define AURALIS_HAVE_GENERATED_HEADER 1
#else
 #define AURALIS_HAVE_GENERATED_HEADER 0
#endif

namespace auralis
{

// ===========================================================================
//  the reserved identity slot
// ===========================================================================
//
//  Layout:   [ 13 marker bytes ][ 4096 region bytes ]
//
//  The region holds the UTF-8 JSON identity record, padded with NUL bytes.
//  The build-time stamper finds the slot by searching for the marker and
//  refuses to continue if the marker occurs more than once, so the marker
//  below is assembled from individual bytes: writing it as a string literal
//  would put the same thirteen bytes into the binary a second time.
//
//  The slot is a const object with a constant initialiser, so the linker
//  stores it in the file (a zero-initialised object would go to .bss,
//  which is not part of the file on disk and could therefore not be
//  patched).  The sentinel byte in the last position of the region keeps
//  the region from looking like an all-zero block.

namespace
{
    constexpr int kIdentityMarkerLength = 13;
    constexpr int kIdentityRegionSize = 4096;

    struct IdentitySlot
    {
        char marker[kIdentityMarkerLength];
        char region[kIdentityRegionSize];
    };

    constexpr IdentitySlot makeIdentitySlot()
    {
        IdentitySlot slot {};

        slot.marker[0]  = 'A';
        slot.marker[1]  = 'U';
        slot.marker[2]  = 'R';
        slot.marker[3]  = 'I';
        slot.marker[4]  = 'D';
        slot.marker[5]  = 'E';
        slot.marker[6]  = 'N';
        slot.marker[7]  = 'T';
        slot.marker[8]  = 'I';
        slot.marker[9]  = 'T';
        slot.marker[10] = 'Y';
        slot.marker[11] = 'v';
        slot.marker[12] = '1';

        slot.region[kIdentityRegionSize - 1] = 0x5a;

        return slot;
    }

    const IdentitySlot gIdentitySlot = makeIdentitySlot();
}

// ===========================================================================
//  reading the identity record
// ===========================================================================

IdentityRecord readEmbeddedIdentity()
{
    IdentityRecord record;

    // The build patches the record into the file AFTER the module has been
    // linked, so the contents of the slot are only known when the module
    // runs.  The slot itself is a const object, and a compiler is free to
    // assume that the contents of a const object never change - which
    // would fold the slot back to its link time value and lose the
    // record.  Reading through a volatile pointer stops that.
    const volatile char* const region = gIdentitySlot.region;

    // A stamped region starts with the opening brace of the JSON object;
    // an unstamped template has an empty region.
    if (region[0] != '{')
        return record;

    int length = 0;
    while (length < kIdentityRegionSize && region[length] != '\0')
        ++length;

    if (length <= 0)
        return record;

    // The bytes are copied out of the volatile region one by one: a
    // volatile pointer cannot be handed to a parser that wants a plain
    // one, and the copy is what the parser reads anyway.
    std::string text;
    text.reserve ((size_t) length);

    for (int i = 0; i < length; ++i)
        text += (char) region[i];

    const juce::String json = juce::String::fromUTF8 (text.c_str(), (int) text.size());
    const juce::var parsed = juce::JSON::parse (json);

    juce::DynamicObject* const object = parsed.getDynamicObject();
    if (object == nullptr)
        return record;

    auto field = [object] (const char* key) -> juce::String
    {
        const juce::var value = object->getProperty (juce::Identifier (key));
        return value.isVoid() ? juce::String() : value.toString();
    };

    record.moduleCode       = field ("module_code");
    record.displayName      = field ("display_name");
    record.productName      = field ("product_name");
    record.fileDescription  = field ("file_description");
    record.originalFilename = field ("original_filename");
    record.internalName     = field ("internal_name");
    record.category         = field ("category");
    record.vst3Fuid         = field ("vst3_fuid").toLowerCase();
    record.vst3FuidHex      = record.vst3Fuid.replace ("-", "");

    // The AAX effect ids are an array of three-element arrays.
    const juce::var ids = object->getProperty (juce::Identifier ("aax_effect_ids"));

    if (const juce::Array<juce::var>* const triples = ids.getArray())
    {
        for (const juce::var& triple : *triples)
        {
            if (! triple.isArray() || triple.size() != 3)
                continue;

            juce::StringArray entry;

            for (int i = 0; i < 3; ++i)
                entry.add (triple[i].toString());

            record.aaxEffectIds.add (entry);
        }
    }

    // The parameter id map: host parameter id -> canonical core name.
    const juce::var map = object->getProperty (juce::Identifier ("param_id_map"));

    if (juce::DynamicObject* const mapObject = map.getDynamicObject())
    {
        const auto& properties = mapObject->getProperties();

        for (auto* item = properties.begin(); item != properties.end(); ++item)
        {
            const int id = item->name.toString().getIntValue();

            if (id < 0)
                continue;

            record.paramIdMap[id] = item->value.toString();
        }
    }

    record.valid = record.moduleCode.isNotEmpty()
                    && record.displayName.isNotEmpty()
                    && record.paramIdMap.size() > 0;

    return record;
}

// ===========================================================================
//  building the host parameters
// ===========================================================================

std::vector<AuralisPlugin::HostParam> AuralisPlugin::buildHostParams (const IdentityRecord& identity)
{
    std::vector<HostParam> params;

    if (! identity.valid)
        return params;

#if AURALIS_HAVE_GENERATED_HEADER
    for (int i = 0; i < kAuralisParamCount; ++i)
    {
        const ParamMeta& meta = kAuralisParams[i];

        // Only the parameters of the stamped module belong to this binary.
        if (identity.moduleCode != juce::String (meta.moduleCode))
            continue;

        // The identity record is authoritative about which ids this module
        // owns; the generated header supplies the metadata.
        const juce::String coreName = identity.paramNameFor (meta.id);

        if (coreName.isEmpty())
            continue;

        HostParam param;
        param.id           = meta.id;
        param.parameterId  = juce::String (meta.id);
        param.name         = coreName;
        param.displayName  = juce::String (meta.name);
        param.unit         = juce::String (meta.unit);
        param.group        = juce::String (meta.group);
        param.curve        = juce::String (meta.curve);
        param.curveExponent = meta.curveExponent;
        param.automatable  = meta.automatable;
        param.minimum      = (double) meta.minimum;
        param.maximum      = (double) meta.maximum;
        param.defaultValue = (double) meta.defaultValue;

        switch (meta.type)
        {
            case ParamType::Float:
                param.kind = ParamKind::Float;
                break;

            case ParamType::Int:
                param.kind = ParamKind::Int;
                break;

            case ParamType::Bool:
                param.kind = ParamKind::Bool;
                break;

            case ParamType::Enum:
            {
                param.kind = ParamKind::Enum;

                for (int c = 0; c < meta.choiceCount; ++c)
                    param.choices.add (juce::String (meta.choices[c]));

                // The generated table stores the default of an enum as the
                // index of the default choice.
                param.choiceIndex = juce::jlimit (0, juce::jmax (0, param.choices.size() - 1),
                                                  (int) std::lround (param.defaultValue));
                break;
            }
        }

        params.push_back (param);
    }
#else
    juce::Logger::writeToLog ("Auralis: AuralisGenerated.h was not found, so this binary "
                               "exposes no parameters.  Run the build's header step first.");
#endif

    std::sort (params.begin(), params.end(),
               [] (const HostParam& a, const HostParam& b) { return a.id < b.id; });

    return params;
}

std::unique_ptr<juce::RangedAudioParameter> AuralisPlugin::createParameter (const HostParam& param)
{
    // The host parameter id is the id the parameter has inside its module, so
    // a session written by one build still loads in the next one.
    const juce::ParameterID id (param.parameterId, 1);
    const juce::String name = param.displayName.isNotEmpty() ? param.displayName : param.name;

    switch (param.kind)
    {
        case ParamKind::Float:
        {
            const float lo = (float) param.minimum;
            const float hi = (float) param.maximum;

            // The range helpers insist on a non-empty range.
            const float top = hi > lo ? hi : lo + 1.0f;

            juce::NormalisableRange<float> range;

            if (param.curve == "log" && lo > 0.0f && top > lo)
            {
                const float loLog = std::log (lo);
                const float hiLog = std::log (top);

                range = juce::NormalisableRange<float> (lo, top,
                    [loLog, hiLog] (float, float, float n)
                    {
                        return std::exp (loLog + n * (hiLog - loLog));
                    },
                    [lo, top, loLog, hiLog] (float, float, float v)
                    {
                        const float clamped = juce::jlimit (lo, top, v);
                        return (float) ((std::log (clamped) - loLog) / (hiLog - loLog));
                    },
                    [] (float start, float end, float v)
                    {
                        return juce::jlimit (start, end, v);
                    });
            }
            else if (param.curve == "taper" && top > lo)
            {
                // The shape of a fader: v = lo + (hi - lo) * n ** p.  The
                // travel spends most of its length below unity, so unity sits
                // in the middle of it.
                const float exponent = (float) (param.curveExponent > 0.0 ? param.curveExponent : 1.0);
                const float span = hi - lo;

                range = juce::NormalisableRange<float> (lo, top,
                    [lo, span, exponent] (float, float, float n)
                    {
                        return lo + span * std::pow (n, exponent);
                    },
                    [lo, span, exponent] (float, float, float v)
                    {
                        const float clamped = juce::jlimit (lo, lo + span, v);
                        return (float) std::pow ((clamped - lo) / span, 1.0 / exponent);
                    },
                    [] (float start, float end, float v)
                    {
                        return juce::jlimit (start, end, v);
                    });
            }
            else
            {
                range = juce::NormalisableRange<float> (lo, top);
            }

            return std::make_unique<juce::AudioParameterFloat> (
                id, name, range, (float) param.defaultValue,
                juce::AudioParameterFloatAttributes()
                    .withLabel (param.unit)
                    .withAutomatable (param.automatable));
        }

        case ParamKind::Int:
        {
            const int lo = (int) std::floor (param.minimum);
            const int hi = (int) std::ceil (param.maximum);
            const int top = hi > lo ? hi : lo + 1;

            return std::make_unique<juce::AudioParameterInt> (
                id, name, lo, top,
                juce::jlimit (lo, top, (int) std::lround (param.defaultValue)),
                juce::AudioParameterIntAttributes()
                    .withLabel (param.unit)
                    .withAutomatable (param.automatable));
        }

        case ParamKind::Bool:
            return std::make_unique<juce::AudioParameterBool> (
                id, name, param.defaultValue != 0.0,
                juce::AudioParameterBoolAttributes()
                    .withLabel (param.unit)
                    .withAutomatable (param.automatable));

        case ParamKind::Enum:
        {
            if (param.choices.size() > 1)
            {
                return std::make_unique<juce::AudioParameterChoice> (
                    id, name, param.choices, param.choiceIndex,
                    juce::AudioParameterChoiceAttributes()
                        .withLabel (param.unit)
                        .withAutomatable (param.automatable));
            }

            if (param.choices.size() == 1)
            {
                // A single choice cannot be expressed as a choice parameter;
                // the parameter is a constant, so it is exposed as a switch
                // the host can still read.
                return std::make_unique<juce::AudioParameterBool> (id, name, false);
            }

            return nullptr;
        }
    }

    return nullptr;
}

juce::AudioProcessorValueTreeState::ParameterLayout
AuralisPlugin::buildParameterLayout (const std::vector<HostParam>& params)
{
    juce::AudioProcessorValueTreeState::ParameterLayout layout;

    // The registry groups a module's parameters, so the host shows them
    // as nested parameter groups in the same shape the module has.
    juce::StringArray groupNames;
    std::vector<std::vector<std::unique_ptr<juce::RangedAudioParameter>>> groupMembers;

    for (const HostParam& param : params)
    {
        auto juceParameter = createParameter (param);

        if (juceParameter == nullptr)
            continue;

        const juce::String groupName = param.group.isNotEmpty() ? param.group : juce::String ("Main");

        int groupIndex = groupNames.indexOf (groupName);

        if (groupIndex < 0)
        {
            groupIndex = (int) groupNames.size();
            groupNames.add (groupName);
            groupMembers.emplace_back();
        }

        groupMembers[(size_t) groupIndex].push_back (std::move (juceParameter));
    }

    for (size_t g = 0; g < groupNames.size(); ++g)
    {
        if (groupMembers[g].empty())
            continue;

        // The group id has to be unique and should not look like a
        // parameter id, so it carries a prefix.
        auto group = std::make_unique<juce::AudioProcessorParameterGroup> (
            juce::String ("grp") + juce::String ((int) g), groupNames[g], juce::String (" | "));

        for (auto& member : groupMembers[g])
            group->addChild (std::move (member));

        layout.add (std::move (group));
    }

    return layout;
}

// ===========================================================================
//  the adapter
// ===========================================================================

AuralisPlugin::AuralisPlugin()
    : identity_ (readEmbeddedIdentity()),
      params_ (buildHostParams (identity_)),
      valueTree_ (*this, nullptr, juce::Identifier ("AURALIS"), buildParameterLayout (params_))
{
    // The bypass switch.  The id is the one every format of the family
    // uses, so a host's automation map lines up with ours.
    bypassParameter_ = new juce::AudioParameterBool (
        juce::ParameterID (juce::String (1652125811), 1), "Bypass", false);

    if (! identity_.valid)
    {
        juce::Logger::writeToLog ("Auralis: this binary carries no identity record.  "
                                   "It was not stamped, so it does not implement any module.");
        return;
    }

    juce::Logger::writeToLog ("Auralis: " + identity_.moduleCode + " ("
                                 + identity_.displayName + ") with "
                                 + juce::String ((int) params_.size()) + " parameter(s), class id "
                                 + identity_.vst3Fuid);
}

AuralisPlugin::~AuralisPlugin()
{
    releaseResources();

    delete bypassParameter_;
    bypassParameter_ = nullptr;
}

juce::AudioProcessorParameter* AuralisPlugin::getBypassParameter() const
{
    return bypassParameter_;
}

const juce::String AuralisPlugin::getName() const
{
    // The stamped identity is the source of truth; the compile-time plug-in
    // name is only a fallback for an unstamped template.
    return identity_.productName.isNotEmpty() ? identity_.productName
                                              : juce::String ("Auralis");
}

double AuralisPlugin::getTailLengthSeconds() const
{
    // The core DOES report the tail through the plug-in ABI:
    // aur_engine_tail_samples answers it.  A module declares how long it
    // keeps ringing after the input stopped (Module.tail_samples), and
    // the engine reports the LONGEST tail of the chain - the modules of a
    // chain are clocked together, block by block, so a host waits for the
    // longer of the two tails and not for their sum.
    //
    // A module that makes a sound of its own - a generator, a bed of
    // ambience - never goes quiet, and the core says so with
    // AUR_TAIL_INFINITE.  The answer is a duration, so it is divided by
    // the sample rate; the tail counts from the end of the module's own
    // latency, so the time from the input stopping until the output is
    // quiet is the latency plus this.
    //
    // The answer comes from the core, so the call reaches Python and
    // belongs on the message thread - the same thread the host asks for
    // the latency on.
    if (engine_ == nullptr)
        return 0.0;

    const int64_t tail = aur_engine_tail_samples (engine_);

    if (tail < 0)
        return 0.0;

    if (tail >= AUR_TAIL_INFINITE)
        return std::numeric_limits<double>::infinity();

    const double rate = getSampleRate();

    if (rate <= 0.0)
        return 0.0;

    return (double) tail / rate;
}

const juce::String AuralisPlugin::getProgramName (int index)
{
    return index == 0 ? getName() : juce::String();
}

juce::AudioProcessorEditor* AuralisPlugin::createEditor()
{
    // The Auralis editor belongs to the user interface layer, which is not
    // part of the adapter.  The host shows its own generic editor.
    return nullptr;
}

// ---------------------------------------------------------------------------
//  the core engine
// ---------------------------------------------------------------------------

bool AuralisPlugin::createEngine (double sampleRate, int channels)
{
    releaseResources();

    if (! identity_.valid)
        return false;

    engineSampleRate_ = juce::jmax (1.0, sampleRate);
    engineChannels_ = juce::jmax (1, channels);

    engine_ = aur_engine_create ((uint32_t) std::llround (engineSampleRate_),
                                 (uint32_t) engineChannels_);

    if (engine_ == nullptr)
    {
        juce::Logger::writeToLog ("Auralis: could not create the core engine.");
        return false;
    }

    const int index = aur_engine_add_module (engine_, identity_.moduleCode.toRawUTF8());

    if (index < 0)
    {
        juce::Logger::writeToLog ("Auralis: the core refused the module "
                                   + identity_.moduleCode + ": "
                                   + juce::String (aur_engine_last_error (engine_)));
        aur_engine_destroy (engine_);
        engine_ = nullptr;
        return false;
    }

    pushAllParams();

    const int64_t latency = aur_engine_latency_samples (engine_);

    if (latency > 0)
        setLatencySamples ((int) latency);

    return true;
}

void AuralisPlugin::prepareToPlay (double sampleRate, int maximumExpectedSamplesPerBlock)
{
    juce::ignoreUnused (maximumExpectedSamplesPerBlock);

    if (! createEngine (sampleRate, getTotalNumInputChannels()))
        return;

    // A chain document that arrived before the engine existed is restored
    // now that there is an engine to restore it into.  This only happens
    // on the message thread, which is where prepareToPlay is called from.
    if (pendingChain_.isNotEmpty())
    {
        const juce::String chain = pendingChain_;
        pendingChain_.clear();

        if (aur_engine_deserialize (engine_, chain.toRawUTF8()) == AUR_OK)
            pullParamsFromCore();
    }
}

void AuralisPlugin::releaseResources()
{
    if (engine_ != nullptr)
    {
        aur_engine_destroy (engine_);
        engine_ = nullptr;
    }

    engineSampleRate_ = 0.0;
    engineChannels_ = 0;
    hasReportedProcessError_ = false;
}

void AuralisPlugin::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midi)
{
    juce::ignoreUnused (midi);

    juce::ScopedNoDenormals noDenormals;

    if (engine_ == nullptr)
        return;   // nothing to do: the audio passes through unchanged

    const int channels = buffer.getNumChannels();
    const int frames = buffer.getNumSamples();

    if (channels != engineChannels_)
    {
        // The host changed the channel count; build the engine for the
        // buffer it actually handed us, so that this cannot loop.
        if (! createEngine (getSampleRate(), channels))
            return;
    }

    if (frames <= 0)
        return;

    pushAllParams();

    const float* const* const inputs = buffer.getArrayOfReadPointers();
    float* const* const outputs = buffer.getArrayOfWritePointers();

    /* The host's block is the number of samples the host asked for, and it
       is the number of samples the plug-in hands over.  A chain that
       changes the rate -- the resampler -- works at another rate, and the
       core converts its output back to the host's rate, so that the block
       the host gets has the duration of the block it gave. */
    AurBlockCounts counts;
    counts.consumed = 0;
    counts.produced = 0;

    const int error = aur_engine_process (engine_, inputs, outputs, (uint32_t) frames,
                                          (uint32_t) frames, (uint32_t) getSampleRate(),
                                          &counts);

    if (error != AUR_OK && ! hasReportedProcessError_)
    {
        hasReportedProcessError_ = true;

        juce::Logger::writeToLog ("Auralis: the core failed to process a block: "
                                   + juce::String (aur_engine_last_error (engine_)));
    }
}

// ---------------------------------------------------------------------------
//  parameters
// ---------------------------------------------------------------------------

double AuralisPlugin::currentValue (const HostParam& param) const
{
    juce::RangedAudioParameter* const ranged = valueTree_.getParameter (param.parameterId);

    if (ranged == nullptr)
        return param.defaultValue;

    // getValue() is the normalised 0..1 position the host works in and
    // convertFrom0to1() maps it back into natural units (dB, Hz, ms, %).
    // For an enum parameter that "natural" value is the choice index; the
    // choice string itself is what the core expects, and it is looked up
    // in the parameter's choice list.
    return (double) ranged->convertFrom0to1 (ranged->getValue());
}

void AuralisPlugin::pushParam (const HostParam& param, double value)
{
    if (engine_ == nullptr)
        return;

    if (param.kind == ParamKind::Enum)
    {
        const int index = juce::jlimit (0, juce::jmax (0, param.choices.size() - 1),
                                        (int) std::lround (value));

        if (index < 0 || index >= param.choices.size())
            return;

        // The core takes the choice STRING for an enum parameter.
        const juce::String choice = param.choices[index];
        aur_engine_set_param_text (engine_, 0, param.name.toRawUTF8(), choice.toRawUTF8());
        return;
    }

    aur_engine_set_param (engine_, 0, param.name.toRawUTF8(), value);
}

void AuralisPlugin::pushAllParams()
{
    if (engine_ == nullptr || isRestoringState_)
        return;

    for (const HostParam& param : params_)
        pushParam (param, currentValue (param));
}

void AuralisPlugin::pullParamsFromCore()
{
    if (engine_ == nullptr)
        return;

    for (const HostParam& param : params_)
    {
        char buffer[256] = { 0 };

        const int error = aur_engine_get_param (engine_, 0, param.name.toRawUTF8(),
                                                buffer, sizeof (buffer));

        if (error != AUR_OK)
            continue;

        juce::RangedAudioParameter* const ranged = valueTree_.getParameter (param.parameterId);

        if (ranged == nullptr)
            continue;

        if (param.kind == ParamKind::Enum)
        {
            const int index = param.choices.indexOf (juce::String (buffer));

            if (index < 0)
                continue;

            ranged->setValueNotifyingHost (ranged->convertTo0to1 ((float) index));
            continue;
        }

        const double value = juce::String (buffer).getDoubleValue();
        ranged->setValueNotifyingHost (ranged->convertTo0to1 ((float) value));
    }
}

// ---------------------------------------------------------------------------
//  state
// ---------------------------------------------------------------------------

juce::String AuralisPlugin::serialiseChain() const
{
    if (engine_ == nullptr)
        return juce::String();

    char* text = nullptr;
    const int64_t length = aur_engine_serialize (engine_, &text);

    if (length <= 0 || text == nullptr)
    {
        if (text != nullptr)
            aur_engine_free (text);

        return juce::String();
    }

    const juce::String result = juce::String::fromUTF8 (text, (int) length);
    aur_engine_free (text);

    return result;
}

void AuralisPlugin::getStateInformation (juce::MemoryBlock& destData)
{
    destData.reset();

    juce::MemoryOutputStream stream (destData, false);

    stream.writeString (juce::String ("auralis.state.v1"));

    // The core's chain document.  It carries the parameter values in
    // natural units and any learned state the module keeps.
    stream.writeString (serialiseChain());

    // The host-visible parameter state.  This is what the host's own
    // automation refers to, so it is kept verbatim.
    const juce::ValueTree state = valueTree_.copyState();
    const auto xml = state.createXml();

    stream.writeString (xml != nullptr ? xml->toString() : juce::String());
}

void AuralisPlugin::setStateInformation (const void* data, int sizeInBytes)
{
    if (data == nullptr || sizeInBytes <= 0)
        return;

    juce::MemoryInputStream stream (data, (size_t) sizeInBytes, false);

    if (stream.readString() != juce::String ("auralis.state.v1"))
        return;   // not our state; leave the plug-in alone

    const juce::String chain = stream.readString();
    const juce::String xmlText = stream.readString();

    isRestoringState_ = true;

    // Restore the host-visible parameter state first.
    if (xmlText.isNotEmpty())
    {
        const std::unique_ptr<juce::XmlElement> xml (juce::XmlDocument::parse (xmlText));

        if (xml != nullptr)
            valueTree_.replaceState (juce::ValueTree::fromXml (*xml));
    }

    // Then the core chain, which may carry learned state as well.  The
    // core is authoritative, so its values are pulled back into the host
    // parameters afterwards.
    if (chain.isNotEmpty())
    {
        if (engine_ != nullptr)
        {
            if (aur_engine_deserialize (engine_, chain.toRawUTF8()) == AUR_OK)
                pullParamsFromCore();
        }
        else
        {
            // There is no engine yet; the chain is applied in prepareToPlay.
            pendingChain_ = chain;
        }
    }

    isRestoringState_ = false;

    pushAllParams();
}

} // namespace auralis

// ===========================================================================
//  the entry point the plug-in wrapper looks for
// ===========================================================================

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new auralis::AuralisPlugin();
}
