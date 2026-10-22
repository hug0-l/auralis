/*
    AuralisAAX.cpp - the Auralis AAX adapter, written directly against the
    AAX SDK.

    See AuralisAAX.h for the architecture and plugins/aax/README.md for the
    licence, the registration and what this adapter does and does not prove.

    THE THREE PIECES
    ----------------
      * the description: the effect ids, the component descriptors, the
        parameter list and the page tables.  It comes from the generated
        header, which comes from the canonical parameter table;
      * the data model: AuralisAAX_Processor, which derives from the SDK's
        AAX_CEffectParameters and owns the parameters, the state chunk and
        the page table;
      * the controller: AuralisAAX_Controller, which derives from the SDK's
        AAX_CEffectGUI.

    THE ENTRY POINT
    ---------------
    GetEffectDescriptions() is what the SDK calls: AAX_Init.cpp builds a
    collection and hands it over, and the function describes the plug-in
    into it.  One binary describes one module, in three channel
    configurations, and each configuration is an effect of its own with its
    own effect id.
*/

#include "AuralisAAX.h"

#include "aurcore_c_api.h"

#include <AAX_CLinearTaperDelegate.h>
#include <AAX_CNumberDisplayDelegate.h>
#include <AAX_CStateDisplayDelegate.h>
#include <AAX_CUnitDisplayDelegateDecorator.h>
#include <AAX_Properties.h>
#include <AAX_UIDs.h>
#include <AAX_CString.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <mutex>
#include <sstream>

#if defined(_WIN32)
  #define WIN32_LEAN_AND_MEAN
  #include <windows.h>
#else
  #include <dlfcn.h>
#endif

// The generated description.  AuralisAAXGenerated.h is written by
// scripts/build_aax.py from docs/aax_page_tables.json.
#include "AuralisAAXGenerated.h"

