/*
    AuralisVST3.cpp - the Auralis VST3 adapter, written directly against the
    VST3 SDK.

    See AuralisVST3.h for the architecture.  The short version:

      * this file is ONE template; it implements any Auralis module;
      * the module is chosen by the identity record that the build stamped
        into the reserved slot below;
      * the parameter table comes from AuralisGenerated.h, which the build
        generates from the canonical registry;
      * the core is reached through the C ABI in aurcore_c_api.h and is
        addressed by parameter NAME;
      * the state chunk is the shared "auralis.state.v1" convention, so a
        session written by the JUCE adapter loads here and the other way
        round.

    Nothing in this file knows the name of a module, a parameter or a
    preset.  Adding a module to the registry therefore produces a plug-in
    for every format without touching this file.

    THE THREE OBJECTS
    -----------------
    The component, the processor and the controller are three objects:

      * the component owns the core engine, the buses and the state, and
        it owns the processor;
      * the processor is the audio path; the host asks the component for
        it through queryInterface;
      * the controller holds the parameter table and the values the host
        sees, and it pushes those values to the component over the
        connection point.

    The host holds the component and the controller as two pointers and
    releases each of them once, so every object is deleted when its last
    reference goes.
*/

#include "AuralisVST3.h"

#include "aurcore_c_api.h"

#include "pluginterfaces/base/ibstream.h"
#include "pluginterfaces/vst/ivstmessage.h"
#include "pluginterfaces/vst/vstspeaker.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <locale>
#include <memory>
#include <sstream>

#if SMTG_OS_WINDOWS
 #include <windows.h>
#endif

// The generated parameter table.  It is written by
// plugins/vst3/build_identity.py at build time; the __has_include guard
// keeps this file readable (and compilable) outside a build tree.
#if __has_include ("AuralisGenerated.h")
 #include "AuralisGenerated.h"
 #define AURALIS_HAVE_GENERATED_HEADER 1
#else
 #define AURALIS_HAVE_GENERATED_HEADER 0
#endif

#ifndef AURALIS_MODULE_CODE
 #define AURALIS_MODULE_CODE "AUR_UNKNOWN"
#endif

#ifndef AURALIS_PRODUCT_CODE
 #define AURALIS_PRODUCT_CODE "UNKN"
#endif

using namespace Steinberg;
using namespace Steinberg::Vst;

// ===========================================================================
//  the shared runtime
// ===========================================================================
//
//  The core is not inside the bundle any more: the family shares one copy
//  of it, in the runtime directory next to the bundles.  A static import on
//  the core cannot be satisfied then - the loader resolves it when the
//  plug-in is loaded, and it never looks outside the bundle - so the import
//  is a delay-load descriptor (see CMakeLists.txt) and the plug-in loads
//  the core itself.
//
//  The load is done here and not in DllMain: loading a library while the
//  loader holds its lock is what made the module fault at teardown
//  (exception 0xC0000005, exit -1073741819).
//
//  The rule that finds the runtime, from the plug-in's own directory:
//
//    1. AURALIS_RUNTIME_DIR, when it is set;
//    2. a directory called "Auralis Runtime" next to the bundle;
//    3. one in the bundle's parent directory (the build layout, where the
//       bundle sits in <module>/stamped/);
//    4. one in the directory above that (the build layout's family root,
//       where the runtime of the whole family lives).
//
//  Four levels are walked and every one of them is searched for a
//  subdirectory called "Auralis Runtime".  The old layout, where the bundle
//  carried the runtime files itself, is not searched for: it does not exist
//  any more, the family shares one runtime in a directory of its own.
//
//  plugins/vst3native/stage_runtime.py implements the same rule for the
//  tooling, so that the tool and the plug-in always agree.

namespace
{

#if SMTG_OS_WINDOWS

/** The directory the plug-in itself was loaded from. */
bool pluginDirectory (wchar_t* out, size_t size)
{
    HMODULE self = nullptr;

    const DWORD flags = GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                        GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT;

    if (! GetModuleHandleExW (flags, reinterpret_cast<LPCWSTR> (&pluginDirectory), &self))
        return false;

    const DWORD length = GetModuleFileNameW (self, out, static_cast<DWORD> (size));

    return length > 0 && length < size;
}

/** The runtime directory, or nothing when there is none. */
bool runtimeDirectory (wchar_t* out, size_t size)
{
    // 1. the environment
    const DWORD length = GetEnvironmentVariableW (L"AURALIS_RUNTIME_DIR", out,
                                                 static_cast<DWORD> (size));

    if (length > 0 && length < size)
        return true;

    // 2. and 3. a directory called "Auralis Runtime" next to the bundle, or
    //    in one of the directories above it
    wchar_t path[1024] = { 0 };

    if (! pluginDirectory (path, 1024))
        return false;

    wchar_t* last = wcsrchr (path, L'\\');

    if (last == nullptr)
        return false;

    *last = 0;  // the bundle's directory

    // The bundle sits in <module>/stamped/ in the build layout, and the
    // runtime of the family sits one directory above that, so four levels
    // are walked.  In the release layout the bundles and the runtime are
    // siblings, and the second level finds it.
    for (int level = 0; level < 4; ++level)
    {
        wchar_t candidate[1200] = { 0 };
        swprintf (candidate, 1200, L"%s\\%s", path, L"Auralis Runtime");

        if (GetFileAttributesW (candidate) != INVALID_FILE_ATTRIBUTES)
        {
            wcsncpy (out, candidate, size - 1);
            out[size - 1] = 0;
            return true;
        }

        last = wcsrchr (path, L'\\');

        if (last == nullptr)
            break;

        *last = 0;
    }

    return false;
}

#endif  // SMTG_OS_WINDOWS

/** Load the core out of the shared runtime directory.
    Returns true when the core is there. */
bool prepareRuntime ()
{
    static bool tried = false;
    static bool ready = false;

    if (tried)
        return ready;

    tried = true;

#if SMTG_OS_WINDOWS
    // A host that loads the runtime itself (the independent host does,
    // through AURALIS_VST3HOST_DEPS) has already loaded the core.  The
    // delayed import finds it by name, so there is nothing left to do.
    if (GetModuleHandleW (L"aurcore_abi.dll") != nullptr)
    {
        ready = true;
        return ready;
    }

    wchar_t directory[1024] = { 0 };

    if (! runtimeDirectory (directory, 1024))
        return false;

    wchar_t path[1200] = { 0 };
    swprintf (path, 1200, L"%s\\aurcore_abi.dll", directory);

    // LOAD_WITH_ALTERED_SEARCH_PATH: the interpreter the core is linked
    // against is found next to the core, and not in the process directory.
    HMODULE core = LoadLibraryExW (path, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);

    ready = core != nullptr;
    return ready;
#else
    // The other platforms resolve the core through the loader, which
    // searches the directory of the plug-in.
    ready = true;
    return ready;
#endif
}

}  // namespace

namespace auralis
{

const char* const kAuralisStateMarker = "auralis.state.v1";

// The interface id of the help extension; see AuralisVST3.h.
DEF_CLASS_IID (IParameterHelp);

namespace
{

// ===========================================================================
//  small helpers
// ===========================================================================

void logMessage (const std::string& text)
{
#if SMTG_OS_WINDOWS
    OutputDebugStringA (text.c_str());
    OutputDebugStringA ("\n");
#endif

    std::fprintf (stderr, "[Auralis] %s\n", text.c_str());
    std::fflush (stderr);
}

double clampValue (double value, double low, double high)
{
    if (value < low)
        return low;
    if (value > high)
        return high;
    return value;
}

// ===========================================================================
//  the digital ceiling
// ===========================================================================
//
//  THE PROMISE OF THE PLUG-IN
//  ---------------------------
//  Nothing above the digital ceiling leaves the plug-in.  A host writes what
//  the plug-in hands it into a file or into a bus, and a sample outside
//  [−1, 1] is a clip that nothing downstream can undo.
//
//  WHERE THE RULE LIVES
//  --------------------
//  The rule of the family lives in the core (aurcore/ceiling.py) and it is
//  applied at the OUTPUT of the product, not inside a module.  The block the
//  core hands back is the chain's output, and the core has already clipped
//  it at the limit the chain declares: the digital ceiling, multiplied by the
//  gain the chain's own level controls declare.  The plug-in adds no level
//  of its own, so the block it hands the host is inside that limit.
//
//  A plug-in is a chain of one module -- that is how this adapter builds it
//  -- so the limit of the plug-in is the limit of that chain: the digital
//  ceiling for a repair plug-in, and the digital ceiling times the user's
//  own gain for the plug-ins whose knob is the source of the level.
//
//  WHY IT CLIPS
//  ------------
//  A clip is a per sample operation: a sample above the limit becomes the
//  limit and every other sample is left exactly as it was.  A scale would be
//  a gain, and a gain moves the level of the whole block -- of the material
//  the user did not touch as much as of the material they did.
//
//  THE TWO PATHS IN WHICH THE CORE DOES NOT RUN
//  --------------------------------------------
//  A bypassed plug-in and a plug-in whose core could not be started are the
//  two paths in which the core does not make the block, so the plug-in holds
//  the line itself, and it clips at the digital ceiling.

/** The digital ceiling: the largest value a sample may have. */
constexpr float kDigitalCeiling = 1.0f;

/** Clip a block of samples at the digital ceiling.

    The clip is per sample: a sample above the limit becomes the limit and
    every other sample keeps its value.  The samples that are inside the
    limit are not touched at all.
*/
void clipToCeiling (float* buffer, int32 frames)
{
    if (buffer == nullptr || frames <= 0)
        return;

    for (int32 i = 0; i < frames; ++i)
    {
        const float value = buffer[i];

        if (value > kDigitalCeiling)
            buffer[i] = kDigitalCeiling;
        else if (value < -kDigitalCeiling)
            buffer[i] = -kDigitalCeiling;
    }
}

/** Copy the host's input to the host's output and hold the digital ceiling.

    This is the path of the bypass and of the plug-in whose core could not
    run: the audio is passed through, and the ceiling still holds.
*/
void copyAndClip (const AudioBusBuffers& input, const AudioBusBuffers& output, int32 frames)
{
    for (int32 c = 0; c < output.numChannels && c < input.numChannels; ++c)
    {
        if (input.channelBuffers32[c] == nullptr || output.channelBuffers32[c] == nullptr)
            continue;

        std::memcpy (output.channelBuffers32[c], input.channelBuffers32[c],
                     static_cast<size_t> (frames) * sizeof (float));

        clipToCeiling (output.channelBuffers32[c], frames);
    }
}

/** The number of choices an enum parameter has. */
int choiceCount (const HostParam& param)
{
    return static_cast<int> (param.choices.size ());
}

/** The index of the last choice of an enum parameter. */
int lastChoiceIndex (const HostParam& param)
{
    return std::max (0, choiceCount (param) - 1);
}

/** Copy a UTF-8 string into one of the SDK's fixed size UTF-16 strings. */
void copyToString128 (String128 target, const std::string& source)
{
    Steinberg::str8ToStr16 (target, source.c_str(), 128);
}

/** Read one of the SDK's UTF-16 strings back as UTF-8.

    This is the exact inverse of ``str8ToStr16``, which copies the bytes
    of the source one by one.
*/
std::string fromString16 (const TChar* text)
{
    std::string out;

    if (text == nullptr)
        return out;

    for (int32 i = 0; i < 128 && text[i] != 0; ++i)
        out += static_cast<char> (text[i] & 0xff);

    return out;
}

/** The class id the build was configured with, as a class id string.

    It is built the way plugins/identity.py builds it, so that a stamped
    binary and an unstamped template name the same class.
*/
std::string fallbackClassIdText ()
{
    const std::string product (AURALIS_PRODUCT_CODE);
    std::string text = "0101ABABABCDEF0141555241";

    for (size_t i = 0; i < 4; ++i)
    {
        const auto c = static_cast<unsigned char> (i < product.size() ? product[i] : '0');
        text += "0123456789ABCDEF"[c >> 4];
        text += "0123456789ABCDEF"[c & 0x0F];
    }

    return text;
}

/** The class id the stamped identity record names.

    The record carries the class id as a canonical GUID string.  The
    sixteen bytes a binary reports are that string's *platform encoding*:
    the SDK lays them out COM compatible on Windows and big endian
    everywhere else, so that FUID::toString of the bytes is the record's
    string again on every platform.  Taking the record's hex digits as
    the bytes instead gives a class id that a host on Windows reads back
    as a different identifier.
*/
void identityClassId (const IdentityRecord& identity, TUID out)
{
    std::memset (out, 0, 16);

    const std::string& hex = identity.vst3FuidHex;
    const std::string text = hex.size() == 32 ? hex : fallbackClassIdText ();

    FUID fuid;

    if (fuid.fromString (text.c_str ()))
    {
        fuid.toTUID (out);
        return;
    }

    std::memset (out, 0, 16);
}

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

constexpr int kIdentityMarkerLength = 13;
constexpr int kIdentityRegionSize = 4096;

#if AURALIS_HAVE_GENERATED_HEADER
static_assert (kAuralisIdentityMarkerLength == kIdentityMarkerLength,
               "the identity marker length must match plugins/identity.py");
static_assert (kAuralisIdentityRegionSize == kIdentityRegionSize,
               "the identity region size must match plugins/identity.py");
#endif

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
//  a small JSON reader
// ===========================================================================
//
//  The identity record is JSON, so the adapter needs a JSON reader.  It only
//  has to understand what the record uses, but it is written as a complete
//  (if small) RFC 8259 reader so that an unexpected record is rejected
//  rather than half understood.

struct JsonValue
{
    enum class Kind
    {
        Null,
        Boolean,
        Number,
        String,
        Array,
        Object
    };

    Kind kind = Kind::Null;
    bool boolean = false;
    double number = 0.0;
    std::string text;
    std::vector<JsonValue> items;
    std::vector<std::pair<std::string, JsonValue>> members;

    const JsonValue* find (const std::string& key) const
    {
        for (const auto& member : members)
            if (member.first == key)
                return &member.second;

        return nullptr;
    }

    std::string stringOr (const std::string& key) const
    {
        const JsonValue* const value = find (key);

        if (value == nullptr || value->kind != Kind::String)
            return {};

        return value->text;
    }
};

class JsonParser
{
public:
    JsonParser (const char* text, size_t length) : cursor_ (text), end_ (text + length) {}

    bool parse (JsonValue& out)
    {
        skipWhitespace ();

        if (! parseValue (out, 0))
            return false;

        skipWhitespace ();
        return cursor_ == end_;
    }

private:
    static constexpr int kMaxDepth = 64;

    void skipWhitespace ()
    {
        while (cursor_ < end_
               && (*cursor_ == ' ' || *cursor_ == '\t' || *cursor_ == '\n' || *cursor_ == '\r'))
            ++cursor_;
    }

