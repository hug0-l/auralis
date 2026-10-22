/*
    AuralisAAXProbe.cpp - the verification probe of the AAX adapter.

    WHAT THIS IS
    ------------
    There is no AAX host on this machine, so the adapter cannot be loaded
    into one.  What CAN be done is done here: the probe builds the very
    same data model the plug-in builds, drives it through the format's own
    interfaces and writes down what it finds.

    It reads back

      * the component descriptors - the channel configurations the plug-in
        registers, with their effect ids;
      * the parameter list, through the format's own parameter interface:
        the count, the identifiers, the names, the types, the number of
        steps, the defaults and the range of every parameter;
      * the page table, through the format's own page table interface: the
        pages and the parameters that are bound to them;
      * the state chunk: its identifier, its size and the bytes it holds.

    The page table is the one object the host owns, so the probe provides
    an in-memory stand-in for it - the same way a host would - and the
    adapter fills that stand-in through the format's own interface.

    The probe writes a JSON report; tests/test_aax_adapter.py compares it
    with the canonical table.
*/

#include "AuralisAAX.h"
#include "AuralisAAXHostDouble.h"

#include <AAX_CParameter.h>
#include <AAX_CString.h>
#include <AAX_IACFPageTable.h>
#include <AAX_Properties.h>
#include <AAX_UIDs.h>
#include <ACF/CACFUnknown.h>
#include <ACF/acfextras.h>

#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>

// ===========================================================================
//  the page table stand-in
// ===========================================================================

class ProbePageTable : public AAX_IACFPageTable_V2, public CACFUnknown
{
public:
    ACF_DECLARE_STANDARD_UNKNOWN()

    ACFMETHOD(InternalQueryInterface)(const acfIID& riid, void** ppvObjOut) override
    {
        if (ppvObjOut == nullptr)
            return ACF_E_INVALIDARG;

        *ppvObjOut = nullptr;

        if (riid == IID_IAAXPageTableV1 || riid == IID_IAAXPageTableV2)
        {
            *ppvObjOut = static_cast<AAX_IACFPageTable_V2*> (this);
            return ACF_OK;
        }

        return this->CACFUnknown::InternalQueryInterface (riid, ppvObjOut);
    }

    // -- AAX_IACFPageTable ------------------------------------------------
    AAX_Result Clear() override
    {
        mPages.clear();
        return AAX_SUCCESS;
    }

    AAX_Result Empty(AAX_CBoolean& oEmpty) const override
    {
        oEmpty = mPages.empty() ? 1 : 0;
        return AAX_SUCCESS;
    }

    AAX_Result GetNumPages(int32_t& oNumPages) const override
    {
        oNumPages = static_cast<int32_t> (mPages.size());
        return AAX_SUCCESS;
    }

    AAX_Result InsertPage(int32_t iPage) override
    {
        mPages[iPage];
        return AAX_SUCCESS;
    }

    AAX_Result RemovePage(int32_t iPage) override
    {
        mPages.erase (iPage);
        return AAX_SUCCESS;
    }

    AAX_Result GetNumMappedParameterIDs(int32_t iPage, int32_t& oNumParameterIdentifiers) const override
    {
        const auto found = mPages.find (iPage);

        oNumParameterIdentifiers = (found == mPages.end())
                                 ? 0 : static_cast<int32_t> (found->second.size());
        return AAX_SUCCESS;
    }

    AAX_Result ClearMappedParameter(int32_t iPage, int32_t iIndex) override
    {
        const auto found = mPages.find (iPage);

        if (found != mPages.end() && iIndex >= 0
            && iIndex < static_cast<int32_t> (found->second.size()))
        {
            found->second[static_cast<size_t> (iIndex)].clear();
        }
        return AAX_SUCCESS;
    }