namespace
{

// ===========================================================================
//  the identity slot
// ===========================================================================
//
//  The build patches the identity record into the file AFTER the module has
//  been linked, so the contents of the slot are only known when the module
//  runs.  The slot is a const object with a constant initialiser, so the
//  linker stores it in the file; a zero initialised object would go to
//  .bss, which is not part of the file on disk and could therefore not be
//  patched.  The sentinel byte in the last position of the region keeps the
//  region from looking like an all-zero block.

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

// ===========================================================================
//  a very small JSON reader
// ===========================================================================
//
//  The identity record is a JSON document, and the adapter needs four
//  strings and one array of triples out of it.  A full parser would be more
//  code than the thing it reads, so the reader below walks the document and
//  picks out the fields it is asked for.  It understands strings, numbers,
//  objects, arrays and the escapes a JSON string may carry.

const char* skipSpace (const char* p)
{
    while (p != nullptr && *p != '\0')
    {
        if (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')
        {
            ++p;
            continue;
        }
        break;
    }
    return p;
}

/** Read a JSON string.  The cursor has to point at the opening quote. */
bool readJsonString (const char*& p, std::string& out)
{
    out.clear();

    p = skipSpace (p);

    if (p == nullptr || *p != '"')
        return false;

    ++p;

    while (*p != '\0' && *p != '"')
    {
        if (*p == '\\' && p[1] != '\0')
        {
            ++p;
            switch (*p)
            {
                case 'n': out += '\n'; break;
                case 't': out += '\t'; break;
                case 'r': out += '\r'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case '/': out += '/';  break;
                case '"': out += '"';  break;
                case '\\': out += '\\'; break;
                case 'u':
                {
                    // Only the ASCII range is decoded; the identity record
                    // carries module codes and display names.
                    if (p[1] == '\0' || p[2] == '\0' || p[3] == '\0')
                        return false;
                    const std::string hex (p + 1, 4);
                    const long code = std::strtol (hex.c_str(), nullptr, 16);
                    if (code > 0 && code < 128)
                        out += static_cast<char> (code);
                    p += 4;
                    break;
                }
                default:
                    out += *p;
                    break;
            }
            ++p;
            continue;
        }
        out += *p;
        ++p;
    }

    if (*p != '"')
        return false;

    ++p;
    return true;
}

/** Skip a JSON value. */
bool skipJsonValue (const char*& p)
{
    p = skipSpace (p);

    if (p == nullptr || *p == '\0')
        return false;

    if (*p == '"')
    {
        std::string ignored;
        return readJsonString (p, ignored);
    }

    if (*p == '{' || *p == '[')
    {
        const char open = *p;
        const char close = (open == '{') ? '}' : ']';
        int depth = 0;
        bool inString = false;

        while (*p != '\0')
        {
            if (inString)
            {
                if (*p == '\\' && p[1] != '\0')
                {
                    p += 2;
                    continue;
                }
                if (*p == '"')
                    inString = false;
                ++p;
                continue;
            }
            if (*p == '"')
            {
                inString = true;
                ++p;
                continue;
            }
            if (*p == open)
                ++depth;
            else if (*p == close)
            {
                --depth;
                if (depth == 0)
                {
                    ++p;
                    return true;
                }
            }
            ++p;
        }
        return false;
    }

    while (*p != '\0' && *p != ',' && *p != '}' && *p != ']')
        ++p;

    return true;
}

/** Find a member of the top level object and return the cursor at its value.

    The walk goes through the object member by member, so a key name that
    happens to appear inside a value is not mistaken for the member. */
bool findJsonMember (const char* text, const char* key, const char*& value)
{
    const char* p = skipSpace (text);

    if (p == nullptr || *p != '{')
        return false;

    ++p;

    for (;;)
    {
        p = skipSpace (p);

        if (*p == '}' || *p == '\0')
            return false;

        std::string name;

        if (! readJsonString (p, name))
            return false;

        p = skipSpace (p);

        if (*p != ':')
            return false;

        ++p;

        const char* const start = skipSpace (p);

        if (name == key)
        {
            value = start;
            return value != nullptr && *value != '\0';
        }

        if (! skipJsonValue (p))
            return false;

        p = skipSpace (p);

        if (*p != ',')
            return false;

        ++p;
    }
}

/** Read a string member of the top level object. */
bool readJsonMemberString (const char* text, const char* key, std::string& out)
{
    const char* p = nullptr;
    if (! findJsonMember (text, key, p))
        return false;
    return readJsonString (p, out);
}

/** Read the array of string triples of the top level object. */
bool readJsonMemberTriples (const char* text, const char* key,
                            std::vector<std::vector<std::string> >& out)
{
    out.clear();

    const char* p = nullptr;
    if (! findJsonMember (text, key, p))
        return false;

    if (*p != '[')
        return false;

    ++p;

    for (;;)
    {
        p = skipSpace (p);

        if (*p == ']')
            return true;

        if (*p != '[')
            return false;

        ++p;

        std::vector<std::string> triple;

        for (;;)
        {
            p = skipSpace (p);
            std::string item;
            if (! readJsonString (p, item))
                return false;
            triple.push_back (item);
            p = skipSpace (p);
            if (*p == ',')
            {
                ++p;
                continue;
            }
            if (*p == ']')
            {
                ++p;
                break;
            }
            return false;
        }

        out.push_back (triple);

        p = skipSpace (p);
        if (*p == ',')
        {
            ++p;
            continue;
        }
        if (*p == ']')
            return true;
        return false;
    }
}

// ===========================================================================
//  the core bridge
// ===========================================================================
//
//  The core is not inside the plug-in: the family shares one copy of it, in
//  the runtime directory next to the bundles.  The plug-in loads it itself,
//  so a static import is not an option - the loader resolves an import when
//  the module is loaded and it never looks outside the bundle.
//
//  The rule that finds the runtime, from the plug-in's own directory:
//
//    1. AURALIS_RUNTIME_DIR, when it is set;
//    2. a directory called "Auralis Runtime" next to the bundle;
//    3. one in the directory above that;
//    4. one in the directory above that.
//
//  The same rule is implemented by plugins/vst3/stage_runtime.py, so the
//  tool and the plug-in always agree.

struct CoreApi
{
    AurEngine* (*create) (uint32_t, uint32_t);
    int  (*add_module) (AurEngine*, const char*);
    int  (*set_param) (AurEngine*, uint32_t, const char*, double);
    int  (*set_param_text) (AurEngine*, uint32_t, const char*, const char*);
    int  (*get_param) (AurEngine*, uint32_t, const char*, char*, size_t);
    int  (*set_bypass) (AurEngine*, uint32_t, int);
    int  (*process) (AurEngine*, const float* const*, float* const*, uint32_t);
    int  (*module_count) (const AurEngine*);
    int64_t (*latency_samples) (const AurEngine*);
    int64_t (*tail_samples) (const AurEngine*);
    int64_t (*serialize) (const AurEngine*, char**);
    int  (*deserialize) (AurEngine*, const char*);
    void (*destroy) (AurEngine*);
    void (*free_text) (char*);
    const char* (*last_error) (const AurEngine*);
    const char* (*version) (void);
    bool loaded;
};

#if defined(_WIN32)

std::wstring moduleDirectory()
{
    HMODULE self = nullptr;

    const DWORD flags = GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                        GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT;

    if (! GetModuleHandleExW (flags, reinterpret_cast<LPCWSTR> (&moduleDirectory), &self))
        return std::wstring();

    wchar_t path[1024] = { 0 };
    const DWORD length = GetModuleFileNameW (self, path, 1024);

    if (length == 0 || length >= 1024)
        return std::wstring();

    std::wstring text (path, length);
    const size_t last = text.find_last_of (L"\\");

    return (last == std::wstring::npos) ? std::wstring() : text.substr (0, last);
}

std::wstring runtimeDirectory()
{
    // 1. the environment
    wchar_t buffer[1200] = { 0 };
    const DWORD length = GetEnvironmentVariableW (L"AURALIS_RUNTIME_DIR", buffer, 1200);

    if (length > 0 && length < 1200)
        return std::wstring (buffer, length);

    // 2. and 3. a directory called "Auralis Runtime" next to the bundle, or
    //    in one of the directories above it
    std::wstring path = moduleDirectory();

    if (path.empty())
        return std::wstring();

    for (int level = 0; level < 3; ++level)
    {
        std::wstring candidate = path + L"\\Auralis Runtime";

        if (GetFileAttributesW (candidate.c_str()) != INVALID_FILE_ATTRIBUTES)
            return candidate;

        const size_t last = path.find_last_of (L"\\");

        if (last == std::wstring::npos)
            break;

        path = path.substr (0, last);
    }

    return std::wstring();
}

void* openLibrary (const std::wstring& path)
{
    return reinterpret_cast<void*> (LoadLibraryW (path.c_str()));
}

void* librarySymbol (void* handle, const char* name)
{
    return reinterpret_cast<void*> (GetProcAddress (static_cast<HMODULE> (handle), name));
}

#else

void* openLibrary (const std::string& path)
{
    return dlopen (path.c_str(), RTLD_NOW | RTLD_LOCAL);
}

void* librarySymbol (void* handle, const char* name)
{
    return dlsym (handle, name);
}

#endif

/** Load the core, once. */
const CoreApi& coreApi()
{
    static CoreApi api = {};
    static bool tried = false;

    if (tried)
        return api;

    tried = true;

#if defined(_WIN32)
    const std::wstring directory = runtimeDirectory();

    if (directory.empty())
        return api;

    void* handle = openLibrary (directory + L"\\aurcore_abi.dll");
#else
    void* handle = openLibrary ("libaurcore_abi.so");
#endif

    if (handle == nullptr)
        return api;

    api.create          = reinterpret_cast<decltype (api.create)> (librarySymbol (handle, "aur_engine_create"));
    api.add_module      = reinterpret_cast<decltype (api.add_module)> (librarySymbol (handle, "aur_engine_add_module"));
    api.set_param       = reinterpret_cast<decltype (api.set_param)> (librarySymbol (handle, "aur_engine_set_param"));
    api.set_param_text  = reinterpret_cast<decltype (api.set_param_text)> (librarySymbol (handle, "aur_engine_set_param_text"));
    api.get_param       = reinterpret_cast<decltype (api.get_param)> (librarySymbol (handle, "aur_engine_get_param"));
    api.set_bypass      = reinterpret_cast<decltype (api.set_bypass)> (librarySymbol (handle, "aur_engine_set_bypass"));
    api.process         = reinterpret_cast<decltype (api.process)> (librarySymbol (handle, "aur_engine_process"));
    api.module_count    = reinterpret_cast<decltype (api.module_count)> (librarySymbol (handle, "aur_engine_module_count"));
    api.latency_samples = reinterpret_cast<decltype (api.latency_samples)> (librarySymbol (handle, "aur_engine_latency_samples"));
    api.tail_samples    = reinterpret_cast<decltype (api.tail_samples)> (librarySymbol (handle, "aur_engine_tail_samples"));
    api.serialize       = reinterpret_cast<decltype (api.serialize)> (librarySymbol (handle, "aur_engine_serialize"));
    api.deserialize     = reinterpret_cast<decltype (api.deserialize)> (librarySymbol (handle, "aur_engine_deserialize"));
    api.destroy         = reinterpret_cast<decltype (api.destroy)> (librarySymbol (handle, "aur_engine_destroy"));
    api.free_text       = reinterpret_cast<decltype (api.free_text)> (librarySymbol (handle, "aur_engine_free"));
    api.last_error      = reinterpret_cast<decltype (api.last_error)> (librarySymbol (handle, "aur_engine_last_error"));
    api.version         = reinterpret_cast<decltype (api.version)> (librarySymbol (handle, "aur_engine_version"));

    api.loaded = api.create != nullptr && api.add_module != nullptr &&
                 api.set_param != nullptr && api.process != nullptr &&
                 api.destroy != nullptr;

    return api;
}

// ===========================================================================
//  the algorithm context
// ===========================================================================
//
//  The fields of the context have to be pointer sized and free of gaps,
//  because the host addresses them by their index (AAX_FIELD_INDEX divides
//  the offset by the size of a pointer).

/** What one instance of the algorithm owns. */
struct AuralisAAXInstance
{
    AurEngine*     engine;          //!< the chain of the core
    uint32_t       inputChannels;   //!< the channels the host gives
    uint32_t       outputChannels;  //!< the channels the host wants
    std::vector<float> scratch;      //!< the input, widened to the chain's width
};

struct AuralisAAXContext
{
    float**             inputChannels;
    float**             outputChannels;
    int32_t*            bufferSize;
    int32_t*            bypass;
    float*              sampleRate;
    AuralisAAXInstance* instance;
};

/** The engines the running instances use.

    A binary implements exactly one module, so the instances of that module
    in one process share the registry: a parameter change is pushed to every
    engine that is running.  The registry is a plain list, guarded by a
    mutex, because the audio thread and the message thread both touch it. */
class EngineRegistry
{
public:
    static EngineRegistry& instance()
    {
        static EngineRegistry registry;
        return registry;
    }

    void add (AurEngine* engine)
    {
        if (engine == nullptr)
            return;

        std::lock_guard<std::mutex> lock (mMutex);
        mEngines.push_back (engine);
    }

    void remove (AurEngine* engine)
    {
        std::lock_guard<std::mutex> lock (mMutex);

        for (size_t i = 0; i < mEngines.size(); ++i)
        {
            if (mEngines[i] == engine)
            {
                mEngines.erase (mEngines.begin() + static_cast<long> (i));
                return;
            }
        }
    }

    std::vector<AurEngine*> engines() const
    {
        std::lock_guard<std::mutex> lock (mMutex);
        return mEngines;
    }

private:
    mutable std::mutex mMutex;
    std::vector<AurEngine*> mEngines;
};

/** The module the binary implements; the engines are built from it. */
const char* sharedModuleCode()
{
    static const std::string* const code = new std::string ([]
    {
        const AuralisAAXIdentity identity = auralisAAXReadIdentity();
        return identity.moduleCode;
    }());
    return code->c_str();
}

/** What the host told the data model about the instance it created.

    The data model publishes it here, because the algorithm instances are
    separate objects: the host creates them, and they have to find the
    chain the data model is driving. */
uint32_t gSampleRate = 48000;
uint32_t gChainChannels = 2;
uint32_t gInputChannels = 2;
uint32_t gOutputChannels = 2;

/** The sample rate the engines are built with.

    The host tells the data model the rate, and the data model tells the
    engines; the algorithm instance reads it from its own context as well,
    which is where the host puts it. */
uint32_t sharedSampleRate()
{
    return gSampleRate;
}

/** The channel count the engines are built with.

    The core's engine has one channel count, so the chain is built with
    the wider of the two channel counts of the configuration the host
    instantiated. */
uint32_t sharedChannelCount()
{
    return gChainChannels;
}

/** The channels the host gives the plug-in. */
uint32_t sharedInputChannels()
{
    return gInputChannels;
}

/** The channels the host wants back. */
uint32_t sharedOutputChannels()
{
    return gOutputChannels;
}

// ===========================================================================
//  the state document
// ===========================================================================
//
//  The parameter state is an XML document with the same shape the other
//  adapter writes:
//
//      <AURALIS>
//        <PARAM id="42" name="Sensitivity" value="0.5" />
//      </AURALIS>
//
//  The name is the identity of a parameter and the id is the position it has
//  inside its module, so a document written before a parameter was inserted
//  in the middle of the module still loads onto the right parameter.  A
//  document that carries only the id is the old format: the id is then read
//  as the position, and a value lands on whatever parameter took that place.
//
//  The children are ordered by the parameter id AS TEXT, which is the order
//  the other adapter's parameter table uses.

constexpr const char* kStateRootTag = "AURALIS";
constexpr const char* kStateEntryTag = "PARAM";

/** Write a double so that it reads back as the same float.

    The shortest text that parses back to the same float is used, so the
    document stays readable and the value survives the round trip. */
std::string serialiseDouble (double input)
{
    const float value = static_cast<float> (input);

    char buffer[64];

    for (int digits = 1; digits <= 9; ++digits)
    {
        std::snprintf (buffer, sizeof (buffer), "%.*g", digits, static_cast<double> (value));

        if (std::strtof (buffer, nullptr) == value)
            return std::string (buffer);
    }

    std::snprintf (buffer, sizeof (buffer), "%.9g", static_cast<double> (value));
    return std::string (buffer);
}

/** One entry of the parameter state document.

    The name is the identity of a parameter; the id is the position the
    parameter has inside its module, and that is the number the host uses.
    A document that carries the name survives an insertion of a parameter in
    the middle of the module; a document that carries only the id does not.
*/
struct ParameterValue
{
    std::string name;    //!< the canonical core name ("" when the entry has none)
    int id = -1;         //!< the host parameter id
    double value = 0.0;  //!< the natural value
};

/** Escape the characters an XML attribute value may not carry. */
std::string escapeXmlText (const std::string& text)
{
    std::string out;
    out.reserve (text.size());

    for (const char c : text)
    {
        switch (c)
        {
            case '&':  out += "&amp;";  break;
            case '<':  out += "&lt;";   break;
            case '>':  out += "&gt;";   break;
            case '"':  out += "&quot;"; break;
            case '\'': out += "&apos;"; break;
            default:   out += c;        break;
        }
    }

    return out;
}

std::string encodeParameterState (const std::vector<ParameterValue>& values)
{
    // The children are ordered by the parameter id AS TEXT.
    std::vector<ParameterValue> ordered (values);
    std::sort (ordered.begin(), ordered.end(), [] (const ParameterValue& a,
                                                   const ParameterValue& b)
    {
        return std::to_string (a.id) < std::to_string (b.id);
    });

    std::string out;
    out += "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\r\n\r\n";
    out += "<";
    out += kStateRootTag;
    out += ">\r\n";

    for (const ParameterValue& entry : ordered)
    {
        out += "  <";
        out += kStateEntryTag;
        out += " id=\"";
        out += std::to_string (entry.id);
        // The name travels beside the id: the id is the position the
        // parameter has today, the name is the identity of the parameter.
        out += "\" name=\"";
        out += escapeXmlText (entry.name);
        out += "\" value=\"";
        out += serialiseDouble (entry.value);
        out += "\" />\r\n";
    }

    out += "</";
    out += kStateRootTag;
    out += ">\r\n";

    return out;
}

/** Read the parameter state document back. */
bool decodeParameterState (const std::string& document,
                           std::vector<ParameterValue>& values)
{
    values.clear();

    const size_t length = document.size();
    size_t pos = 0;

    while (pos < length)
    {
        const size_t open = document.find ('<', pos);

        if (open == std::string::npos)
            break;

        pos = open + 1;

        if (pos >= length)
            break;

        if (document[pos] == '?' || document[pos] == '!')
        {
            const size_t end = document.find ('>', pos);
            if (end == std::string::npos)
                break;
            pos = end + 1;
            continue;
        }

        if (document[pos] == '/')
        {
            const size_t end = document.find ('>', pos);
            if (end == std::string::npos)
                break;
            pos = end + 1;
            continue;
        }

        const size_t nameStart = pos;

        while (pos < length && ! std::isspace (static_cast<unsigned char> (document[pos]))
               && document[pos] != '>' && document[pos] != '/')
            ++pos;

        const std::string name = document.substr (nameStart, pos - nameStart);

        int id = -1;
        double value = 0.0;
        bool haveId = false;
        bool haveValue = false;
        std::string parameterName;

        while (pos < length && document[pos] != '>')
        {
            while (pos < length && std::isspace (static_cast<unsigned char> (document[pos])))
                ++pos;

            if (pos >= length || document[pos] == '>' || document[pos] == '/')
                break;

            const size_t keyStart = pos;

            while (pos < length && document[pos] != '=' && document[pos] != '>'
                   && ! std::isspace (static_cast<unsigned char> (document[pos])))
                ++pos;

            const std::string key = document.substr (keyStart, pos - keyStart);

            while (pos < length && std::isspace (static_cast<unsigned char> (document[pos])))
                ++pos;

            if (pos >= length || document[pos] != '=')
                break;

            ++pos;

            while (pos < length && std::isspace (static_cast<unsigned char> (document[pos])))
                ++pos;

            if (pos >= length || document[pos] != '"')
                break;

            ++pos;

            const size_t valueStart = pos;

            while (pos < length && document[pos] != '"')
                ++pos;

            const std::string text = document.substr (valueStart, pos - valueStart);

            if (pos < length)
                ++pos;    // the closing quote

            if (key == "id")
            {
                id = std::atoi (text.c_str());
                haveId = true;
            }
            else if (key == "name")
            {
                parameterName = text;
            }
            else if (key == "value")
            {
                value = std::atof (text.c_str());
                haveValue = true;
            }
        }

        if (name == kStateEntryTag && haveId && haveValue)
        {
            ParameterValue entry;
            entry.name = parameterName;
            entry.id = id;
            entry.value = value;
            values.push_back (entry);
        }

        const size_t end = document.find ('>', pos);
        if (end == std::string::npos)
            break;
        pos = end + 1;
    }

    return ! values.empty();
}

/** Encode the shared state blob: the marker, the chain and the parameters. */
std::string encodeStateChunk (const std::string& chainJson, const std::string& parameterState)
{
    std::string blob;

    blob.reserve (std::strlen (kAuralisAAXStateMarker) + chainJson.size() + parameterState.size() + 3);

    blob += kAuralisAAXStateMarker;
    blob += '\0';
    blob += chainJson;
    blob += '\0';
    blob += parameterState;
    blob += '\0';

    return blob;
}

/** Split the shared state blob into its two fields. */
bool decodeStateChunk (const char* data, size_t size, std::string& chainJson,
                       std::string& parameterState)
{
    chainJson.clear();
    parameterState.clear();

    if (data == nullptr || size == 0)
        return false;

    const size_t markerEnd = [&]
    {
        size_t i = 0;
        while (i < size && data[i] != '\0')
            ++i;
        return i;
    }();

    if (markerEnd >= size)
        return false;

    if (std::strlen (kAuralisAAXStateMarker) != markerEnd
        || std::memcmp (data, kAuralisAAXStateMarker, markerEnd) != 0)
        return false;

    size_t start = markerEnd + 1;
    size_t end = start;

    while (end < size && data[end] != '\0')
        ++end;

    chainJson.assign (data + start, end - start);

    if (end >= size)
        return true;    // the chunk carries the marker and the chain only

    start = end + 1;
    end = start;

    while (end < size && data[end] != '\0')
        ++end;

    parameterState.assign (data + start, end - start);
    return true;
}

// ===========================================================================
//  the algorithm
// ===========================================================================

void AAX_CALLBACK auralisAAXProcess (AuralisAAXContext* const* inInstancesBegin,
                                     const void* const inInstancesEnd)
{
    const CoreApi& core = coreApi();

    for (AuralisAAXContext* const* instance = inInstancesBegin;
         instance != inInstancesEnd; ++instance)
    {
        AuralisAAXContext* const context = *instance;

        if (context == nullptr)
            continue;

        const int32_t frames = (context->bufferSize != nullptr) ? *context->bufferSize : 0;

        if (frames <= 0)
            continue;

        AuralisAAXInstance* const state = context->instance;

        const bool bypassed = (context->bypass != nullptr) && (*context->bypass != 0);

        if (bypassed || state == nullptr || state->engine == nullptr || ! core.loaded)
        {
            // The plug-in is bypassed, or the core is not there: the audio
            // passes through unchanged.
            if (context->inputChannels != nullptr && context->outputChannels != nullptr)
            {
                const uint32_t channels = (state != nullptr) ? state->inputChannels
                                                             : sharedInputChannels();

                for (uint32_t channel = 0; channel < channels; ++channel)
                {
                    if (context->inputChannels[channel] == nullptr
                        || context->outputChannels[channel] == nullptr)
                        continue;

                    std::memcpy (context->outputChannels[channel], context->inputChannels[channel],
                                 static_cast<size_t> (frames) * sizeof (float));
                }
            }
            continue;
        }

        // The chain has one channel count, so a configuration that is
        // narrower than the chain is widened first: a mono input is
        // copied into both channels of the chain.
        const uint32_t chain = sharedChannelCount();
        const uint32_t given = state->inputChannels;

        if (given < chain)
        {
            const size_t wanted = static_cast<size_t> (chain) * static_cast<size_t> (frames);

            if (state->scratch.size() < wanted)
                state->scratch.resize (wanted);

            for (uint32_t channel = 0; channel < chain; ++channel)
            {
                const float* const source = (channel < given && context->inputChannels[channel] != nullptr)
                                          ? context->inputChannels[channel]
                                          : (given > 0 && context->inputChannels[0] != nullptr
                                                ? context->inputChannels[0] : nullptr);

                if (source == nullptr)
                    continue;

                std::memcpy (&state->scratch[static_cast<size_t> (channel) * frames], source,
                             static_cast<size_t> (frames) * sizeof (float));
            }
        }

        const float* const* input = context->inputChannels;

        if (given < chain)
        {
            std::vector<const float*> views (chain);

            for (uint32_t channel = 0; channel < chain; ++channel)
                views[channel] = &state->scratch[static_cast<size_t> (channel) * frames];

            input = views.data();
        }

        float* const* output = context->outputChannels;

        if (state->outputChannels < chain)
        {
            std::vector<float*> views (chain);

            for (uint32_t channel = 0; channel < chain; ++channel)
                views[channel] = (channel < state->outputChannels && context->outputChannels[channel] != nullptr)
                               ? context->outputChannels[channel] : nullptr;

            output = views.data();
        }

        core.process (state->engine, input, output, static_cast<uint32_t> (frames));
    }
}

int32_t AAX_CALLBACK auralisAAXInstanceInit (const AuralisAAXContext* inContext,
                                             AAX_EComponentInstanceInitAction inAction)
{
    if (inContext == nullptr)
        return 1;

    const CoreApi& core = coreApi();

    switch (inAction)
    {
        case AAX_eComponentInstanceInitAction_AddingNewInstance:
        {
            AuralisAAXInstance* const state = new AuralisAAXInstance();

            state->inputChannels = sharedInputChannels();
            state->outputChannels = sharedOutputChannels();

            if (core.loaded)
            {
                const uint32_t rate = (inContext->sampleRate != nullptr && *inContext->sampleRate > 0.0f)
                                    ? static_cast<uint32_t> (*inContext->sampleRate)
                                    : sharedSampleRate();

                state->engine = core.create (rate, sharedChannelCount());

                if (state->engine != nullptr)
                {
                    const char* module = sharedModuleCode();

                    if (module != nullptr && *module != '\0')
                        core.add_module (state->engine, module);

                    EngineRegistry::instance().add (state->engine);
                }
            }

            const_cast<AuralisAAXContext*> (inContext)->instance = state;
            break;
        }
        case AAX_eComponentInstanceInitAction_RemovingInstance:
        {
            AuralisAAXInstance* const state = const_cast<AuralisAAXContext*> (inContext)->instance;

            if (state != nullptr)
            {
                if (state->engine != nullptr)
                {
                    EngineRegistry::instance().remove (state->engine);
                    core.destroy (state->engine);
                }
                delete state;
                const_cast<AuralisAAXContext*> (inContext)->instance = nullptr;
            }
            break;
        }
        default:
            break;
    }

    return 0;
}

} // namespace (anonymous)

// ===========================================================================
//  the shared constants
// ===========================================================================

const char* const kAuralisAAXStateMarker = "auralis.state.v1";

AAX_CTypeID auralisAAXFourCC (const char* text)
{
    if (text == nullptr)
        return AAX_CTypeID (0);

    return (AAX_CTypeID (static_cast<unsigned char> (text[0])) << 24)
         | (AAX_CTypeID (static_cast<unsigned char> (text[1])) << 16)
         | (AAX_CTypeID (static_cast<unsigned char> (text[2])) << 8)
         |  AAX_CTypeID (static_cast<unsigned char> (text[3]));
}

AAX_CTypeID auralisAAXChunkId()
{
    return auralisAAXFourCC ("AURS");
}

std::string auralisAAXParamId (int registryId)
{
    return std::to_string (kAuralisAAXParamIdBase + registryId);
}

// ===========================================================================
//  the identity record
// ===========================================================================

AuralisAAXIdentity auralisAAXParseIdentity (const char* text, size_t size)
{
    AuralisAAXIdentity record;

    if (text == nullptr || size == 0)
        return record;

    const std::string document (text, size);

    readJsonMemberString (document.c_str(), "module_code", record.moduleCode);
    readJsonMemberString (document.c_str(), "display_name", record.displayName);
    readJsonMemberString (document.c_str(), "category", record.category);
    readJsonMemberString (document.c_str(), "product_name", record.productName);

    std::vector<std::vector<std::string> > triples;
    readJsonMemberTriples (document.c_str(), "aax_effect_ids", triples);

    for (const auto& triple : triples)
    {
        if (triple.size() != 3)
            continue;
        record.effectIds.push_back (triple[0] + ":" + triple[1] + ":" + triple[2]);
    }

    record.valid = ! record.moduleCode.empty();

    return record;
}

AuralisAAXIdentity auralisAAXReadIdentity()
{
    AuralisAAXIdentity record;

    // The build patches the record into the file AFTER the module has been
    // linked, so the contents of the slot are only known when the module
    // runs.  The slot itself is a const object, and a compiler is free to
    // assume that the contents of a const object never change - which would
    // fold the slot back to its link time value and lose the record.
    // Reading through a volatile pointer stops that.
    const volatile char* const region = gIdentitySlot.region;

    // A stamped region starts with the opening brace of the JSON object;
    // an unstamped template has an empty region.
    if (region[0] != '{')
        return record;

    std::string text;

    for (int i = 0; i < kIdentityRegionSize && region[i] != '\0'; ++i)
        text += region[i];

    if (text.empty())
        return record;

    return auralisAAXParseIdentity (text.c_str(), text.size());
}

const AuralisAAXModule* auralisAAXModuleForCode (const std::string& code)
{
    for (int i = 0; i < auralisAAXFamily.moduleCount; ++i)
    {
        const AuralisAAXModule& module = auralisAAXFamily.modules[i];

        if (code == module.code)
            return &module;
    }
    return nullptr;
}

const AuralisAAXModule* auralisAAXIdentityModule (const AuralisAAXIdentity& identity)
{
    if (! identity.valid)
        return nullptr;
    return auralisAAXModuleForCode (identity.moduleCode);
}

// ===========================================================================
//  the component descriptors
// ===========================================================================

std::vector<AuralisAAXComponent> auralisAAXComponents (const AuralisAAXModule& module)
{
    std::vector<AuralisAAXComponent> components;

    for (int variant = 0; variant < kAuralisAAXVariantCount; ++variant)
    {
        AuralisAAXComponent component;

        component.plugId = module.plugId[variant];
        component.inputChannels = module.inputChannels[variant];
        component.outputChannels = module.outputChannels[variant];
        component.manufacturerId = module.manufacturerId;
        component.productId = module.productId;
        component.effectId = module.effectId[variant];

        components.push_back (component);
    }

    return components;
}

// ===========================================================================
//  the data model
// ===========================================================================

AuralisAAX_Processor::AuralisAAX_Processor()
: mModule (nullptr)
, mIdentity (auralisAAXReadIdentity())
{
    mModule = auralisAAXIdentityModule (mIdentity);
}

AuralisAAX_Processor::AuralisAAX_Processor (const AuralisAAXModule* inModule)
: mModule (inModule)
, mIdentity (auralisAAXReadIdentity())
{
}

AuralisAAX_Processor::~AuralisAAX_Processor()
{
}

IACFUnknown* AAX_CALLBACK AuralisAAX_Processor::Create()
{
    return new AuralisAAX_Processor();
}

void AuralisAAX_Processor::buildParameters()
{
    if (mModule == nullptr)
        return;

    for (int index = 0; index < mModule->parameterCount; ++index)
    {
        const AuralisAAXParameter& source = mModule->parameters[index];

        const bool discrete = (std::strcmp (source.type, "enum") == 0)
                           || (std::strcmp (source.type, "bool") == 0);

        const double minimum = discrete ? 0.0 : source.minimum;
        const double maximum = discrete
                             ? static_cast<double> (source.choiceCount > 0 ? source.choiceCount - 1 : 1)
                             : source.maximum;

        const double defaultValue = discrete
                                  ? (std::strcmp (source.type, "bool") == 0
                                        ? (source.defaultValue != 0.0 ? 1.0 : 0.0)
                                        : source.defaultValue)
                                  : source.defaultValue;

        AAX_CParameter<float>* parameter = nullptr;

        if (discrete)
        {
            // The states of a discrete control are drawn on the control
            // itself, so the value is the index of the state and the
            // display delegate turns it back into the text.  A boolean has
            // no choices of its own in the canonical table, so the two
            // states are named here.
            static const char* const kBoolStates[] = { "off", "on", nullptr };

            const bool isBoolean = (std::strcmp (source.type, "bool") == 0);

            // The delegate reads the names and copies them; the cast only
            // drops the constness of the pointers themselves.
            const char** const stateNames = const_cast<const char**> (
                isBoolean ? kBoolStates : source.choices);

            AAX_CStateDisplayDelegate<float> states (stateNames);

            parameter = new AAX_CParameter<float> (auralisAAXParamId (source.id).c_str(),
                                                    AAX_CString (source.name),
                                                    static_cast<float> (defaultValue),
                                                    AAX_CLinearTaperDelegate<float> (
                                                        static_cast<float> (minimum),
                                                        static_cast<float> (maximum)),
                                                    states,
                                                    true);
        }
        else
        {
            AAX_CNumberDisplayDelegate<float, 3> numbers;

            if (source.unit != nullptr && source.unit[0] != '\0')
            {
                const std::string unit = std::string (" ") + source.unit;

                AAX_CUnitDisplayDelegateDecorator<float> decorated (numbers, AAX_CString (unit.c_str()));

                parameter = new AAX_CParameter<float> (auralisAAXParamId (source.id).c_str(),
                                                        AAX_CString (source.name),
                                                        static_cast<float> (defaultValue),
                                                        AAX_CLinearTaperDelegate<float> (
                                                            static_cast<float> (minimum),
                                                            static_cast<float> (maximum)),
                                                        decorated,
                                                        true);
            }
            else
            {
                parameter = new AAX_CParameter<float> (auralisAAXParamId (source.id).c_str(),
                                                        AAX_CString (source.name),
                                                        static_cast<float> (defaultValue),
                                                        AAX_CLinearTaperDelegate<float> (
                                                            static_cast<float> (minimum),
                                                            static_cast<float> (maximum)),
                                                        numbers,
                                                        true);
            }
        }

        parameter->SetType (discrete ? AAX_eParameterType_Discrete : AAX_eParameterType_Continuous);
        parameter->SetOrientation (AAX_eParameterOrientation_LeftMinRightMax |
                                   AAX_eParameterOrientation_BottomMinTopMax |
                                   AAX_eParameterOrientation_RotarySingleDotMode |
                                   AAX_eParameterOrientation_RotaryLeftMinRightMax);

        if (discrete)
        {
            parameter->SetNumberOfSteps (static_cast<uint32_t> (source.choiceCount > 0
                                                                ? source.choiceCount : 2));
        }
        else
        {
            parameter->SetNumberOfSteps (AAX_CParameter<float>::eParameterDefaultNumStepsContinuous);
        }

        // The label is the short name that is drawn on the control; the
        // ladder is the sequence of shorter names the host picks from.
        if (source.label != nullptr && source.label[0] != '\0')
        {
            const std::string label (source.label);

            parameter->AddShortenedName (AAX_CString (label.c_str()));

            for (size_t size = 1; size < label.size(); ++size)
                parameter->AddShortenedName (AAX_CString (label.substr (0, size).c_str()));
        }

        mParameterManager.AddParameter (parameter);
    }

    // The bypass switch.
    //
    // The id is the same in every plug-in format of the family, so a host
    // that automates the reference's switch automates ours with the same
    // curve.  The sentence that explains it travels with the page table,
    // because the format has no field for a sentence.
    if (mModule->bypassId != nullptr && mModule->bypassId[0] != '\0')
    {
        static const char* const kBypassStates[] = { "Off", "On", nullptr };

        const char* const label = (mModule->bypassLabel != nullptr && mModule->bypassLabel[0] != '\0')
                                    ? mModule->bypassLabel
                                    : "Bypass";

        // The delegate reads the names and copies them; the cast only drops
        // the constness of the pointers themselves.
        const char** const stateNames = const_cast<const char**> (kBypassStates);

        AAX_CStateDisplayDelegate<float> states (stateNames);

        AAX_CParameter<float>* parameter = new AAX_CParameter<float> (
            mModule->bypassId,
            AAX_CString (label),
            0.0f,
            AAX_CLinearTaperDelegate<float> (0.0f, 1.0f),
            states,
            true);

        parameter->SetType (AAX_eParameterType_Discrete);
        parameter->SetOrientation (AAX_eParameterOrientation_LeftMinRightMax |
                                   AAX_eParameterOrientation_BottomMinTopMax |
                                   AAX_eParameterOrientation_RotarySingleDotMode |
                                   AAX_eParameterOrientation_RotaryLeftMinRightMax);
        parameter->SetNumberOfSteps (2);

        const std::string shortName (label);

        parameter->AddShortenedName (AAX_CString (shortName.c_str()));

        for (size_t size = 1; size < shortName.size (); ++size)
            parameter->AddShortenedName (AAX_CString (shortName.substr (0, size).c_str()));

        mParameterManager.AddParameter (parameter);
    }
}

void AuralisAAX_Processor::buildChain()
{
    if (mModule == nullptr)
        return;

    const CoreApi& core = coreApi();

    if (! core.loaded)
        return;

    // The chain of the plug-in holds exactly one module: the one the
    // identity record names.  A session therefore means the same thing in
    // every format.
    for (AurEngine* engine : EngineRegistry::instance().engines())
    {
        if (core.module_count != nullptr && core.module_count (engine) > 0)
            continue;

        core.add_module (engine, mModule->code);
    }
}

AAX_Result AuralisAAX_Processor::EffectInit()
{
    buildParameters();
    publishHostState();
    buildChain();
    return AAX_SUCCESS;
}

void AuralisAAX_Processor::publishHostState()
{
    // The host tells the data model which of the three channel
    // configurations it instantiated and at which rate; the algorithm
    // instances are separate objects, so the answer is published for
    // them.
    const AAX_IController* const controller = Controller();

    if (controller == nullptr)
        return;

    AAX_CSampleRate rate = 0.0f;

    if (controller->GetSampleRate (&rate) == AAX_SUCCESS && rate > 0.0f)
        gSampleRate = static_cast<uint32_t> (rate);

    AAX_EStemFormat inputFormat = AAX_eStemFormat_Stereo;
    AAX_EStemFormat outputFormat = AAX_eStemFormat_Stereo;

    if (controller->GetInputStemFormat (&inputFormat) == AAX_SUCCESS)
    {
        const uint32_t channels = AAX_STEM_FORMAT_CHANNEL_COUNT (inputFormat);

        if (channels > 0)
            gInputChannels = channels;
    }

    if (controller->GetOutputStemFormat (&outputFormat) == AAX_SUCCESS)
    {
        const uint32_t channels = AAX_STEM_FORMAT_CHANNEL_COUNT (outputFormat);

        if (channels > 0)
            gOutputChannels = channels;
    }

    gChainChannels = (gInputChannels > gOutputChannels) ? gInputChannels : gOutputChannels;

    if (gChainChannels == 0)
        gChainChannels = 2;
}

// ---------------------------------------------------------------------------
//  the state
// ---------------------------------------------------------------------------

std::string AuralisAAX_Processor::parameterState() const
{
    std::vector<ParameterValue> values;

    if (mModule != nullptr)
    {
        for (int index = 0; index < mModule->parameterCount; ++index)
        {
            const AuralisAAXParameter& source = mModule->parameters[index];

            const AAX_IParameter* parameter =
                mParameterManager.GetParameterByID (auralisAAXParamId (source.id).c_str());

            double value = source.defaultValue;

            if (parameter != nullptr)
            {
                float current = 0.0f;
                if (parameter->GetValueAsFloat (&current))
                    value = static_cast<double> (current);
            }

            ParameterValue entry;
            entry.id = source.id;
            entry.name = source.name != nullptr ? source.name : "";
            entry.value = value;
            values.push_back (entry);
        }

        // The bypass switch is a parameter of the plug-in and not of the
        // module, so it is written out by hand.
        if (mModule->bypassId != nullptr && mModule->bypassId[0] != '\0')
        {
            const AAX_IParameter* parameter =
                mParameterManager.GetParameterByID (mModule->bypassId);

            double value = 0.0;

            if (parameter != nullptr)
            {
                float current = 0.0f;
                if (parameter->GetValueAsFloat (&current))
                    value = static_cast<double> (current);
            }

            ParameterValue entry;
            entry.id = std::atoi (mModule->bypassId);
            entry.name = (mModule->bypassLabel != nullptr && mModule->bypassLabel[0] != '\0')
                            ? mModule->bypassLabel
                            : "Bypass";
            entry.value = value;
            values.push_back (entry);
        }
    }

    return encodeParameterState (values);
}

std::string AuralisAAX_Processor::stateBlob() const
{
    std::string chainJson;

    const CoreApi& core = coreApi();

    if (core.loaded && core.serialize != nullptr)
    {
        const std::vector<AurEngine*> engines = EngineRegistry::instance().engines();

        if (! engines.empty())
        {
            char* text = nullptr;

            if (core.serialize (engines.front(), &text) == 0 && text != nullptr)
            {
                chainJson = text;
                core.free_text (text);
            }
        }
    }

    return encodeStateChunk (chainJson, parameterState());
}

bool AuralisAAX_Processor::restoreParameterState (const std::string& text)
{
    std::vector<ParameterValue> entries;

    if (! decodeParameterState (text, entries))
        return false;

    if (mModule == nullptr)
        return false;

    bool all = true;

    // The name is the identity of a parameter, so an entry that carries one
    // goes to the parameter with that name, whatever id the parameter has
    // now.  An entry that carries only an id is read as the position the
    // parameter had when the document was written: that is the rule of the
    // old format, and after an insertion of a parameter in the middle of
    // the module it names a different parameter.  An entry that names a
    // parameter this binary does not know is reported and skipped; it is
    // never handed to a neighbour.
    for (const ParameterValue& entry : entries)
    {
        int id = entry.id;
        bool known = false;

        if (! entry.name.empty ())
        {
            for (int index = 0; index < mModule->parameterCount; ++index)
            {
                const AuralisAAXParameter& candidate = mModule->parameters[index];

                if (candidate.name != nullptr && entry.name == candidate.name)
                {
                    id = candidate.id;
                    known = true;
                    break;
                }
            }

            if (! known)
            {
                // The bypass switch is a parameter of the plug-in and not of
                // the module, so it is looked for by its own name.
                if (mModule->bypassId != nullptr && mModule->bypassId[0] != '\0'
                    && mModule->bypassLabel != nullptr && entry.name == mModule->bypassLabel)
                {
                    AAX_IParameter* const bypass =
                        mParameterManager.GetParameterByID (mModule->bypassId);

                    if (bypass != nullptr)
                    {
                        bypass->SetValueWithFloat (static_cast<float> (entry.value));
                        continue;
                    }
                }

                all = false;    // the state named a parameter we do not have
                continue;
            }
        }
        else
        {
            for (int index = 0; index < mModule->parameterCount; ++index)
            {
                if (mModule->parameters[index].id == id)
                {
                    known = true;
                    break;
                }
            }
        }

        if (! known)
            continue;

        AAX_IParameter* parameter =
            mParameterManager.GetParameterByID (auralisAAXParamId (id).c_str());

        if (parameter == nullptr)
        {
            all = false;
            continue;
        }

        parameter->SetValueWithFloat (static_cast<float> (entry.value));
    }

    return all;
}

AAX_Result AuralisAAX_Processor::GetNumberOfChunks (int32_t* oNumChunks) const
{
    *oNumChunks = 1;
    return AAX_SUCCESS;
}

AAX_Result AuralisAAX_Processor::GetChunkIDFromIndex (int32_t iIndex, AAX_CTypeID* oChunkID) const
{
    if (iIndex != 0)
    {
        *oChunkID = AAX_CTypeID (0);
        return AAX_ERROR_INVALID_CHUNK_INDEX;
    }

    *oChunkID = auralisAAXChunkId();
    return AAX_SUCCESS;
}

AAX_Result AuralisAAX_Processor::GetChunkSize (AAX_CTypeID inChunkID, uint32_t* oSize) const
{
    if (inChunkID != auralisAAXChunkId())
    {
        *oSize = 0;
        return AAX_ERROR_INVALID_CHUNK_ID;
    }

    const std::string blob = stateBlob();

    *oSize = static_cast<uint32_t> (blob.size());
    return AAX_SUCCESS;
}

AAX_Result AuralisAAX_Processor::GetChunk (AAX_CTypeID inChunkID, AAX_SPlugInChunk* oChunk) const
{
    if (inChunkID != auralisAAXChunkId())
        return AAX_ERROR_INVALID_CHUNK_ID;

    if (oChunk == nullptr)
        return AAX_ERROR_NULL_ARGUMENT;

    const std::string blob = stateBlob();

    oChunk->fSize = static_cast<int32_t> (blob.size());
    oChunk->fVersion = 1;
    oChunk->fChunkID = auralisAAXChunkId();

    if (mModule != nullptr)
    {
        oChunk->fManufacturerID = auralisAAXFourCC (mModule->manufacturerId);
        oChunk->fProductID = auralisAAXFourCC (mModule->productId);
    }

    std::memset (oChunk->fName, 0, sizeof (oChunk->fName));
    std::strncpy (reinterpret_cast<char*> (oChunk->fName), "Auralis state", 31);

    std::memcpy (oChunk->fData, blob.data(), blob.size());

    return AAX_SUCCESS;
}

AAX_Result AuralisAAX_Processor::SetChunk (AAX_CTypeID inChunkID, const AAX_SPlugInChunk* iChunk)
{
    if (inChunkID != auralisAAXChunkId())
        return AAX_ERROR_INVALID_CHUNK_ID;

    if (iChunk == nullptr)
        return AAX_ERROR_NULL_ARGUMENT;

    std::string chainJson;
    std::string parameterStateText;

    if (! decodeStateChunk (iChunk->fData, static_cast<size_t> (iChunk->fSize),
                            chainJson, parameterStateText))
    {
        return AAX_ERROR_INVALID_CHUNK_ID;
    }

    if (! chainJson.empty())
    {
        const CoreApi& core = coreApi();

        if (core.loaded && core.deserialize != nullptr)
        {
            for (AurEngine* engine : EngineRegistry::instance().engines())
                core.deserialize (engine, chainJson.c_str());
        }
    }

    if (! parameterStateText.empty())
        restoreParameterState (parameterStateText);

    return AAX_SUCCESS;
}

AAX_Result AuralisAAX_Processor::CompareActiveChunk (const AAX_SPlugInChunk* iChunk,
                                                      AAX_CBoolean* oIsEqual) const
{
    if (iChunk == nullptr || oIsEqual == nullptr)
        return AAX_ERROR_NULL_ARGUMENT;

    const std::string blob = stateBlob();

    *oIsEqual = (blob.size() == static_cast<size_t> (iChunk->fSize)
                 && std::memcmp (blob.data(), iChunk->fData, blob.size()) == 0)
              ? 1 : 0;

    return AAX_SUCCESS;
}

// ---------------------------------------------------------------------------
//  the page table
// ---------------------------------------------------------------------------

AAX_Result AuralisAAX_Processor::UpdatePageTable (uint32_t /*inTableType*/,
                                                   int32_t /*inTablePageSize*/,
                                                   AAX_IPageTable& ioPageTable) const
{
    if (mModule == nullptr)
        return AAX_ERROR_UNIMPLEMENTED;

    ioPageTable.Clear();

    // The pages come from the generated page tables, so the layout of the
    // plug-in is the layout the application package publishes.
    for (int pageIndex = 0; pageIndex < mModule->pageCount; ++pageIndex)
    {
        const AuralisAAXPage& page = mModule->pages[pageIndex];

        ioPageTable.InsertPage (page.index);

        for (int controlIndex = 0; controlIndex < page.controlCount; ++controlIndex)
        {
            const AuralisAAXControl& control = page.controls[controlIndex];

            const std::string id = auralisAAXParamId (control.parameter);

            ioPageTable.MapParameterID (AAX_CPageTableParamID (id.c_str()),
                                        page.index, controlIndex);
        }
    }

    return AAX_SUCCESS;
}

// ===========================================================================
//  the description the host asks for
// ===========================================================================

namespace
{

/** The AAX category of a module.

    The registry categories are ours; the format's categories are the ones
    the host files the plug-in under.  A restoration plug-in is filed under
    the noise reduction and the effect categories. */
AAX_EPlugInCategory categoryFor (const AuralisAAXModule& module)
{
    const std::string category (module.category != nullptr ? module.category : "");

    if (category == "EQ")
        return AAX_ePlugInCategory_EQ;

    if (category == "Dynamics")
        return AAX_ePlugInCategory_Dynamics;

    if (category == "Pitch")
        return AAX_ePlugInCategory_PitchShift;

    if (category == "Reverb")
        return AAX_ePlugInCategory_Reverb;

    if (category == "Modulation")
        return AAX_ePlugInCategory_Modulation;

    return static_cast<AAX_EPlugInCategory> (AAX_ePlugInCategory_NoiseReduction |
                                            AAX_EPlugInCategory_Effect);
}
/** The stem format of a channel count. */
AAX_EStemFormat stemFormatFor (int channels)
{
    switch (channels)
    {
        case 1:  return AAX_eStemFormat_Mono;
        case 2:  return AAX_eStemFormat_Stereo;
        default: return AAX_eStemFormat_None;
    }
}

} // namespace (anonymous)

AAX_Result GetEffectDescriptions (AAX_ICollection* inCollection)
{
    if (inCollection == nullptr)
        return AAX_ERROR_NULL_ARGUMENT;

    const AuralisAAXIdentity identity = auralisAAXReadIdentity();
    const AuralisAAXModule* const module = auralisAAXIdentityModule (identity);

    return AuralisAAXDescribe (inCollection, module);
}

AAX_Result AuralisAAXDescribe (AAX_ICollection* inCollection, const AuralisAAXModule* module)
{
    if (inCollection == nullptr)
        return AAX_ERROR_NULL_ARGUMENT;

    if (module == nullptr)
        return AAX_ERROR_INVALID_ARGUMENT;

    for (const AuralisAAXComponent& component : auralisAAXComponents (*module))
    {
        AAX_IEffectDescriptor* const descriptor = inCollection->NewDescriptor();

        if (descriptor == nullptr)
            return AAX_ERROR_NULL_OBJECT;

        descriptor->AddName (module->displayName);
        descriptor->AddCategory (categoryFor (*module));

        // The creation procedures: the data model and the controller.
        descriptor->AddProcPtr (reinterpret_cast<void*> (&AuralisAAX_Controller::Create),
                                kAAX_ProcPtrID_Create_EffectGUI);
        descriptor->AddProcPtr (reinterpret_cast<void*> (&AuralisAAX_Processor::Create),
                                kAAX_ProcPtrID_Create_EffectParameters);

        // The page table.  The format expects the page table as a resource
        // document inside the plug-in bundle: the plug-in declares the
        // name of the file here, and the host reads the file out of
        // Contents/Resources of the bundle.  The document is generated from
        // the canonical table and copied into the bundle by the build, so
        // the name is the one the generated description carries.
        descriptor->AddResourceInfo (AAX_eResourceType_PageTable,
                                    AURALIS_AAX_PAGE_TABLE_RESOURCE);

        AAX_IComponentDescriptor* const componentDescriptor = descriptor->NewComponentDescriptor();

        if (componentDescriptor == nullptr)
            return AAX_ERROR_NULL_OBJECT;

        componentDescriptor->AddAudioIn (AAX_FIELD_INDEX (AuralisAAXContext, inputChannels));
        componentDescriptor->AddAudioOut (AAX_FIELD_INDEX (AuralisAAXContext, outputChannels));
        componentDescriptor->AddAudioBufferLength (AAX_FIELD_INDEX (AuralisAAXContext, bufferSize));
        componentDescriptor->AddSampleRate (AAX_FIELD_INDEX (AuralisAAXContext, sampleRate));
        componentDescriptor->AddDataInPort (AAX_FIELD_INDEX (AuralisAAXContext, bypass), sizeof (int32_t));
        componentDescriptor->AddPrivateData (AAX_FIELD_INDEX (AuralisAAXContext, instance),
                                             sizeof (AuralisAAXInstance*));

        AAX_IPropertyMap* const properties = componentDescriptor->NewPropertyMap();

        if (properties == nullptr)
            return AAX_ERROR_NULL_OBJECT;

        properties->AddProperty (AAX_eProperty_ManufacturerID,
                                 AAX_CPropertyValue (auralisAAXFourCC (component.manufacturerId)));
        properties->AddProperty (AAX_eProperty_ProductID,
                                 AAX_CPropertyValue (auralisAAXFourCC (component.productId)));
        properties->AddProperty (AAX_eProperty_PlugInID_Native,
                                 AAX_CPropertyValue (auralisAAXFourCC (component.plugId)));
        properties->AddProperty (AAX_eProperty_CanBypass, 1);
        properties->AddProperty (AAX_eProperty_InputStemFormat,
                                 static_cast<AAX_CPropertyValue> (stemFormatFor (component.inputChannels)));
        properties->AddProperty (AAX_eProperty_OutputStemFormat,
                                 static_cast<AAX_CPropertyValue> (stemFormatFor (component.outputChannels)));
        properties->AddProperty (AAX_eProperty_Constraint_MultiMonoSupport, 0);
        properties->AddProperty (AAX_eProperty_SupportsSaveRestore, 1);
        properties->AddProperty (AAX_eProperty_ObservesTransportState, 0);

        componentDescriptor->AddProcessProc_Native (auralisAAXProcess, properties,
                                                    auralisAAXInstanceInit);

        descriptor->AddComponent (componentDescriptor);

        const AAX_Result added = inCollection->AddEffect (component.effectId.c_str(), descriptor);

        if (added != AAX_SUCCESS)
            return added;
    }

    inCollection->SetManufacturerName ("Auralis");
    inCollection->AddPackageName (module->displayName);
    inCollection->SetPackageVersion (1);

    return AAX_SUCCESS;
}