    bool literal (const char* text)
    {
        const size_t length = std::strlen (text);

        if (static_cast<size_t> (end_ - cursor_) < length)
            return false;

        if (std::memcmp (cursor_, text, length) != 0)
            return false;

        cursor_ += length;
        return true;
    }

    bool parseValue (JsonValue& out, int depth)
    {
        if (depth > kMaxDepth)
            return false;

        skipWhitespace ();

        if (cursor_ >= end_)
            return false;

        switch (*cursor_)
        {
            case '{': return parseObject (out, depth);
            case '[': return parseArray (out, depth);
            case '"':
                out.kind = JsonValue::Kind::String;
                return parseString (out.text);

            case 't':
                if (! literal ("true"))
                    return false;

                out.kind = JsonValue::Kind::Boolean;
                out.boolean = true;
                return true;

            case 'f':
                if (! literal ("false"))
                    return false;

                out.kind = JsonValue::Kind::Boolean;
                out.boolean = false;
                return true;

            case 'n':
                if (! literal ("null"))
                    return false;

                out.kind = JsonValue::Kind::Null;
                return true;

            default: return parseNumber (out);
        }
    }

    bool parseObject (JsonValue& out, int depth)
    {
        out.kind = JsonValue::Kind::Object;
        ++cursor_;    // '{'

        skipWhitespace ();

        if (cursor_ < end_ && *cursor_ == '}')
        {
            ++cursor_;
            return true;
        }

        for (;;)
        {
            skipWhitespace ();

            if (cursor_ >= end_ || *cursor_ != '"')
                return false;

            std::string key;

            if (! parseString (key))
                return false;

            skipWhitespace ();

            if (cursor_ >= end_ || *cursor_ != ':')
                return false;

            ++cursor_;

            JsonValue value;

            if (! parseValue (value, depth + 1))
                return false;

            out.members.emplace_back (std::move (key), std::move (value));

            skipWhitespace ();

            if (cursor_ >= end_)
                return false;

            if (*cursor_ == ',')
            {
                ++cursor_;
                continue;
            }

            if (*cursor_ == '}')
            {
                ++cursor_;
                return true;
            }

            return false;
        }
    }

    bool parseArray (JsonValue& out, int depth)
    {
        out.kind = JsonValue::Kind::Array;
        ++cursor_;    // '['

        skipWhitespace ();

        if (cursor_ < end_ && *cursor_ == ']')
        {
            ++cursor_;
            return true;
        }

        for (;;)
        {
            JsonValue value;

            if (! parseValue (value, depth + 1))
                return false;

            out.items.push_back (std::move (value));

            skipWhitespace ();

            if (cursor_ >= end_)
                return false;

            if (*cursor_ == ',')
            {
                ++cursor_;
                continue;
            }

            if (*cursor_ == ']')
            {
                ++cursor_;
                return true;
            }

            return false;
        }
    }

    bool parseString (std::string& out)
    {
        if (cursor_ >= end_ || *cursor_ != '"')
            return false;

        ++cursor_;
        out.clear ();

        while (cursor_ < end_)
        {
            const char c = *cursor_++;

            if (c == '"')
                return true;

            if (c != '\\')
            {
                out += c;
                continue;
            }

            if (cursor_ >= end_)
                return false;

            const char escape = *cursor_++;

            switch (escape)
            {
                case '"':  out += '"';  break;
                case '\\': out += '\\'; break;
                case '/':  out += '/';  break;
                case 'b':  out += '\b'; break;
                case 'f':  out += '\f'; break;
                case 'n':  out += '\n'; break;
                case 'r':  out += '\r'; break;
                case 't':  out += '\t'; break;
                case 'u':
                {
                    if (end_ - cursor_ < 4)
                        return false;

                    unsigned int code = 0;

                    for (int i = 0; i < 4; ++i)
                    {
                        const char digit = *cursor_++;
                        code <<= 4;

                        if (digit >= '0' && digit <= '9')
                            code |= static_cast<unsigned int> (digit - '0');
                        else if (digit >= 'a' && digit <= 'f')
                            code |= static_cast<unsigned int> (digit - 'a' + 10);
                        else if (digit >= 'A' && digit <= 'F')
                            code |= static_cast<unsigned int> (digit - 'A' + 10);
                        else
                            return false;
                    }

                    // The record is ASCII in practice, so a code point
                    // outside the basic plane is written as the replacement
                    // character rather than as a surrogate pair.
                    if (code < 0x80)
                    {
                        out += static_cast<char> (code);
                    }
                    else if (code < 0x800)
                    {
                        out += static_cast<char> (0xc0 | (code >> 6));
                        out += static_cast<char> (0x80 | (code & 0x3f));
                    }
                    else
                    {
                        out += static_cast<char> (0xe0 | (code >> 12));
                        out += static_cast<char> (0x80 | ((code >> 6) & 0x3f));
                        out += static_cast<char> (0x80 | (code & 0x3f));
                    }

                    break;
                }

                default: return false;
            }
        }

        return false;
    }

    bool parseNumber (JsonValue& out)
    {
        char* stop = nullptr;
        const double value = std::strtod (cursor_, &stop);

        if (stop == cursor_)
            return false;

        cursor_ = stop;
        out.kind = JsonValue::Kind::Number;
        out.number = value;
        return true;
    }

    const char* cursor_;
    const char* end_;
};

// ===========================================================================
//  number formatting
// ===========================================================================
//
//  The parameter state is shared with the JUCE adapter, so the numbers in it
//  are written the way that adapter writes them: the shortest decimal form
//  that still round trips, with a fixed number of decimals for the small
//  values and scientific notation for the very large and very small ones.

std::string formatWithDecimals (double value, int decimals, bool scientific)
{
    std::ostringstream stream;
    stream.imbue (std::locale::classic());

    if (decimals > 0)
    {
        stream.setf (scientific ? std::ios_base::scientific : std::ios_base::fixed);
        stream.precision (decimals);
    }

    stream << value;
    return stream.str();
}

std::string reduceLengthOfFloatString (const std::string& input)
{
    const size_t length = input.size();

    if (length == 0)
        return input;

    size_t trimStart = length;
    size_t trimEnd = trimStart;
    size_t exponentTrimStart = length;
    size_t exponentTrimEnd = exponentTrimStart;

    char currentChar = '\0';

    for (size_t c = length - 1; c > 0; --c)
    {
        currentChar = input[c];

        if (currentChar == '0' && c + 1 == trimStart)
        {
            --trimStart;
        }
        else if (currentChar == '.')
        {
            if (trimStart == c + 1 && trimStart != length && input[trimStart] == '0')
                ++trimStart;

            break;
        }
        else if (currentChar == 'e' || currentChar == 'E')
        {
            size_t cNext = c + 1;

            if (cNext != length)
            {
                if (input[cNext] == '-')
                    ++cNext;

                exponentTrimStart = cNext;

                if (cNext != length && input[cNext] == '+')
                    ++cNext;

                exponentTrimEnd = cNext;
            }

            while (cNext != length && input[cNext++] == '0')
                exponentTrimEnd = cNext;

            if (exponentTrimEnd == length)
                exponentTrimStart = c;

            trimStart = c;
            trimEnd = trimStart;
        }
    }

    if ((trimStart != trimEnd && currentChar == '.') || exponentTrimStart != exponentTrimEnd)
    {
        if (trimStart == trimEnd)
            return input.substr (0, exponentTrimStart) + input.substr (exponentTrimEnd);

        if (exponentTrimStart == exponentTrimEnd)
            return input.substr (0, trimStart) + input.substr (trimEnd);

        if (trimEnd == exponentTrimStart)
            return input.substr (0, trimStart) + input.substr (exponentTrimEnd);

        return input.substr (0, trimStart) + input.substr (trimEnd, exponentTrimStart - trimEnd)
               + input.substr (exponentTrimEnd);
    }

    return input;
}

std::string serialiseDouble (double input)
{
    const double absInput = std::abs (input);

    if (absInput >= 1.0e6 || absInput <= 1.0e-5)
        return reduceLengthOfFloatString (formatWithDecimals (input, 15, true));

    const int intInput = static_cast<int> (input);

    if (static_cast<double> (intInput) == input)
        return formatWithDecimals (input, 1, false);

    int decimals = 0;

    if (absInput < 1.0)
    {
        if (absInput >= 1.0e-1)
            decimals = 16;
        else if (absInput >= 1.0e-2)
            decimals = 17;
        else if (absInput >= 1.0e-3)
            decimals = 18;
        else if (absInput >= 1.0e-4)
            decimals = 19;
        else
            decimals = 20;
    }
    else if (absInput < 1.0e3)
    {
        if (absInput < 1.0e1)
            decimals = 15;
        else if (absInput < 1.0e2)
            decimals = 14;
        else
            decimals = 13;
    }
    else if (absInput < 1.0e4)
        decimals = 12;
    else if (absInput < 1.0e5)
        decimals = 11;
    else
        decimals = 10;

    return reduceLengthOfFloatString (formatWithDecimals (input, decimals, false));
}

// ===========================================================================
//  the built-in preset library
// ===========================================================================
//
//  The library is a directory of files, one per preset, under
//  ``var/presets/builtin/<Category>/``.  A preset names the module it
//  belongs to and carries the values of that module's parameters.
//
//  WHERE THE LIBRARY IS
//  -------------------
//  The rule that finds it is the rule that finds the shared runtime:
//
//    1. AURALIS_PRESET_DIR, when it is set.  The launcher scripts of the
//       application set it, and it names the root of the library;
//    2. a directory called "var/presets" in the directory the plug-in was
//       loaded from, or in one of the directories above it.
//
//  The root of the library holds the shipped presets in "builtin" and the
//  ones the user has made beside them, and both belong in the list.
//
//  A plug-in that finds no library has no presets, and the host sees an
//  empty program list.  That is the honest answer: the plug-in does not
//  make up presets it does not have.

/** The directory the plug-in was loaded from, or nothing. */
std::filesystem::path loadedDirectory ()
{
#if SMTG_OS_WINDOWS
    wchar_t path[1024] = { 0 };

    if (pluginDirectory (path, 1024))
    {
        // The loader answers with the path of the module itself; the
        // directory it sits in is what the search starts from.
        const std::filesystem::path module (path);

        return module.parent_path ();
    }
#endif

    // The other platforms have no cheap way of asking the loader where the
    // module came from; the environment variable is the way there.
    return {};
}

/** The root of the preset library, or nothing. */
std::filesystem::path presetLibraryRoot ()
{
    // 1. the environment
    if (const char* const fromEnvironment = std::getenv ("AURALIS_PRESET_DIR"))
    {
        const std::filesystem::path root (fromEnvironment);

        std::error_code error;

        if (std::filesystem::is_directory (root, error))
            return root;
    }

    // 2. the directory the plug-in was loaded from, and the levels above it
    std::filesystem::path directory = loadedDirectory ();

    logMessage ("Auralis: the plug-in was loaded from \"" + directory.string () + "\"");

    for (int level = 0; level < 6 && ! directory.empty (); ++level)
    {
        const std::filesystem::path candidate = directory / "var" / "presets";

        std::error_code error;

        if (std::filesystem::is_directory (candidate, error))
            return candidate;

        const std::filesystem::path parent = directory.parent_path ();

        if (parent == directory)
            break;

        directory = parent;
    }

    return {};
}

/** The value of one typed field of a preset file.

    The preset format wraps every value in an envelope that names its
    type, so a field is an object with a "Value" member.
*/
std::string typedValue (const JsonValue& field)
{
    if (field.kind != JsonValue::Kind::Object)
        return {};

    const JsonValue* const value = field.find ("Value");

    if (value == nullptr)
        return {};

    switch (value->kind)
    {
        case JsonValue::Kind::String: return value->text;

        case JsonValue::Kind::Number:
        {
            std::ostringstream stream;
            stream.imbue (std::locale::classic());
            stream << std::setprecision (17) << value->number;
            return stream.str();
        }

        case JsonValue::Kind::Boolean: return value->boolean ? "1" : "0";

        default: return {};
    }
}

/** Read one preset file.

    Returns false when the file cannot be read or does not hold a preset.
*/
bool readPresetFile (const std::filesystem::path& path, ProgramPreset& out)
{
    std::ifstream file (path, std::ios::binary);

    if (! file)
        return false;

    const std::string text ((std::istreambuf_iterator<char> (file)),
                            std::istreambuf_iterator<char>());

    JsonValue root;
    JsonParser parser (text.c_str(), text.size());

    if (! parser.parse (root) || root.kind != JsonValue::Kind::Object)
        return false;

    const JsonValue* const name = root.find ("name");

    if (name == nullptr)
        return false;

    out.name = typedValue (*name);

    if (out.name.empty())
        return false;

    if (const JsonValue* const category = root.find ("category"))
        out.category = typedValue (*category);

    if (const JsonValue* const description = root.find ("description"))
        out.description = typedValue (*description);

    if (const JsonValue* const builtin = root.find ("builtin"))
        out.builtin = builtin->kind == JsonValue::Kind::Boolean && builtin->boolean;

    if (const JsonValue* const module = root.find ("module"))
    {
        if (const JsonValue* const code = module->find ("code"))
        {
            if (code->kind == JsonValue::Kind::String)
                out.moduleCode = code->text;
        }
    }

    if (const JsonValue* const params = root.find ("params"))
    {
        if (params->kind == JsonValue::Kind::Object)
        {
            for (const auto& member : params->members)
                out.values.emplace_back (member.first, typedValue (member.second));
        }
    }

    return true;
}

/** Every preset of the library that belongs to one module.

    The library is walked recursively: the shipped presets live in
    ``builtin/<Category>/`` and the ones the user has made in
    ``<Category>/``.  The shipped ones come first, because they are the ones
    a host is expected to show.
*/
std::vector<ProgramPreset> collectPresets (const std::string& moduleCode)
{
    std::vector<ProgramPreset> presets;

    const std::filesystem::path root = presetLibraryRoot ();

    if (root.empty ())
    {
        logMessage ("Auralis: the preset library was not found, so the host sees an empty "
                    "program list.");
        return presets;
    }

    std::error_code error;

    for (const auto& entry : std::filesystem::recursive_directory_iterator (root, error))
    {
        if (error)
            break;

        if (! entry.is_regular_file ())
            continue;

        if (entry.path ().extension () != ".json")
            continue;

        ProgramPreset preset;

        if (! readPresetFile (entry.path (), preset))
            continue;

        if (preset.moduleCode != moduleCode)
            continue;

        presets.push_back (std::move (preset));
    }

    std::sort (presets.begin (), presets.end (),
               [] (const ProgramPreset& a, const ProgramPreset& b)
               {
                   if (a.builtin != b.builtin)
                       return a.builtin;

                   return a.name < b.name;
               });

    logMessage ("Auralis: the preset library is at " + root.string () + ", "
                + std::to_string (presets.size ()) + " preset(s) for " + moduleCode);
    return presets;
}

// ===========================================================================
//  the parameter state document
// ===========================================================================
//
//  The document has this shape:
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

std::string decodeXmlEntities (const std::string& text)
{
    std::string out;
    out.reserve (text.size());

    for (size_t i = 0; i < text.size(); ++i)
    {
        if (text[i] != '&')
        {
            out += text[i];
            continue;
        }

        const size_t end = text.find (';', i);

        if (end == std::string::npos || end - i > 10)
        {
            out += text[i];
            continue;
        }

        const std::string entity = text.substr (i + 1, end - i - 1);

        if (entity == "amp")
            out += '&';
        else if (entity == "lt")
            out += '<';
        else if (entity == "gt")
            out += '>';
        else if (entity == "quot")
            out += '"';
        else if (entity == "apos")
            out += '\'';
        else if (! entity.empty () && entity[0] == '#')
        {
            const int code = std::atoi (entity.c_str() + 1);
            out += static_cast<char> (code);
        }
        else
        {
            out += text[i];
            continue;
        }

        i = end;
    }

    return out;
}

// ===========================================================================
//  the parameter protocol of the connection point
// ===========================================================================
//
//  The controller pushes the parameter values to the component over the
//  connection point, which is the mechanism the format provides for it.
//  Every push is one message: the message id names the protocol, the
//  attributes carry the parameter id and its normalised value.
//
//  The plug-in makes the message itself: the two ends of the connection are
//  the plug-in's own objects, so the message never leaves the plug-in, its
//  reference count is the one the adapter controls, and the plug-in does
//  not depend on the host being able to create one.

constexpr const char* kParameterMessageId = "AuralisParameter";
constexpr const char* kParameterIdAttribute = "paramId";
constexpr const char* kParameterValueAttribute = "paramValue";

/** The message that says the latency of the plug-in has changed.

    The host has to recompute its delay compensation when the latency of a
    plug-in changes, so the plug-in says so instead of leaving the host to
    find out.
*/
constexpr const char* kLatencyMessageId = "AuralisLatency";

/** The name of the core parameter that carries the processing mode.

    The mode says which of the two configurations of the module the plug-in
    works in: the one that fits the deadline of the host, or the best one the
    module has.  A module that uses a longer window in the offline mode
    delays more, so the latency the plug-in reports follows the mode.
*/
constexpr const char* kProcessingModeParameter = "Processing Mode";

/** The attributes of the parameter message.

    The protocol needs two: the id of the parameter and its normalised
    value.  The list belongs to the message, so it carries no reference
    count of its own.
*/
class ParameterAttributeList final : public Steinberg::Vst::IAttributeList
{
public:
    // -- FUnknown ----------------------------------------------------------
    Steinberg::tresult PLUGIN_API queryInterface (const Steinberg::TUID iid,
                                                  void** obj) SMTG_OVERRIDE
    {
        if (obj == nullptr)
            return kInvalidArgument;

        *obj = nullptr;

        if (FUnknownPrivate::iidEqual (iid, IAttributeList::iid)
            || FUnknownPrivate::iidEqual (iid, FUnknown::iid))
        {
            *obj = static_cast<IAttributeList*> (this);
            return kResultOk;
        }

        return kNoInterface;
    }