    AAX_Result GetMappedParameterID(int32_t iPage, int32_t iIndex, AAX_IString& oParameterIdentifier) const override
    {
        const auto found = mPages.find (iPage);

        if (found == mPages.end() || iIndex < 0
            || iIndex >= static_cast<int32_t> (found->second.size()))
        {
            return AAX_ERROR_INVALID_ARGUMENT;
        }

        oParameterIdentifier.Set (found->second[static_cast<size_t> (iIndex)].c_str());
        return AAX_SUCCESS;
    }

    AAX_Result MapParameterID(AAX_CPageTableParamID iParameterIdentifier, int32_t iPage, int32_t iIndex) override
    {
        std::vector<std::string>& page = mPages[iPage];

        if (iIndex < 0)
            return AAX_ERROR_INVALID_ARGUMENT;

        if (static_cast<size_t> (iIndex) >= page.size())
            page.resize (static_cast<size_t> (iIndex) + 1);

        page[static_cast<size_t> (iIndex)] = (iParameterIdentifier != nullptr) ? iParameterIdentifier : "";
        return AAX_SUCCESS;
    }

    AAX_Result GetNumParametersWithNameVariations(int32_t& oNumParameterIdentifiers) const override
    {
        oNumParameterIdentifiers = 0;
        return AAX_SUCCESS;
    }

    AAX_Result GetNameVariationParameterIDAtIndex(int32_t, AAX_IString&) const override
    {
        return AAX_ERROR_INVALID_ARGUMENT;
    }

    AAX_Result GetNumNameVariationsForParameter(AAX_CPageTableParamID, int32_t& oNumVariations) const override
    {
        oNumVariations = 0;
        return AAX_SUCCESS;
    }

    AAX_Result GetParameterNameVariationAtIndex(AAX_CPageTableParamID, int32_t, AAX_IString&, int32_t&) const override
    {
        return AAX_ERROR_INVALID_ARGUMENT;
    }

    AAX_Result GetParameterNameVariationOfLength(AAX_CPageTableParamID, int32_t, AAX_IString&) const override
    {
        return AAX_ERROR_INVALID_ARGUMENT;
    }

    AAX_Result ClearParameterNameVariations() override
    {
        return AAX_SUCCESS;
    }

    AAX_Result ClearNameVariationsForParameter(AAX_CPageTableParamID) override
    {
        return AAX_SUCCESS;
    }

    AAX_Result SetParameterNameVariation(AAX_CPageTableParamID, const AAX_IString&, int32_t) override
    {
        return AAX_SUCCESS;
    }

    // -- the report -------------------------------------------------------
    const std::map<int, std::vector<std::string> >& pages() const { return mPages; }

    /** The unknown the host would hand to the plug-in. */
    IACFUnknown* unknown() { return GetPrivateUnknown(); }

private:
    std::map<int, std::vector<std::string> > mPages;
};

// ===========================================================================
//  a very small JSON writer
// ===========================================================================

namespace
{

/** A trace of the probe's own steps; the report says where it got to. */
void trace (const char* text)
{
    std::fprintf (stderr, "aaxprobe: %s\n", text);
}

std::string quote (const std::string& text)
{
    std::string out = "\"";

    for (char c : text)
    {
        switch (c)
        {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:
                if (static_cast<unsigned char> (c) < 0x20)
                {
                    char buffer[8];
                    std::snprintf (buffer, sizeof (buffer), "\\u%04x", c);
                    out += buffer;
                }
                else
                {
                    out += c;
                }
                break;
        }
    }

    out += "\"";
    return out;
}

std::string number (double value)
{
    char buffer[64];
    std::snprintf (buffer, sizeof (buffer), "%.10g", value);
    return std::string (buffer);
}

/** Return a four character code as the text it stands for. */
std::string fourCC (AAX_CTypeID value)
{
    std::string text;

    for (int shift = 24; shift >= 0; shift -= 8)
    {
        const char character = static_cast<char> ((value >> shift) & 0xFF);
        text += (character >= 0x20 && character < 0x7F) ? character : '?';
    }

    return text;
}

} // namespace (anonymous)