    Steinberg::uint32 PLUGIN_API addRef () SMTG_OVERRIDE { return 1; }
    Steinberg::uint32 PLUGIN_API release () SMTG_OVERRIDE { return 1; }

    // -- IAttributeList ----------------------------------------------------
    Steinberg::tresult PLUGIN_API setInt (AttrID id, int64 value) SMTG_OVERRIDE
    {
        if (id == nullptr)
            return kInvalidArgument;

        integers_[id] = value;
        return kResultOk;
    }

    Steinberg::tresult PLUGIN_API getInt (AttrID id, int64& value) SMTG_OVERRIDE
    {
        if (id == nullptr)
            return kInvalidArgument;

        const auto entry = integers_.find (id);

        if (entry == integers_.end ())
            return kResultFalse;

        value = entry->second;
        return kResultOk;
    }

    Steinberg::tresult PLUGIN_API setFloat (AttrID id, double value) SMTG_OVERRIDE
    {
        if (id == nullptr)
            return kInvalidArgument;

        floats_[id] = value;
        return kResultOk;
    }

    Steinberg::tresult PLUGIN_API getFloat (AttrID id, double& value) SMTG_OVERRIDE
    {
        if (id == nullptr)
            return kInvalidArgument;

        const auto entry = floats_.find (id);

        if (entry == floats_.end ())
            return kResultFalse;

        value = entry->second;
        return kResultOk;
    }

    Steinberg::tresult PLUGIN_API setString (AttrID, const TChar*) SMTG_OVERRIDE
    {
        return kResultFalse;   // the protocol does not use strings
    }

    Steinberg::tresult PLUGIN_API getString (AttrID, TChar*, uint32) SMTG_OVERRIDE
    {
        return kResultFalse;
    }

    Steinberg::tresult PLUGIN_API setBinary (AttrID, const void*, uint32) SMTG_OVERRIDE
    {
        return kResultFalse;   // the protocol does not use binary data
    }

    Steinberg::tresult PLUGIN_API getBinary (AttrID, const void*& data,
                                             uint32& sizeInBytes) SMTG_OVERRIDE
    {
        data = nullptr;
        sizeInBytes = 0;
        return kResultFalse;
    }

private:
    std::map<std::string, int64> integers_;
    std::map<std::string, double> floats_;
};

/** The message that carries one parameter value. */
class ParameterMessage final : public Steinberg::Vst::IMessage
{
public:
    // -- FUnknown ----------------------------------------------------------
    Steinberg::tresult PLUGIN_API queryInterface (const Steinberg::TUID iid,
                                                  void** obj) SMTG_OVERRIDE
    {
        if (obj == nullptr)
            return kInvalidArgument;

        *obj = nullptr;

        if (FUnknownPrivate::iidEqual (iid, IMessage::iid)
            || FUnknownPrivate::iidEqual (iid, FUnknown::iid))
        {
            *obj = static_cast<IMessage*> (this);
            addRef();
            return kResultOk;
        }

        return kNoInterface;
    }

    Steinberg::uint32 PLUGIN_API addRef () SMTG_OVERRIDE
    {
        return static_cast<uint32> (FUnknownPrivate::atomicAdd (refCount_, 1));
    }

    Steinberg::uint32 PLUGIN_API release () SMTG_OVERRIDE
    {
        const uint32 remaining = static_cast<uint32> (FUnknownPrivate::atomicAdd (refCount_, -1));

        if (remaining == 0)
            delete this;

        return remaining;
    }

    // -- IMessage ----------------------------------------------------------
    Steinberg::FIDString PLUGIN_API getMessageID () SMTG_OVERRIDE
    {
        return messageId_.c_str();
    }

    void PLUGIN_API setMessageID (Steinberg::FIDString id) SMTG_OVERRIDE
    {
        messageId_ = (id != nullptr) ? id : "";
    }

    Steinberg::Vst::IAttributeList* PLUGIN_API getAttributes () SMTG_OVERRIDE
    {
        return &attributes_;
    }

private:
    std::string messageId_;
    ParameterAttributeList attributes_;
    Steinberg::int32 refCount_ = 1;
};

// ===========================================================================
//  the live objects
// ===========================================================================
//
//  The host creates one component and one controller per plug-in instance,
//  and the component owns a processor.  The interpreter is ended when the
//  last of them goes.

int g_objectCount = 0;

void retainObject()
{
    ++g_objectCount;
}

void releaseObject()
{
    if (--g_objectCount == 0)
    {
        /* The last plug-in object ends the interpreter.  A host unloads the
           module at a moment of its own choosing, and the interpreter's exit
           handler then runs during that unload, where it walks structures that
           are already being torn down.  Ending the interpreter here leaves
           that handler nothing to do. */
        aur_shutdown ();
    }
}

/** Send one parameter value over a connection point.

    The message is read by the other end right away, so it lives on the
    stack for the call.
*/
void sendParameterValue (Steinberg::Vst::IConnectionPoint* point, int paramId, double normalized)
{
    if (point == nullptr)
        return;

    ParameterMessage message;
    message.setMessageID (kParameterMessageId);

    IAttributeList* const attributes = message.getAttributes();

    if (attributes == nullptr)
        return;

    attributes->setInt (kParameterIdAttribute, paramId);
    attributes->setFloat (kParameterValueAttribute, normalized);

    point->notify (&message);
}

/** Tell the other end that the latency of the plug-in has changed.

    The message travels over the connection point the two halves already use.
    It is sent on the thread the change happened on: the audio thread when
    the host automates the parameter, the message thread when the user moves
    it in the interface.
*/
void sendLatencyChanged (Steinberg::Vst::IConnectionPoint* point)
{
    if (point == nullptr)
        return;

    ParameterMessage message;
    message.setMessageID (kLatencyMessageId);

    point->notify (&message);
}

/** The index of the parameter that carries the processing mode, or -1.

    The mode is one of the parameters of the module, so the plug-in publishes
    it the way it publishes every other parameter and the host sets it like a
    parameter.  A module without that parameter says so with -1, and the
    plug-in then has no mode to publish.
*/
int processingModeParamIndex (const ParameterTable& table)
{
    const std::vector<HostParam>& params = table.params ();

    for (size_t i = 0; i < params.size (); ++i)
    {
        if (params[i].name == kProcessingModeParameter)
            return static_cast<int> (i);
    }

    return -1;
}

/** True when the host parameter is the one that carries the mode. */
bool isProcessingModeParam (const ParameterTable& table, Steinberg::Vst::ParamID id)
{
    const int index = table.indexOfParam (id);

    if (index < 0)
        return false;

    const std::vector<HostParam>& params = table.params ();

    return static_cast<size_t> (index) < params.size ()
        && params[static_cast<size_t> (index)].name == kProcessingModeParameter;
}

/** True when the host parameter is the one that carries the program.

    The program change parameter is the plug-in's own: it names the preset
    of the built-in library that the module works with.  A host may drive
    it like any other parameter, so the value that arrives over the
    automation channel has to mean the same thing as the value that
    arrives at the controller.
*/
bool isProgramChangeParam (const ParameterTable& table, Steinberg::Vst::ParamID id)
{
    const int index = table.indexOfParam (id);

    if (index < 0)
        return false;

    const std::vector<HostParam>& params = table.params ();

    return static_cast<size_t> (index) < params.size ()
        && params[static_cast<size_t> (index)].isProgramChange;
}

/** Read one parameter value out of a message.

    Returns false when the message does not carry one: the host sends
    messages of its own over the same connection.
*/
bool readParameterValue (Steinberg::Vst::IMessage* message, int& paramId, double& normalized)
{
    if (message == nullptr)
        return false;

    const char* const id = message->getMessageID ();

    if (id == nullptr || std::strcmp (id, kParameterMessageId) != 0)
        return false;

    IAttributeList* const attributes = message->getAttributes ();

    if (attributes == nullptr)
        return false;

    int64 rawId = 0;
    double value = 0.0;

    if (attributes->getInt (kParameterIdAttribute, rawId) != kResultOk)
        return false;

    if (attributes->getFloat (kParameterValueAttribute, value) != kResultOk)
        return false;

    paramId = static_cast<int> (rawId);
    normalized = value;
    return true;
}

/** Read a state chunk from a stream and split it into its two fields.

    Returns false when the stream does not hold a chunk of ours, in which
    case the caller must leave the plug-in alone.
*/
bool readStateStream (Steinberg::IBStream* state, std::string& chain,
                      std::string& parameterState)
{
    chain.clear ();
    parameterState.clear ();

    if (state == nullptr)
        return false;

    std::string blob;

    // The chunk is the very same bytes the other adapter writes, so it is
    // read back verbatim.
    if (ISizeableStream* const sizeable = [&]
    {
        ISizeableStream* candidate = nullptr;

        if (state->queryInterface (ISizeableStream::iid, reinterpret_cast<void**> (&candidate))
            != kResultOk)
            return static_cast<ISizeableStream*> (nullptr);

        return candidate;
    }())
    {
        int64 size = 0;

        if (sizeable->getStreamSize (size) == kResultOk && size > 0 && size < (1 << 26))
        {
            blob.resize (static_cast<size_t> (size));

            int32 read = 0;
            state->read (&blob[0], static_cast<int32> (blob.size()), &read);
            blob.resize (read > 0 ? static_cast<size_t> (read) : 0);
        }

        sizeable->release();
    }
    else
    {
        char buffer[4096];

        for (;;)
        {
            int32 read = 0;
            const tresult result = state->read (buffer, static_cast<int32> (sizeof (buffer)), &read);

            if (read > 0)
                blob.append (buffer, static_cast<size_t> (read));

            if (read <= 0 || result != kResultOk)
                break;
        }
    }

    return decodeStateChunk (blob.data(), blob.size(), chain, parameterState);
}

} // namespace (anonymous)

// ===========================================================================
//  the presets of the built-in library
// ===========================================================================

const std::vector<ProgramPreset>& programPresetsForModule (const IdentityRecord& identity)
{
    /* The list is read once and kept.  The plug-in is loaded and unloaded
       again and again, and a static that is torn down at the moment the
       module is unloaded walks the containers of a module that is already
       half unloaded.  So the cache is deliberately never destroyed. */
    static std::map<std::string, std::vector<ProgramPreset>>* const cache =
        new std::map<std::string, std::vector<ProgramPreset>>();

    const auto entry = cache->find (identity.moduleCode);

    if (entry != cache->end ())
        return entry->second;

    // The names of the parameters the module has.  A preset that names one
    // this build does not have cannot be loaded, and the library calls such
    // a file damaged.
    std::vector<std::string> names;

    for (const auto& parameter : identity.paramIdMap)
        names.push_back (parameter.second);

    std::vector<ProgramPreset> presets;

    for (ProgramPreset& preset : collectPresets (identity.moduleCode))
    {
        bool sound = true;

        for (const auto& value : preset.values)
        {
            if (std::find (names.begin (), names.end (), value.first) != names.end ())
                continue;

            sound = false;
            break;
        }

        if (sound)
            presets.push_back (std::move (preset));
    }

    return cache->emplace (identity.moduleCode, std::move (presets)).first->second;
}

// ===========================================================================
//  the identity record
// ===========================================================================

std::string IdentityRecord::paramNameFor (int hostParamId) const
{
    const auto entry = paramIdMap.find (hostParamId);
    return entry == paramIdMap.end () ? std::string() : entry->second;
}

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

    // The region is NUL padded, so the record ends at the first NUL.
    std::string text;

    for (int i = 0; i < kIdentityRegionSize && region[i] != '\0'; ++i)
        text += region[i];

    if (text.empty())
        return record;

    JsonValue root;

    {
        JsonParser parser (text.c_str(), text.size());

        if (! parser.parse (root) || root.kind != JsonValue::Kind::Object)
        {
            logMessage ("Auralis: the identity record could not be read.");
            return record;
        }
    }

    record.moduleCode       = root.stringOr ("module_code");
    record.displayName      = root.stringOr ("display_name");
    record.productName      = root.stringOr ("product_name");
    record.fileDescription  = root.stringOr ("file_description");
    record.originalFilename = root.stringOr ("original_filename");
    record.internalName     = root.stringOr ("internal_name");
    record.category         = root.stringOr ("category");
    record.vst3Fuid         = root.stringOr ("vst3_fuid");

    for (char& c : record.vst3Fuid)
        c = static_cast<char> (std::tolower (static_cast<unsigned char> (c)));

    record.vst3FuidHex = record.vst3Fuid;

    record.vst3FuidHex.erase (std::remove (record.vst3FuidHex.begin(), record.vst3FuidHex.end(), '-'),
                               record.vst3FuidHex.end());

    // The AAX effect ids are an array of three-element arrays.
    if (const JsonValue* const ids = root.find ("aax_effect_ids"))
    {
        if (ids->kind == JsonValue::Kind::Array)
        {
            for (const JsonValue& item : ids->items)
            {
                if (item.kind != JsonValue::Kind::Array || item.items.size() != 3)
                    continue;

                std::array<std::string, 3> triple {};

                for (size_t i = 0; i < 3; ++i)
                    triple[i] = item.items[i].kind == JsonValue::Kind::String ? item.items[i].text
                                                                             : std::string();

                record.aaxEffectIds.push_back (std::move (triple));
            }
        }
    }

    // The parameter id map: host parameter id -> canonical core name.
    if (const JsonValue* const map = root.find ("param_id_map"))
    {
        if (map->kind == JsonValue::Kind::Object)
        {
            for (const auto& member : map->members)
            {
                const int id = std::atoi (member.first.c_str());

                if (id < 0 || member.second.kind != JsonValue::Kind::String)
                    continue;

                record.paramIdMap[id] = member.second.text;
            }
        }
    }

    record.valid = ! record.moduleCode.empty()
                    && ! record.displayName.empty()
                    && ! record.paramIdMap.empty();

    return record;
}

const IdentityRecord& sharedIdentity()
{
    /* The record is deliberately never destroyed.  It is a function local
       static, and the module is unloaded at a moment of the host's
       choosing: a static that is torn down at that moment walks the
       containers of a module that is already half unloaded. */
    static const IdentityRecord* const identity = new IdentityRecord (readEmbeddedIdentity());
    return *identity;
}

// ===========================================================================
//  the state chunk
// ===========================================================================

std::string encodeStateChunk (const std::string& chainJson, const std::string& parameterState)
{
    std::string blob;

    blob.reserve (std::strlen (kAuralisStateMarker) + chainJson.size() + parameterState.size() + 3);

    blob += kAuralisStateMarker;
    blob += '\0';
    blob += chainJson;
    blob += '\0';
    blob += parameterState;
    blob += '\0';

    return blob;
}

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

    if (std::strlen (kAuralisStateMarker) != markerEnd
        || std::memcmp (data, kAuralisStateMarker, markerEnd) != 0)
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

std::string encodeParameterState (const std::vector<HostParam>& params,
                                   const std::vector<double>& naturalValues)
{
    // The parameter table of the other adapter is ordered by the parameter
    // id AS TEXT, so the document lists the entries in the same order.
    std::vector<size_t> order (params.size());

    for (size_t i = 0; i < order.size(); ++i)
        order[i] = i;

    std::sort (order.begin(), order.end(), [&params] (size_t a, size_t b)
    {
        return std::to_string (params[a].id) < std::to_string (params[b].id);
    });

    std::string out;
    out += "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\r\n\r\n";
    out += "<";
    out += kStateRootTag;
    out += ">\r\n";

    for (size_t index : order)
    {
        const HostParam& param = params[index];

        const double value = index < naturalValues.size()
                             ? static_cast<double> (static_cast<float> (naturalValues[index]))
                             : 0.0;

        out += "  <";
        out += kStateEntryTag;
        out += " id=\"";
        out += std::to_string (param.id);
        // The name travels beside the id: the id is the position the
        // parameter has today, the name is the identity of the parameter.
        out += "\" name=\"";
        out += escapeXmlText (param.name);
        out += "\" value=\"";
        out += serialiseDouble (value);
        out += "\" />\r\n";
    }

    out += "</";
    out += kStateRootTag;
    out += ">\r\n";

    return out;
}

bool decodeParameterState (const std::string& document, std::vector<ParameterValue>& values)
{
    values.clear();

    const size_t length = document.size();
    size_t pos = 0;
    int depth = 0;

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
            // A processing instruction or a comment: skip it.
            const size_t end = document.find ('>', pos);

            if (end == std::string::npos)
                break;

            pos = end + 1;
            continue;
        }

        if (document[pos] == '/')
        {
            // A closing tag.
            const size_t end = document.find ('>', pos);

            if (end == std::string::npos)
                break;

            pos = end + 1;

            if (depth > 0)
                --depth;

            continue;
        }

        // A start tag: read the name, then the attributes.
        const size_t nameStart = pos;

        while (pos < length
               && ! std::isspace (static_cast<unsigned char> (document[pos]))
               && document[pos] != '>' && document[pos] != '/')
            ++pos;

        const std::string name = document.substr (nameStart, pos - nameStart);

        bool hasId = false;
        bool hasValue = false;
        bool selfClosing = false;
        double id = 0.0;
        double value = 0.0;
        std::string parameterName;

        for (;;)
        {
            while (pos < length && std::isspace (static_cast<unsigned char> (document[pos])))
                ++pos;

            if (pos >= length)
                break;

            if (document[pos] == '>')
            {
                ++pos;
                break;
            }

            if (document[pos] == '/')
            {
                selfClosing = true;
                ++pos;
                continue;
            }

            const size_t attributeStart = pos;

            while (pos < length && document[pos] != '=' && document[pos] != '>'
                   && ! std::isspace (static_cast<unsigned char> (document[pos])))
                ++pos;

            const std::string attributeName = document.substr (attributeStart, pos - attributeStart);

            while (pos < length && std::isspace (static_cast<unsigned char> (document[pos])))
                ++pos;

            std::string attributeValue;

            if (pos < length && document[pos] == '=')
            {
                ++pos;

                while (pos < length && std::isspace (static_cast<unsigned char> (document[pos])))
                    ++pos;

                if (pos < length && (document[pos] == '"' || document[pos] == '\''))
                {
                    const char quote = document[pos++];
                    const size_t valueStart = pos;

                    while (pos < length && document[pos] != quote)
                        ++pos;

                    attributeValue = document.substr (valueStart, pos - valueStart);

                    if (pos < length)
                        ++pos;
                }
                else
                {
                    const size_t valueStart = pos;

                    while (pos < length && ! std::isspace (static_cast<unsigned char> (document[pos]))
                           && document[pos] != '>' && document[pos] != '/')
                        ++pos;

                    attributeValue = document.substr (valueStart, pos - valueStart);
                }
            }

            attributeValue = decodeXmlEntities (attributeValue);

            if (attributeName == "id")
            {
                id = std::strtod (attributeValue.c_str(), nullptr);
                hasId = true;
            }
            else if (attributeName == "name")
            {
                parameterName = attributeValue;
            }
            else if (attributeName == "value")
            {
                value = std::strtod (attributeValue.c_str(), nullptr);
                hasValue = true;
            }
        }

        if (name == kStateEntryTag && depth == 1 && hasId && hasValue)
        {
            ParameterValue entry;
            entry.name = parameterName;
            entry.id = static_cast<int> (id);
            entry.value = value;
            values.push_back (entry);
        }

        // A start tag that is not closed opens a level; the state document
        // only nests one level deep, so the counter is enough.
        if (! selfClosing)
            ++depth;
    }

    return true;
}

/** The values of a decoded parameter document, addressed by the id.

    The name is the identity of a parameter, so an entry that carries one
    goes to the parameter with that name, whatever id the parameter has now.
    An entry that carries only an id is read as the position the parameter
    had when the document was written: that is the rule of the old format,
    and after an insertion of a parameter in the middle of the module it
    names a different parameter.
    An entry that names a parameter this binary does not know is reported
    and skipped; it is never handed to a neighbour.
*/
std::map<int, double> valuesById (const ParameterTable& table,
                                   const std::vector<ParameterValue>& values)
{
    std::map<int, double> out;

    for (const ParameterValue& entry : values)
    {
        int id = entry.id;

        if (! entry.name.empty ())
        {
            id = -1;

            for (const HostParam& param : table.params())
            {
                if (param.name == entry.name)
                {
                    id = param.id;
                    break;
                }
            }

            if (id < 0)
            {
                const std::string message =
                    "Auralis: the state names the parameter '" + entry.name +
                    "', which this binary does not know.  The value is skipped.";

                logMessage (message.c_str());
                continue;
            }
        }

        out[id] = entry.value;
    }

    return out;
}

// ===========================================================================
//  the parameter table
// ===========================================================================

#if AURALIS_HAVE_GENERATED_HEADER

namespace
{

/** True when the generated table carries a help column.

    The column is the one the parameter table of the product grew; a table
    that does not have it yet simply has no help, and the adapter says so
    instead of pretending.
*/
template <typename Meta, typename = void>
struct HasHelp : std::false_type
{
};

template <typename Meta>
struct HasHelp<Meta, std::void_t<decltype (std::declval<const Meta&> ().help)>> : std::true_type
{
};

/** The sentence that explains a parameter, or nothing. */
template <typename Meta>
std::string helpText (const Meta& meta)
{
    if constexpr (HasHelp<Meta>::value)
        return meta.help != nullptr ? std::string (meta.help) : std::string();

    return {};
}

} // namespace (anonymous)

#endif  // AURALIS_HAVE_GENERATED_HEADER

void ParameterTable::build (const IdentityRecord& identity,
                            const std::vector<ProgramPreset>& presets)
{
    params_.clear();
    paramIndex_.clear();

    if (identity.valid)
    {
#if AURALIS_HAVE_GENERATED_HEADER
        for (int i = 0; i < kAuralisParamCount; ++i)
        {
            const ParamMeta& meta = kAuralisParams[i];

            // Only the parameters of the stamped module belong to this binary.
            if (identity.moduleCode != meta.moduleCode)
                continue;

            // The identity record is authoritative about which ids this module
            // owns; the generated header supplies the metadata.
            const std::string coreName = identity.paramNameFor (meta.id);

            if (coreName.empty())
                continue;

            HostParam param;
            param.id = meta.id;
            param.name = coreName;
            param.displayName = meta.name;
            param.unit = meta.unit;
            param.group = meta.group;
            param.curve = meta.curve;
            param.curveExponent = meta.curveExponent;
            param.help = helpText (meta);
            param.automatable = meta.automatable;
            param.stepped = meta.stepped;
            param.minimum = static_cast<double> (meta.minimum);
            param.maximum = static_cast<double> (meta.maximum);
            param.defaultValue = static_cast<double> (meta.defaultValue);

            switch (meta.type)
            {
                case ParamType::Float: param.kind = ParamKind::Float; break;
                case ParamType::Int:   param.kind = ParamKind::Int;   break;
                case ParamType::Bool:  param.kind = ParamKind::Bool;  break;

                case ParamType::Enum:
                {
                    param.kind = ParamKind::Enum;

                    for (int c = 0; c < meta.choiceCount; ++c)
                        param.choices.emplace_back (meta.choices[c]);

                    // The generated table stores the default of an enum as the
                    // index of the default choice.
                    param.defaultValue = clampValue (std::round (param.defaultValue), 0.0,
                                                     static_cast<double> (lastChoiceIndex (param)));

                    // An enum is a list: the host has to draw the choices
                    // instead of a slider.  The format has a flag for it.
                    param.isList = true;

                    break;
                }
            }

            params_.push_back (std::move (param));
        }
#else
        logMessage ("Auralis: AuralisGenerated.h was not found, so this binary exposes no "
                    "parameters.  Run the build's header step first.");
#endif
    }

    // The bypass switch: the parameter the format reserves for it.  It is
    // part of the table, so it is part of the state: a session that is saved
    // with the module bypassed comes back bypassed.
    {
        HostParam bypass;

        bypass.id = kAuralisBypassParamId;
        bypass.name = "Bypass";
        bypass.displayName = kAuralisBypassTitle;
        bypass.kind = ParamKind::Bool;
        bypass.minimum = 0.0;
        bypass.maximum = 1.0;
        bypass.defaultValue = 0.0;
        bypass.automatable = true;
        bypass.stepped = true;
        bypass.isBypass = true;
        bypass.help = "Bypass the module: the audio passes through it unchanged.";

        params_.push_back (std::move (bypass));
    }

    // The program change parameter: what a host's program list drives.  A
    // list with one entry has nothing to choose, so the parameter is only
    // published when the module has more than one preset.
    if (presets.size () > 1)
    {
        HostParam program;

        program.id = kAuralisProgramParamId;
        program.name = "Program";
        program.displayName = kAuralisProgramTitle;
        program.kind = ParamKind::Enum;
        program.minimum = 0.0;
        program.maximum = static_cast<double> (presets.size () - 1);
        program.defaultValue = 0.0;
        program.automatable = true;
        program.stepped = true;
        program.isList = true;
        program.isProgramChange = true;
        program.help = "The preset of the built-in library that the module works with.";

        for (const ProgramPreset& preset : presets)
            program.choices.push_back (preset.name);

        params_.push_back (std::move (program));
    }

    std::sort (params_.begin(), params_.end(),
               [] (const HostParam& a, const HostParam& b) { return a.id < b.id; });

    for (size_t i = 0; i < params_.size(); ++i)
        paramIndex_[params_[i].id] = i;

    values_ = std::make_unique<std::atomic<double>[]> (params_.size());

    for (size_t i = 0; i < params_.size(); ++i)
    {
        const HostParam& param = params_[i];
        const double normalized = normalizedFromNatural (param, param.defaultValue);
        values_[i].store (normalized, std::memory_order_relaxed);
    }
}

int ParameterTable::indexOfParam (ParamID id) const
{
    const auto entry = paramIndex_.find (static_cast<int> (id));
    return entry == paramIndex_.end () ? -1 : static_cast<int> (entry->second);
}

bool ParameterTable::isBypassed () const
{
    const int index = indexOfParam (kAuralisBypassParamId);

    if (index < 0)
        return false;

    return values_[static_cast<size_t> (index)].load (std::memory_order_relaxed) >= 0.5;
}

double ParameterTable::normalizedValue (ParamID id) const
{
    const int index = indexOfParam (id);

    if (index < 0 || static_cast<size_t> (index) >= params_.size())
        return 0.0;

    return values_[static_cast<size_t> (index)].load (std::memory_order_relaxed);
}

double ParameterTable::normalizedValueAt (size_t index) const
{
    if (index >= params_.size())
        return 0.0;

    return values_[index].load (std::memory_order_relaxed);
}