// ===========================================================================
//  the probe
// ===========================================================================

int main (int argc, char** argv)
{
    std::string moduleCode;
    std::string outputPath;
    std::string identityPath;

    for (int index = 1; index < argc; ++index)
    {
        const std::string argument (argv[index]);

        if (argument == "--module" && index + 1 < argc)
        {
            moduleCode = argv[++index];
            continue;
        }

        if (argument == "--out" && index + 1 < argc)
        {
            outputPath = argv[++index];
            continue;
        }

        if (argument == "--identity" && index + 1 < argc)
        {
            identityPath = argv[++index];
            continue;
        }
    }

    if (moduleCode.empty())
    {
        if (auralisAAXFamily.moduleCount == 0)
        {
            std::fprintf (stderr, "aaxprobe: the family is empty\n");
            return 2;
        }
        moduleCode = auralisAAXFamily.modules[0].code;
    }

    const AuralisAAXModule* const module = auralisAAXModuleForCode (moduleCode);

    if (module == nullptr)
    {
        std::fprintf (stderr, "aaxprobe: unknown module %s\n", moduleCode.c_str());
        return 2;
    }

    // -- the data model ---------------------------------------------------
    trace ("building the data model");

    AuralisAAX_Processor processor (module);

    trace ("initialising the data model");

    if (processor.Initialize (nullptr) != AAX_SUCCESS)
    {
        std::fprintf (stderr, "aaxprobe: the data model could not be initialised\n");
        return 1;
    }

    trace ("the data model is up");

    // -- the parameters, through the format's own interface ----------------
    int32_t parameterCount = 0;
    processor.GetNumberOfParameters (&parameterCount);

    trace ("reading the parameters back");

    std::string parameters;
    parameters += "\"parameters\": [";

    for (int32_t index = 0; index < parameterCount; ++index)
    {
        AAX_CString idString;
        processor.GetParameterIDFromIndex (index, &idString);

        const AAX_CParamID id = idString.Get();

        AAX_CString nameString;
        processor.GetParameterName (id, &nameString);

        AAX_EParameterType type = AAX_eParameterType_Continuous;
        processor.GetParameterType (id, &type);

        double normalisedDefault = 0.0;
        processor.GetParameterDefaultNormalizedValue (id, &normalisedDefault);

        AAX_IParameter* parameter = nullptr;
        processor.GetParameter (id, &parameter);

        double minimum = 0.0;
        double maximum = 0.0;
        double value = 0.0;
        uint32_t steps = 0;
        bool automatable = false;

        if (parameter != nullptr)
        {
            steps = parameter->GetNumberOfSteps();
            automatable = parameter->Automatable();

            float current = 0.0f;
            if (parameter->GetValueAsFloat (&current))
                value = static_cast<double> (current);

            // The taper delegate is what carries the range of a parameter;
            // the adapter builds every parameter as a float parameter, so
            // the cast is the one the adapter itself makes.
            const AAX_CParameter<float>* const typed =
                static_cast<const AAX_CParameter<float>*> (parameter);

            if (typed->TaperDelegate() != nullptr)
            {
                minimum = static_cast<double> (typed->TaperDelegate()->GetMinimumValue());
                maximum = static_cast<double> (typed->TaperDelegate()->GetMaximumValue());
            }

            parameter->Release();
        }

        // The registry id, the unit, the choices and the label come from the
        // description the adapter was built from.
        const AuralisAAXParameter* source = nullptr;

        for (int i = 0; i < module->parameterCount; ++i)
        {
            if (auralisAAXParamId (module->parameters[i].id) == std::string (id))
            {
                source = &module->parameters[i];
                break;
            }
        }

        if (index > 0)
            parameters += ",";

        parameters += "{";
        parameters += "\"id\": " + quote (idString.Get());
        parameters += ", \"registry_id\": " + std::to_string (source != nullptr ? source->id : -1);
        parameters += ", \"name\": " + quote (nameString.Get());
        parameters += ", \"type\": " + quote (type == AAX_eParameterType_Discrete ? "discrete" : "continuous");
        parameters += ", \"steps\": " + std::to_string (steps);
        parameters += ", \"automatable\": " + std::string (automatable ? "true" : "false");
        parameters += ", \"minimum\": " + number (minimum);
        parameters += ", \"maximum\": " + number (maximum);
        parameters += ", \"value\": " + number (value);
        parameters += ", \"normalised_default\": " + number (normalisedDefault);

        if (source != nullptr)
        {
            parameters += ", \"source_type\": " + quote (source->type);
            parameters += ", \"unit\": " + quote (source->unit);
            parameters += ", \"label\": " + quote (source->label);
            parameters += ", \"group\": " + quote (source->group);
            parameters += ", \"declared_minimum\": " + number (source->minimum);
            parameters += ", \"declared_maximum\": " + number (source->maximum);
            parameters += ", \"declared_default\": " + number (source->defaultValue);

            parameters += ", \"choices\": [";
            for (int i = 0; i < source->choiceCount; ++i)
            {
                if (i > 0)
                    parameters += ",";
                parameters += quote (source->choices[i]);
            }
            parameters += "]";
        }

        parameters += "}";
    }

    parameters += "]";

    // -- the page table, through the format's own interface ---------------
    trace ("filling the page table");

    ProbePageTable pageTable;

    processor.UpdatePageTable (0, 0, nullptr, pageTable.unknown());

    trace ("the page table is filled");

    std::string pages;
    pages += "\"pages\": [";

    bool first = true;

    for (const auto& page : pageTable.pages())
    {
        if (! first)
            pages += ",";
        first = false;

        pages += "{\"index\": " + std::to_string (page.first) + ", \"controls\": [";

        for (size_t index = 0; index < page.second.size(); ++index)
        {
            if (index > 0)
                pages += ",";

            pages += "{\"slot\": " + std::to_string (index) +
                     ", \"parameter\": " + quote (page.second[index]) + "}";
        }

        pages += "]}";
    }

    pages += "]";

    // -- the state chunk --------------------------------------------------
    trace ("reading the state chunk");

    int32_t chunkCount = 0;
    processor.GetNumberOfChunks (&chunkCount);

    AAX_CTypeID chunkId = 0;
    processor.GetChunkIDFromIndex (0, &chunkId);

    uint32_t chunkSize = 0;
    processor.GetChunkSize (chunkId, &chunkSize);

    // The chunk is a header followed by its data, so the buffer has to be
    // big enough for both: the host allocates the same way.
    std::vector<char> chunkBuffer (sizeof (AAX_SPlugInChunk) + chunkSize + 64, 0);
    AAX_SPlugInChunk* const chunk = reinterpret_cast<AAX_SPlugInChunk*> (chunkBuffer.data());

    processor.GetChunk (chunkId, chunk);

    const std::string marker (chunk->fData,
                              std::min<size_t> (chunkSize, std::strlen (chunk->fData)));

    std::string state;
    state += "\"chunk\": {";
    state += "\"count\": " + std::to_string (chunkCount);
    state += ", \"id\": " + std::to_string (chunkId);
    state += ", \"size\": " + std::to_string (chunkSize);
    state += ", \"marker\": " + quote (marker);
    state += ", \"parameter_state\": " + quote (processor.parameterState());
    state += "}";

    // -- the component descriptors ---------------------------------------
    std::string components;
    components += "\"components\": [";

    const std::vector<AuralisAAXComponent> described = auralisAAXComponents (*module);

    for (size_t index = 0; index < described.size(); ++index)
    {
        if (index > 0)
            components += ",";

        components += "{";
        components += "\"plug_id\": " + quote (described[index].plugId);
        components += ", \"input_channels\": " + std::to_string (described[index].inputChannels);
        components += ", \"output_channels\": " + std::to_string (described[index].outputChannels);
        components += ", \"manufacturer_id\": " + quote (described[index].manufacturerId);
        components += ", \"product_id\": " + quote (described[index].productId);
        components += ", \"effect_id\": " + quote (described[index].effectId);
        components += "}";
    }

    components += "]";

    // -- the description the plug-in registers with the host -------------
    trace ("describing the plug-in into the host");

    AuralisAAXHostCollection collection;

    if (AuralisAAXDescribe (&collection, module) != AAX_SUCCESS)
    {
        std::fprintf (stderr, "aaxprobe: the plug-in could not describe itself\n");
        return 1;
    }

    std::string description;
    description += "\"description\": {";
    description += "\"manufacturer_name\": " + quote (collection.manufacturerName());
    description += ", \"package_names\": [";

    for (size_t index = 0; index < collection.packageNames().size(); ++index)
    {
        if (index > 0)
            description += ",";
        description += quote (collection.packageNames()[index]);
    }

    description += "], \"effects\": [";

    for (size_t index = 0; index < collection.effectIds().size(); ++index)
    {
        if (index > 0)
            description += ",";

        const AuralisAAXHostEffectDescriptor* const descriptor =
            static_cast<const AuralisAAXHostEffectDescriptor*> (collection.descriptors()[index]);

        description += "{\"effect_id\": " + quote (collection.effectIds()[index]);
        description += ", \"names\": [";

        for (size_t name = 0; name < descriptor->names().size(); ++name)
        {
            if (name > 0)
                description += ",";
            description += quote (descriptor->names()[name]);
        }

        description += "], \"category\": " + std::to_string (descriptor->category());
        description += ", \"proc_ids\": [";

        for (size_t proc = 0; proc < descriptor->procIds().size(); ++proc)
        {
            if (proc > 0)
                description += ",";
            description += std::to_string (static_cast<int> (descriptor->procIds()[proc]));
        }

        // The resources the plug-in declared.  The page table is one of
        // them: a document in the bundle, whose name the host reads out of
        // Contents/Resources.
        description += "], \"resources\": [";

        for (size_t resource = 0; resource < descriptor->resourceInfos().size(); ++resource)
        {
            if (resource > 0)
                description += ",";

            description += "{\"type\": ";
            description += std::to_string (static_cast<int> (descriptor->resourceTypes()[resource]));
            description += ", \"info\": " + quote (descriptor->resourceInfos()[resource]);
            description += "}";
        }

        description += "], \"components\": [";

        const std::vector<AAX_IComponentDescriptor*>& parts = descriptor->components();

        for (size_t part = 0; part < parts.size(); ++part)
        {
            if (part > 0)
                description += ",";

            const AuralisAAXHostComponentDescriptor* const component =
                static_cast<const AuralisAAXHostComponentDescriptor*> (parts[part]);

            description += "{\"audio_in\": " + std::to_string (component->audioIn().size());
            description += ", \"audio_out\": " + std::to_string (component->audioOut().size());
            description += ", \"buffer_length\": " + std::to_string (component->bufferLength().size());
            description += ", \"data_in\": " + std::to_string (component->dataIn().size());
            description += ", \"private_data\": " + std::to_string (component->privateData().size());
            description += ", \"process_proc\": " + std::string (component->hasProcessProc() ? "true" : "false");
            description += ", \"instance_init_proc\": " + std::string (component->hasInstanceInitProc() ? "true" : "false");

            const AuralisAAXHostPropertyMap* const properties =
                static_cast<const AuralisAAXHostPropertyMap*> (component->properties());

            if (properties != nullptr)
            {
                description += ", \"manufacturer_id\": " + quote (fourCC (
                    static_cast<AAX_CTypeID> (properties->value (AAX_eProperty_ManufacturerID))));
                description += ", \"product_id\": " + quote (fourCC (
                    static_cast<AAX_CTypeID> (properties->value (AAX_eProperty_ProductID))));
                description += ", \"plug_in_id\": " + quote (fourCC (
                    static_cast<AAX_CTypeID> (properties->value (AAX_eProperty_PlugInID_Native))));
                description += ", \"input_stem_format\": " + std::to_string (
                    properties->value (AAX_eProperty_InputStemFormat));
                description += ", \"output_stem_format\": " + std::to_string (
                    properties->value (AAX_eProperty_OutputStemFormat));
                description += ", \"can_bypass\": " + std::to_string (
                    properties->value (AAX_eProperty_CanBypass));
            }

            description += "}";
        }

        description += "]}";
    }

    description += "]}";

    // -- the identity a stamped binary carries ----------------------------
    std::string identity;

    if (! identityPath.empty())
    {
        trace ("reading the identity out of the binary");

        std::FILE* const file = std::fopen (identityPath.c_str(), "rb");

        if (file == nullptr)
        {
            std::fprintf (stderr, "aaxprobe: cannot read %s\n", identityPath.c_str());
            return 1;
        }

        std::fseek (file, 0, SEEK_END);
        const long size = std::ftell (file);
        std::fseek (file, 0, SEEK_SET);

        std::vector<char> data (size > 0 ? static_cast<size_t> (size) : 0, 0);

        if (! data.empty())
            std::fread (data.data(), 1, data.size(), file);

        std::fclose (file);

        // The slot is the marker followed by the region; the region is
        // NUL padded, so the record ends at the first NUL.
        const std::string marker ("AURIDENTITYv1");
        const std::string content (data.begin(), data.end());
        const size_t at = content.find (marker);

        if (at == std::string::npos)
        {
            std::fprintf (stderr, "aaxprobe: %s carries no identity slot\n", identityPath.c_str());
            return 1;
        }

        const size_t start = at + marker.size();
        size_t end = content.find ('\0', start);
        const size_t length = (end == std::string::npos) ? (data.size() - start) : (end - start);

        const AuralisAAXIdentity record = auralisAAXParseIdentity (data.data() + start, length);

        identity += "\"identity\": {";
        identity += "\"valid\": " + std::string (record.valid ? "true" : "false");
        identity += ", \"module_code\": " + quote (record.moduleCode);
        identity += ", \"display_name\": " + quote (record.displayName);
        identity += ", \"category\": " + quote (record.category);
        identity += ", \"product_name\": " + quote (record.productName);
        identity += ", \"effect_ids\": [";

        for (size_t index = 0; index < record.effectIds.size(); ++index)
        {
            if (index > 0)
                identity += ",";
            identity += quote (record.effectIds[index]);
        }

        identity += "]}";
    }

    // -- the report -------------------------------------------------------
    std::string report;
    report += "{";
    report += "\"module\": " + quote (module->code);
    report += ", \"display_name\": " + quote (module->displayName);
    report += ", \"category\": " + quote (module->category);
    report += ", \"manufacturer_id\": " + quote (module->manufacturerId);
    report += ", \"product_id\": " + quote (module->productId);
    report += ", \"param_id_base\": " + std::to_string (kAuralisAAXParamIdBase);
    if (! identity.empty())
    {
        report += ", " + identity;
    }
    report += ", " + components;
    report += ", " + description;
    report += ", " + parameters;
    report += ", " + pages;
    report += ", " + state;
    report += "}";

    if (outputPath.empty())
    {
        std::printf ("%s\n", report.c_str());
    }
    else
    {
        std::FILE* const file = std::fopen (outputPath.c_str(), "wb");

        if (file == nullptr)
        {
            std::fprintf (stderr, "aaxprobe: cannot write %s\n", outputPath.c_str());
            return 1;
        }

        std::fwrite (report.data(), 1, report.size(), file);
        std::fclose (file);
    }

    processor.Uninitialize();

    return 0;
}