void ParameterTable::setNormalizedValue (ParamID id, double value)
{
    const int index = indexOfParam (id);

    if (index < 0 || static_cast<size_t> (index) >= params_.size())
        return;

    values_[static_cast<size_t> (index)].store (clampValue (value, 0.0, 1.0),
                                                std::memory_order_relaxed);
}

double ParameterTable::naturalValue (const HostParam& param, double normalized) const
{
    normalized = clampValue (normalized, 0.0, 1.0);

    if (param.kind == ParamKind::Enum)
    {
        const double last = static_cast<double> (lastChoiceIndex (param));

        if (last <= 0.0)
            return 0.0;

        return std::round (normalized * last);
    }

    double low = param.minimum;
    double high = param.maximum;

    if (param.kind == ParamKind::Int)
    {
        low = std::floor (low);
        high = std::ceil (high);
    }

    if (high <= low)
        return low;

    if (param.kind == ParamKind::Bool)
        return normalized >= 0.5 ? 1.0 : 0.0;

    if (param.curve == "log" && low > 0.0)
    {
        const double lowLog = std::log (low);
        const double highLog = std::log (high);
        return std::exp (lowLog + normalized * (highLog - lowLog));
    }

    if (param.curve == "taper")
    {
        // The shape of a fader: v = lo + (hi - lo) * n ** p.  The travel
        // spends most of its length below unity, so unity sits in the
        // middle of it.
        const double exponent = param.curveExponent > 0.0 ? param.curveExponent : 1.0;
        return low + (high - low) * std::pow (normalized, exponent);
    }

    return low + normalized * (high - low);
}

double ParameterTable::normalizedFromNatural (const HostParam& param, double natural) const
{
    if (param.kind == ParamKind::Enum)
    {
        const double last = static_cast<double> (lastChoiceIndex (param));

        if (last <= 0.0)
            return 0.0;

        return clampValue (natural / last, 0.0, 1.0);
    }

    double low = param.minimum;
    double high = param.maximum;

    if (param.kind == ParamKind::Int)
    {
        low = std::floor (low);
        high = std::ceil (high);
    }

    if (high <= low)
        return 0.0;

    if (param.kind == ParamKind::Bool)
        return natural != 0.0 ? 1.0 : 0.0;

    natural = clampValue (natural, low, high);

    if (param.curve == "log" && low > 0.0)
    {
        const double lowLog = std::log (low);
        const double highLog = std::log (high);
        return clampValue ((std::log (natural) - lowLog) / (highLog - lowLog), 0.0, 1.0);
    }

    if (param.curve == "taper")
    {
        // The inverse of the taper: the position of a value is the root of
        // the share of the range the value stands at.
        const double exponent = param.curveExponent > 0.0 ? param.curveExponent : 1.0;
        const double share = (natural - low) / (high - low);
        return clampValue (std::pow (share, 1.0 / exponent), 0.0, 1.0);
    }

    return clampValue ((natural - low) / (high - low), 0.0, 1.0);
}

std::string ParameterTable::parameterState () const
{
    std::vector<double> natural;
    natural.reserve (params_.size());

    for (size_t i = 0; i < params_.size(); ++i)
        natural.push_back (naturalValue (params_[i], normalizedValueAt (i)));

    return encodeParameterState (params_, natural);
}

void ParameterTable::applyParameterState (const std::map<int, double>& valuesById)
{
    for (const auto& entry : valuesById)
    {
        const auto index = paramIndex_.find (entry.first);

        if (index == paramIndex_.end())
            continue;   // a parameter this binary does not know about

        const HostParam& param = params_[index->second];

        double natural = entry.second;

        if (param.kind == ParamKind::Enum)
        {
            const int choice = static_cast<int> (std::lround (natural));
            natural = clampValue (static_cast<double> (choice), 0.0,
                                  static_cast<double> (lastChoiceIndex (param)));
        }

        values_[index->second].store (normalizedFromNatural (param, natural),
                                      std::memory_order_relaxed);
    }
}

// ===========================================================================
//  the component
// ===========================================================================

AuralisComponent::AuralisComponent()
    : identity_ (sharedIdentity())
{
    retainObject();

    parameters_.build (identity_, programPresetsForModule (identity_));

    processorLifetime_ = std::make_shared<ProcessorLifetime>();
    processor_ = new AuralisProcessor (this);

    if (! identity_.valid)
    {
        logMessage ("Auralis: this binary carries no identity record.  It was not stamped, "
                    "so it does not implement any module.");
        return;
    }

    logMessage ("Auralis: " + identity_.moduleCode + " (" + identity_.displayName + ") with "
                + std::to_string (parameters_.params ().size()) + " parameter(s), class id "
                + identity_.vst3Fuid);

    /* The ramp is a DECLARATION: the format has no field for it, so the
       plug-in says it in its own report and in the description of every
       parameter it moves. */
    logMessage ("Auralis: a parameter change is applied over "
                + std::to_string (static_cast<int> (AUR_PARAM_RAMP_MS))
                + " ms, not as a step: the value moves to its target over the ramp.");
}

AuralisComponent::~AuralisComponent()
{
    destroyEngine();

    // The processor belongs to the component.  It deletes itself when its
    // last reference is gone; if it is still here the host never released
    // its own reference, and the component drops its own.
    if (processor_ != nullptr)
    {
        if (processorLifetime_ && processorLifetime_->alive.load (std::memory_order_acquire))
            processor_->release();

        processor_ = nullptr;
    }

    releaseObject();
}

void AuralisComponent::pushAllParams()
{
    if (engine_ == nullptr || isRestoringState_.load (std::memory_order_relaxed))
        return;

    const std::vector<HostParam>& params = parameters_.params ();

    for (size_t i = 0; i < params.size(); ++i)
    {
        const HostParam& param = params[i];

        // The bypass switch and the program change parameter are the
        // plug-in's own: the core knows neither of them.
        if (param.isBypass || param.isProgramChange)
            continue;

        const double natural = parameters_.naturalValue (param, parameters_.normalizedValueAt (i));

        if (param.kind == ParamKind::Enum)
        {
            if (param.choices.empty())
                continue;

            int index = static_cast<int> (std::lround (natural));
            index = std::max (0, std::min (static_cast<int> (param.choices.size ()) - 1, index));

            // The core takes the choice STRING for an enum parameter.
            aur_engine_set_param_text (engine_, 0, param.name.c_str (),
                                       param.choices[static_cast<size_t> (index)].c_str ());
            continue;
        }

        aur_engine_set_param (engine_, 0, param.name.c_str (), natural);
    }
}

void AuralisComponent::pushAllParamsToController()
{
    if (controllerPoint_ == nullptr)
        return;   // the host has not connected the two yet

    const std::vector<HostParam>& params = parameters_.params ();

    for (size_t i = 0; i < params.size(); ++i)
        sendParameterValue (controllerPoint_, params[i].id, parameters_.normalizedValueAt (i));
}

void AuralisComponent::pullParamsFromCore()
{
    if (engine_ == nullptr)
        return;

    const std::vector<HostParam>& params = parameters_.params ();

    for (size_t i = 0; i < params.size(); ++i)
    {
        const HostParam& param = params[i];

        // The bypass switch and the program change parameter are the
        // plug-in's own: the core knows neither of them.
        if (param.isBypass || param.isProgramChange)
            continue;

        char buffer[256] = { 0 };

        const int error = aur_engine_get_param (engine_, 0, param.name.c_str (), buffer, sizeof (buffer));

        if (error != AUR_OK)
            continue;

        if (param.kind == ParamKind::Enum)
        {
            const std::string choice (buffer);

            for (size_t c = 0; c < param.choices.size (); ++c)
            {
                if (param.choices[c] != choice)
                    continue;

                parameters_.setNormalizedValue (static_cast<ParamID> (param.id),
                                               parameters_.normalizedFromNatural (param, static_cast<double> (c)));
                break;
            }

            continue;
        }

        const double value = std::strtod (buffer, nullptr);
        parameters_.setNormalizedValue (static_cast<ParamID> (param.id),
                                       parameters_.normalizedFromNatural (param, value));
    }
}

bool AuralisComponent::createEngine (double sampleRate)
{
    destroyEngine();

    if (! identity_.valid)
        return false;

    if (sampleRate <= 0.0)
        return false;

    /* The core is a delayed import: it has to be loaded before the first
       call into it.  Without the runtime the plug-in does not make an
       engine, and the host gets the audio unchanged. */
    if (! prepareRuntime ())
    {
        logMessage ("Auralis: the shared runtime was not found; the plug-in passes the audio through.");
        return false;
    }

    engine_ = aur_engine_create (static_cast<uint32_t> (std::llround (sampleRate)), 2);

    if (engine_ == nullptr)
    {
        logMessage ("Auralis: could not create the core engine.");
        return false;
    }

    engineSampleRate_ = sampleRate;

    const int index = aur_engine_add_module (engine_, identity_.moduleCode.c_str ());

    if (index < 0)
    {
        logMessage ("Auralis: the core refused the module " + identity_.moduleCode + ": "
                    + std::string (aur_engine_last_error (engine_)));
        destroyEngine();
        return false;
    }

    pushAllParams();
    return true;
}

void AuralisComponent::destroyEngine()
{
    if (engine_ != nullptr)
    {
        aur_engine_destroy (engine_);
        engine_ = nullptr;
    }

    engineSampleRate_ = 0.0;
    hasReportedProcessError_ = false;
}

std::string AuralisComponent::serialiseChain() const
{
    if (engine_ == nullptr)
        return {};

    char* text = nullptr;
    const int64_t length = aur_engine_serialize (engine_, &text);

    if (length <= 0 || text == nullptr)
    {
        if (text != nullptr)
            aur_engine_free (text);

        return {};
    }

    std::string result (text, static_cast<size_t> (length));
    aur_engine_free (text);

    return result;
}

/** Put one parameter of the host into the core, applied over the ramp.

    The change the host delivers with a block is a change the user made, so
    it is applied over the declared ramp: the value moves to its target
    over AUR_PARAM_RAMP_MS milliseconds instead of jumping there, and a
    change that arrives while a ramp runs turns the ramp around from where
    it is.

    The value that is transferred over the connection point is not a change
    the user made -- it is the state of the plug-in being put back where it
    belongs -- so it is applied at once, by the plain call that
    pushAllParams makes every block.

    Only a continuous parameter is ramped: an enum, a switch and a count
    have no value between two of their values to move through.
*/
static void rampParameterIntoCore (AurEngine* engine, const ParameterTable& parameters,
                                   ParamID id, double normalized)
{
    if (engine == nullptr)
        return;

    const int index = parameters.indexOfParam (id);

    if (index < 0)
        return;

    const HostParam& param = parameters.params ()[static_cast<size_t> (index)];

    // The bypass switch and the program change parameter are the plug-in's
    // own: the core knows neither of them.
    if (param.isBypass || param.isProgramChange)
        return;

    if (param.kind != ParamKind::Float)
        return;

    const double natural = parameters.naturalValue (param, normalized);

    aur_engine_set_param_ramped (engine, 0, param.name.c_str (), natural,
                                 AUR_PARAM_RAMP_MS);
}

/** Put the values of a preset into a parameter table.

    A preset names the parameters it sets and the values it sets them to.
    The values are written in the natural units of the parameters, and a
    parameter this build does not have is ignored: the preset was written
    for another version of the module.

    The table is the one of the side that applies the preset: the
    controller when the host tells the controller the program, the
    component when the host automates it.  Both apply the same values, so
    the two ways of asking for a preset end in the same state.
*/
static bool applyPresetToTable (const ProgramPreset& preset, ParameterTable& table)
{
    const std::vector<HostParam>& params = table.params ();

    bool applied = false;

    for (const auto& value : preset.values)
    {
        const HostParam* param = nullptr;

        for (const auto& candidate : params)
        {
            if (candidate.name != value.first)
                continue;

            param = &candidate;
            break;
        }

        // A parameter this build does not have is ignored: the preset was
        // written for another version of the module.
        if (param == nullptr)
            continue;

        double natural = 0.0;

        if (param->kind == ParamKind::Enum)
        {
            // An enum parameter carries the CHOICE STRING.
            size_t choice = param->choices.size ();

            for (size_t c = 0; c < param->choices.size (); ++c)
            {
                if (param->choices[c] == value.second)
                {
                    choice = c;
                    break;
                }
            }

            if (choice == param->choices.size ())
                continue;   // a choice this build does not have

            natural = static_cast<double> (choice);
        }
        else if (param->kind == ParamKind::Bool)
        {
            natural = (value.second == "1" || value.second == "true" || value.second == "True")
                      ? 1.0 : 0.0;
        }
        else
        {
            natural = std::strtod (value.second.c_str (), nullptr);
        }

        table.setNormalizedValue (static_cast<ParamID> (param->id),
                                  table.normalizedFromNatural (*param, natural));
        applied = true;
    }

    return applied;
}

void AuralisComponent::applyParameterChanges (IParameterChanges* changes)
{
    if (changes == nullptr)
        return;

    const int32 count = changes->getParameterCount ();

    for (int32 i = 0; i < count; ++i)
    {
        IParamValueQueue* const queue = changes->getParameterData (i);

        if (queue == nullptr)
            continue;

        const ParamID id = queue->getParameterId ();
        const int32 points = queue->getPointCount ();

        for (int32 p = 0; p < points; ++p)
        {
            int32 offset = 0;
            ParamValue value = 0.0;

            if (queue->getPoint (p, offset, value) != kResultOk)
                continue;

            /* The offset the host delivers the change at is deliberately
               not used.  A controller change has no offset, so honouring
               the one the automation carries would put the two paths a
               few samples apart: the same value would sound different
               depending on how it arrived.  The change starts at the
               beginning of the block on both paths, and the ramp makes it
               smooth there. */
            (void) offset;

            parameters_.setNormalizedValue (id, value);

            /* The mode decides how long the chain of the core takes, so the
               core is told at once and the host is told that the latency
               has changed: it has to recompute its delay compensation. */
            if (isProcessingModeParam (parameters_, id))
            {
                pushAllParams();
                sendLatencyChanged (controllerPoint_);
            }
            else if (isProgramChangeParam (parameters_, id))
            {
                /* The program change parameter carries a preset, so the
                   values the preset names have to reach the core: the
                   module has to work with the preset the host asked for.
                   The controller does the same when the host tells it the
                   program, so a host that automates the program and a host
                   that tells the controller the program end in the same
                   state and hear the same thing. */
                const std::vector<ProgramPreset>& presets =
                    programPresetsForModule (identity_);

                const int index = parameters_.indexOfParam (id);
                const int choice = index < 0
                    ? -1
                    : static_cast<int> (std::lround (parameters_.naturalValue (
                          parameters_.params ()[static_cast<size_t> (index)], value)));

                if (choice >= 0 && choice < static_cast<int> (presets.size ()))
                {
                    if (applyPresetToTable (presets[static_cast<size_t> (choice)], parameters_))
                    {
                        /* The values the preset names reach the core over
                           the ramp, the same way they reach it when the
                           controller pushes them. */
                        const std::vector<HostParam>& table = parameters_.params ();

                        for (size_t t = 0; t < table.size (); ++t)
                            rampParameterIntoCore (engine_, parameters_, table[t].id,
                                                   parameters_.normalizedValueAt (t));

                        /* The controller holds the values the host sees, so
                           it is told what the preset set: the host that
                           automated the program has to see the values of
                           the preset as well. */
                        pushAllParamsToController ();
                    }
                }
            }
            else
            {
                /* The change the host made is a change the user made, so it
                   is applied over the ramp: a step is a click, and a click
                   is a defect the user hears. */
                rampParameterIntoCore (engine_, parameters_, id, value);
            }
        }

        queue->release();
    }
}

// -- FUnknown ---------------------------------------------------------------

tresult PLUGIN_API AuralisComponent::queryInterface (const TUID iid, void** obj)
{
    if (obj == nullptr)
        return kInvalidArgument;

    *obj = nullptr;

    if (FUnknownPrivate::iidEqual (iid, IComponent::iid))
    {
        *obj = static_cast<IComponent*> (this);
        addRef();
        return kResultOk;
    }

    if (FUnknownPrivate::iidEqual (iid, IAudioProcessor::iid))
    {
        /* The processor is an object of its own.  The host holds the
           component and the controller as two pointers and releases each
           of them once, so a pointer that is an alias of the component's
           own interface must not be able to bring the component down with
           it. */
        if (processor_ == nullptr)
            return kNoInterface;

        *obj = static_cast<IAudioProcessor*> (processor_);
        processor_->addRef();
        return kResultOk;
    }

    if (FUnknownPrivate::iidEqual (iid, IConnectionPoint::iid))
    {
        *obj = static_cast<IConnectionPoint*> (this);
        addRef();
        return kResultOk;
    }

    if (FUnknownPrivate::iidEqual (iid, IPluginBase::iid))
    {
        // The plug-in base interface is reached through the component, so
        // that the cast is not ambiguous.
        *obj = static_cast<IPluginBase*> (static_cast<IComponent*> (this));
        addRef();
        return kResultOk;
    }

    if (FUnknownPrivate::iidEqual (iid, FUnknown::iid))
    {
        *obj = static_cast<FUnknown*> (static_cast<IComponent*> (this));
        addRef();
        return kResultOk;
    }

    return kNoInterface;
}

uint32 PLUGIN_API AuralisComponent::addRef()
{
    return static_cast<uint32> (FUnknownPrivate::atomicAdd (refCount_, 1));
}

uint32 PLUGIN_API AuralisComponent::release()
{
    /* The ordinary rule: the last reference takes the object with it.  The
       host holds the component as one pointer and releases it once, and
       the processor is a separate object, so the count matches what the
       host took. */
    const uint32 remaining = static_cast<uint32> (FUnknownPrivate::atomicAdd (refCount_, -1));

    if (remaining == 0)
        delete this;

    return remaining;
}

// -- IPluginBase ------------------------------------------------------------

tresult PLUGIN_API AuralisComponent::initialize (FUnknown* /*context*/)
{
    return kResultOk;
}

tresult PLUGIN_API AuralisComponent::terminate()
{
    destroyEngine();
    return kResultOk;
}

// -- IComponent -------------------------------------------------------------

tresult PLUGIN_API AuralisComponent::getControllerClassId (TUID classId)
{
    if (classId == nullptr)
        return kInvalidArgument;

    /* The controller is a class of its own.  The host asks the factory for
       it with this id, and the factory creates the controller because the
       host asked for the IEditController interface. */
    identityClassId (identity_, classId);
    return kResultOk;
}

tresult PLUGIN_API AuralisComponent::setIoMode (IoMode /*mode*/)
{
    return kResultOk;
}

int32 PLUGIN_API AuralisComponent::getBusCount (MediaType type, BusDirection dir)
{
    if (type != kAudio)
        return 0;

    if (dir == kInput || dir == kOutput)
        return 1;

    return 0;
}

tresult PLUGIN_API AuralisComponent::getBusInfo (MediaType type, BusDirection dir, int32 index,
                                                  BusInfo& bus)
{
    if (type != kAudio || index != 0)
        return kInvalidArgument;

    if (dir != kInput && dir != kOutput)
        return kInvalidArgument;

    bus.mediaType = kAudio;
    bus.direction = dir;
    bus.channelCount = 2;
    bus.busType = kMain;
    bus.flags = BusInfo::kDefaultActive;

    const char* const name = (dir == kInput) ? "Stereo In" : "Stereo Out";
    copyToString128 (bus.name, name);

    return kResultOk;
}

tresult PLUGIN_API AuralisComponent::activateBus (MediaType type, BusDirection dir, int32 index,
                                                   TBool /*state*/)
{
    if (type != kAudio || index != 0)
        return kInvalidArgument;

    if (dir != kInput && dir != kOutput)
        return kInvalidArgument;

    return kResultOk;
}

tresult PLUGIN_API AuralisComponent::getRoutingInfo (RoutingInfo& /*inInfo*/,
                                                      RoutingInfo& /*outInfo*/)
{
    // One input and one output, straight through: there is no routing to
    // describe.
    return kNotImplemented;
}

tresult PLUGIN_API AuralisComponent::setActive (TBool state)
{
    if (state)
    {
        // The host activates the plug-in before it reports the sample
        // rate, so the engine is built in setupProcessing.  When the rate
        // is already known (a second activation) the engine is built here,
        // because the host asks for the latency right after this call.
        if (engine_ == nullptr && hasSampleRate_)
            createEngine (sampleRate_);
    }
    else
    {
        destroyEngine();
    }

    return kResultOk;
}

tresult PLUGIN_API AuralisComponent::setState (IBStream* state)
{
    std::string chain;
    std::string parameterState;

    if (! readStateStream (state, chain, parameterState))
        return kResultFalse;   // not our state; leave the plug-in alone

    isRestoringState_.store (true, std::memory_order_relaxed);

    // The host visible parameter state first.
    if (! parameterState.empty())
    {
        std::vector<ParameterValue> values;

        if (decodeParameterState (parameterState, values))
            parameters_.applyParameterState (valuesById (parameters_, values));
    }

    // Then the core chain, which may carry learned state as well.  The core
    // is authoritative, so its values are pulled back into the host
    // parameters afterwards.
    if (! chain.empty())
    {
        if (engine_ != nullptr)
        {
            if (aur_engine_deserialize (engine_, chain.c_str ()) == AUR_OK)
                pullParamsFromCore();
        }
        else
        {
            // There is no engine yet; the chain is applied when the plug-in
            // is activated.
            pendingChain_ = chain;
        }
    }

    isRestoringState_.store (false, std::memory_order_relaxed);

    // The controller holds the values the host sees.  The state carries
    // them as well, so they are pushed over the connection and the two do
    // not drift apart.
    pushAllParamsToController();

    /* The state may carry the name of a model, and a model that reads a
       context is latency: the context of the model is part of the delay the
       plug-in reports.  The host has to recompute its delay compensation,
       so it is told that the latency changed. */
    sendLatencyChanged (controllerPoint_);

    return kResultOk;
}

tresult PLUGIN_API AuralisComponent::getState (IBStream* state)
{
    if (state == nullptr)
        return kInvalidArgument;

    const std::string blob = encodeStateChunk (serialiseChain(), parameters_.parameterState());

    int32 written = 0;
    const tresult result = state->write (const_cast<char*> (blob.data()),
                                         static_cast<int32> (blob.size()), &written);

    if (result != kResultOk || written != static_cast<int32> (blob.size()))
        return kResultFalse;

    return kResultOk;
}

// -- IUnitInfo ---------------------------------------------------------------
//
//  The program list of the plug-in.  It holds the presets of the built-in
//  library that belong to this module, and the program change parameter is
//  what a host drives to load one of them.

int32 PLUGIN_API AuralisController::getUnitCount ()
{
    // The root unit: the plug-in itself.
    return 1;
}

tresult PLUGIN_API AuralisController::getUnitInfo (int32 unitIndex, UnitInfo& info)
{
    if (unitIndex != 0)
        return kInvalidArgument;

    info.id = kRootUnitId;
    info.parentUnitId = kNoParentUnitId;

    const std::string name = identity_.displayName.empty () ? std::string ("Auralis")
                                                            : identity_.displayName;

    copyToString128 (info.name, name);

    // The unit points at the program list, and the reference must not
    // change: a unit that has no list says so.
    info.programListId = presets_.empty () ? kNoProgramListId : 0;

    return kResultOk;
}

int32 PLUGIN_API AuralisController::getProgramListCount ()
{
    return presets_.empty () ? 0 : 1;
}

tresult PLUGIN_API AuralisController::getProgramListInfo (int32 listIndex, ProgramListInfo& info)
{
    if (listIndex != 0 || presets_.empty ())
        return kInvalidArgument;

    info.id = 0;
    copyToString128 (info.name, "Presets");
    info.programCount = static_cast<int32> (presets_.size ());

    return kResultOk;
}

tresult PLUGIN_API AuralisController::getProgramName (ProgramListID listId, int32 programIndex,
                                                       String128 name)
{
    if (listId != 0 || programIndex < 0
        || static_cast<size_t> (programIndex) >= presets_.size ())
        return kInvalidArgument;

    copyToString128 (name, presets_[static_cast<size_t> (programIndex)].name);

    return kResultOk;
}

tresult PLUGIN_API AuralisController::getProgramInfo (ProgramListID listId, int32 programIndex,
                                                       Steinberg::Vst::CString attributeId,
                                                       String128 attributeValue)
{
    if (listId != 0 || programIndex < 0 || attributeId == nullptr
        || static_cast<size_t> (programIndex) >= presets_.size ())
        return kInvalidArgument;

    const ProgramPreset& preset = presets_[static_cast<size_t> (programIndex)];
    const std::string attribute (attributeId);

    if (attribute == "category")
    {
        copyToString128 (attributeValue, preset.category);
        return kResultOk;
    }

    if (attribute == "description")
    {
        copyToString128 (attributeValue, preset.description);
        return kResultOk;
    }

    return kResultFalse;
}

tresult PLUGIN_API AuralisController::hasProgramPitchNames (ProgramListID /*listId*/,
                                                            int32 /*programIndex*/)
{
    // The presets carry no pitches: they are settings of a module, not
    // samples of an instrument.
    return kResultFalse;
}

tresult PLUGIN_API AuralisController::getProgramPitchName (ProgramListID /*listId*/,
                                                            int32 /*programIndex*/,
                                                            int16 /*midiPitch*/,
                                                            String128 /*name*/)
{
    return kResultFalse;
}

Steinberg::Vst::UnitID PLUGIN_API AuralisController::getSelectedUnit ()
{
    return kRootUnitId;
}

tresult PLUGIN_API AuralisController::selectUnit (UnitID unitId)
{
    if (unitId != kRootUnitId)
        return kInvalidArgument;

    return kResultOk;
}

tresult PLUGIN_API AuralisController::getUnitByBus (MediaType /*type*/, BusDirection /*dir*/,
                                                     int32 /*busIndex*/, int32 /*channel*/,
                                                     UnitID& unitId)
{
    // One unit, and it is the root unit: every bus belongs to it.
    unitId = kRootUnitId;
    return kResultOk;
}

tresult PLUGIN_API AuralisController::setUnitProgramData (int32 /*listOrUnitId*/,
                                                           int32 /*programIndex*/,
                                                           IBStream* /*data*/)
{
    // The host sends program data to a plug-in that keeps its programs in
    // its own format.  Ours are loaded through the program change
    // parameter, so there is nothing to receive here.
    return kNotImplemented;
}

// -- IParameterHelp ----------------------------------------------------------

int32 PLUGIN_API AuralisController::getParameterHelpCount ()
{
    int32 count = 0;

    for (const HostParam& param : parameters_.params ())
    {
        if (! param.help.empty ())
            ++count;
    }

    return count;
}

tresult PLUGIN_API AuralisController::getParameterHelp (ParamID id, String128 help)
{
    const int index = parameters_.indexOfParam (id);

    if (index < 0)
        return kResultFalse;

    const HostParam& param = parameters_.params ()[static_cast<size_t> (index)];

    if (param.help.empty ())
        return kResultFalse;

    /* The format has no field for the ramp, so the ramp is said in the
       parameter's own description: a change of this parameter is applied
       over AUR_PARAM_RAMP_MS milliseconds, not as a step. */
    std::string text = param.help;

    if (param.kind == ParamKind::Float)
    {
        text += " A change is applied over ";
        text += std::to_string (static_cast<int> (AUR_PARAM_RAMP_MS));
        text += " ms, not as a step.";
    }

    copyToString128 (help, text);

    return kResultOk;
}

// -- IConnectionPoint -------------------------------------------------------

tresult PLUGIN_API AuralisComponent::connect (IConnectionPoint* other)
{
    if (other == nullptr)
        return kInvalidArgument;

    // The controller's connection point.  The controller pushes the
    // parameter values over it.
    controllerPoint_ = other;
    return kResultOk;
}

tresult PLUGIN_API AuralisComponent::disconnect (IConnectionPoint* other)
{
    if (other == nullptr)
        return kInvalidArgument;

    if (controllerPoint_ == other)
        controllerPoint_ = nullptr;

    return kResultOk;
}

tresult PLUGIN_API AuralisComponent::notify (IMessage* message)
{
    if (message == nullptr)
        return kInvalidArgument;

    int paramId = 0;
    double value = 0.0;

    if (! readParameterValue (message, paramId, value))
        return kResultOk;    // a message that is not ours

    // The value goes into the local copy the audio thread reads; it takes
    // no lock and it does not ask the core.
    parameters_.setNormalizedValue (static_cast<ParamID> (paramId), value);

    /* The mode is the one value that has to reach the core at once: it
       decides how long the chain takes, so the host has to be told that the
       latency has changed. */
    if (isProcessingModeParam (parameters_, static_cast<ParamID> (paramId)))
    {
        pushAllParams();
        sendLatencyChanged (controllerPoint_);

        return kResultOk;
    }

    /* A change the host makes through the controller is a change the user
       made, so it is applied over the ramp, the same way the change the
       host delivers with a block is.  A host that automates a parameter and
       a host that tells the controller the value expect the same sound
       either way. */
    rampParameterIntoCore (engine_, parameters_, static_cast<ParamID> (paramId), value);

    return kResultOk;
}

// -- the audio side ---------------------------------------------------------

tresult AuralisComponent::setupProcessing (ProcessSetup& setup)
{
    sampleRate_ = setup.sampleRate;
    hasSampleRate_ = true;

    if (engine_ == nullptr || engineSampleRate_ != setup.sampleRate)
        createEngine (setup.sampleRate);

    // A chain document that arrived before the engine existed is restored
    // now that there is an engine to restore it into.  This only happens
    // on the message thread, which is where setupProcessing is called from.
    if (! pendingChain_.empty() && engine_ != nullptr)
    {
        const std::string chain = pendingChain_;
        pendingChain_.clear();

        /* The state carries the mode the session was written in, so the
           latency of the plug-in can be a different one after a restore.
           The host has to be told. */
        const int modeIndex = processingModeParamIndex (parameters_);
        const double modeBefore = (modeIndex >= 0)
            ? parameters_.normalizedValueAt (static_cast<size_t> (modeIndex))
            : 0.0;

        if (aur_engine_deserialize (engine_, chain.c_str ()) == AUR_OK)
        {
            pullParamsFromCore();

            if (modeIndex >= 0)
            {
                const double modeAfter =
                    parameters_.normalizedValueAt (static_cast<size_t> (modeIndex));

                if (modeAfter != modeBefore)
                    sendLatencyChanged (controllerPoint_);
            }
        }
    }

    /* A chain that changes the rate -- the resampler -- works at another
       rate than the host's.  A host cannot be told to change its rate, so
       the plug-in says what it is doing: the core converts the chain's
       output back to the host's rate, and the look ahead of that
       conversion is part of the latency the plug-in reports. */
    if (engine_ != nullptr)
    {
        const int64_t chainRate = aur_engine_output_sample_rate (engine_);
        const int64_t hostRate = static_cast<int64_t> (std::llround (sampleRate_));

        if (chainRate > 0 && chainRate != hostRate)
        {
            logMessage ("Auralis: the chain works at " + std::to_string (chainRate)
                        + " Hz and the host runs at " + std::to_string (hostRate)
                        + " Hz; the core converts the chain's output back to the host's rate.");
        }
    }

    return kResultOk;
}

tresult AuralisComponent::process (ProcessData& data)
{
    if (data.numInputs < 1 || data.numOutputs < 1)
        return kResultOk;

    const int32 frames = data.numSamples;

    if (frames <= 0)
        return kResultOk;

    const AudioBusBuffers& input = data.inputs[0];
    const AudioBusBuffers& output = data.outputs[0];

    // The host's automation is applied even when the core cannot run, so
    // that the controller shows the same values the host does.
    applyParameterChanges (data.inputParameterChanges);

    /* The bypass switch.
       A bypassed module is transparent: the audio passes through it, and
       the module adds no delay of its own.  So the block is copied and the
       core is not asked at all - not even to keep its state, because a
       core that is fed silence would ring on after the bypass is switched
       off.  The digital ceiling still holds: the plug-in never hands the
       host a sample outside [-1, 1]. */
    if (parameters_.isBypassed ())
    {
        copyAndClip (input, output, frames);

        return kResultOk;
    }

    // The host does not clear the output buffers, so the plug-in has to
    // write all of them.  When the core cannot run, the audio is passed
    // through unchanged - and the digital ceiling still holds.
    if (engine_ == nullptr || input.numChannels != 2 || output.numChannels != 2
        || input.channelBuffers32 == nullptr || output.channelBuffers32 == nullptr)
    {
        copyAndClip (input, output, frames);

        return kResultOk;
    }

    pushAllParams();

    const float* const inputs[2] = { input.channelBuffers32[0], input.channelBuffers32[1] };
    float* const outputs[2] = { output.channelBuffers32[0], output.channelBuffers32[1] };

    /* The host's block is the number of samples the host asked for, and it
       is the number of samples the plug-in hands over: the host plays the
       block it gave, and the plug-in may not hand it a different number.

       A chain that changes the rate -- the resampler -- works at another
       rate, and the samples it makes belong to that rate.  The core is
       asked for the result at the host's rate, so it converts the chain's
       output back to it, and the block the host gets has the duration of
       the block it gave.  The look ahead of that conversion is part of
       the latency the plug-in reports. */
    AurBlockCounts counts;
    counts.consumed = 0;
    counts.produced = 0;

    const int error = aur_engine_process (engine_, inputs, outputs,
                                          static_cast<uint32> (frames),
                                          static_cast<uint32> (frames),
                                          static_cast<uint32> (std::llround (sampleRate_)),
                                          &counts);

    if (error != AUR_OK && ! hasReportedProcessError_)
    {
        hasReportedProcessError_ = true;
        logMessage ("Auralis: the core failed to process a block: "
                    + std::string (aur_engine_last_error (engine_)));
    }

    /* The block the core has just written is the chain's output, and the
       core has clipped it at the limit the chain declares: the digital
       ceiling, multiplied by the gain the chain's own level controls
       declare.  The plug-in adds no level of its own, so the block the
       host is given is inside that limit -- that is the promise the
       plug-in makes, and it is kept by the core in this path.

       The plug-in does not clip the block again: the limit of a chain that
       carries the user's own gain is above the digital ceiling, and
       clipping it here would take the user's own gain away. */
    return kResultOk;
}

uint32 AuralisComponent::getLatencySamples() const
{
    // A bypassed module adds no delay: the audio passes through it.
    if (parameters_.isBypassed ())
        return 0;

    if (engine_ == nullptr)
        return 0;

    const int64_t latency = aur_engine_latency_samples (engine_);
    return latency > 0 ? static_cast<uint32> (latency) : 0;
}

tresult AuralisComponent::setBusArrangements (SpeakerArrangement* inputs, int32 numIns,
                                               SpeakerArrangement* outputs, int32 numOuts)
{
    // One stereo input and one stereo output, and nothing else.
    if (numIns != 1 || numOuts != 1)
        return kResultFalse;

    if (inputs == nullptr || outputs == nullptr)
        return kInvalidArgument;

    const SpeakerArrangement stereo = SpeakerArr::kStereo;

    if (inputs[0] != stereo || outputs[0] != stereo)
        return kResultFalse;

    return kResultOk;
}

tresult AuralisComponent::getBusArrangement (BusDirection dir, int32 index,
                                              SpeakerArrangement& arr)
{
    if (index != 0)
        return kInvalidArgument;

    if (dir != kInput && dir != kOutput)
        return kInvalidArgument;

    arr = SpeakerArr::kStereo;
    return kResultOk;
}

// ===========================================================================
//  the processor
// ===========================================================================

AuralisProcessor::AuralisProcessor (AuralisComponent* owner)
    : owner_ (owner)
    , lifetime_ (owner != nullptr ? owner->processorLifetime_ : nullptr)
{
    retainObject();
}

AuralisProcessor::~AuralisProcessor()
{
    if (lifetime_)
        lifetime_->alive.store (false, std::memory_order_release);

    releaseObject();
}

tresult PLUGIN_API AuralisProcessor::queryInterface (const TUID iid, void** obj)
{
    if (obj == nullptr)
        return kInvalidArgument;

    *obj = nullptr;

    if (FUnknownPrivate::iidEqual (iid, IAudioProcessor::iid))
    {
        *obj = static_cast<IAudioProcessor*> (this);
        addRef();
        return kResultOk;
    }

    if (FUnknownPrivate::iidEqual (iid, FUnknown::iid))
    {
        *obj = static_cast<FUnknown*> (this);
        addRef();
        return kResultOk;
    }

    return kNoInterface;
}

uint32 PLUGIN_API AuralisProcessor::addRef()
{
    return static_cast<uint32> (FUnknownPrivate::atomicAdd (refCount_, 1));
}

uint32 PLUGIN_API AuralisProcessor::release()
{
    /* The ordinary rule: the last reference takes the object with it.  A
       host may hold an alias of this interface and release it twice; the
       owner then sees that the object is gone and keeps its hands off it. */
    const uint32 remaining = static_cast<uint32> (FUnknownPrivate::atomicAdd (refCount_, -1));

    if (remaining == 0)
        delete this;

    return remaining;
}

tresult PLUGIN_API AuralisProcessor::canProcessSampleSize (int32 symbolicSampleSize)
{
    if (symbolicSampleSize == kSample32)
        return kResultOk;

    return kResultFalse;
}

uint32 PLUGIN_API AuralisProcessor::getLatencySamples()
{
    return owner_ != nullptr ? owner_->getLatencySamples() : 0;
}

tresult PLUGIN_API AuralisProcessor::setupProcessing (ProcessSetup& setup)
{
    return owner_ != nullptr ? owner_->setupProcessing (setup) : kResultFalse;
}

tresult PLUGIN_API AuralisProcessor::setProcessing (TBool /*state*/)
{
    return kResultOk;
}

tresult PLUGIN_API AuralisProcessor::process (ProcessData& data)
{
    return owner_ != nullptr ? owner_->process (data) : kResultFalse;
}

uint32 PLUGIN_API AuralisProcessor::getTailSamples()
{
    /* The tail comes from the core.  A module declares how long it keeps
       ringing after the input stopped, and the engine reports the longest
       of the tails of the chain.  A module that makes a sound of its own
       -- a generator, a bed of ambience -- never goes quiet, and the core
       says so with AUR_TAIL_INFINITE, which is the value the format uses
       for it.

       The answer comes from the core, so the call reaches Python: it
       belongs to the message thread, which is where a host asks for the
       latency as well. */
    if (owner_ == nullptr)
        return kNoTail;

    // A bypassed module is transparent: it rings for nothing.
    if (owner_->parameters_.isBypassed ())
        return 0;

    AurEngine* const engine = owner_->engine_;

    if (engine == nullptr)
        return kNoTail;

    const int64_t tail = aur_engine_tail_samples (engine);

    if (tail < 0)
        return kNoTail;

    if (tail >= AUR_TAIL_INFINITE)
        return kInfiniteTail;

    return static_cast<uint32> (tail);
}

tresult PLUGIN_API AuralisProcessor::setBusArrangements (SpeakerArrangement* inputs, int32 numIns,
                                                         SpeakerArrangement* outputs, int32 numOuts)
{
    return owner_ != nullptr ? owner_->setBusArrangements (inputs, numIns, outputs, numOuts)
                              : kResultFalse;
}

tresult PLUGIN_API AuralisProcessor::getBusArrangement (BusDirection dir, int32 index,
                                                        SpeakerArrangement& arr)
{
    return owner_ != nullptr ? owner_->getBusArrangement (dir, index, arr) : kResultFalse;
}

// ===========================================================================
//  the controller
// ===========================================================================

AuralisController::AuralisController()
    : identity_ (sharedIdentity())
{
    retainObject();

    presets_ = programPresetsForModule (identity_);

    parameters_.build (identity_, presets_);

    if (! identity_.valid)
    {
        logMessage ("Auralis: this binary carries no identity record.  It was not stamped, "
                    "so it does not implement any module.");
        return;
    }

    logMessage ("Auralis: the controller of " + identity_.moduleCode + " ("
                + identity_.displayName + ") with "
                + std::to_string (parameters_.params ().size()) + " parameter(s), "
                + std::to_string (presets_.size()) + " preset(s)");
}

AuralisController::~AuralisController()
{
    releaseObject();
}

void AuralisController::pushParameter (int paramId, double normalized)
{
    if (componentPoint_ == nullptr)
        return;   // the host has not connected the two yet

    // The component reads the value out of the message right away.
    sendParameterValue (componentPoint_, paramId, normalized);
}

void AuralisController::pushAllParams()
{
    const std::vector<HostParam>& params = parameters_.params ();

    for (size_t i = 0; i < params.size(); ++i)
        pushParameter (params[i].id, parameters_.normalizedValueAt (i));
}

void AuralisController::applyParameterDocument (const std::string& document)
{
    if (document.empty())
        return;

    std::vector<ParameterValue> values;

    if (decodeParameterState (document, values))
        parameters_.applyParameterState (valuesById (parameters_, values));
}

bool AuralisController::applyPreset (size_t index)
{
    if (index >= presets_.size ())
        return false;

    if (! applyPresetToTable (presets_[index], parameters_))
        return false;

    // The audio side works with the values the host sees, so the whole
    // table is pushed over the connection.
    pushAllParams ();

    return true;
}

// -- FUnknown ---------------------------------------------------------------

tresult PLUGIN_API AuralisController::queryInterface (const TUID iid, void** obj)
{
    if (obj == nullptr)
        return kInvalidArgument;

    *obj = nullptr;

    if (FUnknownPrivate::iidEqual (iid, IEditController::iid))
    {
        *obj = static_cast<IEditController*> (this);
        addRef();
        return kResultOk;
    }

    if (FUnknownPrivate::iidEqual (iid, IEditController2::iid))
    {
        *obj = static_cast<IEditController2*> (this);
        addRef();
        return kResultOk;
    }

    // The program list: the presets of the built-in library that belong to
    // this module.
    if (FUnknownPrivate::iidEqual (iid, IUnitInfo::iid))
    {
        *obj = static_cast<IUnitInfo*> (this);
        addRef();
        return kResultOk;
    }

    // The help text of the parameters.  The format has no field for it, so
    // the plug-in publishes it through an interface of its own.
    if (FUnknownPrivate::iidEqual (iid, IParameterHelp::iid))
    {
        *obj = static_cast<IParameterHelp*> (this);
        addRef();
        return kResultOk;
    }

    if (FUnknownPrivate::iidEqual (iid, IConnectionPoint::iid))
    {
        *obj = static_cast<IConnectionPoint*> (this);
        addRef();
        return kResultOk;
    }

    if (FUnknownPrivate::iidEqual (iid, IPluginBase::iid))
    {
        *obj = static_cast<IPluginBase*> (static_cast<IEditController*> (this));
        addRef();
        return kResultOk;
    }

    if (FUnknownPrivate::iidEqual (iid, FUnknown::iid))
    {
        *obj = static_cast<FUnknown*> (static_cast<IEditController*> (this));
        addRef();
        return kResultOk;
    }

    return kNoInterface;
}

uint32 PLUGIN_API AuralisController::addRef()
{
    return static_cast<uint32> (FUnknownPrivate::atomicAdd (refCount_, 1));
}

uint32 PLUGIN_API AuralisController::release()
{
    /* The ordinary rule: the host holds the controller as one pointer and
       releases it once, so the last release takes the object with it. */
    const uint32 remaining = static_cast<uint32> (FUnknownPrivate::atomicAdd (refCount_, -1));

    if (remaining == 0)
        delete this;

    return remaining;
}

// -- IPluginBase ------------------------------------------------------------

tresult PLUGIN_API AuralisController::initialize (FUnknown* /*context*/)
{
    return kResultOk;
}

tresult PLUGIN_API AuralisController::terminate()
{
    return kResultOk;
}

// -- IEditController --------------------------------------------------------

tresult PLUGIN_API AuralisController::setComponentState (IBStream* state)
{
    // The host hands the controller the component's state; the parameter
    // values are taken from it.
    std::string chain;
    std::string parameterState;

    if (! readStateStream (state, chain, parameterState))
        return kResultFalse;

    applyParameterDocument (parameterState);
    return kResultOk;
}

tresult PLUGIN_API AuralisController::getState (IBStream* state)
{
    if (state == nullptr)
        return kInvalidArgument;

    /* The controller has no engine, so the chain field is empty.  The
       container is the shared one, so a host that saves the controller's
       state can hand it to the component, and the other way round. */
    const std::string blob = encodeStateChunk (std::string(), parameters_.parameterState());

    int32 written = 0;
    const tresult result = state->write (const_cast<char*> (blob.data()),
                                         static_cast<int32> (blob.size()), &written);

    if (result != kResultOk || written != static_cast<int32> (blob.size()))
        return kResultFalse;

    return kResultOk;
}

tresult PLUGIN_API AuralisController::setState (IBStream* state)
{
    std::string chain;
    std::string parameterState;

    if (! readStateStream (state, chain, parameterState))
        return kResultFalse;

    applyParameterDocument (parameterState);
    return kResultOk;
}

tresult PLUGIN_API AuralisController::getParameterCount()
{
    return static_cast<int32> (parameters_.params ().size());
}

tresult PLUGIN_API AuralisController::getParameterInfo (int32 paramIndex, ParameterInfo& info)
{
    const std::vector<HostParam>& params = parameters_.params ();

    if (paramIndex < 0 || static_cast<size_t> (paramIndex) >= params.size())
        return kInvalidArgument;

    const HostParam& param = params[static_cast<size_t> (paramIndex)];

    info.id = static_cast<ParamID> (param.id);

    copyToString128 (info.title, param.displayName);
    copyToString128 (info.shortTitle, param.displayName);
    copyToString128 (info.units, param.unit);

    info.stepCount = 0;

    if (param.kind == ParamKind::Enum)
        info.stepCount = static_cast<int32> (lastChoiceIndex (param));
    else if (param.kind == ParamKind::Bool)
        info.stepCount = 1;
    else if (param.kind == ParamKind::Int)
        info.stepCount = static_cast<int32> (std::ceil (param.maximum) - std::floor (param.minimum));

    info.defaultNormalizedValue = parameters_.normalizedFromNatural (param, param.defaultValue);
    info.unitId = -1;
    info.flags = param.automatable ? ParameterInfo::kCanAutomate : ParameterInfo::kNoFlags;

    // A list is drawn as a list: the format has a flag for it, and a host
    // that reads it draws the choices instead of a slider.
    if (param.isList)
        info.flags |= ParameterInfo::kIsList;

    // The bypass switch: the parameter the format reserves for it.
    if (param.isBypass)
        info.flags |= ParameterInfo::kIsBypass;

    // The program change parameter: the unit it belongs to is the root unit,
    // and that unit holds the program list.
    if (param.isProgramChange)
    {
        info.flags |= ParameterInfo::kIsProgramChange;
        info.unitId = kRootUnitId;
    }

    return kResultOk;
}

tresult PLUGIN_API AuralisController::getParamStringByValue (ParamID id, ParamValue valueNormalized,
                                                              String128 string)
{
    const int index = parameters_.indexOfParam (id);

    if (index < 0)
        return kInvalidArgument;

    const HostParam& param = parameters_.params ()[static_cast<size_t> (index)];
    const double natural = parameters_.naturalValue (param, valueNormalized);

    std::string text;

    if (param.kind == ParamKind::Enum)
    {
        const int choice = static_cast<int> (std::lround (natural));

        if (choice >= 0 && choice < static_cast<int> (param.choices.size ()))
            text = param.choices[static_cast<size_t> (choice)];
    }
    else if (param.kind == ParamKind::Bool)
    {
        text = natural != 0.0 ? "On" : "Off";
    }
    else if (param.kind == ParamKind::Int)
    {
        text = std::to_string (static_cast<long long> (std::llround (natural)));
    }
    else
    {
        std::ostringstream stream;
        stream.imbue (std::locale::classic());
        stream << std::fixed << std::setprecision (2) << natural;
        text = stream.str();
    }

    if (! param.unit.empty())
    {
        if (! text.empty())
            text += ' ';

        text += param.unit;
    }

    copyToString128 (string, text);

    return kResultOk;
}

tresult PLUGIN_API AuralisController::getParamValueByString (ParamID id, TChar* string,
                                                              ParamValue& valueNormalized)
{
    const int index = parameters_.indexOfParam (id);

    if (index < 0 || string == nullptr)
        return kInvalidArgument;

    const HostParam& param = parameters_.params ()[static_cast<size_t> (index)];
    const std::string text = fromString16 (string);

    if (param.kind == ParamKind::Enum)
    {
        for (size_t c = 0; c < param.choices.size (); ++c)
        {
            if (param.choices[c] == text)
            {
                valueNormalized = parameters_.normalizedFromNatural (param, static_cast<double> (c));
                return kResultOk;
            }
        }

        return kResultFalse;
    }

    if (param.kind == ParamKind::Bool)
    {
        // The text of a switch is "On" or "Off".  The number is read as
        // well, so a host that writes "1" gets what it means.
        if (text == "On" || text == "on" || text == "ON" || text == "True" || text == "true"
            || text == "TRUE" || text == "1")
        {
            valueNormalized = 1.0;
            return kResultOk;
        }

        if (text == "Off" || text == "off" || text == "OFF" || text == "False" || text == "false"
            || text == "FALSE" || text == "0")
        {
            valueNormalized = 0.0;
            return kResultOk;
        }

        return kResultFalse;
    }

    valueNormalized = parameters_.normalizedFromNatural (param, std::strtod (text.c_str(), nullptr));
    return kResultOk;
}

ParamValue PLUGIN_API AuralisController::normalizedParamToPlain (ParamID id, ParamValue valueNormalized)
{
    const int index = parameters_.indexOfParam (id);

    if (index < 0)
        return 0.0;

    return parameters_.naturalValue (parameters_.params ()[static_cast<size_t> (index)], valueNormalized);
}

ParamValue PLUGIN_API AuralisController::plainParamToNormalized (ParamID id, ParamValue plainValue)
{
    const int index = parameters_.indexOfParam (id);

    if (index < 0)
        return 0.0;

    return parameters_.normalizedFromNatural (parameters_.params ()[static_cast<size_t> (index)], plainValue);
}

ParamValue PLUGIN_API AuralisController::getParamNormalized (ParamID id)
{
    return parameters_.normalizedValue (id);
}

tresult PLUGIN_API AuralisController::setParamNormalized (ParamID id, ParamValue value)
{
    if (parameters_.indexOfParam (id) < 0)
        return kInvalidArgument;

    // The controller must never pass the value back to the host; it only
    // updates its own copy and pushes the value to the component, which is
    // where the audio thread reads it from.
    parameters_.setNormalizedValue (id, value);
    pushParameter (static_cast<int> (id), value);

    // The program change parameter carries a preset: setting it loads that
    // preset, which sets the parameters the preset names.
    if (static_cast<int> (id) == kAuralisProgramParamId)
    {
        const int index = parameters_.indexOfParam (id);

        if (index >= 0)
        {
            const HostParam& param = parameters_.params ()[static_cast<size_t> (index)];
            const double natural = parameters_.naturalValue (param, value);

            applyPreset (static_cast<size_t> (std::lround (natural)));
        }
    }

    return kResultOk;
}

tresult PLUGIN_API AuralisController::setComponentHandler (IComponentHandler* handler)
{
    componentHandler_ = handler;
    return kResultOk;
}

IPlugView* PLUGIN_API AuralisController::createView (FIDString /*name*/)
{
    // The Auralis editor belongs to the user interface layer, which is not
    // part of the adapter.  The host shows its own generic editor.
    return nullptr;
}

// -- IEditController2 -------------------------------------------------------

tresult PLUGIN_API AuralisController::setKnobMode (KnobMode /*mode*/)
{
    return kResultOk;
}

tresult PLUGIN_API AuralisController::openHelp (TBool /*onlyCheck*/)
{
    return kResultFalse;
}

tresult PLUGIN_API AuralisController::openAboutBox (TBool /*onlyCheck*/)
{
    return kResultFalse;
}

// -- IConnectionPoint -------------------------------------------------------

tresult PLUGIN_API AuralisController::connect (IConnectionPoint* other)
{
    if (other == nullptr)
        return kInvalidArgument;

    // The component's connection point.  The values are pushed over it.
    componentPoint_ = other;

    // The values the host sees are the values the audio side works with, so
    // they are pushed as soon as the connection is made.
    pushAllParams();

    return kResultOk;
}

tresult PLUGIN_API AuralisController::disconnect (IConnectionPoint* other)
{
    if (other == nullptr)
        return kInvalidArgument;

    if (componentPoint_ == other)
        componentPoint_ = nullptr;

    return kResultOk;
}

tresult PLUGIN_API AuralisController::notify (IMessage* message)
{
    if (message == nullptr)
        return kInvalidArgument;

    /* The component says the latency of the plug-in has changed.  Only the
       controller can talk to the host about it, and the host has to
       recompute its delay compensation. */
    if (message->getMessageID () != nullptr
        && std::strcmp (message->getMessageID (), kLatencyMessageId) == 0)
    {
        if (componentHandler_ != nullptr)
            componentHandler_->restartComponent (Steinberg::Vst::kLatencyChanged);

        return kResultOk;
    }

    int paramId = 0;
    double value = 0.0;

    if (! readParameterValue (message, paramId, value))
    {
        // The host may send messages of its own over the connection.  They
        // are not ours, and saying so is the honest answer.
        return kResultOk;
    }

    /* The component pushes the values it restored from a state, so that
       the values the host sees are the ones the audio side works with.
       The value is not pushed back: the other end already has it. */
    parameters_.setNormalizedValue (static_cast<ParamID> (paramId), value);
    return kResultOk;
}

// ===========================================================================
//  the class factory
// ===========================================================================

AuralisFactory::AuralisFactory() = default;
AuralisFactory::~AuralisFactory() = default;

tresult PLUGIN_API AuralisFactory::queryInterface (const TUID iid, void** obj)
{
    if (obj == nullptr)
        return kInvalidArgument;

    *obj = nullptr;

    if (FUnknownPrivate::iidEqual (iid, IPluginFactory2::iid))
    {
        *obj = static_cast<IPluginFactory2*> (this);
        addRef();
        return kResultOk;
    }

    if (FUnknownPrivate::iidEqual (iid, IPluginFactory::iid))
    {
        *obj = static_cast<IPluginFactory*> (this);
        addRef();
        return kResultOk;
    }

    if (FUnknownPrivate::iidEqual (iid, FUnknown::iid))
    {
        *obj = static_cast<FUnknown*> (this);
        addRef();
        return kResultOk;
    }

    return kNoInterface;
}

uint32 PLUGIN_API AuralisFactory::addRef()
{
    return static_cast<uint32> (FUnknownPrivate::atomicAdd (refCount_, 1));
}

uint32 PLUGIN_API AuralisFactory::release()
{
    /* The factory is a static object of the module, so it is not deleted
       here: the host releases the pointers it asked for through
       queryInterface, and the module is unloaded right after the last
       release. */
    FUnknownPrivate::atomicAdd (refCount_, -1);

    return static_cast<uint32> (refCount_);
}

tresult PLUGIN_API AuralisFactory::getFactoryInfo (PFactoryInfo* info)
{
    if (info == nullptr)
        return kInvalidArgument;

    *info = PFactoryInfo ("Auralis", "https://auralis.example", "support@auralis.example",
                          PFactoryInfo::kUnicode);

    return kResultOk;
}

int32 PLUGIN_API AuralisFactory::countClasses()
{
    // One class: the component and the controller are two instances of it,
    // and the factory tells them apart by the interface the host asks for.
    return 1;
}

tresult PLUGIN_API AuralisFactory::getClassInfo (int32 index, PClassInfo* info)
{
    if (info == nullptr || index != 0)
        return kInvalidArgument;

    const IdentityRecord& identity = sharedIdentity();

    TUID cid { 0 };
    identityClassId (identity, cid);

    *info = PClassInfo (cid, PClassInfo::kManyInstances, kVstAudioEffectClass,
                        identity.productName.empty () ? "Auralis" : identity.productName.c_str ());

    return kResultOk;
}

tresult PLUGIN_API AuralisFactory::getClassInfo2 (int32 index, PClassInfo2* info)
{
    if (info == nullptr || index != 0)
        return kInvalidArgument;

    const IdentityRecord& identity = sharedIdentity();

    TUID cid { 0 };
    identityClassId (identity, cid);

    *info = PClassInfo2 (cid, PClassInfo::kManyInstances, kVstAudioEffectClass,
                         identity.productName.empty () ? "Auralis" : identity.productName.c_str (),
                         0, PlugType::kFxRestoration, nullptr, "1.0.0", kVstVersionString);

    return kResultOk;
}

tresult PLUGIN_API AuralisFactory::createInstance (FIDString cid, FIDString iid, void** obj)
{
    if (obj == nullptr)
        return kInvalidArgument;

    *obj = nullptr;

    if (cid == nullptr || iid == nullptr)
        return kInvalidArgument;

    const IdentityRecord& identity = sharedIdentity();

    TUID wanted { 0 };
    identityClassId (identity, wanted);

    if (! FUnknownPrivate::iidEqual (cid, wanted))
        return kNoInterface;

    // The role of the instance is decided by the interface the host asks
    // for: the component carries the audio, the buses and the state, the
    // controller carries the parameters.
    if (FUnknownPrivate::iidEqual (iid, IComponent::iid))
    {
        auto* const component = new AuralisComponent();

        if (component->queryInterface (iid, obj) != kResultOk)
        {
            component->release();
            return kNoInterface;
        }

        component->release();
        return kResultOk;
    }

    if (FUnknownPrivate::iidEqual (iid, IEditController::iid)
        || FUnknownPrivate::iidEqual (iid, IEditController2::iid))
    {
        auto* const controller = new AuralisController();

        if (controller->queryInterface (iid, obj) != kResultOk)
        {
            controller->release();
            return kNoInterface;
        }

        controller->release();
        return kResultOk;
    }

    return kNoInterface;
}

} // namespace auralis

// ===========================================================================
//  the entry points the VST3 format expects
// ===========================================================================

extern "C"
{

/** The class factory.  Every VST3 host looks for this symbol first. */
SMTG_EXPORT_SYMBOL Steinberg::IPluginFactory* PLUGIN_API GetPluginFactory()
{
    static auralis::AuralisFactory factory;
    return static_cast<Steinberg::IPluginFactory*> (&factory);
}

/** The module entry point.

    The VST3 format uses it on the platforms whose loader calls it; the
    Windows loader only needs GetPluginFactory, but exporting it as well
    costs nothing and keeps the module loadable everywhere.
*/
SMTG_EXPORT_SYMBOL bool PLUGIN_API ModuleEntry (void* /*sharedLibraryHandle*/)
{
    return true;
}

/** The counterpart of ModuleEntry. */
SMTG_EXPORT_SYMBOL bool PLUGIN_API ModuleExit()
{
    return true;
}

} // extern "C"
