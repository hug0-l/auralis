// ============================================================================
//  qa/vst3host/main.cpp
//
//  Auralis VST3 host - an independent, DAW-free harness that loads a built
//  VST3 module and exercises it end to end.
//
//  Why this exists
//  ---------------
//  The plug-in build produces one binary per Auralis module.  A DAW would
//  normally be the thing that loads it, but a DAW is a poor test instrument:
//  it is heavy, it is interactive, and it hides failures behind a UI.  This
//  host does the minimum a host has to do - load the binary, enumerate the
//  classes, instantiate the component and its controller, wire them
//  together, ask the audio processor what it can do, drive a parameter,
//  push a block of audio through it and save/restore its state - and then
//  reports the result in a machine readable form.
//
//  The interface layout is taken from the VST3 SDK headers that ship in
//  vendor/vst3sdk (pluginterfaces/base/ipluginbase.h holds PClassInfo and
//  PClassInfo2, pluginterfaces/vst/ holds the VST interfaces).  No field
//  offset is guessed anywhere in this file.
//
//  Output contract
//  ---------------
//    stdout : one JSON object, and nothing else.  The plug-in's own chatter
//             is redirected to stderr while it is loaded, so the JSON stays
//             parseable.
//    stderr : a human readable report.
//
//  Exit codes
//  ----------
//    0  everything worked
//    1  the module could not be loaded or instantiated (this includes
//       "no module given")
//    2  a step failed (the module was fine, the exercise was not)
//
//  Usage
//  -----
//    vst3host <module>            the module: a .vst3 bundle, a .vst3.dll,
//                                  or a folder that contains one
//    vst3host --list <folder>     only list the modules found in <folder>
//    vst3host --tone <module>     additionally process a 1 kHz tone block
//    vst3host --search-bound N    search the delay up to N samples instead
//                                  of up to the bound of the catalogue.  A
//                                  diagnostic: the default is the bound
//                                  that the catalogue of the product gives.
//
//  WHAT THE HOST CHECKS
//  --------------------
//    A host that only asks questions is not much use.  This one measures:
//
//      * the connection between the component and the controller,
//      * the bus layout: the channel counts, the names, the directions and
//        the speaker arrangements, against what the plug-in says about
//        them,
//      * the parameter catalogue: the titles, the value texts, the text to
//        value and the value to text round trips,
//      * that every published parameter is carried by the plug-in's state,
//      * that the host's automation reaches the plug-in, and that the value
//        the plug-in then processes with is exactly the value that was sent,
//      * that the state of the controller and the state of the component
//        agree, and that both survive a save and restore,
//      * the latency the plug-in reports against the delay it really adds,
//        searched over the whole range the catalogue of the product can
//        declare, and not over a range that a constant decides,
//      * the tail the plug-in reports against the time it really rings,
//      * that the plug-in really wrote its output: the host fills the output
//        buffers with a value no plug-in produces and counts the samples the
//        plug-in left untouched.
//
//  THE DELAY AND THE CATALOGUE
//  ---------------------------
//    The delay a plug-in puts into its output is measured with a sweep: the
//    sweep goes in, the processed sweep comes out, and the cross correlation
//    says where the output sits against the input.  The correlation is
//    searched over a range of lags, and the range has to be wide enough to
//    hold every delay the product is able to produce.
//
//    The range is not a constant of this file.  It is the largest delay the
//    modules of the catalogue declare, plus a margin of one analysis window
//    (Setup::kLatencySearchMargin).  The catalogue is written by
//    qa/vst3host/make_catalogue.py, which asks the product's own registry
//    for every module, and the host reads it from qa/vst3host/catalogue.tsv
//    or from the file AURALIS_VST3HOST_CATALOGUE names.
//
//    A constant would go blind the moment a module declares a longer delay
//    than the constant allows, and a host that cannot measure a delay
//    cannot tell a plug-in that lies about it: the host would report the
//    edge of its search instead of the delay.
//
//  THE ORDER OF THE EXERCISE
//  -------------------------
//    The VST3 format asks the host to activate the buses before it sets the
//    component active, because a plug-in only processes an activated bus:
//    "The plug-in should only processed an activated bus, the host could
//    provide less AudioBusBuffers in the process call if last busses are not
//    activated" (pluginterfaces/vst/ivstcomponent.h).  A plug-in that takes
//    the specification seriously writes nothing at all when the host forgets,
//    and the host sees a peak of 1e30 (its own sentinel) and 512 unwritten
//    output samples for a 256 frame stereo block.
//
//    The order the host therefore walks is
//
//        setupProcessing
//        activateBus (true) for every bus, the audio ones and the event
//                     ones, in both directions
//        setActive (true)
//        setProcessing (true)
//
//    and the way out is the same list in reverse:
//
//        setProcessing (false)
//        setActive (false)
//        activateBus (false) for every bus
//
//    The buses are activated again for every instance the host makes, and
//    they are deactivated when the instance is let go.
//
//  Environment
//  -----------
//    AURALIS_VST3HOST_DEPS   extra directories (path-separator separated)
//                            that are added to the shared library search
//                            path before the module is loaded.  Auralis
//                            plug-ins link against the core C ABI shared
//                            library, so this is where it has to point.
//    AURALIS_VST3HOST_CATALOGUE
//                            the file the catalogue of the product is read
//                            from.  Without it the host looks for
//                            qa/vst3host/catalogue.tsv in the tree the host
//                            and the module live in.
// ============================================================================

// The interface identifiers have to be produced exactly once in the program.
// Defining INIT_CLASS_IID before the interface headers turn the DECLARE_
// CLASS_IID declarations into real symbols; this translation unit is that
// one place.
#define INIT_CLASS_IID

#include "pluginterfaces/base/funknown.h"
#include "pluginterfaces/base/ibstream.h"
#include "pluginterfaces/base/ipluginbase.h"
#include "pluginterfaces/base/smartpointer.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivsthostapplication.h"
#include "pluginterfaces/vst/ivstmessage.h"
#include "pluginterfaces/vst/ivstparameterchanges.h"
#include "pluginterfaces/vst/ivstpluginterfacesupport.h"
#include "pluginterfaces/vst/ivstunits.h"

#include "public.sdk/source/common/memorystream.h"

#include "measurements.h"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#if defined (_WIN32)
    #include <windows.h>
    #include <fcntl.h>
    #include <io.h>
#else
    #include <dlfcn.h>
    #include <unistd.h>
#endif

namespace fs = std::filesystem;

using namespace Steinberg;
using namespace Steinberg::Vst;

// ---------------------------------------------------------------------------
//  the help extension of the Auralis plug-ins
// ---------------------------------------------------------------------------
//
//  The parameter info of the VST3 format carries three texts: the title, the
//  short title and the units.  A sentence that says what a parameter does
//  fits in none of them, so the Auralis plug-ins publish the sentences
//  through an interface of their own.  The host asks for them through
//  queryInterface, and a plug-in that does not have the interface simply
//  has no help to offer.

class IParameterHelp : public Steinberg::FUnknown
{
public:
    /** The number of parameters that carry a sentence. */
    virtual int32 PLUGIN_API getParameterHelpCount () = 0;

    /** The sentence of one parameter, by its host parameter id. */
    virtual Steinberg::tresult PLUGIN_API getParameterHelp (Steinberg::Vst::ParamID id,
                                                            Steinberg::Vst::String128 help) = 0;

    static const Steinberg::FUID iid;
};

const Steinberg::FUID IParameterHelp::iid (0xA17A9E11, 0x4C2B0D6E, 0x9F31C4A7, 0x2B5E8D30);

// ---------------------------------------------------------------------------
//  exit codes
// ---------------------------------------------------------------------------
namespace ExitCode
{
constexpr int kOk = 0;        ///< everything worked
constexpr int kLoad = 1;      ///< the module could not be loaded / instantiated
constexpr int kStep = 2;      ///< a step of the exercise failed
} // namespace ExitCode

// ---------------------------------------------------------------------------
//  the tolerances of the measurements
// ---------------------------------------------------------------------------
//
//  A measurement only turns into a finding when it is larger than the noise
//  of the measurement itself.  Every comparison the host makes carries one
//  of these numbers, so a reader can see how much was allowed.

namespace Tolerance
{
/** How much the delay the plug-in really adds may exceed the latency it
    reports, in samples.  64 samples are 1.33 ms at 48 kHz: below that the
    misalignment is inaudible. */
constexpr int64_t kLatency = 64;

/** How long the plug-in may ring past the tail it reports, in samples.

    The spectral modules of the family analyse the signal in windows of
    2048 samples, and the edge of such a window rings for a part of that
    after the signal stops.  A plug-in may therefore ring for up to one
    window more than it reports before the host calls it a defect; the
    level of the ring-out is reported as well, so that a reader can see
    how loud it is. */
constexpr int64_t kTail = 2048;

/** How far a value may move on the way through the plain conversion, as a
    fraction of the parameter's own range. */
constexpr double kPlain = 0.01;

/** How far a value may move on the way through the text conversion, as a
    fraction of the parameter's own range. */
constexpr double kString = 0.01;

/** The level below which a sample counts as silence when the tail is
    measured: -60 dBFS, which is below what any system shows. */
constexpr double kSilence = 1.0e-3;

/** The level below which two audio blocks count as the same, as a fraction
    of the block's peak. */
constexpr double kAudio = 1.0e-6;

/** The smallest cross correlation that still says something about where
    the output sits against the input. */
constexpr double kCorrelation = 0.2;
} // namespace Tolerance

// ---------------------------------------------------------------------------
//  the shape of the exercise
// ---------------------------------------------------------------------------

namespace Setup
{
/** The sample rate the exercise runs at. */
constexpr double kSampleRate = 48000.0;

/** The block size the exercise runs with. */
constexpr int32 kBlockSize = 256;

/** The number of warm up blocks a measurement run starts with.  The core
    buffers up to 4096 samples, so the warm up has to be longer than that
    before a measurement means anything. */
constexpr int32 kWarmUpBlocks = 16;

/** The number of blocks of the measured run. */
constexpr int32 kMeasureBlocks = 4;

/** The tone the exercise feeds in, in hertz. */
constexpr double kToneHz = 1000.0;

/** The amplitude of the tone, which is -6 dBFS. */
constexpr double kToneAmplitude = 0.5;

/** The margin above the largest delay the catalogue declares that the delay
    search covers, in samples.

    The margin is one window of the analysis of the core (2048 samples,
    42.7 ms at 48 kHz), and it is there for three reasons:

      * a plug-in that delays the signal by more than it declares is still
        measured inside the search, so the host finds the delay the plug-in
        really has instead of the edge of its own search,
      * a module whose delay depends on its parameters, or on the mode it
        runs in, declares the delay of the state the host measured in, and
        the search covers the states next to it as well,
      * it is the margin the survey of the catalogue uses as well
        (qa/survey/harness.py, LATENCY_SEARCH_MARGIN), so the host and the
        survey look at the same range and their numbers can be compared.

    The cost of the wider search is measured and not assumed: the report
    says how long the search took (correlation_search_ms). */
constexpr int64_t kLatencySearchMargin = 2048;
} // namespace Setup

// ---------------------------------------------------------------------------
//  the catalogue of the product
// ---------------------------------------------------------------------------
//
//  The host measures how long a plug-in delays its output, and the range it
//  searches is not a constant of the source: it is the largest delay the
//  modules of the catalogue declare, plus the margin above.  The catalogue
//  is the product's own registry, asked for every module; the host is a
//  C++ program and the registry is Python, so the catalogue is written to a
//  file of plain text that the host reads:
//
//      code <TAB> class id <TAB> display name <TAB> declared delay <TAB> tolerance
//
//  The class id is what the host matches on: the plug-in under test says
//  which class it is, and the class id is the identity of the module.  A
//  plug-in that is not in the catalogue - the fixture of the gate, a
//  plug-in of somebody else - is measured against the delay it reports
//  itself.

namespace Catalogue
{

/** The name of the file the catalogue is written to. */
constexpr const char* kFileName = "catalogue.tsv";

/** The environment variable that names the catalogue, when the file is not
    where the host looks for it. */
constexpr const char* kEnvironmentVariable = "AURALIS_VST3HOST_CATALOGUE";

/** How far the measured delay may be from the declared one, in samples, for
    a module that declares a delay.  2 samples are 41.7 us at 48 kHz: the
    delay line of a module has a fixed length, so the measurement resolves
    it to the sample. */
constexpr int64_t kToleranceDeclared = 2;

/** How far the measured delay may be from the declared one, in samples, for
    a module that declares no delay.  64 samples are 1.33 ms at 48 kHz: the
    cross correlation of a filtered signal peaks at the group delay of the
    filter and not at a latency, and one block is the resolution a host
    compensates a delay with. */
constexpr int64_t kToleranceUndeclared = 64;

/** One module of the catalogue. */
struct Entry
{
    std::string code;
    std::string classId;
    std::string displayName;
    int64_t declaredLatency {0};
    int64_t latencyTolerance {0};
};

/** The catalogue of the product. */
struct Data
{
    std::vector<Entry> entries;
    /** The largest delay any module of the catalogue declares. */
    int64_t maxDeclaredLatency {0};
    /** True when the catalogue was read. */
    bool loaded {false};
    /** Where it was read from. */
    std::string path;
    /** Why it could not be read, when it could not. */
    std::string problem;
};

/** The tolerance of a module that is not in the catalogue: the rule of the
    survey, applied to the delay the plug-in reports itself. */
inline int64_t toleranceFor (int64_t declaredLatency)
{
    return declaredLatency > 0 ? kToleranceDeclared : kToleranceUndeclared;
}

/** Split a line into its fields, on the tab. */
inline std::vector<std::string> splitFields (const std::string& line)
{
    std::vector<std::string> fields;
    std::string current;

    for (char c : line)
    {
        if (c == '\t')
        {
            fields.push_back (current);
            current.clear();
            continue;
        }
        if (c == '\r')
            continue;
        current.push_back (c);
    }

    fields.push_back (current);
    return fields;
}

/** The form an identifier has in both its spellings: lower case, without
    the braces and without the dashes. */
inline std::string normaliseId (const std::string& id)
{
    std::string out;

    for (char c : id)
    {
        if (c == '{' || c == '}' || c == '-' || c == ' ')
            continue;
        out.push_back (static_cast<char> (std::tolower (static_cast<unsigned char> (c))));
    }

    return out;
}

/** Read the catalogue of a file. */
inline Data load (const fs::path& file)
{
    Data data;

    if (file.empty())
    {
        data.problem = "the catalogue was not found";
        return data;
    }

    std::ifstream stream (file);
    if (! stream)
    {
        data.problem = "the catalogue could not be read";
        return data;
    }

    data.path = file.string();
    data.loaded = true;

    std::string line;
    while (std::getline (stream, line))
    {
        if (line.empty() || line[0] == '#')
            continue;

        const std::vector<std::string> fields = splitFields (line);
        if (fields.size() < 5)
            continue;

        Entry entry;
        entry.code = fields[0];
        entry.classId = normaliseId (fields[1]);
        entry.displayName = fields[2];
        entry.declaredLatency = std::strtoll (fields[3].c_str(), nullptr, 10);
        entry.latencyTolerance = std::strtoll (fields[4].c_str(), nullptr, 10);

        data.maxDeclaredLatency = std::max (data.maxDeclaredLatency, entry.declaredLatency);
        data.entries.push_back (entry);
    }

    return data;
}

/** Find the catalogue: the environment variable first, then the tree the
    host and the module live in. */
inline fs::path locate (const std::vector<fs::path>& roots)
{
    const char* given = std::getenv (kEnvironmentVariable);
    if (given != nullptr && *given != 0)
    {
        const fs::path path (given);
        std::error_code error;
        if (fs::exists (path, error))
            return path;
    }

    for (const fs::path& root : roots)
    {
        fs::path folder = root;

        for (int depth = 0; depth < 8; ++depth)
        {
            if (folder.empty())
                break;

            const fs::path candidate = folder / "qa" / "vst3host" / kFileName;
            std::error_code error;
            if (fs::exists (candidate, error))
                return candidate;

            const fs::path parent = folder.parent_path();
            if (parent == folder)
                break;
            folder = parent;
        }
    }

    return fs::path();
}

/** The entry of the module with this class identifier, or null when the
    module is not in the catalogue. */
inline const Entry* findByClassId (const Data& data, const std::string& classId)
{
    const std::string wanted = normaliseId (classId);
    if (wanted.empty())
        return nullptr;

    for (const Entry& entry : data.entries)
    {
        if (entry.classId == wanted)
            return &entry;
    }

    return nullptr;
}

/** The directory the host itself lives in. */
inline fs::path executableDirectory ()
{
#if defined (_WIN32)
    char buffer[MAX_PATH] = {0};
    const DWORD written = GetModuleFileNameA (nullptr, buffer, MAX_PATH);
    if (written == 0)
        return fs::path();
    return fs::path (buffer).parent_path ();
#else
    char buffer[4096] = {0};
    const ssize_t written = readlink ("/proc/self/exe", buffer, sizeof (buffer) - 1);
    if (written <= 0)
        return fs::path();
    return fs::path (buffer).parent_path ();
#endif
}

} // namespace Catalogue

// ---------------------------------------------------------------------------
//  a very small JSON writer
// ---------------------------------------------------------------------------
namespace json
{

class Value;

// Ordered so the output is stable from run to run.  std::vector is used
// instead of std::map because the value type is incomplete at this point.
using Members = std::vector<std::pair<std::string, Value>>;

class Value
{
public:
    enum class Kind
    {
        Null,
        Bool,
        Number,
        String,
        Array,
        Object
    };

    Value() = default;

    Value (bool b)
        : kind (Kind::Bool)
        , boolean (b)
    {
    }

    Value (double d)
        : kind (Kind::Number)
        , number (d)
    {
    }

    Value (int i)
        : kind (Kind::Number)
        , number (static_cast<double> (i))
    {
    }

    Value (int64_t i)
        : kind (Kind::Number)
        , number (static_cast<double> (i))
    {
    }

    Value (uint32_t u)
        : kind (Kind::Number)
        , number (static_cast<double> (u))
    {
    }

    Value (const char* s)
        : kind (Kind::String)
        , text (s ? s : "")
    {
    }

    Value (std::string s)
        : kind (Kind::String)
        , text (std::move (s))
    {
    }

    static Value makeArray()
    {
        Value v;
        v.kind = Kind::Array;
        return v;
    }

    static Value makeObject()
    {
        Value v;
        v.kind = Kind::Object;
        return v;
    }

    /** Append to an array. */
    Value& push (Value v)
    {
        kind = Kind::Array;
        items.push_back (std::move (v));
        return *this;
    }

    /** Set a member of an object; an existing key is replaced. */
    Value& set (std::string key, Value v)
    {
        kind = Kind::Object;
        for (auto& m : members)
        {
            if (m.first == key)
            {
                m.second = std::move (v);
                return *this;
            }
        }
        members.emplace_back (std::move (key), std::move (v));
        return *this;
    }

    /** True when the object has the given member. */
    bool hasMember (const std::string& key) const
    {
        for (const auto& m : members)
            if (m.first == key)
                return true;
        return false;
    }

    /** The numeric value of a member, or the fallback. */
    double getNumber (const std::string& key, double fallback = 0.0) const
    {
        for (const auto& m : members)
            if (m.first == key && m.second.kind == Kind::Number)
                return m.second.number;
        return fallback;
    }

    /** The boolean value of a member, or the fallback. */
    bool getBool (const std::string& key, bool fallback = false) const
    {
        for (const auto& m : members)
            if (m.first == key && m.second.kind == Kind::Bool)
                return m.second.boolean;
        return fallback;
    }

    /** The text of the member at the given index of an array. */
    std::string getMemberText (size_t index) const
    {
        if (kind != Kind::Array || index >= items.size())
            return std::string();
        if (items[index].kind != Kind::String)
            return std::string();
        return items[index].text;
    }

    /** The number of members of an array. */
    size_t getMemberCount() const
    {
        return kind == Kind::Array ? items.size() : 0;
    }

    /** The number of the member at the given index of an array. */
    double getMemberNumber (size_t index) const
    {
        if (kind != Kind::Array || index >= items.size())
            return 0.0;
        if (items[index].kind != Kind::Number)
            return 0.0;
        return items[index].number;
    }

    std::string serialise (int indent = 0) const
    {
        std::string out;
        write (out, indent, 0);
        return out;
    }

private:
    Kind kind {Kind::Null};
    bool boolean {false};
    double number {0.0};
    std::string text;
    std::vector<Value> items;
    Members members;

    static void writeString (std::string& out, const std::string& s)
    {
        out += '"';
        for (unsigned char c : s)
        {
            switch (c)
            {
                case '"': out += "\\\""; break;
                case '\\': out += "\\\\"; break;
                case '\b': out += "\\b"; break;
                case '\f': out += "\\f"; break;
                case '\n': out += "\\n"; break;
                case '\r': out += "\\r"; break;
                case '\t': out += "\\t"; break;
                default:
                    if (c < 0x20)
                    {
                        char buf[8];
                        std::snprintf (buf, sizeof (buf), "\\u%04x", (unsigned) c);
                        out += buf;
                    }
                    else
                    {
                        out += static_cast<char> (c);
                    }
            }
        }
        out += '"';
    }

    static void writeNumber (std::string& out, double v)
    {
        if (std::isnan (v) || std::isinf (v))
        {
            out += "null";
            return;
        }
        if (v == static_cast<double> (static_cast<int64_t> (v)) && std::fabs (v) < 1e15)
        {
            char buf[32];
            std::snprintf (buf, sizeof (buf), "%lld", (long long) v);
            out += buf;
            return;
        }
        char buf[40];
        std::snprintf (buf, sizeof (buf), "%.12g", v);
        out += buf;
    }

    void write (std::string& out, int indent, int depth) const
    {
        const std::string pad (indent > 0 ? (depth + 1) * indent : 0, ' ');
        const std::string padEnd (indent > 0 ? depth * indent : 0, ' ');
        const char* nl = indent > 0 ? "\n" : "";

        switch (kind)
        {
            case Kind::Null: out += "null"; break;
            case Kind::Bool: out += boolean ? "true" : "false"; break;
            case Kind::Number: writeNumber (out, number); break;
            case Kind::String: writeString (out, text); break;
            case Kind::Array:
            {
                if (items.empty())
                {
                    out += "[]";
                    break;
                }
                out += "[";
                out += nl;
                for (size_t i = 0; i < items.size(); ++i)
                {
                    out += pad;
                    items[i].write (out, indent, depth + 1);
                    if (i + 1 < items.size())
                        out += ",";
                    out += nl;
                }
                out += padEnd;
                out += "]";
                break;
            }
            case Kind::Object:
            {
                if (members.empty())
                {
                    out += "{}";
                    break;
                }
                out += "{";
                out += nl;
                for (size_t i = 0; i < members.size(); ++i)
                {
                    out += pad;
                    writeString (out, members[i].first);
                    out += indent > 0 ? ": " : ":";
                    members[i].second.write (out, indent, depth + 1);
                    if (i + 1 < members.size())
                        out += ",";
                    out += nl;
                }
                out += padEnd;
                out += "}";
                break;
            }
        }
    }
};

} // namespace json

// ---------------------------------------------------------------------------
//  small string helpers
// ---------------------------------------------------------------------------

/** Convert UTF-8 to the SDK's UTF-16 String128. */
static void toString128 (String128 dst, const std::string& src)
{
    size_t i = 0;
    size_t o = 0;
    while (i < src.size() && o < 127)
    {
        const unsigned char c = static_cast<unsigned char> (src[i]);
        char32_t cp = 0xFFFD;
        int extra = 0;

        if (c < 0x80)
        {
            cp = c;
        }
        else if ((c & 0xE0) == 0xC0)
        {
            cp = c & 0x1Fu;
            extra = 1;
        }
        else if ((c & 0xF0) == 0xE0)
        {
            cp = c & 0x0Fu;
            extra = 2;
        }
        else if ((c & 0xF8) == 0xF0)
        {
            cp = c & 0x07u;
            extra = 3;
        }
        ++i;

        for (int k = 0; k < extra; ++k)
        {
            if (i >= src.size())
            {
                cp = 0xFFFD;
                break;
            }
            const unsigned char cc = static_cast<unsigned char> (src[i++]);
            cp = (cp << 6) | (cc & 0x3Fu);
        }

        dst[o++] = static_cast<char16> (cp > 0xFFFF ? 0xFFFD : cp);
    }
    dst[o] = 0;
}

/** Convert the SDK's UTF-16 String128 to UTF-8. */
static std::string fromString128 (const String128& src)
{
    std::string out;
    for (int i = 0; i < 128 && src[i] != 0; ++i)
    {
        const char32_t cp = static_cast<char32_t> (src[i]);
        if (cp < 0x80)
        {
            out += static_cast<char> (cp);
        }
        else if (cp < 0x800)
        {
            out += static_cast<char> (0xC0 | (cp >> 6));
            out += static_cast<char> (0x80 | (cp & 0x3F));
        }
        else
        {
            out += static_cast<char> (0xE0 | (cp >> 12));
            out += static_cast<char> (0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char> (0x80 | (cp & 0x3F));
        }
    }
    return out;
}

/** Convert UTF-8 to the SDK's UTF-16 character array (TChar), which is what
    getParamValueByString wants. */
static std::vector<TChar> toTChar (const std::string& src)
{
    String128 wide;
    toString128 (wide, src);

    std::vector<TChar> out;

    for (int i = 0; i < 128 && wide[i] != 0; ++i)
        out.push_back (static_cast<TChar> (wide[i]));

    out.push_back (static_cast<TChar> (0));

    return out;
}

/** The canonical GUID text of a 16 byte class id. */
static std::string guidText (const TUID& uid)
{
    FUID f = FUID::fromTUID (uid);
    char8 buf[64] = {0};
    f.toRegistryString (buf);
    return std::string (reinterpret_cast<const char*> (buf));
}

/** The flat 32 hex digit text of a 16 byte class id. */
static std::string guidHex (const TUID& uid)
{
    FUID f = FUID::fromTUID (uid);
    char8 buf[64] = {0};
    f.toString (buf);
    return std::string (reinterpret_cast<const char*> (buf));
}

/** Trim a fixed size SDK string down to something printable. */
static std::string fixedText (const char* text, size_t maxLen)
{
    std::string out;
    for (size_t i = 0; i < maxLen && text[i] != 0; ++i)
        out += text[i];
    while (! out.empty() && (out.back() == ' ' || out.back() == '\t'))
        out.pop_back();
    return out;
}

// ---------------------------------------------------------------------------
//  the host context: IHostApplication + IPlugInterfaceSupport
// ---------------------------------------------------------------------------

/** The attribute list of a host message. */
class HostAttributeList final : public IAttributeList
{
public:
    tresult PLUGIN_API setInt (AttrID id, int64 value) override
    {
        if (id == nullptr)
            return kInvalidArgument;
        integers[id] = value;
        return kResultOk;
    }

    tresult PLUGIN_API getInt (AttrID id, int64& value) override
    {
        if (id == nullptr)
            return kInvalidArgument;
        const auto it = integers.find (id);
        if (it == integers.end())
            return kResultFalse;
        value = it->second;
        return kResultOk;
    }

    tresult PLUGIN_API setFloat (AttrID id, double value) override
    {
        if (id == nullptr)
            return kInvalidArgument;
        floats[id] = value;
        return kResultOk;
    }

    tresult PLUGIN_API getFloat (AttrID id, double& value) override
    {
        if (id == nullptr)
            return kInvalidArgument;
        const auto it = floats.find (id);
        if (it == floats.end())
            return kResultFalse;
        value = it->second;
        return kResultOk;
    }

    tresult PLUGIN_API setString (AttrID id, const TChar* string) override
    {
        if (id == nullptr || string == nullptr)
            return kInvalidArgument;
        std::string utf8;
        for (const TChar* p = string; *p != 0; ++p)
            utf8 += static_cast<char> (*p);
        strings[id] = utf8;
        return kResultOk;
    }

    tresult PLUGIN_API getString (AttrID id, TChar* string, uint32 sizeInBytes) override
    {
        if (id == nullptr || string == nullptr)
            return kInvalidArgument;
        const auto it = strings.find (id);
        if (it == strings.end())
            return kResultFalse;
        const size_t maxUnits = sizeInBytes / sizeof (TChar);
        size_t i = 0;
        for (; i < it->second.size() && i + 1 < maxUnits; ++i)
            string[i] = static_cast<TChar> (static_cast<unsigned char> (it->second[i]));
        string[i] = 0;
        return kResultOk;
    }

    tresult PLUGIN_API setBinary (AttrID id, const void* data, uint32 sizeInBytes) override
    {
        if (id == nullptr || (data == nullptr && sizeInBytes > 0))
            return kInvalidArgument;
        const auto* bytes = static_cast<const char*> (data);
        binaries[id] = std::vector<char> (bytes, bytes + sizeInBytes);
        return kResultOk;
    }

    tresult PLUGIN_API getBinary (AttrID id, const void*& data, uint32& sizeInBytes) override
    {
        if (id == nullptr)
            return kInvalidArgument;
        const auto it = binaries.find (id);
        if (it == binaries.end())
        {
            data = nullptr;
            sizeInBytes = 0;
            return kResultFalse;
        }
        data = it->second.data();
        sizeInBytes = static_cast<uint32_t> (it->second.size());
        return kResultOk;
    }

    tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
    {
        if (obj == nullptr)
            return kInvalidArgument;
        *obj = nullptr;
        if (matches (iid, IAttributeList::iid) || matches (iid, FUnknown::iid))
        {
            *obj = static_cast<IAttributeList*> (this);
            addRef();
            return kResultOk;
        }
        return kNoInterface;
    }

    uint32 PLUGIN_API addRef() override { return ++refCount; }
    uint32 PLUGIN_API release() override
    {
        if (--refCount == 0)
            delete this;
        return refCount;
    }

private:
    std::map<std::string, int64_t> integers;
    std::map<std::string, double> floats;
    std::map<std::string, std::string> strings;
    std::map<std::string, std::vector<char>> binaries;
    uint32 refCount {1};

    static bool matches (const void* a, const FUID& b)
    {
        TUID tmp;
        b.toTUID (tmp);
        return FUnknownPrivate::iidEqual (a, tmp);
    }
};

/** A message the controller can send to the component. */
class HostMessage final : public IMessage
{
public:
    ~HostMessage()
    {
        if (attributes != nullptr)
            attributes->release();
    }

    const char* PLUGIN_API getMessageID() override { return messageId.c_str(); }

    void PLUGIN_API setMessageID (FIDString id) override
    {
        messageId = id ? std::string (id) : std::string();
    }

    IAttributeList* PLUGIN_API getAttributes() override
    {
        if (attributes == nullptr)
            attributes = new HostAttributeList();
        return attributes;
    }

    tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
    {
        if (obj == nullptr)
            return kInvalidArgument;
        *obj = nullptr;
        if (matches (iid, IMessage::iid) || matches (iid, FUnknown::iid))
        {
            *obj = static_cast<IMessage*> (this);
            addRef();
            return kResultOk;
        }
        return kNoInterface;
    }

    uint32 PLUGIN_API addRef() override { return ++refCount; }
    uint32 PLUGIN_API release() override
    {
        if (--refCount == 0)
            delete this;
        return refCount;
    }

private:
    std::string messageId;
    HostAttributeList* attributes {nullptr};
    uint32 refCount {1};

    static bool matches (const void* a, const FUID& b)
    {
        TUID tmp;
        b.toTUID (tmp);
        return FUnknownPrivate::iidEqual (a, tmp);
    }
};

/** Answers the optional "which plug-in interfaces does the host use" question. */
class HostPlugInterfaceSupport final : public IPlugInterfaceSupport
{
public:
    tresult PLUGIN_API isPlugInterfaceSupported (const TUID) override
    {
        // The host implements no optional plug-in interfaces.
        return kResultFalse;
    }

    tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
    {
        if (obj == nullptr)
            return kInvalidArgument;
        *obj = nullptr;
        if (matches (iid, IPlugInterfaceSupport::iid) || matches (iid, FUnknown::iid))
        {
            *obj = static_cast<IPlugInterfaceSupport*> (this);
            return kResultOk;
        }
        return kNoInterface;
    }

    uint32 PLUGIN_API addRef () override { return 1; }
    uint32 PLUGIN_API release () override { return 1; }

    static bool matches (const void* a, const FUID& b)
    {
        TUID tmp;
        b.toTUID (tmp);
        return FUnknownPrivate::iidEqual (a, tmp);
    }
};

/** A minimal host application.  The plug-in is told who it is talking to and
    is given a way to ask which optional interfaces the host supports. */
class HostContext final : public IHostApplication
{
public:
    tresult PLUGIN_API getName (String128 name) override
    {
        toString128 (name, "Auralis VST3 Host");
        return kResultOk;
    }

    tresult PLUGIN_API createInstance (TUID cid, TUID iid, void** obj) override
    {
        if (obj == nullptr)
            return kInvalidArgument;
        *obj = nullptr;

        // The controller uses the host to create the message that carries it
        // over to the component.  Without this the controller never learns
        // about the plug-in's parameters.
        if (matches (cid, IMessage::iid) && matches (iid, IMessage::iid))
        {
            *obj = new HostMessage();
            return kResultOk;
        }
        if (matches (cid, IAttributeList::iid) && matches (iid, IAttributeList::iid))
        {
            *obj = new HostAttributeList();
            return kResultOk;
        }
        return kResultFalse;
    }

    tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
    {
        if (obj == nullptr)
            return kInvalidArgument;
        *obj = nullptr;

        if (matches (iid, IHostApplication::iid))
        {
            *obj = static_cast<IHostApplication*> (this);
            addRef();
            return kResultOk;
        }
        if (matches (iid, FUnknown::iid))
        {
            *obj = static_cast<FUnknown*> (this);
            addRef();
            return kResultOk;
        }
        // The plug-in may ask for the optional interface support object.
        return interfaceSupport.queryInterface (iid, obj);
    }

    uint32 PLUGIN_API addRef () override { return ++refCount; }
    uint32 PLUGIN_API release () override { return --refCount; }

private:
    uint32 refCount {0};
    HostPlugInterfaceSupport interfaceSupport;

    static bool matches (const void* a, const FUID& b)
    {
        TUID tmp;
        b.toTUID (tmp);
        return FUnknownPrivate::iidEqual (a, tmp);
    }
};

/** A component handler that accepts everything and does nothing. */
class HostComponentHandler final : public IComponentHandler
{
public:
    tresult PLUGIN_API beginEdit (ParamID) override { return kResultOk; }
    tresult PLUGIN_API performEdit (ParamID, ParamValue) override { return kResultOk; }
    tresult PLUGIN_API endEdit (ParamID) override { return kResultOk; }
    tresult PLUGIN_API restartComponent (int32) override { return kResultOk; }

    tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
    {
        if (obj == nullptr)
            return kInvalidArgument;
        *obj = nullptr;
        if (matches (iid, IComponentHandler::iid) || matches (iid, FUnknown::iid))
        {
            *obj = static_cast<IComponentHandler*> (this);
            addRef();
            return kResultOk;
        }
        return kNoInterface;
    }

    uint32 PLUGIN_API addRef () override { return ++refCount; }
    uint32 PLUGIN_API release () override { return --refCount; }

private:
    uint32 refCount {0};

    static bool matches (const void* a, const FUID& b)
    {
        TUID tmp;
        b.toTUID (tmp);
        return FUnknownPrivate::iidEqual (a, tmp);
    }
};

// ---------------------------------------------------------------------------
//  parameter changes: the host side of the automation channel
// ---------------------------------------------------------------------------

/** One parameter's value curve for the current block. */
class HostParamValueQueue final : public IParamValueQueue
{
public:
    explicit HostParamValueQueue (ParamID id)
        : parameterId (id)
    {
    }

    ParamID PLUGIN_API getParameterId () override { return parameterId; }

    int32 PLUGIN_API getPointCount () override { return static_cast<int32> (points.size()); }

    tresult PLUGIN_API getPoint (int32 index, int32& sampleOffset, ParamValue& value) override
    {
        if (index < 0 || index >= static_cast<int32> (points.size()))
            return kInvalidArgument;
        sampleOffset = points[static_cast<size_t> (index)].first;
        value = points[static_cast<size_t> (index)].second;
        return kResultOk;
    }

    tresult PLUGIN_API addPoint (int32 sampleOffset, ParamValue value, int32& index) override
    {
        index = static_cast<int32> (points.size());
        points.emplace_back (sampleOffset, value);
        return kResultOk;
    }

    tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
    {
        if (obj == nullptr)
            return kInvalidArgument;
        *obj = nullptr;
        if (matches (iid, IParamValueQueue::iid) || matches (iid, FUnknown::iid))
        {
            *obj = static_cast<IParamValueQueue*> (this);
            addRef();
            return kResultOk;
        }
        return kNoInterface;
    }

    uint32 PLUGIN_API addRef () override { return ++refCount; }
    uint32 PLUGIN_API release () override { return --refCount; }

private:
    ParamID parameterId {0};
    std::vector<std::pair<int32, ParamValue>> points;
    uint32 refCount {0};

    static bool matches (const void* a, const FUID& b)
    {
        TUID tmp;
        b.toTUID (tmp);
        return FUnknownPrivate::iidEqual (a, tmp);
    }
};

/** The list of parameter changes handed to the processor for one block. */
class HostParameterChanges final : public IParameterChanges
{
public:
    int32 PLUGIN_API getParameterCount () override
    {
        return static_cast<int32> (queues.size());
    }

    IParamValueQueue* PLUGIN_API getParameterData (int32 index) override
    {
        if (index < 0 || index >= static_cast<int32> (queues.size()))
            return nullptr;
        return queues[static_cast<size_t> (index)].get();
    }

    IParamValueQueue* PLUGIN_API addParameterData (const ParamID& id, int32& index) override
    {
        queues.push_back (std::make_unique<HostParamValueQueue> (id));
        index = static_cast<int32> (queues.size()) - 1;
        return queues.back().get();
    }

    /** Convenience: queue one value for one parameter. */
    void queue (ParamID id, ParamValue value, int32 sampleOffset = 0)
    {
        int32 index = 0;
        if (auto* q = addParameterData (id, index))
        {
            int32 pointIndex = 0;
            q->addPoint (sampleOffset, value, pointIndex);
        }
    }

    tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
    {
        if (obj == nullptr)
            return kInvalidArgument;
        *obj = nullptr;
        if (matches (iid, IParameterChanges::iid) || matches (iid, FUnknown::iid))
        {
            *obj = static_cast<IParameterChanges*> (this);
            addRef();
            return kResultOk;
        }
        return kNoInterface;
    }

    uint32 PLUGIN_API addRef () override { return ++refCount; }
    uint32 PLUGIN_API release () override { return --refCount; }

private:
    std::vector<std::unique_ptr<HostParamValueQueue>> queues;
    uint32 refCount {0};

    static bool matches (const void* a, const FUID& b)
    {
        TUID tmp;
        b.toTUID (tmp);
        return FUnknownPrivate::iidEqual (a, tmp);
    }
};

// ---------------------------------------------------------------------------
//  guarded calls into the module
// ---------------------------------------------------------------------------
//
//  The module is third party code and may fault.  A test host that dies
//  with an access violation cannot report anything, so the calls that are
//  allowed to fault are wrapped: the fault is recorded and the host keeps
//  going, so the report can say what happened.

static bool guardedTerminate (IPluginBase* object)
{
    if (object == nullptr)
        return true;
#if defined (_MSC_VER)
    __try
    {
        object->terminate();
        return true;
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return false;
    }
#else
    object->terminate();
    return true;
#endif
}

static bool guardedRelease (FUnknown* object)
{
    if (object == nullptr)
        return true;
#if defined (_MSC_VER)
    __try
    {
        object->release();
        return true;
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return false;
    }
#else
    object->release();
    return true;
#endif
}

// ---------------------------------------------------------------------------
//  module loading
// ---------------------------------------------------------------------------

/** The exported names of a PE image, read out of the file.

    A module is recognised by its export: the factory entry point.  Reading
    the export table out of the file lets the host pick the module out of a
    folder that holds more than one file, instead of guessing from the name
    or the extension.  Nothing is mapped and no code of the module runs. */
struct PeExports
{
    bool isPe {false};
    bool is64 {false};
    std::vector<std::string> names;
};

static PeExports readPeExports (const fs::path& file)
{
    PeExports result;

    std::ifstream stream (file, std::ios::binary);
    if (! stream)
        return result;

    std::vector<uint8_t> image;
    stream.seekg (0, std::ios::end);
    const std::streamoff size = stream.tellg();
    if (size <= 0 || size > (256 << 20))
        return result;
    image.resize (static_cast<size_t> (size));
    stream.seekg (0, std::ios::beg);
    stream.read (reinterpret_cast<char*> (image.data()), size);
    if (! stream)
        return result;

    auto read16 = [&image] (size_t at) -> uint32_t {
        if (at + 2 > image.size())
            return 0;
        return static_cast<uint32_t> (image[at]) | (static_cast<uint32_t> (image[at + 1]) << 8);
    };
    auto read32 = [&image] (size_t at) -> uint32_t {
        if (at + 4 > image.size())
            return 0;
        return static_cast<uint32_t> (image[at]) | (static_cast<uint32_t> (image[at + 1]) << 8)
             | (static_cast<uint32_t> (image[at + 2]) << 16) | (static_cast<uint32_t> (image[at + 3]) << 24);
    };

    if (image.size() < 0x40 || image[0] != 'M' || image[1] != 'Z')
        return result;

    const uint32_t peOffset = read32 (0x3C);
    if (peOffset + 24 > image.size())
        return result;
    if (image[peOffset] != 'P' || image[peOffset + 1] != 'E')
        return result;

    const uint32_t sectionCount = read16 (peOffset + 6);
    const uint32_t optionalSize = read16 (peOffset + 20);
    const size_t optional = peOffset + 24;

    const uint32_t magic = read16 (optional);
    result.is64 = (magic == 0x20B);
    if (magic != 0x10B && magic != 0x20B)
        return result;
    result.isPe = true;

    // The data directories: the first one holds the export table.
    const size_t directories = optional + (result.is64 ? 112 : 96);
    const uint32_t exportRva = read32 (directories);
    if (exportRva == 0)
        return result;

    struct Section
    {
        uint32_t virtualAddress;
        uint32_t virtualSize;
        uint32_t rawSize;
        uint32_t rawPointer;
    };
    std::vector<Section> sections;
    const size_t table = optional + optionalSize;
    for (uint32_t index = 0; index < sectionCount; ++index)
    {
        const size_t entry = table + index * 40;
        if (entry + 40 > image.size())
            break;
        Section section;
        section.virtualSize = read32 (entry + 8);
        section.virtualAddress = read32 (entry + 12);
        section.rawSize = read32 (entry + 16);
        section.rawPointer = read32 (entry + 20);
        sections.push_back (section);
    }

    auto toOffset = [&sections] (uint32_t rva) -> size_t {
        if (rva == 0)
            return 0;
        for (const auto& section : sections)
        {
            const uint32_t span = section.virtualSize > section.rawSize ? section.virtualSize : section.rawSize;
            if (rva >= section.virtualAddress && rva < section.virtualAddress + span)
            {
                const size_t offset = section.rawPointer + (rva - section.virtualAddress);
                return offset < (1u << 31) ? offset : 0;
            }
        }
        return 0;
    };

    const size_t exportTable = toOffset (exportRva);
    if (exportTable == 0)
        return result;

    const uint32_t numberOfNames = read32 (exportTable + 24);
    const size_t names = toOffset (read32 (exportTable + 32));
    if (names == 0 || numberOfNames > 65536)
        return result;

    for (uint32_t index = 0; index < numberOfNames; ++index)
    {
        const size_t at = toOffset (read32 (names + index * 4));
        if (at == 0)
            continue;
        std::string text;
        while (at + text.size() < image.size() && image[at + text.size()] != 0)
            text.push_back (static_cast<char> (image[at + text.size()]));
        result.names.push_back (text);
    }

    return result;
}

/** Does the file export the given symbol? */
static bool exportsSymbol (const fs::path& file, const char* symbol)
{
    const PeExports exports = readPeExports (file);
    for (const auto& name : exports.names)
        if (name == symbol)
            return true;
    return false;
}

/** The names of the binary inside a VST3 bundle, per platform. */
static std::vector<std::string> bundleBinaryNames (const std::string& bundleName)
{
#if defined (_WIN32)
    return {bundleName + ".vst3", bundleName + ".dll"};
#elif defined (__APPLE__)
    return {bundleName + ".vst3", bundleName + ".dylib"};
#else
    return {bundleName + ".vst3", bundleName + ".so"};
#endif
}

/** The architecture directory names a VST3 bundle may use. */
static std::vector<std::string> bundleArchNames()
{
#if defined (_WIN32)
    return {"x86_64-win", "x86-win", "arm64-win"};
#elif defined (__APPLE__)
    return {"arm64-mac", "x86_64-mac", "universal-mac"};
#else
    return {"x86_64-linux", "arm64-linux"};
#endif
}

/** The extensions a shared library may have. */
static bool isLibraryExtension (const std::string& extension)
{
    return extension == ".dll" || extension == ".so" || extension == ".dylib";
}

/** Look for the module inside a directory.

    A folder that holds a module may hold more than one file: a module that
    ships a payload next to itself, a second architecture, a readme.  The
    module is the file that exports the factory entry point, so that is what
    the host looks for; the name of the bundle and the extension only break
    a tie.  The order of the files in the folder is not a property of the
    folder, so it may not decide anything. */
static fs::path findBinaryIn (const fs::path& dir, const std::string& bundleName)
{
    for (const auto& name : bundleBinaryNames (bundleName))
    {
        const fs::path candidate = dir / name;
        if (fs::is_regular_file (candidate))
            return candidate;
    }

    std::error_code ec;
    std::vector<fs::path> files;
    for (const auto& entry : fs::directory_iterator (dir, ec))
    {
        if (! entry.is_regular_file (ec))
            continue;
        files.push_back (entry.path());
    }

    fs::path best;
    int bestScore = 0;

    for (const auto& candidate : files)
    {
        const std::string extension = candidate.extension().string();
        const std::string stem = candidate.stem().string();

        // A file that exports the factory entry point is a module, whatever
        // it is called and whatever it is called next to.
        const bool isModule = exportsSymbol (candidate, "GetPluginFactory");
        if (! isModule && ! isLibraryExtension (extension) && extension != ".vst3")
            continue;

        int score = 0;
        if (isModule)
            score += 4;
        if (extension == ".vst3")
            score += 2;
        if (stem == bundleName && (isLibraryExtension (extension) || extension == ".vst3"))
            score += 2;
        if (isLibraryExtension (extension))
            score += 1;

        if (score > bestScore)
        {
            bestScore = score;
            best = candidate;
        }
    }

    return best;
}

/** Turn the command line argument into the path of the shared library. */
static fs::path resolveModuleBinary (const fs::path& argument, std::string& error)
{
    std::error_code ec;
    if (! fs::exists (argument, ec))
    {
        error = "no such file or directory: " + argument.string();
        return {};
    }

    if (fs::is_regular_file (argument, ec))
        return argument;

    if (! fs::is_directory (argument, ec))
    {
        error = "not a file and not a directory: " + argument.string();
        return {};
    }

    const std::string bundleName = argument.filename().string();

    // The bundle layout: <bundle>/Contents/<arch>/<binary>
    const fs::path contents = argument / "Contents";
    if (fs::is_directory (contents, ec))
    {
        for (const auto& arch : bundleArchNames())
        {
            const fs::path archDir = contents / arch;
            if (! fs::is_directory (archDir, ec))
                continue;
            const fs::path binary = findBinaryIn (archDir, bundleName);
            if (! binary.empty())
                return binary;
        }
    }

    // The flat layout: the binary sits directly in the folder.
    const fs::path binary = findBinaryIn (argument, bundleName);
    if (! binary.empty())
        return binary;

    error = "no VST3 binary found inside " + argument.string();
    return {};
}

/** Split a path list on the platform's path separator. */
static std::vector<std::string> splitPathList (const std::string& list)
{
    std::vector<std::string> parts;
#if defined (_WIN32)
    constexpr char kSeparator = ';';
#else
    constexpr char kSeparator = ':';
#endif
    std::string current;
    for (char c : list)
    {
        if (c == kSeparator)
        {
            if (! current.empty())
                parts.push_back (current);
            current.clear();
        }
        else
        {
            current += c;
        }
    }
    if (! current.empty())
        parts.push_back (current);
    return parts;
}

/** Every directory below the bundle that holds a shared library.

    A bundle may ship the module's runtime (the core's C ABI, the Python
    runtime) in a sub folder; those directories are searched for the
    module's dependencies as well. */
static std::vector<fs::path> bundleLibraryDirectories (const fs::path& bundle)
{
    std::vector<fs::path> directories;
    std::error_code ec;

    if (! fs::is_directory (bundle, ec))
        return directories;

    for (fs::recursive_directory_iterator it (bundle, fs::directory_options::skip_permission_denied, ec), end;
         it != end;
         it.increment (ec))
    {
        if (ec)
            break;
        if (! it->is_directory (ec))
            continue;
        for (const auto& entry : fs::directory_iterator (it->path(), ec))
        {
            if (! entry.is_regular_file (ec))
                continue;
            const std::string ext = entry.path().extension().string();
            if (ext == ".dll" || ext == ".so" || ext == ".dylib")
            {
                directories.push_back (it->path());
                break;
            }
        }
    }
    return directories;
}

/** A loaded module: the shared library and its factory. */
class LoadedModule
{
public:
    ~LoadedModule()
    {
        // The unload is normally done by the host, with a time limit; this
        // is the safety net for the case where the host did not get there.
        if (handle != nullptr)
            unload (2000);
#if defined (_WIN32)
        // A module that did not let go of itself may still hold the lock
        // of the loader, and taking that lock here would wait for it
        // forever.  The process is about to end anyway.
        if (! abandoned)
        {
            for (auto dir : addedDirectories)
                RemoveDllDirectory (dir);
        }
#endif
    }

    /** True when the module did not let go of its library. */
    bool isAbandoned() const { return abandoned; }

    /** Load the shared library and fetch the plug-in factory. */
    bool open (const fs::path& binary, const std::vector<fs::path>& extraDirectories, std::string& error)
    {
        libraryPath = binary;

        // The plug-in may depend on shared libraries that do not sit next to
        // it (the Auralis plug-ins link against the core's C ABI).  The extra
        // directories come from AURALIS_VST3HOST_DEPS.
        //
        // The order matters.  The directories the host is told about are
        // searched first, and the plug-in's own directory comes after them:
        // a library that the family shares has to be loaded once, from the
        // directory the family keeps it in.  When the plug-in's own copy
        // wins, the module loads a second, private copy of the library, and
        // a library that carries a runtime of its own then looks for that
        // runtime next to itself and does not find it.
        const std::string deps = std::getenv ("AURALIS_VST3HOST_DEPS") ? std::getenv ("AURALIS_VST3HOST_DEPS") : "";
        std::vector<fs::path> searchDirectories;

        for (const auto& dir : splitPathList (deps))
        {
            if (dir.empty())
                continue;
            const fs::path path (dir);
            searchDirectories.push_back (path);
#if defined (_WIN32)
            DLL_DIRECTORY_COOKIE h = AddDllDirectory (path.wstring().c_str());
            if (h != nullptr)
                addedDirectories.push_back (h);
#else
            const char* current = std::getenv ("LD_LIBRARY_PATH");
            const std::string updated = current ? (dir + ":" + current) : dir;
            setenv ("LD_LIBRARY_PATH", updated.c_str(), 1);
#endif
        }

        // The plug-in's own directory is reachable too, so a sibling shared
        // library is found.
        searchDirectories.push_back (binary.parent_path());
        for (const auto& dir : extraDirectories)
            searchDirectories.push_back (dir);
#if defined (_WIN32)
        DLL_DIRECTORY_COOKIE own = AddDllDirectory (binary.parent_path().wstring().c_str());
        if (own != nullptr)
            addedDirectories.push_back (own);
        for (const auto& dir : extraDirectories)
        {
            DLL_DIRECTORY_COOKIE cookie = AddDllDirectory (dir.wstring().c_str());
            if (cookie != nullptr)
                addedDirectories.push_back (cookie);
        }
#endif

#if defined (_WIN32)
        // Load the shared libraries that travel with the module first, so
        // that the loader can satisfy the module's imports by name.  The
        // altered search path is used on purpose: it puts the directory of
        // the library being loaded first, and it propagates to that
        // library's own dependencies, so a runtime that lives in a
        // directory of its own is found together with the interpreter
        // next to it.  The standard search order does not do that: it
        // leaves the directory of the library being loaded out, and the
        // preload of a library whose dependency sits next to it fails
        // with error 126.
        for (const auto& dir : searchDirectories)
        {
            std::error_code listError;
            for (const auto& entry : fs::directory_iterator (dir, listError))
            {
                if (! entry.is_regular_file (listError))
                    continue;
                const std::string ext = entry.path().extension().string();
                if (ext != ".dll" && ext != ".so" && ext != ".dylib")
                    continue;
                if (fs::equivalent (entry.path(), binary, listError))
                    continue;
                // A failure here is not an error: the file may not be a
                // library at all.
                LoadLibraryExW (entry.path().wstring().c_str(), nullptr,
                                LOAD_WITH_ALTERED_SEARCH_PATH);
            }
        }
#endif

#if defined (_WIN32)
        // The plug-in's own directory is reachable too, so a sibling shared
        // library is found.
        HMODULE mod = LoadLibraryW (binary.wstring().c_str());
        if (mod == nullptr)
        {
            const DWORD code = GetLastError();
            error = "LoadLibrary failed (" + std::to_string (code) + "): " + binary.string();
            if (code == ERROR_MOD_NOT_FOUND || code == ERROR_PROC_NOT_FOUND)
                error += "\n    a shared library the module depends on could not be found."
                         "  Point AURALIS_VST3HOST_DEPS at the directories that hold them.";
            return false;
        }
        handle = mod;

        using InitDllFunc = bool (PLUGIN_API*) ();
        using GetFactoryProc = IPluginFactory* (PLUGIN_API*) ();

        auto initDll = reinterpret_cast<InitDllFunc> (GetProcAddress (mod, "InitDll"));
        if (initDll != nullptr && ! initDll())
        {
            error = "the module's InitDll() failed: " + binary.string();
            return false;
        }
        exitDll = reinterpret_cast<InitDllFunc> (GetProcAddress (mod, "ExitDll"));

        auto getFactory = reinterpret_cast<GetFactoryProc> (GetProcAddress (mod, "GetPluginFactory"));
#else
        handle = dlopen (binary.c_str(), RTLD_NOW | RTLD_LOCAL);
        if (handle == nullptr)
        {
            error = "dlopen failed: " + std::string (dlerror() ? dlerror() : "unknown error");
            return false;
        }

        using InitDllFunc = bool (PLUGIN_API*) ();
        using GetFactoryProc = IPluginFactory* (PLUGIN_API*) ();

        auto initDll = reinterpret_cast<InitDllFunc> (dlsym (handle, "InitDll"));
        if (initDll != nullptr && ! initDll())
        {
            error = "the module's InitDll() failed: " + binary.string();
            return false;
        }
        exitDll = reinterpret_cast<InitDllFunc> (dlsym (handle, "ExitDll"));

        auto getFactory = reinterpret_cast<GetFactoryProc> (dlsym (handle, "GetPluginFactory"));
#endif

        if (getFactory == nullptr)
        {
            error = "the module does not export GetPluginFactory: " + binary.string();
            return false;
        }

        IPluginFactory* raw = getFactory();
        if (raw == nullptr)
        {
            error = "GetPluginFactory() returned null: " + binary.string();
            return false;
        }
        factory = raw;
        return true;
    }

    IPluginFactory* getFactory() const { return factory; }
    const fs::path& getLibraryPath() const { return libraryPath; }

    /** Give the factory back.  The host owns it from the moment it is
        returned by GetPluginFactory, and releases it when it is done. */
    bool releaseFactory()
    {
        if (factory == nullptr)
            return true;
        IPluginFactory* toRelease = factory;
        factory = nullptr;
        return guardedRelease (toRelease);
    }

    /** Let the shared library go.

        A module that is not ours may not let go of itself: the code that
        runs when the library is unloaded is third party code, and it may
        take its time or never finish.  The host does not wait for ever, so
        the unload runs on a thread of its own and the host gives up on it
        after a while.  The report says so when that happens. */
    bool unload (int32 timeoutMs)
    {
        if (handle == nullptr)
            return true;

        void* library = handle;
        handle = nullptr;
        auto entry = exitDll;
        exitDll = nullptr;

        std::atomic<bool> done {false};
        std::thread worker ([library, entry, &done] {
#if defined (_WIN32)
            if (entry != nullptr)
                entry();
            FreeLibrary (static_cast<HMODULE> (library));
#else
            if (entry != nullptr)
                entry();
            dlclose (library);
#endif
            done.store (true);
        });

        const bool finished = waitFor (done, timeoutMs);
        if (finished)
        {
            worker.join ();
        }
        else
        {
            // The thread stays where it is.  The process cannot wait for
            // it, so the host leaves it behind on purpose and says so.
            worker.detach ();
            abandoned = true;
        }
        return finished;
    }

private:
    /** Wait for a flag, up to the given time. */
    static bool waitFor (const std::atomic<bool>& flag, int32 timeoutMs)
    {
        const auto limit = std::chrono::steady_clock::now () + std::chrono::milliseconds (timeoutMs);
        while (std::chrono::steady_clock::now () < limit)
        {
            if (flag.load ())
                return true;
            std::this_thread::sleep_for (std::chrono::milliseconds (5));
        }
        return flag.load ();
    }

    void* handle {nullptr};
    bool (PLUGIN_API* exitDll) () {nullptr};
    IPluginFactory* factory {nullptr};
    fs::path libraryPath;
    /** True when the module did not let go of its library in time. */
    bool abandoned {false};
#if defined (_WIN32)
    std::vector<DLL_DIRECTORY_COOKIE> addedDirectories;
#endif
};

// ---------------------------------------------------------------------------
//  audio bus buffers
// ---------------------------------------------------------------------------

/** One audio bus worth of host owned buffers. */
struct BusBuffers
{
    int32 numChannels {0};
    std::vector<std::vector<float>> floatStorage;
    std::vector<std::vector<double>> doubleStorage;
    std::vector<float*> floatPointers;
    std::vector<double*> doublePointers;
    AudioBusBuffers buffers {};

    void prepare (int32 channels, int32 frames, bool useDouble)
    {
        numChannels = channels;
        floatStorage.assign (static_cast<size_t> (std::max (channels, 0)), {});
        doubleStorage.assign (static_cast<size_t> (std::max (channels, 0)), {});
        floatPointers.assign (static_cast<size_t> (std::max (channels, 0)), nullptr);
        doublePointers.assign (static_cast<size_t> (std::max (channels, 0)), nullptr);

        for (int32 c = 0; c < numChannels; ++c)
        {
            floatStorage[static_cast<size_t> (c)].assign (static_cast<size_t> (frames), 0.f);
            doubleStorage[static_cast<size_t> (c)].assign (static_cast<size_t> (frames), 0.0);
            floatPointers[static_cast<size_t> (c)] = floatStorage[static_cast<size_t> (c)].data();
            doublePointers[static_cast<size_t> (c)] = doubleStorage[static_cast<size_t> (c)].data();
        }

        buffers.numChannels = numChannels;
        buffers.silenceFlags = 0;
        if (useDouble)
            buffers.channelBuffers64 = doublePointers.data();
        else
            buffers.channelBuffers32 = floatPointers.data();
    }

    void clear()
    {
        for (auto& v : floatStorage)
            std::fill (v.begin(), v.end(), 0.f);
        for (auto& v : doubleStorage)
            std::fill (v.begin(), v.end(), 0.0);
    }

    /** Fill the buffers with a value the plug-in is not going to write.

        A host does not clear the output buffers, and a plug-in that does
        not write all of its output leaves whatever was there.  The host
        fills them with a value that no sane plug-in produces, so it can
        see the samples the plug-in did not write. */
    void fillSentinel()
    {
        for (auto& v : floatStorage)
            std::fill (v.begin(), v.end(), kSentinel);
        for (auto& v : doubleStorage)
            std::fill (v.begin(), v.end(), static_cast<double> (kSentinel));
    }

    /** The number of samples that still hold the sentinel.

        Only the storage the plug-in works in is looked at: the other one
        is never written. */
    int64_t sentinelCount (bool doublePrecision) const
    {
        int64_t count = 0;

        if (doublePrecision)
        {
            for (const auto& v : doubleStorage)
                for (double value : v)
                    if (value == static_cast<double> (kSentinel))
                        ++count;
        }
        else
        {
            for (const auto& v : floatStorage)
                for (float value : v)
                    if (value == kSentinel)
                        ++count;
        }

        return count;
    }

    /** The value the host puts into the output buffers. */
    static constexpr float kSentinel = -1.0e30f;
};

// ---------------------------------------------------------------------------
//  the plug-in's own error stream
// ---------------------------------------------------------------------------
//
//  The module is third party code and it says things: when it cannot find
//  its core it says so, and it passes the audio through.  A host that
//  cannot see that has no way of telling a working plug-in from a broken
//  one.  So the host keeps its own report on the real error stream and
//  catches what the module writes.

namespace
{

int g_savedStderr = -1;
FILE* g_captureRead = nullptr;
#if defined (_WIN32)
HANDLE g_savedStderrHandle = nullptr;
int g_captureWriteFd = -1;
#endif

/** Start catching what the module writes to the error stream. */
bool startErrorCapture()
{
#if defined (_WIN32)
    HANDLE readEnd = nullptr;
    HANDLE writeEnd = nullptr;

    if (! CreatePipe (&readEnd, &writeEnd, nullptr, 0))
        return false;

    g_savedStderr = _dup (_fileno (stderr));
    g_savedStderrHandle = GetStdHandle (STD_ERROR_HANDLE);

    if (g_savedStderr == -1)
    {
        CloseHandle (readEnd);
        CloseHandle (writeEnd);
        return false;
    }

    // The write end of the pipe becomes the error stream of the process,
    // for the C runtime and for the Win32 calls alike.
    const int writeFd = _open_osfhandle ((intptr_t) writeEnd, _O_WRONLY | _O_TEXT);
    if (writeFd == -1)
    {
        CloseHandle (readEnd);
        CloseHandle (writeEnd);
        return false;
    }

    _dup2 (writeFd, _fileno (stderr));
    SetStdHandle (STD_ERROR_HANDLE, (HANDLE) _get_osfhandle (writeFd));

    // The handle the stream was duplicated from is kept, so that the pipe
    // can be closed when the capture ends: until then the reader would
    // wait for a writer that never goes away.
    g_captureWriteFd = writeFd;

    const int readFd = _open_osfhandle ((intptr_t) readEnd, _O_RDONLY | _O_TEXT);
    if (readFd == -1)
    {
        _close (writeFd);
        return false;
    }

    g_captureRead = _fdopen (readFd, "r");
    return g_captureRead != nullptr;
#else
    int fds[2] = { -1, -1 };
    if (pipe (fds) != 0)
        return false;

    g_savedStderr = dup (fileno (stderr));
    if (g_savedStderr == -1)
    {
        close (fds[0]);
        close (fds[1]);
        return false;
    }

    dup2 (fds[1], fileno (stderr));
    close (fds[1]);

    g_captureRead = fdopen (fds[0], "r");
    return g_captureRead != nullptr;
#endif
}

/** Stop catching and return what the module wrote. */
std::string stopErrorCapture()
{
    std::string captured;

    if (g_savedStderr == -1)
        return captured;

#if defined (_WIN32)
    // The module writes to the stream the process had when it started, so
    // the handle has to be put back before the text can be read.
    if (g_savedStderrHandle != nullptr)
        SetStdHandle (STD_ERROR_HANDLE, g_savedStderrHandle);
    _dup2 (g_savedStderr, _fileno (stderr));
    fflush (stderr);

    // The last writer of the pipe has to go, or the reader waits for a
    // writer that never comes.
    if (g_captureWriteFd != -1)
    {
        _close (g_captureWriteFd);
        g_captureWriteFd = -1;
    }
#else
    fflush (stderr);
    dup2 (g_savedStderr, fileno (stderr));
    fflush (stderr);
#endif

    if (g_captureRead != nullptr)
    {
        char buffer[1024];
        size_t got = 0;
        while ((got = fread (buffer, 1, sizeof (buffer), g_captureRead)) > 0)
            captured.append (buffer, got);
        fclose (g_captureRead);
        g_captureRead = nullptr;
    }

    _close (g_savedStderr);
    g_savedStderr = -1;

    return captured;
}

/** Write to the real error stream, past the capture. */
void writeToStderr (const std::string& text)
{
    if (g_savedStderr != -1)
    {
#if defined (_WIN32)
        _write (g_savedStderr, text.data(), (unsigned int) text.size());
        return;
#else
        const ssize_t written = write (g_savedStderr, text.data(), text.size());
        (void) written;
        return;
#endif
    }

    std::cerr << text;
    std::cerr.flush();
}

// ---------------------------------------------------------------------------
//  the debug output of the module
// ---------------------------------------------------------------------------
//
//  A module that has no console of its own often writes its log to the
//  debug output of the operating system, and that is where its own
//  diagnostics end up.  The operating system hands those lines to the one
//  reader in the session, and the reader owns three objects:
//
//      DBWIN_BUFFER          the shared memory the line is copied into
//      DBWIN_BUFFER_READY    the reader says "the buffer is free"
//      DBWIN_DATA_READY      the writer says "the buffer is filled"
//
//  The reader announces a free buffer and then waits for the writer to fill
//  it.  The names are the ones the operating system looks for, and the
//  buffer starts with the identifier of the process that wrote the line.

#if defined (_WIN32)

/** Catches what the module writes with OutputDebugString. */
class DebugOutputCapture
{
public:
    ~DebugOutputCapture() { stop(); }

    bool start()
    {
        // With a debugger attached the output goes to the debugger, and the
        // shared memory is not used at all.
        if (IsDebuggerPresent())
        {
            reason = "a debugger is attached, so the output goes to it";
            return false;
        }

        bufferReady = CreateEventA (nullptr, FALSE, FALSE, "DBWIN_BUFFER_READY");
        dataReady = CreateEventA (nullptr, FALSE, FALSE, "DBWIN_DATA_READY");
        if (bufferReady == nullptr || dataReady == nullptr)
        {
            reason = "the events of the debug output could not be created";
            return false;
        }

        mapping = CreateFileMappingA (INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof (Buffer),
                                      "DBWIN_BUFFER");
        if (mapping == nullptr)
        {
            reason = "the shared memory of the debug output could not be created";
            return false;
        }

        buffer = static_cast<Buffer*> (MapViewOfFile (mapping, SECTION_MAP_READ, 0, 0, 0));
        if (buffer == nullptr)
        {
            reason = "the shared memory of the debug output could not be mapped";
            return false;
        }

        ownProcessId = static_cast<uint32> (GetCurrentProcessId ());
        running = true;
        worker = std::thread ([this] { readLoop(); });
        return true;
    }

    /** Why the capture is not running, when it is not. */
    const std::string& getReason() const { return reason; }

    /** Stop catching and return the lines that were written. */
    std::string stop()
    {
        if (! running.exchange (false))
            return std::string();

        if (worker.joinable())
            worker.join ();

        std::string text;
        {
            std::lock_guard<std::mutex> guard (mutex);
            text = collected;
        }

        if (buffer != nullptr)
        {
            UnmapViewOfFile (buffer);
            buffer = nullptr;
        }
        if (mapping != nullptr)
        {
            CloseHandle (mapping);
            mapping = nullptr;
        }
        if (dataReady != nullptr)
        {
            CloseHandle (dataReady);
            dataReady = nullptr;
        }
        if (bufferReady != nullptr)
        {
            CloseHandle (bufferReady);
            bufferReady = nullptr;
        }

        return text;
    }

    /** True when the capture is running. */
    bool isRunning() const { return running.load(); }

private:
    /** The shared memory the operating system copies the line into. */
    struct Buffer
    {
        uint32 processId;
        char text[4096];
    };

    HANDLE mapping {nullptr};
    HANDLE bufferReady {nullptr};
    HANDLE dataReady {nullptr};
    Buffer* buffer {nullptr};
    uint32 ownProcessId {0};
    std::string reason;
    std::atomic<bool> running {false};
    std::thread worker;
    std::mutex mutex;
    std::string collected;

    /** Read the lines the writers leave in the shared buffer. */
    void readLoop()
    {
        while (running.load())
        {
            // The buffer is free: let a writer fill it.
            SetEvent (bufferReady);

            if (WaitForSingleObject (dataReady, 50) != WAIT_OBJECT_0)
                continue;

            size_t length = 0;
            while (length < sizeof (buffer->text) && buffer->text[length] != '\0')
                ++length;

            // The shared memory belongs to the whole session, so the lines
            // of other processes arrive here too.  The buffer carries the
            // identifier of the process that wrote the line, and only the
            // lines of this process are the module's own words.
            if (buffer->processId != ownProcessId)
                continue;

            std::string line (buffer->text, length);
            while (! line.empty() && (line.back() == '\r' || line.back() == '\n'))
                line.pop_back();

            if (! line.empty())
            {
                std::lock_guard<std::mutex> guard (mutex);
                collected += line;
                collected += "\n";
            }
        }
    }
};

#else

/** Nothing to catch on this platform: the module writes to a stream. */
class DebugOutputCapture
{
public:
    bool start() { return false; }
    std::string stop() { return std::string(); }
    bool isRunning() const { return false; }
};

#endif

/** The first line of the plug-in's own words that reports a failure.

    The words are the plug-in's, so the host looks for the plain marks of
    a failure rather than for the plug-in's own wording. */
std::string firstComplaint (const std::string& text)
{
    std::istringstream stream (text);
    std::string line;

    while (std::getline (stream, line))
    {
        std::string lower = line;
        for (char& c : lower)
            c = static_cast<char> (std::tolower (static_cast<unsigned char> (c)));

        if (lower.find ("not found") != std::string::npos
            || lower.find ("failed") != std::string::npos
            || lower.find ("could not") != std::string::npos
            || lower.find ("error") != std::string::npos)
        {
            return line;
        }
    }

    return std::string();
}

} // namespace

// ---------------------------------------------------------------------------
//  the host
// ---------------------------------------------------------------------------

/** The first channel of a bus, in the storage the plug-in works in.

    The host fills both storages, and the plug-in only writes the one it
    was given, so a measurement has to look at the right one. */
static std::vector<float> firstChannel (const BusBuffers& bus, bool doublePrecision)
{
    std::vector<float> out;

    if (doublePrecision)
    {
        if (! bus.doubleStorage.empty())
            out.assign (bus.doubleStorage[0].begin(), bus.doubleStorage[0].end());
    }
    else if (! bus.floatStorage.empty())
    {
        out = bus.floatStorage[0];
    }

    return out;
}

class Host
{
public:
    /** Run the whole exercise.  Returns the process exit code. */
    int run (const std::string& moduleArgument, bool wantTone);

    /** Put the plug-in back into the state it was found in. */
    void teardown();

    /** Record a fault that happened inside the module. */
    void recordFault (const std::string& message);

    /** Print the reports. */
    void report();

    /** Remember what the plug-in said while it was exercised. */
    void setPlugInMessages (const std::string& text) { plugInMessages = text; }

    /** True when the module did not let go of its library in time.

        A thread of the module is still inside the operating system then,
        and the host cannot wait for it.  The process ends the moment the
        report is written. */
    bool moduleIsAbandoned() const { return module.isAbandoned(); }

    /** Write the audio the exercise produced, when the caller asked for it. */
    bool writeAudioDump (const std::string& path) const;

    /** The samples the exercise produced, when the caller asked for them. */
    const std::vector<float>& audioDump() const { return dumpedAudio; }

    /** The block size the exercise runs with. */
    void setBlockSize (int32 size) { blockSize = size; }

    /** The sample rate the exercise runs at. */
    void setSampleRate (double rate) { sampleRate = rate; }

    /** The parameter the exercise moves, and the value it moves it to. */
    void setForcedParameter (ParamID id, double value)
    {
        forcedParamId = id;
        forcedParamValue = value;
        haveForcedParam = true;
    }

    /** Report what the module says without stopping at the first problem.

        A module that is not ours does not have to pass the checks the
        host makes on our own modules.  The checks stay, and the exit code
        still says that a step failed, but the exercise goes on with the
        steps that can still run, so that the report holds the whole
        picture and not only the part before the first complaint. */
    void setReportOnly (bool value) { reportOnly = value; }

    /** Where the processed audio is written. */
    void setAudioDump (const std::string& path) { dumpPath = path; }

    /** Search the delay up to this many samples instead of up to the bound
        the catalogue of the product gives.

        A diagnostic: it is what makes the cost of the wider search
        measurable with one binary, and it is what shows what the host
        reports when its search is too narrow.  The default is the bound of
        the catalogue. */
    void setSearchBound (int64_t samples) { searchBoundOverride = samples; }

private:
    /** The measurements of one processed block. */
    struct BlockResult
    {
        double inputPeak {0.0};
        double outputPeak {0.0};
        double outputRms {0.0};
        bool finite {true};
    };

    /** One published parameter, as the host remembers it. */
    struct ParameterRecord
    {
        ParamID id {0};
        int32 index {0};
        std::string title;
        std::string units;
        int32 stepCount {0};
        double defaultNormalized {0.0};
        double currentNormalized {0.0};
        bool automatable {true};
        bool readOnly {false};
        bool hidden {false};
        /** The special bypass parameter of the plug-in. */
        bool bypass {false};
        /** A parameter that selects a program, not a value. */
        bool programChange {false};
    };

    /** How a witness run tells the plug-in about the probe parameter. */
    enum class WitnessMode
    {
        Baseline,   ///< the parameter is left alone
        Automation, ///< the host delivers the change with the block
        Controller  ///< the controller is told the value
    };

    /** What one witness run observed. */
    struct WitnessRun
    {
        bool ok {false};
        std::string error;
        std::vector<float> output;    ///< the measured block
        std::vector<uint8_t> state;   ///< the state after the measured block
        uint64_t stateHash {0};
    };

    // -- steps ----------------------------------------------------------------
    bool enumerateClasses();
    bool createComponent();
    bool createController();
    bool connectComponents();
    bool checkConnection();
    bool inspectBuses();
    bool probeFormats();
    bool enumerateParameters();
    bool exerciseParameter();
    bool setupAudio();
    bool activateBuses (IComponent* target, bool state);
    bool processAudio();
    bool runBlock (bool tone, HostParameterChanges& changes, BlockResult& result);
    bool checkParameterLiveness();
    bool measureParameterEffects();
    bool processBlockWith (const std::vector<float>& signal, size_t offset, std::vector<float>& output);
    bool measureParameterStep();
    double differentValue (const ParameterRecord& parameter) const;
    bool checkAgreement();
    bool runWitness (WitnessMode mode, ParamID probeId, double probeValue, WitnessRun& run);
    bool exerciseState();
    bool measureLatencyAndTail();
    bool inspectSurface();
    bool dumpAudio();

    // -- data -----------------------------------------------------------------
    std::string moduleArgument;
    bool wantTone {false};
    bool reportOnly {false};
    fs::path binaryPath;
    LoadedModule module;
    /** Catches what the module writes to the debug output. */
    DebugOutputCapture debugCapture;
    // The plug-in objects are owned by the host and released one by one in
    // teardown(), so that a module that faults cannot take the host with it.
    IPluginFactory* factory {nullptr};
    IComponent* component {nullptr};
    // The component handler has to outlive the controller: the controller
    // keeps the pointer and calls back into it while it is being torn down.
    HostComponentHandler componentHandler;
    IEditController* controller {nullptr};
    IAudioProcessor* processor {nullptr};
    // The host context has to outlive the plug-in objects as well: a plug-in
    // is allowed to ask the host for something at any time.
    HostContext context;
    bool componentHandlerSet {false};
    /** True when the host has activated the component, so that the teardown
        only deactivates what it activated. */
    bool componentActivated {false};
    /** True when the host has activated the buses of the component.  The
        format asks for it before the component is set active, and a plug-in
        that takes the specification seriously writes nothing at all when a
        host forgets. */
    bool busesActivated {false};
    /** The number of buses the host activated. */
    int32 activatedBusCount {0};
    /** The number of buses that refused to be activated. */
    int32 activationFailures {0};

    std::vector<json::Value> classRecords;
    json::Value factoryRecord = json::Value::makeObject();
    /** What a parameter change does to the output. */
    json::Value stepRecord = json::Value::makeObject();
    json::Value unitRecord = json::Value::makeObject();
    json::Value connectionRecord = json::Value::makeObject();
    json::Value busRecord = json::Value::makeObject();
    json::Value parameterCatalogue = json::Value::makeObject();
    json::Value livenessRecord = json::Value::makeObject();
    json::Value agreementRecord = json::Value::makeObject();
    json::Value latencyRecord = json::Value::makeObject();
    json::Value surfaceRecord = json::Value::makeObject();
    json::Value findings = json::Value::makeArray();
    bool componentCreated {false};
    bool controllerCreated {false};
    int32 selectedClassIndex {-1};
    std::string selectedClassName;
    std::string selectedClassId;

    /** The catalogue of the product, as the host read it. */
    Catalogue::Data catalogue;
    /** The bound of the delay search, when the caller gives one. */
    int64_t searchBoundOverride {0};

    json::Value audioInfo = json::Value::makeObject();
    json::Value parameterRecords = json::Value::makeArray();
    json::Value parameterRoundTrip = json::Value::makeObject();
    json::Value processingInfo = json::Value::makeObject();
    json::Value stateInfo = json::Value::makeObject();

    /** The published parameters, in the order the controller lists them. */
    std::vector<ParameterRecord> parameters;
    /** The id of the parameter the exercise moves, and the value it moves
        it to. */
    ParamID probeId {0};
    double probeValue {0.0};
    std::string probeTitle;
    bool haveProbe {false};

    int32 numInputBuses {0};
    int32 numOutputBuses {0};
    int32 inputChannels {0};
    int32 outputChannels {0};
    bool useDoublePrecision {false};
    int32 blockSize {Setup::kBlockSize};
    double sampleRate {Setup::kSampleRate};

    std::vector<BusBuffers> inputBuses;
    std::vector<BusBuffers> outputBuses;

    /** The output samples the plug-in did not write. */
    int64_t unwrittenOutputSamples {0};

    /** The parameter the exercise moves, when the caller names one. */
    ParamID forcedParamId {0};
    bool haveForcedParam {false};
    double forcedParamValue {0.0};

    /** Where the processed audio is written, when the caller asks for it. */
    std::string dumpPath;
    std::vector<float> dumpedAudio;

    std::string errorMessage;
    std::vector<std::string> faults;
    std::string plugInMessages;
    /** What the module wrote to the debug output of the operating system. */
    std::string plugInDebugOutput;
    /** True when the debug output could be caught at all. */
    bool debugCaptureAvailable {false};
    /** Why the debug output could not be caught, when it could not. */
    std::string debugCaptureReason;
    bool doesNotProcess {false};
    int exitCode {ExitCode::kOk};

    void fail (int code, const std::string& message)
    {
        if (exitCode == ExitCode::kOk)
            exitCode = code;
        if (errorMessage.empty())
            errorMessage = message;
        writeToStderr ("! " + message + "\n");
    }

    void note (const std::string& text)
    {
        // The report is streamed, so a crash still shows how far the
        // exercise got.
        writeToStderr (text + "\n");
    }

    /** The clock the step timings are taken with. */
    using Clock = std::chrono::steady_clock;

    /** Remember when the current step started. */
    void startStep (const std::string& name)
    {
        stepName = name;
        stepStart = Clock::now();
    }

    /** Note how long the current step took. */
    void endStep()
    {
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds> (
            Clock::now() - stepStart);
        stepTimes.push_back (std::make_pair (stepName, elapsed.count()));
    }

    /** The time each step of the exercise took, in milliseconds. */
    std::string stepName;
    Clock::time_point stepStart;
    std::vector<std::pair<std::string, int64_t>> stepTimes;
    /** What happened to every step of the exercise: ok, failed or skipped. */
    std::vector<std::pair<std::string, std::string>> stepRecords;
    /** The layout the module was found in: the folder and the files that
        travel with it. */
    json::Value layoutRecord = json::Value::makeObject();

    /** The channel count of one bus, as the plug-in reports it. */
    int32 busChannels (BusDirection direction, int32 index) const
    {
        BusInfo bus;
        std::memset (&bus, 0, sizeof (bus));
        if (component == nullptr)
            return 0;
        if (component->getBusInfo (kAudio, direction, index, bus) != kResultOk)
            return 0;
        return bus.channelCount;
    }

    /** Read the state of one object into a byte vector.  The component and
        the controller both have a state, and both are asked the same way. */
    template <typename T>
    bool readState (T* object, std::vector<uint8_t>& out)
    {
        out.clear();

        if (object == nullptr)
            return false;

        MemoryStream stream;
        if (object->getState (&stream) != kResultOk)
            return false;

        const auto size = stream.getSize();
        if (size <= 0)
            return false;

        const auto* data = reinterpret_cast<const uint8_t*> (stream.getData());
        out.assign (data, data + size);

        return true;
    }

    /** Give a state back to one object. */
    template <typename T>
    bool writeState (T* object, const std::vector<uint8_t>& data)
    {
        if (object == nullptr || data.empty())
            return false;

        MemoryStream stream (const_cast<uint8_t*> (data.data()),
                             static_cast<int32> (data.size()));
        return object->setState (&stream) == kResultOk;
    }

    /** True when the controller and the component are one object. */
    bool isCombined() const
    {
        // The two pointers cannot be compared directly: an object that
        // implements both interfaces has one address per interface.  The
        // question is whether the controller hands back the very component
        // the host already holds.
        if (controller == nullptr || component == nullptr)
            return false;

        FUnknownPtr<IComponent> asComponent (controller);

        return asComponent.getInterface () == component;
    }
};

bool Host::enumerateClasses()
{
    const int32 count = factory->countClasses();
    if (count <= 0)
    {
        fail (ExitCode::kLoad, "the module exports no classes");
        return false;
    }

    PFactoryInfo factoryInfo;
    std::memset (&factoryInfo, 0, sizeof (factoryInfo));
    if (factory->getFactoryInfo (&factoryInfo) == kResultOk)
    {
        // The record is kept, because the factory is released before the
        // report is written.
        factoryRecord.set ("vendor", json::Value (fixedText (factoryInfo.vendor, PFactoryInfo::kNameSize)));
        factoryRecord.set ("url", json::Value (fixedText (factoryInfo.url, PFactoryInfo::kURLSize)));
        factoryRecord.set ("email", json::Value (fixedText (factoryInfo.email, PFactoryInfo::kEmailSize)));
        factoryRecord.set ("flags", json::Value (factoryInfo.flags));

        note ("factory         : vendor=\"" + fixedText (factoryInfo.vendor, PFactoryInfo::kNameSize) + "\" url=\""
                  + fixedText (factoryInfo.url, PFactoryInfo::kURLSize) + "\" email=\""
                  + fixedText (factoryInfo.email, PFactoryInfo::kEmailSize)
                  + "\" flags=" + std::to_string (factoryInfo.flags));
    }
    note ("classes         : " + std::to_string (count));

    FUnknownPtr<IPluginFactory2> factory2 (factory);

    for (int32 i = 0; i < count; ++i)
    {
        PClassInfo info;
        std::memset (&info, 0, sizeof (info));
        if (factory->getClassInfo (i, &info) != kResultOk)
        {
            note (std::string ("  [") + std::to_string (i) + "] <class info unavailable>");
            continue;
        }

        PClassInfo2 info2;
        std::memset (&info2, 0, sizeof (info2));
        bool haveInfo2 = false;
        if (factory2 != nullptr && factory2->getClassInfo2 (i, &info2) == kResultOk)
            haveInfo2 = true;

        const std::string id = guidText (info.cid);
        const std::string idHex = guidHex (info.cid);
        const std::string name = fixedText (info.name, PClassInfo::kNameSize);
        const std::string category = fixedText (info.category, PClassInfo::kCategorySize);

        json::Value record = json::Value::makeObject();
        record.set ("index", json::Value (i));
        record.set ("class_id", json::Value (id));
        record.set ("class_id_hex", json::Value (idHex));
        record.set ("name", json::Value (name));
        record.set ("category", json::Value (category));
        record.set ("cardinality", json::Value (info.cardinality));
        if (haveInfo2)
        {
            record.set ("class_flags", json::Value (static_cast<uint32_t> (info2.classFlags)));
            record.set ("vendor", json::Value (fixedText (info2.vendor, PClassInfo2::kVendorSize)));
            record.set ("version", json::Value (fixedText (info2.version, PClassInfo2::kVersionSize)));
            record.set ("sdk_version", json::Value (fixedText (info2.sdkVersion, PClassInfo2::kVersionSize)));
            record.set ("sub_categories", json::Value (fixedText (info2.subCategories, PClassInfo2::kSubCategoriesSize)));
        }
        else
        {
            record.set ("class_flags", json::Value (static_cast<uint32_t> (0)));
        }
        classRecords.push_back (record);

        note ("  [" + std::to_string (i) + "] " + id + "  \"" + name + "\"  category=\"" + category
                  + "\"  classFlags="
                  + std::to_string (haveInfo2 ? static_cast<uint32_t> (info2.classFlags) : 0u)
                  + (haveInfo2 ? ("  version=\"" + fixedText (info2.version, PClassInfo2::kVersionSize) + "\"") : std::string()));
    }
    return true;
}

bool Host::createComponent()
{
    const int32 count = factory->countClasses();

    for (int32 i = 0; i < count; ++i)
    {
        PClassInfo info;
        std::memset (&info, 0, sizeof (info));
        if (factory->getClassInfo (i, &info) != kResultOk)
            continue;

        TUID componentIid;
        IComponent::iid.toTUID (componentIid);

        IComponent* raw = nullptr;
        if (factory->createInstance (info.cid, componentIid, reinterpret_cast<void**> (&raw)) != kResultOk
            || raw == nullptr)
            continue;

        component = raw;
        selectedClassIndex = i;
        selectedClassName = fixedText (info.name, PClassInfo::kNameSize);
        selectedClassId = guidText (info.cid);
        break;
    }

    if (component == nullptr)
    {
        fail (ExitCode::kLoad, "no class of the module could be instantiated as an IComponent");
        return false;
    }

    note ("selected class  : [" + std::to_string (selectedClassIndex) + "] \"" + selectedClassName + "\"");

    // The context is a member of the host, so it outlives the plug-in
    // objects: a plug-in may ask the host for something at any time.
    const tresult res = component->initialize (static_cast<FUnknown*> (&context));
    if (res != kResultOk)
    {
        fail (ExitCode::kLoad,
              "the component refused to initialize (result " + std::to_string (res) + ")");
        return false;
    }
    note ("component       : created and initialized");
    componentCreated = true;

    FUnknownPtr<IAudioProcessor> audio (component);
    if (audio == nullptr)
    {
        fail (ExitCode::kStep, "the component does not implement IAudioProcessor");
        return false;
    }
    processor = audio.getInterface();
    return true;
}

bool Host::createController()
{
    TUID controllerClassId;
    std::memset (controllerClassId, 0, sizeof (controllerClassId));

    if (component->getControllerClassId (controllerClassId) == kResultOk)
    {
        TUID controllerIid;
        IEditController::iid.toTUID (controllerIid);
        IEditController* raw = nullptr;
        if (factory->createInstance (controllerClassId, controllerIid, reinterpret_cast<void**> (&raw))
                == kResultOk
            && raw != nullptr)
            controller = raw;
    }

    if (controller == nullptr)
    {
        // A single component plug-in implements the controller itself.
        FUnknownPtr<IEditController> own (component);
        if (own != nullptr)
            controller = own.getInterface();
    }

    if (controller == nullptr)
    {
        fail (ExitCode::kLoad, "no edit controller could be created for the component");
        return false;
    }

    HostContext& hostContext = context;
    const tresult res = controller->initialize (static_cast<FUnknown*> (&hostContext));
    if (res != kResultOk)
    {
        fail (ExitCode::kLoad,
              "the controller refused to initialize (result " + std::to_string (res) + ")");
        return false;
    }

    HostComponentHandler& handler = componentHandler;
    controller->setComponentHandler (static_cast<IComponentHandler*> (&handler));
    componentHandlerSet = true;

    note ("controller      : created and initialized");
    controllerCreated = true;

    // The unit and program lists are the plug-in's own description of its
    // structure.  The host does not need them, but they are a cheap way to
    // see what the module thinks it is.
    FUnknownPtr<IUnitInfo> unitInfo (controller);
    if (unitInfo != nullptr)
    {
        const int32 unitCount = unitInfo->getUnitCount();
        const int32 listCount = unitInfo->getProgramListCount();

        // The program list: the entries the host would show in its program
        // menu.  Every one of them is read, because the number of them and
        // their names are what the plug-in offers the user.
        json::Value programNames = json::Value::makeArray();
        int32 programCount = 0;
        std::string programName;

        if (listCount > 0)
        {
            ProgramListInfo listInfo;
            std::memset (&listInfo, 0, sizeof (listInfo));
            if (unitInfo->getProgramListInfo (0, listInfo) == kResultOk)
            {
                programCount = listInfo.programCount;

                for (int32 i = 0; i < listInfo.programCount; ++i)
                {
                    String128 name;
                    std::memset (name, 0, sizeof (name));
                    if (unitInfo->getProgramName (listInfo.id, i, name) != kResultOk)
                        continue;

                    const std::string text = fromString128 (name);
                    programNames.push (json::Value (text));

                    if (i == 0)
                        programName = text;
                }
            }
        }

        unitRecord.set ("unit_count", json::Value (unitCount));
        unitRecord.set ("program_list_count", json::Value (listCount));
        unitRecord.set ("program_count", json::Value (programCount));
        unitRecord.set ("program_name", json::Value (programName));
        unitRecord.set ("program_names", programNames);
        note ("units           : " + std::to_string (unitCount) + " unit(s), "
                  + std::to_string (listCount) + " program list(s), "
                  + std::to_string (programCount) + " program(s)"
                  + (programName.empty() ? std::string() : (", program 0 = \"" + programName + "\"")));
    }
    return true;
}

bool Host::connectComponents()
{
    FUnknownPtr<IConnectionPoint> componentPoint (component);
    FUnknownPtr<IConnectionPoint> controllerPoint (controller);

    if (componentPoint == nullptr || controllerPoint == nullptr)
    {
        fail (ExitCode::kStep, "component or controller does not implement IConnectionPoint");
        return false;
    }

    // The order matters: the component first learns about the controller, then
    // the controller learns about the component.
    if (componentPoint->connect (controllerPoint.getInterface()) != kResultOk)
    {
        fail (ExitCode::kStep, "the component refused the connection to the controller");
        return false;
    }
    if (controllerPoint->connect (componentPoint.getInterface()) != kResultOk)
    {
        fail (ExitCode::kStep, "the controller refused the connection to the component");
        return false;
    }

    note ("connected       : component <-> controller");
    return true;
}

bool Host::checkConnection()
{
    json::Value record = json::Value::makeObject();

    // A plug-in that answers the controller's class id with a class of its
    // own has a controller that is not the component.  A plug-in that does
    // not has one object that plays both roles.  The host has to know which
    // it is talking to, because the two shapes have to be checked
    // differently: in the combined shape the component's state carries the
    // parameters, in the split shape the controller's does.
    const bool combined = isCombined();
    record.set ("controller_is_component", json::Value (combined));

    // The connection point is the channel the controller uses to talk to
    // the component.  A host checks that it answers.
    //
    // The answer is not always "yes".  The interface does not say that a
    // message has to be accepted: the reference implementation declines
    // every message it does not know (kResultFalse), and a plug-in that does
    // not implement the channel at all says kNotImplemented.  Neither is a
    // broken channel: the message was delivered.  Only a call that failed
    // outright means that the channel is not there.
    FUnknownPtr<IConnectionPoint> point (controller);
    bool notifyAccepted = false;
    bool notifyHandled = false;
    tresult notifyResult = kResultFalse;
    if (point != nullptr)
    {
        HostMessage message;
        message.setMessageID ("AuralisHostCheck");
        notifyResult = point->notify (&message);
        notifyAccepted = (notifyResult == kResultOk) || (notifyResult == kResultFalse)
                         || (notifyResult == kNotImplemented);
        notifyHandled = (notifyResult == kResultOk);
    }
    record.set ("notify_accepted", json::Value (notifyAccepted));
    record.set ("notify_handled", json::Value (notifyHandled));
    record.set ("notify_result", json::Value (static_cast<int32> (notifyResult)));

    // The component handler has to be there, or the plug-in cannot tell
    // the host about an edit.
    record.set ("component_handler_set", json::Value (componentHandlerSet));

    connectionRecord = record;

    note ("connection     : the controller " + std::string (combined ? "is" : "is not")
              + " the component, the message channel is "
              + (notifyAccepted ? "open" : "CLOSED")
              + (notifyAccepted && ! notifyHandled ? ", the plug-in did not handle the probe" : ""));

    if (! notifyAccepted)
    {
        fail (ExitCode::kStep, "the connection point refused a message from the host");
        return false;
    }

    if (! componentHandlerSet)
    {
        fail (ExitCode::kStep, "the controller did not take the host's component handler");
        return false;
    }

    return true;
}

bool Host::inspectBuses()
{
    numInputBuses = component->getBusCount (kAudio, kInput);
    numOutputBuses = component->getBusCount (kAudio, kOutput);

    // A plug-in without an audio input or without an audio output cannot be
    // used in a host at all.
    if (numInputBuses <= 0 || numOutputBuses <= 0)
    {
        fail (ExitCode::kStep,
              "the plug-in has no audio bus to work with (" + std::to_string (numInputBuses)
                  + " input, " + std::to_string (numOutputBuses) + " output)");
        return false;
    }

    // Event buses are not part of this exercise, but a plug-in that claims
    // some has to describe them, and it must not claim an event bus it
    // cannot describe.
    const int32 eventBuses =
        component->getBusCount (kEvent, kInput) + component->getBusCount (kEvent, kOutput);

    json::Value busRecords = json::Value::makeArray();
    json::Value problems = json::Value::makeArray();
    int32 problemCount = 0;

    auto describe = [&] (MediaType mediaType, BusDirection direction, int32 count) {
        for (int32 i = 0; i < count; ++i)
        {
            BusInfo bus;
            std::memset (&bus, 0, sizeof (bus));
            if (component->getBusInfo (mediaType, direction, i, bus) != kResultOk)
            {
                problems.push (json::Value (std::string (direction == kInput ? "input" : "output")
                                            + " bus " + std::to_string (i)
                                            + " could not be described"));
                ++problemCount;
                continue;
            }

            const std::string busName = fromString128 (bus.name);
            const char* dirName = direction == kInput ? "input" : "output";
            const char* typeName = bus.busType == kAux ? "aux" : "main";

            json::Value record = json::Value::makeObject();
            record.set ("direction", json::Value (dirName));
            record.set ("index", json::Value (i));
            record.set ("channels", json::Value (bus.channelCount));
            record.set ("name", json::Value (busName));
            record.set ("type", json::Value (typeName));
            record.set ("flags", json::Value (static_cast<uint32_t> (bus.flags)));

            // The direction has to be the one that was asked for, and the
            // first bus of a direction is the main bus.
            if (bus.direction != direction)
            {
                problems.push (json::Value (std::string (dirName) + " bus " + std::to_string (i)
                                            + " reports the wrong direction"));
                ++problemCount;
            }
            if (bus.mediaType != kAudio)
            {
                problems.push (json::Value (std::string (dirName) + " bus " + std::to_string (i)
                                            + " reports the wrong media type"));
                ++problemCount;
            }
            if (bus.channelCount <= 0)
            {
                problems.push (json::Value (std::string (dirName) + " bus " + std::to_string (i)
                                            + " has no channels"));
                ++problemCount;
            }
            if (busName.empty())
            {
                problems.push (json::Value (std::string (dirName) + " bus " + std::to_string (i)
                                            + " has no name"));
                ++problemCount;
            }
            if (i == 0 && bus.busType != kMain)
            {
                problems.push (json::Value (std::string (dirName) + " bus 0 is not the main bus"));
                ++problemCount;
            }

            // The speaker arrangement of the bus has to hold as many
            // channels as the bus says it has.  This is the check that says
            // the bus arrangement is really what the plug-in says it is.
            SpeakerArrangement arrangement = SpeakerArr::kEmpty;
            const tresult arrangementResult = processor->getBusArrangement (direction, i, arrangement);
            const int32 arrangementChannels =
                auralis::countSpeakers (static_cast<uint64_t> (arrangement));
            record.set ("arrangement_channels", json::Value (arrangementChannels));

            if (arrangementResult != kResultOk)
            {
                problems.push (json::Value (std::string (dirName) + " bus " + std::to_string (i)
                                            + " has no readable speaker arrangement"));
                ++problemCount;
            }
            else if (arrangementChannels != bus.channelCount)
            {
                problems.push (json::Value (std::string (dirName) + " bus " + std::to_string (i)
                                            + " says " + std::to_string (bus.channelCount)
                                            + " channels but its arrangement holds "
                                            + std::to_string (arrangementChannels)));
                ++problemCount;
            }

            busRecords.push (record);

            note (std::string ("  ") + dirName + " [" + std::to_string (i) + "] channels=" + std::to_string (bus.channelCount)
                      + " name=\"" + busName + "\" type=" + typeName
                      + " arrangement=" + std::to_string (arrangementChannels) + "ch");
        }
    };

    describe (kAudio, kInput, numInputBuses);
    describe (kAudio, kOutput, numOutputBuses);

    // A bus that does not exist may not be described.
    {
        BusInfo bus;
        std::memset (&bus, 0, sizeof (bus));
        if (component->getBusInfo (kAudio, kInput, numInputBuses, bus) == kResultOk)
        {
            problems.push (json::Value ("the plug-in describes an input bus that does not exist"));
            ++problemCount;
        }
    }

    // Hand the plug-in its own bus arrangement, which is what a host does
    // before it starts processing.
    std::vector<SpeakerArrangement> inputArrangement (static_cast<size_t> (std::max (numInputBuses, 0)));
    std::vector<SpeakerArrangement> outputArrangement (static_cast<size_t> (std::max (numOutputBuses, 0)));
    for (int32 i = 0; i < numInputBuses; ++i)
        processor->getBusArrangement (kInput, i, inputArrangement[static_cast<size_t> (i)]);
    for (int32 i = 0; i < numOutputBuses; ++i)
        processor->getBusArrangement (kOutput, i, outputArrangement[static_cast<size_t> (i)]);

    bool arrangementAccepted = false;
    if (numInputBuses > 0 || numOutputBuses > 0)
    {
        const tresult res = processor->setBusArrangements (inputArrangement.data(), numInputBuses,
                                                          outputArrangement.data(), numOutputBuses);
        arrangementAccepted = (res == kResultOk);
        if (! arrangementAccepted)
        {
            problems.push (json::Value ("the plug-in refused the bus arrangement it had just published"));
            ++problemCount;
        }
        note (std::string ("bus arrangement : ") + (arrangementAccepted ? "accepted" : "NOT ACCEPTED"));
    }

    if (numInputBuses > 0 || numOutputBuses > 0)
    {
        const tresult res = processor->setBusArrangements (inputArrangement.data(), numInputBuses,
                                                          outputArrangement.data(), numOutputBuses);
        note (std::string ("bus arrangement : ") + (res == kResultOk ? "accepted" : "not accepted (the plug-in keeps its own)"));
    }

    // Re-read the buses: the arrangement call may have changed them.
    numInputBuses = component->getBusCount (kAudio, kInput);
    numOutputBuses = component->getBusCount (kAudio, kOutput);

    inputChannels = numInputBuses > 0 ? busChannels (kInput, 0) : 0;
    outputChannels = numOutputBuses > 0 ? busChannels (kOutput, 0) : 0;

    audioInfo.set ("input_buses", json::Value (numInputBuses));
    audioInfo.set ("output_buses", json::Value (numOutputBuses));
    audioInfo.set ("input_channels", json::Value (inputChannels));
    audioInfo.set ("output_channels", json::Value (outputChannels));
    audioInfo.set ("buses", busRecords);

    // The latency and the tail are read again once the engine exists; the
    // numbers here are only what the plug-in says before it was set up.
    audioInfo.set ("latency_samples_before_setup",
                   json::Value (static_cast<uint32_t> (processor->getLatencySamples())));
    audioInfo.set ("tail_samples",
                   json::Value (static_cast<uint32_t> (processor->getTailSamples())));

    busRecord.set ("event_buses", json::Value (eventBuses));
    busRecord.set ("arrangement_accepted", json::Value (arrangementAccepted));
    busRecord.set ("problems", problems);
    busRecord.set ("problem_count", json::Value (problemCount));

    if (problemCount > 0)
    {
        fail (ExitCode::kStep,
              "the bus layout does not hold up: " + std::to_string (problemCount) + " problem(s), the first is \""
                  + problems.getMemberText (0) + "\"");
        return false;
    }

    return true;
}

bool Host::probeFormats()
{
    const bool can32 = processor->canProcessSampleSize (kSample32) == kResultOk;
    const bool can64 = processor->canProcessSampleSize (kSample64) == kResultOk;
    useDoublePrecision = ! can32 && can64;

    audioInfo.set ("can_process_32bit", json::Value (can32));
    audioInfo.set ("can_process_64bit", json::Value (can64));
    audioInfo.set ("sample_precision", json::Value (useDoublePrecision ? "double" : "single"));

    // The VST3 interface does not publish a list of sample rates or block
    // sizes, so they are probed: a rate or a size the plug-in refuses to
    // set up is not supported.
    const double rates[] = {44100.0, 48000.0, 88200.0, 96000.0, 192000.0};
    const int32 sizes[] = {16, 32, 64, 128, 256, 512, 1024, 4096};

    json::Value rateList = json::Value::makeArray();
    std::string rateText;
    for (double rate : rates)
    {
        ProcessSetup setup;
        setup.processMode = kRealtime;
        setup.symbolicSampleSize = useDoublePrecision ? kSample64 : kSample32;
        setup.maxSamplesPerBlock = 128;
        setup.sampleRate = rate;
        if (processor->setupProcessing (setup) == kResultOk)
        {
            rateList.push (json::Value (rate));
            if (! rateText.empty())
                rateText += " ";
            rateText += std::to_string (static_cast<int> (rate));
        }
    }

    json::Value sizeList = json::Value::makeArray();
    std::string sizeText;
    for (int32 size : sizes)
    {
        ProcessSetup setup;
        setup.processMode = kRealtime;
        setup.symbolicSampleSize = useDoublePrecision ? kSample64 : kSample32;
        setup.maxSamplesPerBlock = size;
        setup.sampleRate = 48000.0;
        if (processor->setupProcessing (setup) == kResultOk)
        {
            sizeList.push (json::Value (size));
            if (! sizeText.empty())
                sizeText += " ";
            sizeText += std::to_string (size);
        }
    }

    audioInfo.set ("sample_rates", rateList);
    audioInfo.set ("block_sizes", sizeList);

    note ("sample rates    : " + (rateText.empty() ? std::string ("none accepted") : rateText));
    note ("block sizes     : " + (sizeText.empty() ? std::string ("none accepted") : sizeText));
    return true;
}

bool Host::enumerateParameters()
{
    const int32 count = controller->getParameterCount();

    json::Value problems = json::Value::makeArray();
    int32 problemCount = 0;
    int32 stringProblems = 0;
    int32 plainProblems = 0;
    int32 idProblems = 0;
    int32 listCount = 0;
    int32 bypassCount = 0;
    int32 programChangeCount = 0;

    auto noteProblem = [&] (const std::string& text) {
        problems.push (json::Value (text));
        ++problemCount;
    };

    for (int32 i = 0; i < count; ++i)
    {
        ParameterInfo info;
        std::memset (&info, 0, sizeof (info));
        if (controller->getParameterInfo (i, info) != kResultOk)
        {
            noteProblem ("the information of parameter " + std::to_string (i) + " could not be read");
            continue;
        }

        const std::string title = fromString128 (info.title);
        const std::string shortTitle = fromString128 (info.shortTitle);
        const std::string units = fromString128 (info.units);

        const ParamValue value = controller->getParamNormalized (info.id);
        const ParamValue plain = controller->normalizedParamToPlain (info.id, value);

        String128 text;
        std::memset (text, 0, sizeof (text));
        std::string valueText;
        if (controller->getParamStringByValue (info.id, value, text) == kResultOk)
            valueText = fromString128 (text);

        String128 defaultText;
        std::memset (defaultText, 0, sizeof (text));
        std::string defaultValueText;
        if (controller->getParamStringByValue (info.id, info.defaultNormalizedValue, defaultText)
            == kResultOk)
            defaultValueText = fromString128 (defaultText);

        // -- the catalogue checks ------------------------------------------
        //
        // A host shows the title, the unit and the value of every
        // parameter, and it lets the user type a value back in.  All three
        // have to work.

        String128 zeroText;
        std::memset (zeroText, 0, sizeof (zeroText));
        std::string zeroValueText;
        const bool zeroOk = controller->getParamStringByValue (info.id, 0.0, zeroText) == kResultOk;
        if (zeroOk)
            zeroValueText = fromString128 (zeroText);

        String128 oneText;
        std::memset (oneText, 0, sizeof (oneText));
        std::string oneValueText;
        const bool oneOk = controller->getParamStringByValue (info.id, 1.0, oneText) == kResultOk;
        if (oneOk)
            oneValueText = fromString128 (oneText);

        // The text of the default has to be readable, and parsing it back
        // has to give the default again.
        ParamValue parsed = 0.0;
        bool parsedOk = false;
        double parsedError = 0.0;
        if (! defaultValueText.empty())
        {
            auto buffer = toTChar (defaultValueText);
            parsedOk = controller->getParamValueByString (info.id, buffer.data(), parsed) == kResultOk;
            if (parsedOk)
                parsedError = std::fabs (parsed - info.defaultNormalizedValue);
        }
        const bool stringOk = parsedOk && parsedError <= Tolerance::kString;

        // The plain value has to survive the way through the plain
        // conversion.
        const ParamValue plainBack = controller->plainParamToNormalized (info.id, plain);
        const double plainError = std::fabs (plainBack - value);
        const bool plainOk = plainError <= Tolerance::kPlain;

        const bool defaultInRange =
            info.defaultNormalizedValue >= 0.0 && info.defaultNormalizedValue <= 1.0;

        // A fresh plug-in sits at its defaults: that is what a host shows
        // before the user touches anything.
        const bool startsAtDefault = std::fabs (value - info.defaultNormalizedValue) <= 1.0e-6;

        // A stepped parameter sits on one of its steps.
        bool onAStep = true;
        if (info.stepCount > 0)
        {
            const double step = 1.0 / static_cast<double> (info.stepCount);
            const double steps = value / step;
            onAStep = std::fabs (steps - std::round (steps)) <= 1.0e-6;
        }

        // Every parameter needs an id of its own.
        bool uniqueId = true;
        for (const auto& known : parameters)
        {
            if (known.id == info.id)
            {
                uniqueId = false;
                break;
            }
        }

        if (title.empty())
            noteProblem ("parameter " + std::to_string (info.id) + " has no title");
        if (defaultValueText.empty())
            noteProblem ("parameter " + std::to_string (info.id) + " has no text for its default value");
        if (! zeroOk || zeroValueText.empty())
            noteProblem ("parameter " + std::to_string (info.id) + " has no text for the value 0");
        if (! oneOk || oneValueText.empty())
            noteProblem ("parameter " + std::to_string (info.id) + " has no text for the value 1");
        if (! stringOk)
        {
            ++stringProblems;
            noteProblem ("the text of parameter " + std::to_string (info.id)
                         + " does not read back as its own value (error " + std::to_string (parsedError) + ")");
        }
        if (! plainOk)
        {
            ++plainProblems;
            noteProblem ("the plain value of parameter " + std::to_string (info.id)
                         + " does not survive the conversion (error " + std::to_string (plainError) + ")");
        }
        if (! defaultInRange)
            noteProblem ("the default of parameter " + std::to_string (info.id) + " is outside 0..1");
        if (! startsAtDefault)
            noteProblem ("parameter " + std::to_string (info.id)
                         + " does not start at its default value");
        if (! onAStep)
            noteProblem ("parameter " + std::to_string (info.id)
                         + " does not sit on one of its steps");
        if (! uniqueId)
        {
            ++idProblems;
            noteProblem ("parameter " + std::to_string (info.id) + " has an id another parameter already uses");
        }

        json::Value record = json::Value::makeObject();
        record.set ("index", json::Value (i));
        record.set ("id", json::Value (info.id));
        record.set ("title", json::Value (title));
        record.set ("short_title", json::Value (shortTitle));
        record.set ("units", json::Value (units));
        record.set ("step_count", json::Value (info.stepCount));
        record.set ("default_normalized", json::Value (info.defaultNormalizedValue));
        record.set ("default_string", json::Value (defaultValueText));

        // A list parameter is drawn as a list, so the host reads the text of
        // every one of its values: those are the choices the user picks
        // from.
        const bool isList = (info.flags & ParameterInfo::kIsList) != 0;
        json::Value listValues = json::Value::makeArray();
        int32 listValueCount = 0;

        if (isList)
        {
            ++listCount;

            const int32 steps = info.stepCount;

            for (int32 step = 0; step <= steps; ++step)
            {
                const ParamValue at = steps > 0
                                        ? static_cast<ParamValue> (step) / static_cast<ParamValue> (steps)
                                        : ParamValue (0);

                String128 stepText;
                std::memset (stepText, 0, sizeof (stepText));
                if (controller->getParamStringByValue (info.id, at, stepText) != kResultOk)
                    continue;

                const std::string text = fromString128 (stepText);

                if (text.empty())
                    continue;

                listValues.push (json::Value (text));
                ++listValueCount;
            }
        }

        const bool isBypass = (info.flags & ParameterInfo::kIsBypass) != 0;
        const bool isProgramChange = (info.flags & ParameterInfo::kIsProgramChange) != 0;

        if (isBypass)
            ++bypassCount;
        if (isProgramChange)
            ++programChangeCount;

        record.set ("is_list", json::Value (isList));
        record.set ("list_values", listValues);
        record.set ("list_value_count", json::Value (listValueCount));
        record.set ("is_bypass", json::Value (isBypass));
        record.set ("is_program_change", json::Value (isProgramChange));
        // The range of a parameter: the normalized range is always 0..1,
        // and the plain range is 0..the number of steps of a stepped
        // parameter.  A continuous parameter has no plain range.
        record.set ("min_normalized", json::Value (0.0));
        record.set ("max_normalized", json::Value (1.0));
        record.set ("min_plain", json::Value (0.0));
        record.set ("max_plain", json::Value (static_cast<double> (info.stepCount)));
        record.set ("stepped", json::Value (info.stepCount > 0));
        record.set ("default_plain",
                    json::Value (controller->normalizedParamToPlain (info.id, info.defaultNormalizedValue)));
        record.set ("flags", json::Value (info.flags));
        record.set ("value_normalized", json::Value (value));
        record.set ("value_plain", json::Value (plain));
        record.set ("value_string", json::Value (valueText));
        record.set ("text_at_zero", json::Value (zeroValueText));
        record.set ("text_at_one", json::Value (oneValueText));
        record.set ("text_round_trip_error", json::Value (parsedError));
        record.set ("text_round_trip_ok", json::Value (stringOk));
        record.set ("plain_round_trip_error", json::Value (plainError));
        record.set ("plain_round_trip_ok", json::Value (plainOk));
        record.set ("starts_at_default", json::Value (startsAtDefault));
        record.set ("on_a_step", json::Value (onAStep));
        parameterRecords.push (record);

        ParameterRecord known;
        known.id = info.id;
        known.index = i;
        known.title = title;
        known.units = units;
        known.stepCount = info.stepCount;
        known.defaultNormalized = info.defaultNormalizedValue;
        known.currentNormalized = value;
        known.automatable = (info.flags & ParameterInfo::kCanAutomate) != 0;
        known.readOnly = (info.flags & ParameterInfo::kIsReadOnly) != 0;
        known.hidden = (info.flags & ParameterInfo::kIsHidden) != 0;
        known.bypass = (info.flags & ParameterInfo::kIsBypass) != 0;
        known.programChange = (info.flags & ParameterInfo::kIsProgramChange) != 0;
        parameters.push_back (known);

        note ("  [" + std::to_string (i) + "] id=" + std::to_string (info.id) + " \"" + title + "\""
                  + (units.empty() ? std::string() : (" [" + units + "]"))
                  + "  steps=" + std::to_string (info.stepCount)
                  + "  value=" + std::to_string (value) + " (\"" + valueText + "\")");
    }

    audioInfo.set ("parameter_count", json::Value (count));

    parameterCatalogue.set ("count", json::Value (count));
    parameterCatalogue.set ("problems", problems);
    parameterCatalogue.set ("problem_count", json::Value (problemCount));
    parameterCatalogue.set ("text_round_trip_failures", json::Value (stringProblems));
    parameterCatalogue.set ("plain_round_trip_failures", json::Value (plainProblems));
    parameterCatalogue.set ("duplicate_id_failures", json::Value (idProblems));
    parameterCatalogue.set ("list_parameters", json::Value (listCount));
    parameterCatalogue.set ("bypass_parameters", json::Value (bypassCount));
    parameterCatalogue.set ("program_change_parameters", json::Value (programChangeCount));

    if (count <= 0)
    {
        fail (ExitCode::kStep, "the controller publishes no parameter at all");
        return false;
    }

    if (problemCount > 0)
    {
        fail (ExitCode::kStep,
              "the parameter catalogue does not hold up: " + std::to_string (problemCount)
                  + " problem(s), the first is \"" + problems.getMemberText (0) + "\"");
        return false;
    }

    return true;
}

bool Host::exerciseParameter()
{
    const int32 count = controller->getParameterCount();
    if (count <= 0)
    {
        note ("parameters      : none");
        parameterRoundTrip.set ("performed", json::Value (false));
        parameterRoundTrip.set ("reason", json::Value ("the controller exports no parameters"));
        return true;
    }

    // Prefer a continuous, automatable, writable parameter.
    int32 chosen = -1;
    ParameterInfo chosenInfo;
    std::memset (&chosenInfo, 0, sizeof (chosenInfo));

    for (int32 i = 0; i < count; ++i)
    {
        ParameterInfo info;
        std::memset (&info, 0, sizeof (info));
        if (controller->getParameterInfo (i, info) != kResultOk)
            continue;
        const bool readOnly = (info.flags & ParameterInfo::kIsReadOnly) != 0;
        const bool hidden = (info.flags & ParameterInfo::kIsHidden) != 0;
        if (readOnly || hidden)
            continue;
        chosen = i;
        chosenInfo = info;
        break;
    }

    if (chosen < 0)
    {
        chosen = 0;
        if (controller->getParameterInfo (0, chosenInfo) != kResultOk)
        {
            fail (ExitCode::kStep, "the controller's parameter info could not be read");
            return false;
        }
    }

    const ParamValue before = controller->getParamNormalized (chosenInfo.id);
    const ParamValue beforePlain = controller->normalizedParamToPlain (chosenInfo.id, before);

    String128 beforeText;
    std::memset (beforeText, 0, sizeof (beforeText));
    std::string beforeString;
    if (controller->getParamStringByValue (chosenInfo.id, before, beforeText) == kResultOk)
        beforeString = fromString128 (beforeText);

    // A different value, inside the legal range.  A stepped parameter is
    // moved one step, a continuous one a quarter of the way.
    ParamValue target = before;
    if (chosenInfo.stepCount == 1)
        target = before > 0.5 ? 0.0 : 1.0;
    else if (chosenInfo.stepCount > 1)
    {
        const int32 steps = chosenInfo.stepCount;
        const int32 current = static_cast<int32> (std::lround (before * steps));
        const int32 wanted = (current + 1) % (steps + 1);
        target = static_cast<ParamValue> (wanted) / static_cast<ParamValue> (steps);
    }
    else if (before < 0.5)
        target = std::min (1.0, before + 0.25);
    else
        target = std::max (0.0, before - 0.25);

    if (std::fabs (target - before) < 1.0e-9)
        target = before > 0.5 ? before - 0.1 : before + 0.1;

    const tresult setRes = controller->setParamNormalized (chosenInfo.id, target);
    const ParamValue after = controller->getParamNormalized (chosenInfo.id);
    const ParamValue afterPlain = controller->normalizedParamToPlain (chosenInfo.id, after);

    String128 afterText;
    std::memset (afterText, 0, sizeof (afterText));
    std::string afterString;
    if (controller->getParamStringByValue (chosenInfo.id, after, afterText) == kResultOk)
        afterString = fromString128 (afterText);

    const bool matches = std::fabs (after - target) <= 1.0e-6;

    // The probe is the parameter the rest of the exercise moves.  It is the
    // one the host has just proved it may write.
    probeId = chosenInfo.id;
    probeValue = target;
    probeTitle = fromString128 (chosenInfo.title);
    haveProbe = true;

    // A caller that names the parameter it wants moved gets it: the
    // exercise then measures the plug-in with that parameter at that
    // value, which is how a rate changing plug-in is measured.
    if (haveForcedParam)
    {
        probeId = forcedParamId;
        probeValue = forcedParamValue;
        probeTitle = "<the parameter the caller named>";
        controller->setParamNormalized (forcedParamId, forcedParamValue);
    }

    parameterRoundTrip.set ("performed", json::Value (true));
    parameterRoundTrip.set ("index", json::Value (chosen));
    parameterRoundTrip.set ("id", json::Value (chosenInfo.id));
    parameterRoundTrip.set ("title", json::Value (fromString128 (chosenInfo.title)));
    parameterRoundTrip.set ("before_normalized", json::Value (before));
    parameterRoundTrip.set ("before_plain", json::Value (beforePlain));
    parameterRoundTrip.set ("before_string", json::Value (beforeString));
    parameterRoundTrip.set ("target_normalized", json::Value (target));
    parameterRoundTrip.set ("after_normalized", json::Value (after));
    parameterRoundTrip.set ("after_plain", json::Value (afterPlain));
    parameterRoundTrip.set ("after_string", json::Value (afterString));
    parameterRoundTrip.set ("read_back_matches", json::Value (matches));
    parameterRoundTrip.set ("set_result", json::Value (setRes == kResultOk));

    note ("parameter test  : id=" + std::to_string (chosenInfo.id) + " \""
              + fromString128 (chosenInfo.title) + "\"  " + std::to_string (before) + " -> " + std::to_string (after)
              + " (\"" + beforeString + "\" -> \"" + afterString + "\")  " + (matches ? "read back OK" : "READ BACK MISMATCH"));

    if (! matches)
    {
        fail (ExitCode::kStep,
              "the controller did not keep the value that was set (expected " + std::to_string (target) + ", read "
                  + std::to_string (after) + ")");
        return false;
    }
    return true;
}

bool Host::activateBuses (IComponent* target, bool state)
{
    if (target == nullptr)
        return false;

    int32 activated = 0;
    int32 failed = 0;
    int32 audioFailed = 0;

    // The audio buses first, then the event ones: the media types of the
    // format, in the order the format lists them.
    for (int32 media = 0; media < static_cast<int32> (kNumMediaTypes); ++media)
    {
        const MediaType mediaType = (media == 0) ? kAudio : kEvent;

        for (int32 direction = 0; direction < 2; ++direction)
        {
            const BusDirection busDirection = (direction == 0) ? kInput : kOutput;
            const int32 count = target->getBusCount (mediaType, busDirection);

            for (int32 index = 0; index < count; ++index)
            {
                const tresult res = target->activateBus (mediaType, busDirection, index, state);

                if (res == kResultOk)
                {
                    ++activated;
                    continue;
                }

                ++failed;
                if (mediaType == kAudio)
                    ++audioFailed;

                note (std::string ("bus activation  : ") + (mediaType == kAudio ? "audio " : "event ")
                          + (busDirection == kInput ? "input " : "output ") + "bus " + std::to_string (index)
                          + " could not be " + (state ? "activated" : "deactivated")
                          + " (result " + std::to_string (res) + ")");
            }
        }
    }

    if (target == component)
    {
        activatedBusCount = activated;
        activationFailures = failed;
        busesActivated = state;
    }

    note ("bus activation  : " + std::to_string (activated) + " bus(es) "
              + (state ? "activated" : "deactivated")
              + (failed > 0 ? (", " + std::to_string (failed) + " refused") : std::string()));

    // The audio buses have to be there: without them the host cannot hand
    // the plug-in any audio at all.  The event buses are the host's wish,
    // and a plug-in that does not want them may say so.
    return audioFailed == 0;
}

bool Host::setupAudio()
{
    ProcessSetup setup;
    setup.processMode = kRealtime;
    setup.symbolicSampleSize = useDoublePrecision ? kSample64 : kSample32;
    setup.maxSamplesPerBlock = blockSize;
    setup.sampleRate = sampleRate;

    if (processor->setupProcessing (setup) != kResultOk)
    {
        fail (ExitCode::kStep, "setupProcessing() failed for " + std::to_string (blockSize)
                                  + " frames at " + std::to_string (static_cast<int> (sampleRate)) + " Hz");
        return false;
    }

    // The buses are activated before the component is set active.  The
    // format asks the host for it ("The plug-in should only processed an
    // activated bus"), and a plug-in that takes the specification seriously
    // writes nothing at all when a host forgets: the host then sees its own
    // sentinel (a peak of 1e30) and 512 unwritten output samples for a 256
    // frame stereo block.
    if (! activateBuses (component, true))
    {
        fail (ExitCode::kStep, "the component refused to activate its audio buses");
        return false;
    }

    busRecord.set ("buses_activated", json::Value (activatedBusCount));
    busRecord.set ("activation_failures", json::Value (activationFailures));
    busRecord.set ("activated_before_set_active", json::Value (true));

    if (component->setActive (true) != kResultOk)
    {
        fail (ExitCode::kStep, "setActive(true) failed");
        return false;
    }
    componentActivated = true;
    if (processor->setProcessing (true) != kResultOk)
    {
        fail (ExitCode::kStep, "setProcessing(true) failed");
        return false;
    }

    // Buffers for every audio bus.
    inputBuses.assign (static_cast<size_t> (std::max (numInputBuses, 0)), BusBuffers());
    outputBuses.assign (static_cast<size_t> (std::max (numOutputBuses, 0)), BusBuffers());

    for (int32 i = 0; i < numInputBuses; ++i)
        inputBuses[static_cast<size_t> (i)].prepare (busChannels (kInput, i), blockSize, useDoublePrecision);
    for (int32 i = 0; i < numOutputBuses; ++i)
        outputBuses[static_cast<size_t> (i)].prepare (busChannels (kOutput, i), blockSize, useDoublePrecision);

    return true;
}

bool Host::processAudio()
{
    // The parameter change the exercise made is handed to the processor the
    // way a host hands an automation change over.
    HostParameterChanges changes;
    if (parameterRoundTrip.getBool ("performed") && parameterRoundTrip.hasMember ("id"))
    {
        const auto id = static_cast<ParamID> (parameterRoundTrip.getNumber ("id"));
        const auto value = parameterRoundTrip.getNumber ("target_normalized");
        changes.queue (id, value, 0);
    }

    // -- the required pass: a block of silence -----------------------------
    //
    // The number of samples the host hands over is the number of samples
    // the plug-in has to write.  A host does not clear its output buffers,
    // so the host fills them with a value no plug-in produces and counts
    // the samples the plug-in left untouched.  A plug-in that writes
    // nothing at all is caught here: the samples keep the sentinel, and the
    // peak of the output is the sentinel itself.
    int64_t expectedOutputSamples = 0;
    for (const auto& bus : outputBuses)
        expectedOutputSamples += static_cast<int64_t> (bus.numChannels) * static_cast<int64_t> (blockSize);

    const int64_t unwrittenBefore = unwrittenOutputSamples;

    BlockResult silence;
    if (! runBlock (false, changes, silence))
        return false;

    // The count of the required pass on its own: the exercise measures more
    // than one block, and the report says which block it is talking about.
    const int64_t unwrittenRequired = unwrittenOutputSamples - unwrittenBefore;

    processingInfo.set ("frames", json::Value (blockSize));
    processingInfo.set ("sample_rate", json::Value (sampleRate));
    processingInfo.set ("input_channels", json::Value (inputChannels));
    processingInfo.set ("output_channels", json::Value (outputChannels));
    processingInfo.set ("input_peak", json::Value (silence.inputPeak));
    processingInfo.set ("output_peak", json::Value (silence.outputPeak));
    processingInfo.set ("output_rms", json::Value (silence.outputRms));
    processingInfo.set ("output_finite", json::Value (silence.finite));
    processingInfo.set ("blocks_processed", json::Value (1));
    processingInfo.set ("parameter_change_delivered", json::Value (changes.getParameterCount() > 0));
    // The samples the plug-in wrote, and the samples it did not write.  The
    // two together are the samples the host handed over.
    processingInfo.set ("unwritten_output_samples", json::Value (unwrittenRequired));
    processingInfo.set ("output_samples_expected", json::Value (expectedOutputSamples));
    processingInfo.set ("written_output_samples",
                        json::Value (expectedOutputSamples - unwrittenRequired));
    // The length of the output: the host gives the plug-in a buffer of
    // so many frames, and the plug-in has to fill all of them.
    processingInfo.set ("output_frames", json::Value (blockSize));
    processingInfo.set ("output_length_ok", json::Value (unwrittenRequired == 0));

    note ("processing      : " + std::to_string (blockSize) + " frames, "
              + std::to_string (outputChannels) + " ch, " + std::to_string (static_cast<int> (sampleRate))
              + " Hz  in peak=" + std::to_string (silence.inputPeak)
              + "  out peak=" + std::to_string (silence.outputPeak) + "  rms="
              + std::to_string (silence.outputRms) + (silence.finite ? "" : "  NOT FINITE")
              + "  written=" + std::to_string (expectedOutputSamples - unwrittenRequired)
              + "/" + std::to_string (expectedOutputSamples)
              + (unwrittenRequired > 0
                     ? ("  " + std::to_string (unwrittenRequired)
                        + " output sample(s) were not written")
                     : std::string()));

    if (unwrittenRequired > 0)
    {
        fail (ExitCode::kStep,
              "the plug-in did not write " + std::to_string (unwrittenRequired)
                  + " of its " + std::to_string (expectedOutputSamples)
                  + " output samples: a host would pass them on as they were");
        return false;
    }

    if (! silence.finite)
    {
        fail (ExitCode::kStep, "the processor produced a sample that is not finite");
        return false;
    }

    // -- the optional pass: a 1 kHz tone -----------------------------------
    if (wantTone)
    {
        BlockResult tone;
        if (! runBlock (true, changes, tone))
            return false;

        // The caller may want the samples the plug-in produced, so that the
        // output can be compared with a reference.  The dump step at the end
        // of the exercise collects them.
        json::Value toneInfo = json::Value::makeObject();
        toneInfo.set ("input_peak", json::Value (tone.inputPeak));
        toneInfo.set ("output_peak", json::Value (tone.outputPeak));
        toneInfo.set ("output_rms", json::Value (tone.outputRms));
        toneInfo.set ("output_finite", json::Value (tone.finite));
        processingInfo.set ("tone", toneInfo);

        note ("tone pass       : 1 kHz, -6 dBFS  in peak=" + std::to_string (tone.inputPeak)
                  + "  out peak=" + std::to_string (tone.outputPeak) + "  rms="
                  + std::to_string (tone.outputRms) + (tone.finite ? "" : "  NOT FINITE"));

        if (! tone.finite)
        {
            fail (ExitCode::kStep, "the processor produced a sample that is not finite on the tone pass");
            return false;
        }
    }

    return true;
}

bool Host::runBlock (bool tone, HostParameterChanges& changes, BlockResult& result)
{
    const int32 kBlockSize = blockSize;
    const double kSampleRate = sampleRate;
    constexpr double kToneHz = Setup::kToneHz;
    constexpr double kToneAmplitude = Setup::kToneAmplitude; // -6 dBFS

    for (auto& bus : inputBuses)
        bus.clear();
    for (auto& bus : outputBuses)
        bus.fillSentinel();

    if (tone)
    {
        for (auto& bus : inputBuses)
        {
            for (size_t c = 0; c < bus.floatStorage.size(); ++c)
            {
                for (int32 n = 0; n < kBlockSize; ++n)
                {
                    const double phase = 2.0 * 3.14159265358979323846 * kToneHz
                                           * static_cast<double> (n) / kSampleRate;
                    bus.floatStorage[c][static_cast<size_t> (n)] =
                        static_cast<float> (kToneAmplitude * std::sin (phase));
                    bus.doubleStorage[c][static_cast<size_t> (n)] = kToneAmplitude * std::sin (phase);
                }
            }
        }
    }

    ProcessData data;
    data.processMode = kRealtime;
    data.symbolicSampleSize = useDoublePrecision ? kSample64 : kSample32;
    data.numSamples = kBlockSize;
    data.numInputs = numInputBuses;
    data.numOutputs = numOutputBuses;
    data.inputs = numInputBuses > 0 ? &inputBuses[0].buffers : nullptr;
    data.outputs = numOutputBuses > 0 ? &outputBuses[0].buffers : nullptr;
    data.inputParameterChanges = &changes;
    data.outputParameterChanges = nullptr;
    data.inputEvents = nullptr;
    data.outputEvents = nullptr;
    data.processContext = nullptr;

    const tresult res = processor->process (data);
    if (res != kResultOk)
    {
        fail (ExitCode::kStep, "process() failed with result " + std::to_string (res));
        return false;
    }

    double inputPeak = 0.0;
    double outputPeak = 0.0;
    double outputSumSquares = 0.0;
    int64 outputSamples = 0;
    bool finite = true;

    // The samples the plug-in did not write.
    for (const auto& bus : outputBuses)
        unwrittenOutputSamples += bus.sentinelCount (useDoublePrecision);

    for (const auto& bus : inputBuses)
    {
        for (const auto& channel : bus.floatStorage)
            for (float v : channel)
                inputPeak = std::max (inputPeak, std::fabs (static_cast<double> (v)));
        for (const auto& channel : bus.doubleStorage)
            for (double v : channel)
                inputPeak = std::max (inputPeak, std::fabs (v));
    }

    // Only the storage the plug-in works in is measured: the other one
    // still holds the sentinel the host put there.
    for (const auto& bus : outputBuses)
    {
        if (useDoublePrecision)
        {
            for (const auto& channel : bus.doubleStorage)
            {
                for (double v : channel)
                {
                    if (! std::isfinite (v))
                        finite = false;
                    outputPeak = std::max (outputPeak, std::fabs (v));
                    outputSumSquares += v * v;
                    ++outputSamples;
                }
            }
        }
        else
        {
            for (const auto& channel : bus.floatStorage)
            {
                for (float v : channel)
                {
                    const double d = static_cast<double> (v);
                    if (! std::isfinite (d))
                        finite = false;
                    outputPeak = std::max (outputPeak, std::fabs (d));
                    outputSumSquares += d * d;
                    ++outputSamples;
                }
            }
        }
    }

    result.inputPeak = inputPeak;
    result.outputPeak = outputPeak;
    result.outputRms = outputSamples > 0 ? std::sqrt (outputSumSquares / static_cast<double> (outputSamples)) : 0.0;
    result.finite = finite;
    return true;
}

// ---------------------------------------------------------------------------
//  the parameters the plug-in really carries
// ---------------------------------------------------------------------------
//
//  A host moves a parameter and expects the plug-in to work with the new
//  value.  The only place a host can see what the plug-in holds is the
//  state: the plug-in's own idea of its parameters is written there.  So
//  the check is simple: move the parameter, and see whether the state
//  moves with it.
//
//  In the combined shape (the component is the controller) the component's
//  state carries the parameters.  In the split shape the controller's
//  state does, and the component's state carries the audio.  The host
//  knows which shape it is talking to, so it looks in the right place.

bool Host::checkParameterLiveness()
{
    const bool combined = isCombined();

    // The state that carries the parameters.
    //
    // The shape of the plug-in says which state it is: in the combined
    // shape the component carries them, in the split shape the controller
    // does.  A plug-in may keep its parameters in the component anyway -
    // a controller that hands out an empty state and a component that
    // carries the values is a shape the host has to cope with, so the
    // carrier is measured and not assumed.
    std::string carrierName = combined ? "component" : "controller";
    std::vector<uint8_t> probe;
    const bool controllerReadable = readState (controller, probe);
    if (! combined && ! controllerReadable)
    {
        if (readState (component, probe))
        {
            carrierName = "component";
            note ("liveness        : the controller hands out no state, the component carries the parameters");
        }
    }

    auto readCarrier = [this, &carrierName] (std::vector<uint8_t>& out) -> bool {
        if (carrierName == "component")
            return readState (component, out);
        return readState (controller, out);
    };

    std::vector<uint8_t> before;
    if (! readCarrier (before))
    {
        fail (ExitCode::kStep, "the state that carries the parameters could not be read");
        return false;
    }

    json::Value inert = json::Value::makeArray();
    json::Value inertDetail = json::Value::makeArray();
    int32 inertCount = 0;
    int32 liveCount = 0;

    for (const auto& parameter : parameters)
    {
        if (parameter.readOnly || parameter.hidden)
            continue;

        // The value that is there now, not the one that was there when the
        // catalogue was read: the exercise has moved some parameters
        // already.
        const double current = controller->getParamNormalized (parameter.id);

        // A value that is clearly different from the one that is there.
        double target = current;
        if (parameter.stepCount > 0)
        {
            const int32 steps = parameter.stepCount;
            const int32 currentStep = static_cast<int32> (std::lround (current * steps));
            target = static_cast<double> ((currentStep + 1) % (steps + 1)) / static_cast<double> (steps);
        }
        else
        {
            target = current < 0.5 ? 0.8 : 0.2;
        }

        if (std::fabs (target - current) < 1.0e-9)
            target = current < 0.5 ? 0.6 : 0.4;

        controller->setParamNormalized (parameter.id, target);

        std::vector<uint8_t> after;
        const bool readable = readCarrier (after);
        const bool live = readable && (after != before);

        if (live)
        {
            ++liveCount;
        }
        else
        {
            ++inertCount;
            inert.push (json::Value (parameter.id));
            json::Value detail = json::Value::makeObject();
            detail.set ("id", json::Value (parameter.id));
            detail.set ("title", json::Value (parameter.title));
            detail.set ("value", json::Value (target));
            inertDetail.push (detail);
        }

        // Put the parameter back where it was.
        controller->setParamNormalized (parameter.id, current);
    }

    livenessRecord.set ("carrier", json::Value (carrierName));
    livenessRecord.set ("live", json::Value (liveCount));
    livenessRecord.set ("inert", json::Value (inertCount));
    livenessRecord.set ("inert_ids", inert);
    livenessRecord.set ("inert_detail", inertDetail);

    note ("parameter state : " + std::to_string (liveCount) + " parameter(s) live in the "
              + (combined ? "component" : "controller") + "'s state, "
              + std::to_string (inertCount) + " inert");

    if (inertCount > 0)
    {
        fail (ExitCode::kStep,
              std::to_string (inertCount) + " parameter(s) are published but the plug-in does not "
                  "carry them: the first is id " + std::to_string (inert.getMemberNumber (0)));
        return false;
    }

    return true;
}

// ---------------------------------------------------------------------------
//  the agreement between the controller and the component
// ---------------------------------------------------------------------------
//
//  The question the host has to answer is: when the host sends a parameter
//  change, does the plug-in work with the value the host sent?
//
//  The host cannot look into the plug-in.  It has two witnesses:
//
//    the state.  The state of a plug-in that has been told a new value
//    differs from the state of the same plug-in that has not, and the
//    difference is the parameter.  Three runs of the same exercise with
//    three fresh instances make this exact: the run that was automated and
//    the run whose controller was told the value have to end in the very
//    same state.
//
//    the audio.  The same three runs say whether the value the plug-in
//    processes with is the one that changes the sound.
//
//  Three fresh instances are used because a plug-in's own state moves on
//  while it processes: two runs only compare cleanly when they start from
//  the same place.

bool Host::runWitness (WitnessMode mode, ParamID probe, double value, WitnessRun& run)
{
    run = WitnessRun();

    const char* label = mode == WitnessMode::Baseline ? "baseline"
                        : mode == WitnessMode::Automation ? "automation"
                                                         : "controller";
    note ("witness         : " + std::string (label) + " run, parameter " + std::to_string (probe)
              + " -> " + std::to_string (value));

    // -- a fresh instance --------------------------------------------------
    PClassInfo info;
    std::memset (&info, 0, sizeof (info));
    if (factory->getClassInfo (selectedClassIndex, &info) != kResultOk)
    {
        run.error = "the class info of the selected class could not be read";
        return false;
    }

    TUID componentIid;
    IComponent::iid.toTUID (componentIid);

    IComponent* instance = nullptr;
    if (factory->createInstance (info.cid, componentIid, reinterpret_cast<void**> (&instance))
            != kResultOk
        || instance == nullptr)
    {
        run.error = "a second instance of the module could not be created";
        return false;
    }

    // The handler has to outlive the controller, so it is declared first.
    HostComponentHandler handler;
    FUnknownPtr<IAudioProcessor> audio;
    FUnknownPtr<IEditController> controllerOfInstance;

    auto giveUp = [&] (const std::string& why) {
        run.error = why;
        run.ok = false;
        if (controllerOfInstance != nullptr)
            controllerOfInstance->terminate();
        if (instance != nullptr)
        {
            instance->terminate();
            guardedRelease (instance);
        }
        return false;
    };

    note ("witness         : the instance is created");
    if (instance->initialize (static_cast<FUnknown*> (&context)) != kResultOk)
        return giveUp ("the second instance refused to initialize");
    note ("witness         : the instance is initialized");

    audio = FUnknownPtr<IAudioProcessor> (instance);
    if (audio == nullptr)
        return giveUp ("the second instance has no audio processor");

    controllerOfInstance = FUnknownPtr<IEditController> (instance);
    if (controllerOfInstance == nullptr)
    {
        // The plug-in may have a controller of its own.
        TUID controllerClassId;
        std::memset (controllerClassId, 0, sizeof (controllerClassId));
        if (instance->getControllerClassId (controllerClassId) == kResultOk)
        {
            TUID controllerIid;
            IEditController::iid.toTUID (controllerIid);
            IEditController* raw = nullptr;
            if (factory->createInstance (controllerClassId, controllerIid,
                                         reinterpret_cast<void**> (&raw))
                    == kResultOk
                && raw != nullptr)
                controllerOfInstance = FUnknownPtr<IEditController> (raw);
        }
    }

    if (controllerOfInstance == nullptr)
        return giveUp ("the second instance has no edit controller");

    controllerOfInstance->setComponentHandler (static_cast<IComponentHandler*> (&handler));

    FUnknownPtr<IConnectionPoint> componentPoint (instance);
    FUnknownPtr<IConnectionPoint> controllerPoint (controllerOfInstance);
    if (componentPoint != nullptr && controllerPoint != nullptr)
    {
        componentPoint->connect (controllerPoint.getInterface());
        controllerPoint->connect (componentPoint.getInterface());
    }

    // -- the same setup the main instance has ------------------------------
    ProcessSetup setup;
    setup.processMode = kRealtime;
    setup.symbolicSampleSize = useDoublePrecision ? kSample64 : kSample32;
    setup.maxSamplesPerBlock = blockSize;
    setup.sampleRate = sampleRate;

    note ("witness         : the instance is connected");
    if (audio->setupProcessing (setup) != kResultOk)
        return giveUp ("the second instance refused the processing setup");
    note ("witness         : the instance is set up");
    // The buses of the second instance are activated before it is set
    // active, exactly as the buses of the first one are.
    if (! activateBuses (instance, true))
        return giveUp ("the second instance refused to activate its audio buses");
    if (instance->setActive (true) != kResultOk)
        return giveUp ("the second instance refused to become active");
    if (audio->setProcessing (true) != kResultOk)
        return giveUp ("the second instance refused to start processing");

    const int32 instanceInputs = instance->getBusCount (kAudio, kInput);
    const int32 instanceOutputs = instance->getBusCount (kAudio, kOutput);

    std::vector<BusBuffers> inputs (static_cast<size_t> (std::max (instanceInputs, 0)), BusBuffers());
    std::vector<BusBuffers> outputs (static_cast<size_t> (std::max (instanceOutputs, 0)), BusBuffers());

    auto channelsOf = [instance] (BusDirection direction, int32 index) -> int32 {
        BusInfo bus;
        std::memset (&bus, 0, sizeof (bus));
        if (instance->getBusInfo (kAudio, direction, index, bus) != kResultOk)
            return 0;
        return bus.channelCount;
    };

    for (int32 i = 0; i < instanceInputs; ++i)
        inputs[static_cast<size_t> (i)].prepare (channelsOf (kInput, i), blockSize, useDoublePrecision);
    for (int32 i = 0; i < instanceOutputs; ++i)
        outputs[static_cast<size_t> (i)].prepare (channelsOf (kOutput, i), blockSize, useDoublePrecision);

    // -- the probe ---------------------------------------------------------
    if (mode == WitnessMode::Controller)
    {
        controllerOfInstance->setParamNormalized (probe, value);
    }

    // -- the run -----------------------------------------------------------
    const size_t totalSamples =
        static_cast<size_t> (Setup::kWarmUpBlocks + Setup::kMeasureBlocks) * static_cast<size_t> (blockSize);
    const std::vector<float> signal =
        auralis::makeMixed (Setup::kToneHz, Setup::kToneAmplitude, totalSamples, sampleRate);

    HostParameterChanges changes;

    std::vector<float> measured;

    for (int32 block = 0; block < Setup::kWarmUpBlocks + Setup::kMeasureBlocks; ++block)
    {
        // The automation is delivered with the first block, the way a host
        // delivers it: as part of the block, not before it.
        if (mode == WitnessMode::Automation && block == 0)
            changes.queue (probe, value, 0);

        for (auto& bus : inputs)
            bus.clear();
        for (auto& bus : outputs)
            bus.fillSentinel();

        const size_t offset = static_cast<size_t> (block) * static_cast<size_t> (blockSize);
        for (auto& bus : inputs)
        {
            for (size_t c = 0; c < bus.floatStorage.size(); ++c)
                for (int32 n = 0; n < blockSize; ++n)
                {
                    const float value = signal[offset + static_cast<size_t> (n)];
                    bus.floatStorage[c][static_cast<size_t> (n)] = value;
                    bus.doubleStorage[c][static_cast<size_t> (n)] = value;
                }
        }

        ProcessData data;
        data.processMode = kRealtime;
        data.symbolicSampleSize = useDoublePrecision ? kSample64 : kSample32;
        data.numSamples = blockSize;
        data.numInputs = instanceInputs;
        data.numOutputs = instanceOutputs;
        data.inputs = instanceInputs > 0 ? &inputs[0].buffers : nullptr;
        data.outputs = instanceOutputs > 0 ? &outputs[0].buffers : nullptr;
        data.inputParameterChanges = &changes;
        data.outputParameterChanges = nullptr;
        data.inputEvents = nullptr;
        data.outputEvents = nullptr;
        data.processContext = nullptr;

        const tresult res = audio->process (data);
        if (res != kResultOk)
            return giveUp ("the second instance failed to process a block");

        if (block >= Setup::kWarmUpBlocks)
        {
            // Only the first channel of the first output bus is measured:
            // the channels of a plug-in are the same signal in most cases,
            // and mixing them would make the comparison meaningless.
            if (! outputs.empty())
                measured = firstChannel (outputs[0], useDoublePrecision);
        }
    }

    // -- what the instance holds at the end --------------------------------
    std::vector<uint8_t> state;
    if (! readState (instance, state))
        return giveUp ("the second instance would not give its state");

    run.state = state;
    run.stateHash = auralis::hashBytes (state.data(), state.size());
    run.output = measured;
    run.ok = true;

    // -- the way out -------------------------------------------------------
    audio->setProcessing (false);
    instance->setActive (false);
    // The buses are deactivated after the component, the reverse of the
    // order they were activated in.
    activateBuses (instance, false);
    if (componentPoint != nullptr && controllerPoint != nullptr)
    {
        componentPoint->disconnect (controllerPoint.getInterface());
        controllerPoint->disconnect (componentPoint.getInterface());
    }
    controllerOfInstance->terminate();
    instance->terminate();
    controllerOfInstance = nullptr;
    audio = nullptr;
    guardedRelease (instance);

    return true;
}

bool Host::checkAgreement()
{
    if (! haveProbe)
    {
        agreementRecord.set ("performed", json::Value (false));
        agreementRecord.set ("reason", json::Value ("there is no parameter the host may move"));
        return true;
    }

    WitnessRun baseline;
    WitnessRun automated;
    WitnessRun told;

    if (! runWitness (WitnessMode::Baseline, probeId, probeValue, baseline))
    {
        fail (ExitCode::kStep, "the baseline run failed: " + baseline.error);
        return false;
    }
    if (! runWitness (WitnessMode::Automation, probeId, probeValue, automated))
    {
        fail (ExitCode::kStep, "the automated run failed: " + automated.error);
        return false;
    }
    if (! runWitness (WitnessMode::Controller, probeId, probeValue, told))
    {
        fail (ExitCode::kStep, "the controller run failed: " + told.error);
        return false;
    }

    // The state is the witness: the run that was automated has to end in
    // the same state as the run whose controller was told the value, and
    // in a different state from the run that was left alone.
    const bool stateMatchesTold = (automated.state == told.state);
    const bool stateMatchesBaseline = (automated.state == baseline.state);

    // The audio is the second witness.
    const double audioToTold = auralis::maxDifference (automated.output, told.output);
    const double audioToBaseline = auralis::maxDifference (automated.output, baseline.output);
    const double peak = auralis::peakOf (automated.output);
    const double audioTolerance = std::max (Tolerance::kAudio * peak, 1.0e-9);

    const bool audioMatchesTold = (audioToTold <= audioTolerance);
    const bool probeIsAudible = (audioToBaseline > audioTolerance);

    agreementRecord.set ("performed", json::Value (true));
    agreementRecord.set ("probe_id", json::Value (probeId));
    agreementRecord.set ("probe_title", json::Value (probeTitle));
    agreementRecord.set ("probe_value", json::Value (probeValue));
    agreementRecord.set ("state_matches_controller", json::Value (stateMatchesTold));
    agreementRecord.set ("state_matches_baseline", json::Value (stateMatchesBaseline));
    agreementRecord.set ("state_bytes", json::Value (static_cast<uint32_t> (automated.state.size())));
    agreementRecord.set ("audio_difference_to_controller", json::Value (audioToTold));
    agreementRecord.set ("audio_difference_to_baseline", json::Value (audioToBaseline));
    agreementRecord.set ("audio_tolerance", json::Value (audioTolerance));
    agreementRecord.set ("audio_matches_controller", json::Value (audioMatchesTold));
    agreementRecord.set ("probe_is_audible", json::Value (probeIsAudible));

    note ("agreement      : parameter " + std::to_string (probeId) + " \"" + probeTitle + "\": the automation "
              + (stateMatchesTold ? "reached the plug-in and the plug-in holds the value that was sent"
                                  : "did NOT reach the plug-in")
              + ", the audio differs from the default by " + std::to_string (audioToBaseline));

    if (stateMatchesBaseline)
    {
        fail (ExitCode::kStep,
              "the automation the host sent did not reach the plug-in: the state after the automated run "
                  "is the state of a plug-in that was never told");
        return false;
    }

    if (! stateMatchesTold)
    {
        fail (ExitCode::kStep,
              "the value the plug-in holds after the automation is not the value the host sent: "
                  "the state after the automated run differs from the state after the controller was told "
                  "the same value");
        return false;
    }

    return true;
}

// ---------------------------------------------------------------------------
//  the step a parameter change makes
// ---------------------------------------------------------------------------
//
//  A parameter change that arrives while the plug-in is playing is applied
//  over a short, declared time instead of as a step.  A step is a click:
//  the output jumps by the whole change at once, and the jump is heard as a
//  tick in the programme.
//
//  The measurement delivers a change with a block, the way a host delivers
//  automation, and watches the output.  The largest difference between two
//  neighbouring samples at the change is the step the change made; the
//  largest difference the signal makes on its own is the floor under it,
//  because a tone has a step of its own.

/** A value near `current` that is clearly different from it (defined below). */
static double neighbourValue (double current, int32 stepCount);

bool Host::measureParameterStep()
{
    stepRecord.set ("performed", json::Value (false));

    if (! haveProbe)
    {
        stepRecord.set ("reason", json::Value ("there is no parameter the host may move"));
        return true;
    }

    const ParamID id = probeId;
    const double original = controller->getParamNormalized (id);
    const double target = neighbourValue (original, 0);

    // The tone runs on across the blocks: a tone that starts again in every
    // block has a step of its own at every block boundary, and the step of
    // the change would be lost in it.
    const size_t totalSamples = static_cast<size_t> (blockSize) * 24;
    const std::vector<float> signal =
        auralis::makeTone (Setup::kToneHz, Setup::kToneAmplitude, totalSamples, sampleRate);

    constexpr int32 kChangeBlock = 8;
    constexpr int32 kOffset = 64;    // the change is delivered in the middle of the block

    std::vector<float> steady;        // the blocks before the change
    std::vector<float> changed;       // the blocks the change is in
    int32 changedBlock = kChangeBlock;  // the block the largest step is in

    for (int32 block = 0; block < kChangeBlock + 4; ++block)
    {
        HostParameterChanges changes;

        if (block == kChangeBlock)
            changes.queue (id, target, kOffset);

        for (auto& bus : inputBuses)
            bus.clear();
        for (auto& bus : outputBuses)
            bus.fillSentinel();

        const size_t offset = static_cast<size_t> (block) * static_cast<size_t> (blockSize);

        for (auto& bus : inputBuses)
        {
            for (size_t c = 0; c < bus.floatStorage.size (); ++c)
                for (int32 n = 0; n < blockSize; ++n)
                {
                    const size_t index = offset + static_cast<size_t> (n);
                    const float value = index < signal.size () ? signal[index] : 0.f;
                    bus.floatStorage[c][static_cast<size_t> (n)] = value;
                    bus.doubleStorage[c][static_cast<size_t> (n)] = value;
                }
        }

        ProcessData data;
        data.processMode = kRealtime;
        data.symbolicSampleSize = useDoublePrecision ? kSample64 : kSample32;
        data.numSamples = blockSize;
        data.numInputs = numInputBuses;
        data.numOutputs = numOutputBuses;
        data.inputs = numInputBuses > 0 ? &inputBuses[0].buffers : nullptr;
        data.outputs = numOutputBuses > 0 ? &outputBuses[0].buffers : nullptr;
        data.inputParameterChanges = &changes;
        data.outputParameterChanges = nullptr;
        data.inputEvents = nullptr;
        data.outputEvents = nullptr;
        data.processContext = nullptr;

        if (processor->process (data) != kResultOk)
        {
            fail (ExitCode::kStep, "the processor failed while the step of a parameter change was measured");
            return false;
        }

        for (const auto& bus : outputBuses)
            unwrittenOutputSamples += bus.sentinelCount (useDoublePrecision);

        if (outputBuses.empty ())
            continue;

        const std::vector<float> made = firstChannel (outputBuses[0], useDoublePrecision);

        if (block < kChangeBlock)
            steady.insert (steady.end (), made.begin (), made.end ());
        else
            changed.insert (changed.end (), made.begin (), made.end ());
    }

    // The step the signal makes on its own.
    double signalStep = 0.0;
    for (size_t i = 1; i < steady.size (); ++i)
        signalStep = std::max (signalStep,
                               std::fabs (static_cast<double> (steady[i]) - steady[i - 1]));

    // The step at the change, and where in the block it landed.  The whole
    // of the change is measured, not only the block it arrives in: a ramp
    // runs on into the blocks after it.
    double changeStep = 0.0;
    int32 index = -1;
    for (size_t i = 1; i < changed.size (); ++i)
    {
        const double d = std::fabs (static_cast<double> (changed[i]) - changed[i - 1]);
        if (d > changeStep)
        {
            changeStep = d;
            index = static_cast<int32> (i);
            changedBlock = kChangeBlock
                         + static_cast<int32> (i / static_cast<size_t> (blockSize));
        }
    }

    stepRecord.set ("performed", json::Value (true));
    stepRecord.set ("id", json::Value (id));
    stepRecord.set ("title", json::Value (probeTitle));
    stepRecord.set ("value", json::Value (target));
    stepRecord.set ("signal_step", json::Value (signalStep));
    stepRecord.set ("change_step", json::Value (changeStep));
    stepRecord.set ("excess", json::Value (changeStep - signalStep));
    stepRecord.set ("index_in_block", json::Value (index % blockSize));
    stepRecord.set ("offset", json::Value (static_cast<int32> (kOffset)));
    stepRecord.set ("block", json::Value (static_cast<int32> (kChangeBlock)));
    stepRecord.set ("worst_block", json::Value (static_cast<int32> (changedBlock)));
    stepRecord.set ("block_size", json::Value (static_cast<int32> (blockSize)));

    note ("parameter step : a change of " + std::to_string (changeStep)
          + " at the change, the signal makes " + std::to_string (signalStep)
          + " on its own, at sample " + std::to_string (index) + " of the block");

    // The parameter goes back to where it was.
    controller->setParamNormalized (id, original);

    return true;
}

// ---------------------------------------------------------------------------
//  what the parameters do to the audio
// ---------------------------------------------------------------------------
//
//  Not every parameter of a plug-in changes the sound: a meter, a mode
//  that only matters while something else is happening, a switch that
//  arms a learning mode.  The host cannot tell them apart, so it measures.
//
//  The measurement uses three blocks of the same tone:
//
//      X1   the parameter at its value
//      X2   the parameter at another value
//      X3   the parameter at its value again
//
//  A parameter that changes nothing gives three blocks that drift apart no
//  more than the plug-in drifts on its own.  A parameter that changes
//  something gives a middle block that stands out from both its
//  neighbours.

bool Host::processBlockWith (const std::vector<float>& signal, size_t offset,
                             std::vector<float>& output)
{
    output.clear();

    for (auto& bus : inputBuses)
        bus.clear();
    for (auto& bus : outputBuses)
        bus.fillSentinel();

    for (auto& bus : inputBuses)
    {
        for (size_t c = 0; c < bus.floatStorage.size(); ++c)
            for (int32 n = 0; n < blockSize; ++n)
            {
                const size_t index = offset + static_cast<size_t> (n);
                const float value = index < signal.size() ? signal[index] : 0.f;
                bus.floatStorage[c][static_cast<size_t> (n)] = value;
                bus.doubleStorage[c][static_cast<size_t> (n)] = value;
            }
    }

    HostParameterChanges noChanges;

    ProcessData data;
    data.processMode = kRealtime;
    data.symbolicSampleSize = useDoublePrecision ? kSample64 : kSample32;
    data.numSamples = blockSize;
    data.numInputs = numInputBuses;
    data.numOutputs = numOutputBuses;
    data.inputs = numInputBuses > 0 ? &inputBuses[0].buffers : nullptr;
    data.outputs = numOutputBuses > 0 ? &outputBuses[0].buffers : nullptr;
    data.inputParameterChanges = &noChanges;
    data.outputParameterChanges = nullptr;
    data.inputEvents = nullptr;
    data.outputEvents = nullptr;
    data.processContext = nullptr;

    if (processor->process (data) != kResultOk)
    {
        fail (ExitCode::kStep, "the processor failed while the parameters were measured");
        return false;
    }

    for (const auto& bus : outputBuses)
        unwrittenOutputSamples += bus.sentinelCount (useDoublePrecision);

    if (! outputBuses.empty())
        output = firstChannel (outputBuses[0], useDoublePrecision);

    return true;
}

/** A value near `current` that is clearly different from it.

    A stepped parameter is moved one step, a continuous one a long way
    from where it is. */
static double neighbourValue (double current, int32 stepCount)
{
    double target = current;

    if (stepCount > 0)
    {
        const int32 steps = stepCount;
        const int32 currentStep = static_cast<int32> (std::lround (current * steps));
        target = static_cast<double> ((currentStep + 1) % (steps + 1)) / static_cast<double> (steps);
    }
    else
    {
        target = current < 0.5 ? 0.8 : 0.2;
    }

    if (std::fabs (target - current) < 1.0e-9)
        target = current < 0.5 ? 0.6 : 0.4;

    return target;
}

/** A value for a parameter that is clearly different from the one it starts
    out with.

    The value is measured against the parameter's *default*, because that
    is the value a fresh instance of the plug-in starts with, and the
    witness compares a fresh instance that was told a value with a fresh
    instance that was not. */
double Host::differentValue (const ParameterRecord& parameter) const
{
    return neighbourValue (parameter.defaultNormalized, parameter.stepCount);
}

bool Host::measureParameterEffects()
{
    const size_t signalLength = static_cast<size_t> (blockSize) * 8;
    const std::vector<float> signal =
        auralis::makeMixed (Setup::kToneHz, Setup::kToneAmplitude, signalLength, sampleRate);

    json::Value effects = json::Value::makeArray();
    json::Value silent = json::Value::makeArray();
    int32 audibleCount = 0;
    int32 processingCount = 0;

    double bestEffect = -1.0;
    ParamID bestId = 0;
    std::string bestTitle;
    bool haveBest = false;

    // The probe is the parameter the host can hear the most clearly, but
    // it has to be a parameter a host moves in the usual way: the bypass
    // parameter and a program change are not values a host automates, and
    // a plug-in may well ignore them when the change arrives with a
    // block.  The best ordinary parameter wins; the best of all is only
    // used when there is no ordinary one.
    double bestOrdinaryEffect = -1.0;
    ParamID bestOrdinaryId = 0;
    std::string bestOrdinaryTitle;
    bool haveBestOrdinary = false;

    for (const auto& parameter : parameters)
    {
        if (parameter.readOnly || parameter.hidden)
            continue;

        const double original = controller->getParamNormalized (parameter.id);
        const double target = neighbourValue (original, parameter.stepCount);

        std::vector<float> first;
        std::vector<float> second;
        std::vector<float> third;

        // The very same signal goes in all three times, so the only thing
        // that differs between the three blocks is the parameter.
        controller->setParamNormalized (parameter.id, original);
        if (! processBlockWith (signal, 0, first))
            return false;

        controller->setParamNormalized (parameter.id, target);
        if (! processBlockWith (signal, 0, second))
            return false;

        controller->setParamNormalized (parameter.id, original);
        if (! processBlockWith (signal, 0, third))
            return false;

        const double drift = auralis::maxDifference (first, third);
        const double effect = std::min (auralis::maxDifference (first, second),
                                        auralis::maxDifference (second, third));
        const double peak = std::max (auralis::peakOf (first), auralis::peakOf (second));
        const double threshold = std::max (1.0e-4 * peak, 1.0e-9);
        const bool audible = effect > std::max (2.0 * drift, threshold);

        // Does the plug-in do anything with the audio at all?  A plug-in
        // that hands the input straight back has not processed anything,
        // whatever its parameters say.
        const std::vector<float> inputBlock (signal.begin(), signal.begin() + static_cast<long> (blockSize));
        const bool processes = auralis::maxDifference (first, inputBlock) > 0.0
                               || auralis::maxDifference (second, inputBlock) > 0.0
                               || auralis::maxDifference (third, inputBlock) > 0.0;
        if (processes)
            ++processingCount;

        json::Value record = json::Value::makeObject();
        record.set ("id", json::Value (parameter.id));
        record.set ("title", json::Value (parameter.title));
        record.set ("value", json::Value (target));
        record.set ("effect", json::Value (effect));
        record.set ("drift", json::Value (drift));
        record.set ("audible", json::Value (audible));
        effects.push (record);

        if (audible)
        {
            ++audibleCount;
            if (effect - drift > bestEffect)
            {
                bestEffect = effect - drift;
                bestId = parameter.id;
                bestTitle = parameter.title;
                haveBest = true;
            }

            const bool ordinary = parameter.automatable && ! parameter.readOnly && ! parameter.hidden
                                  && ! parameter.bypass && ! parameter.programChange;
            if (ordinary && effect - drift > bestOrdinaryEffect)
            {
                bestOrdinaryEffect = effect - drift;
                bestOrdinaryId = parameter.id;
                bestOrdinaryTitle = parameter.title;
                haveBestOrdinary = true;
            }
        }
        else
        {
            silent.push (json::Value (parameter.id));
        }

        // Put the parameter back where it was.
        controller->setParamNormalized (parameter.id, original);
    }

    livenessRecord.set ("audible_parameters", json::Value (audibleCount));
    livenessRecord.set ("processing_parameters", json::Value (processingCount));
    livenessRecord.set ("silent_parameters", silent);
    livenessRecord.set ("parameter_effects", effects);

    // The probe is the parameter the host can hear the most clearly: the
    // audio can only confirm the automation for a parameter that changes
    // the sound.  A parameter a host does not automate in the usual way
    // is only used when there is no other one.
    if ((haveBestOrdinary || haveBest) && ! haveForcedParam)
    {
        const ParamID chosen = haveBestOrdinary ? bestOrdinaryId : bestId;
        const std::string& chosenTitle = haveBestOrdinary ? bestOrdinaryTitle : bestTitle;
        probeId = chosen;
        probeTitle = chosenTitle;
        for (const auto& parameter : parameters)
        {
            if (parameter.id == chosen)
            {
                probeValue = differentValue (parameter);
                break;
            }
        }
    }

    note ("parameter audio : " + std::to_string (audibleCount) + " of "
              + std::to_string (effects.getMemberCount()) + " parameter(s) change the sound, "
              + std::to_string (processingCount) + " of them change the audio at all"
              + (haveBest ? (", the probe is \"" + probeTitle + "\"") : std::string()));

    if (processingCount == 0 && effects.getMemberCount() > 0)
    {
        // The plug-in hands its input straight back, whatever its
        // parameters say.  A plug-in that cannot find its core says so in
        // its log and passes the audio through, and a host has no way to
        // tell that from the outside.  Whether it is a defect is decided
        // at the end of the exercise, when the plug-in's own words are
        // known.
        doesNotProcess = true;

        json::Value finding = json::Value::makeObject();
        finding.set ("kind", json::Value ("audio_untouched"));
        finding.set ("text", json::Value ("the plug-in hands the audio straight back: its output is "
                                          "the input, so it is not processing anything"));
        findings.push (finding);
    }

    if (! haveBest)
    {
        // No parameter of the plug-in changes the sound on the signal the
        // host uses, so the audio cannot confirm the automation; the state
        // does.
        json::Value finding = json::Value::makeObject();
        finding.set ("kind", json::Value ("no_audible_parameter"));
        finding.set ("text", json::Value ("no parameter of the plug-in changes the sound on the "
                                          "signal the host uses, so the audio cannot confirm the "
                                          "automation; the state does"));
        findings.push (finding);
    }

    return true;
}

bool Host::exerciseState()
{
    // -- the two states ----------------------------------------------------
    //
    // The component's state is the plug-in's audio state, the controller's
    // state is the plug-in's parameter state.  In the combined shape they
    // are one and the same, and the host can say so.

    std::vector<uint8_t> componentState;
    std::vector<uint8_t> controllerState;

    const bool componentHasState = readState (component, componentState);
    const bool controllerHasState = readState (controller, controllerState);

    if (! componentHasState)
    {
        fail (ExitCode::kStep, "getState() on the component produced nothing");
        return false;
    }

    const bool statesIdentical = (componentState == controllerState);
    const bool combined = isCombined();

    // -- the state survives a save and restore -----------------------------
    //
    // The probe parameter is moved away from the value the state carries.
    // Both states have to move with it: the controller's state carries the
    // parameter, and the component's state has to carry it too, or the
    // audio side would keep working with the old value.
    //
    // Then both states are given back and the parameter has to be where the
    // state said it was.

    bool controllerStateMoved = false;
    bool componentStateMoved = false;
    bool restoredThroughController = false;
    bool restoredThroughComponent = false;
    bool componentRoundTrip = true;
    ParamValue savedValue = 0.0;

    if (haveProbe)
    {
        savedValue = controller->getParamNormalized (probeId);

        double moved = savedValue;
        if (moved < 0.5)
            moved = 0.9;
        else
            moved = 0.1;
        controller->setParamNormalized (probeId, moved);

        std::vector<uint8_t> after;
        if (readState (controller, after))
            controllerStateMoved = (after != controllerState);
        if (readState (component, after))
            componentStateMoved = (after != componentState);

        // -- the controller's state comes back ------------------------------
        if (controllerHasState)
        {
            const bool accepted = writeState (controller, controllerState);
            if (! accepted)
            {
                fail (ExitCode::kStep, "the controller refused its own state back");
                return false;
            }

            const ParamValue afterRestore = controller->getParamNormalized (probeId);
            restoredThroughController = std::fabs (afterRestore - savedValue) <= 1.0e-6;
        }

        // -- the component's state comes back -------------------------------
        {
            const bool accepted = writeState (component, componentState);
            if (! accepted)
            {
                fail (ExitCode::kStep, "the component refused its own state back");
                return false;
            }

            std::vector<uint8_t> restored;
            if (readState (component, restored))
                componentRoundTrip = (restored == componentState);

            // The host hands the component's state to the controller, the
            // way a host does when it opens a saved session.  The
            // controller takes the parameter values from it.
            MemoryStream stream (const_cast<uint8_t*> (componentState.data()),
                                 static_cast<int32> (componentState.size()));
            controller->setComponentState (&stream);

            const ParamValue afterRestore = controller->getParamNormalized (probeId);
            restoredThroughComponent = std::fabs (afterRestore - savedValue) <= 1.0e-6;
        }
    }

    // The controller's own state has to survive as well.
    bool controllerRestored = true;
    std::string controllerNote = "not implemented";

    if (! controllerHasState)
    {
        controllerNote = "empty";
    }
    else
    {
        controllerRestored = writeState (controller, controllerState);
        controllerNote = controllerRestored ? "restored" : "restore failed";
    }

    stateInfo.set ("component_bytes", json::Value (static_cast<uint32_t> (componentState.size())));
    stateInfo.set ("component_restored", json::Value (true));
    stateInfo.set ("controller_bytes", json::Value (static_cast<uint32_t> (controllerState.size())));
    stateInfo.set ("controller_restored", json::Value (controllerRestored));
    stateInfo.set ("controller_state", json::Value (controllerNote));
    stateInfo.set ("component_and_controller_identical", json::Value (statesIdentical));
    stateInfo.set ("combined_object", json::Value (combined));
    stateInfo.set ("controller_state_moved_with_the_parameter", json::Value (controllerStateMoved));
    stateInfo.set ("component_state_moved_with_the_parameter", json::Value (componentStateMoved));
    stateInfo.set ("parameter_restored_through_the_controller", json::Value (restoredThroughController));
    stateInfo.set ("parameter_restored_through_the_component", json::Value (restoredThroughComponent));
    stateInfo.set ("round_trip_identical", json::Value (componentRoundTrip));

    note ("state           : component " + std::to_string (componentState.size()) + " bytes, controller "
              + std::to_string (controllerState.size()) + " bytes ("
              + (statesIdentical ? "the same state" : "two different states") + "), "
              + (controllerStateMoved ? "the controller's state moved" : "THE CONTROLLER'S STATE DID NOT MOVE")
              + ", "
              + (componentStateMoved ? "the component's state moved" : "THE COMPONENT'S STATE DID NOT MOVE")
              + ", "
              + (restoredThroughController ? "the parameters came back" : "THE PARAMETERS DID NOT COME BACK"));

    if (! controllerRestored)
    {
        fail (ExitCode::kStep, "the controller refused its own state back");
        return false;
    }

    if (! controllerStateMoved)
    {
        fail (ExitCode::kStep,
              "the state of the controller does not carry the parameters: moving a parameter did not "
                  "change the controller's state");
        return false;
    }

    if (! componentStateMoved)
    {
        fail (ExitCode::kStep,
              "the parameters of the controller are not the ones the component uses: moving a parameter "
                  "did not change the component's state");
        return false;
    }

    if (! restoredThroughController)
    {
        fail (ExitCode::kStep,
              "the state of the controller does not carry the parameters: after the state was given back "
                  "the probe parameter was not where the state said it was");
        return false;
    }

    if (! restoredThroughComponent)
    {
        fail (ExitCode::kStep,
              "the state of the component and the state of the controller do not agree: the component's "
                  "state does not carry the parameter values the controller has");
        return false;
    }

    if (combined && ! statesIdentical)
    {
        fail (ExitCode::kStep,
              "the controller and the component are one object, but their states differ: the two do not agree");
        return false;
    }

    return true;
}

// ---------------------------------------------------------------------------
//  the latency and the tail
// ---------------------------------------------------------------------------
//
//  A host has to know how late the plug-in's output is, or the plug-in
//  sits behind the rest of the session, and it has to know how long the
//  plug-in keeps ringing, or the host cuts the tail off and clicks.
//
//  The latency is measured with a sweep: the sweep goes in, the processed
//  sweep comes out, and the cross correlation says where the output sits
//  against the input.  The tail is measured by letting the tone stop and
//  watching how long the output keeps going.
//
//  The range the correlation is searched over is the largest delay the
//  modules of the catalogue declare, plus a margin, and never less than the
//  delay the plug-in reports itself.  A search that is too narrow does not
//  report a small delay: it reports the edge of the search and a
//  correlation of nothing, and the host cannot tell a plug-in that delays
//  the signal by 4848 samples from one that does not delay it at all.

/** The correlation of two signals at one lag, normalised over the samples
    the two of them have in common.

    The peak that the cross correlation reports is normalised over the whole
    reference and over the part of the signal that is left at that lag, so
    it falls off as the lag grows: at the edge of a long search it says
    almost nothing even when the two signals are the same signal.  The
    number a reader wants is the one over the samples that are really
    compared. */
static double normalisedCorrelation (const std::vector<float>& reference,
                                     const std::vector<float>& signal,
                                     int64_t lag)
{
    if (lag < 0 || static_cast<size_t> (lag) >= signal.size())
        return 0.0;

    const size_t count = std::min (reference.size(), signal.size() - static_cast<size_t> (lag));
    if (count == 0)
        return 0.0;

    double sum = 0.0;
    double referenceEnergy = 0.0;
    double signalEnergy = 0.0;

    for (size_t n = 0; n < count; ++n)
    {
        const double a = static_cast<double> (reference[n]);
        const double b = static_cast<double> (signal[n + static_cast<size_t> (lag)]);
        sum += a * b;
        referenceEnergy += a * a;
        signalEnergy += b * b;
    }

    const double denominator = std::sqrt (referenceEnergy * signalEnergy);
    return denominator > 0.0 ? sum / denominator : 0.0;
}

bool Host::measureLatencyAndTail()
{
    // -- what the plug-in says ---------------------------------------------
    //
    // The latency is only meaningful once the plug-in has been set up: the
    // engine, and with it the latency, does not exist before that.
    const uint32 reportedLatency = processor->getLatencySamples();
    const uint32 reportedAgain = processor->getLatencySamples();
    const bool latencyStable = (reportedLatency == reportedAgain);
    const uint32 reportedTail = processor->getTailSamples();

    // -- the range the delay is searched in --------------------------------
    //
    // The bound is the largest delay the catalogue of the product declares,
    // plus the margin, and it is never smaller than the delay the plug-in
    // reports: a plug-in that delays the signal by more than it says is
    // measured inside the search as well.
    const Catalogue::Entry* catalogueEntry =
        Catalogue::findByClassId (catalogue, selectedClassId);

    const int64_t catalogueMax = catalogue.maxDeclaredLatency;
    const int64_t reportedDelay = static_cast<int64_t> (reportedLatency);

    int64_t searchBound = std::max (catalogueMax, reportedDelay) + Setup::kLatencySearchMargin;
    if (searchBoundOverride > 0)
        searchBound = searchBoundOverride;

    // What the catalogue says about the module under test, and how far the
    // measured delay may be from it.
    const int64_t catalogueDeclared =
        catalogueEntry != nullptr ? catalogueEntry->declaredLatency : reportedDelay;
    const int64_t latencyTolerance =
        catalogueEntry != nullptr ? catalogueEntry->latencyTolerance
                                  : Catalogue::toleranceFor (reportedDelay);

    // -- the delay the plug-in really adds ---------------------------------
    //
    // A sweep goes in.  The first half is the warm up: the core buffers up
    // to 4096 samples, and the delay that a buffer causes at the start is
    // not a delay of the steady state.  The second half is measured.
    //
    // The measured half has to hold the whole search and enough of the
    // signal to correlate: a lag at the edge of the search is only worth
    // looking at when a thousand samples or so are left to compare.
    const int64_t minimumOverlap = 1024;
    const int64_t wantedHalf = searchBound + minimumOverlap;

    int32 sweepBlocks = 64;
    if (wantedHalf * 2 > static_cast<int64_t> (sweepBlocks) * blockSize)
    {
        sweepBlocks = static_cast<int32_t> ((wantedHalf * 2 + blockSize - 1) / blockSize);
    }

    const size_t sweepTotal = static_cast<size_t> (sweepBlocks) * static_cast<size_t> (blockSize);
    const size_t sweepHalf = sweepTotal / 2;

    const std::vector<float> sweep =
        auralis::makeSweep (200.0, 18000.0, Setup::kToneAmplitude, sweepTotal, sampleRate);

    std::vector<float> swept;
    swept.reserve (sweepTotal);

    HostParameterChanges noChanges;

    for (int32 block = 0; block < sweepBlocks; ++block)
    {
        for (auto& bus : inputBuses)
            bus.clear();
        for (auto& bus : outputBuses)
            bus.fillSentinel();

        const size_t offset = static_cast<size_t> (block) * static_cast<size_t> (blockSize);
        for (auto& bus : inputBuses)
        {
            for (size_t c = 0; c < bus.floatStorage.size(); ++c)
                for (int32 n = 0; n < blockSize; ++n)
                {
                    const float value = sweep[offset + static_cast<size_t> (n)];
                    bus.floatStorage[c][static_cast<size_t> (n)] = value;
                    bus.doubleStorage[c][static_cast<size_t> (n)] = value;
                }
        }

        ProcessData data;
        data.processMode = kRealtime;
        data.symbolicSampleSize = useDoublePrecision ? kSample64 : kSample32;
        data.numSamples = blockSize;
        data.numInputs = numInputBuses;
        data.numOutputs = numOutputBuses;
        data.inputs = numInputBuses > 0 ? &inputBuses[0].buffers : nullptr;
        data.outputs = numOutputBuses > 0 ? &outputBuses[0].buffers : nullptr;
        data.inputParameterChanges = &noChanges;
        data.outputParameterChanges = nullptr;
        data.inputEvents = nullptr;
        data.outputEvents = nullptr;
        data.processContext = nullptr;

        if (processor->process (data) != kResultOk)
        {
            fail (ExitCode::kStep, "the processor failed while the delay was measured");
            return false;
        }

        for (const auto& bus : outputBuses)
            unwrittenOutputSamples += bus.sentinelCount (useDoublePrecision);

        if (! outputBuses.empty())
        {
            const std::vector<float> channel = firstChannel (outputBuses[0], useDoublePrecision);
            swept.insert (swept.end(), channel.begin(), channel.end());
        }
    }

    int64_t measuredDelay = 0;
    double correlation = 0.0;
    double correlationPeak = 0.0;
    int64_t correlationMilliseconds = 0;
    if (swept.size() >= sweepHalf + blockSize)
    {
        const std::vector<float> reference (sweep.begin() + static_cast<long> (sweepHalf), sweep.end());
        const std::vector<float> answer (swept.begin() + static_cast<long> (sweepHalf), swept.end());

        // The search is timed: the wider the range, the longer the walk
        // over the lags, and the report says what it cost.
        const auto searchStarted = std::chrono::steady_clock::now();
        measuredDelay = auralis::correlationLag (reference, answer, searchBound, &correlationPeak);
        const auto searchFinished = std::chrono::steady_clock::now();
        correlationMilliseconds =
            std::chrono::duration_cast<std::chrono::milliseconds> (searchFinished - searchStarted).count();

        // The correlation over the samples the two signals have in common.
        correlation = normalisedCorrelation (reference, answer, measuredDelay);
    }

    // -- the onset the plug-in really has ----------------------------------
    //
    // An impulse goes in and the host watches where the output starts.  A
    // plug-in that buffers its input holds the output back, and the host
    // has to know how long.

    std::vector<float> impulse;
    {
        const std::vector<float> click =
            auralis::makeImpulse (Setup::kToneAmplitude, static_cast<size_t> (blockSize));

        // The impulse goes in after a short warm up, so that the plug-in
        // is in its steady state when the click arrives.
        std::vector<float> warmup;
        if (! processBlockWith (auralis::makeTone (Setup::kToneHz, Setup::kToneAmplitude,
                                                   static_cast<size_t> (blockSize), sampleRate),
                                0, warmup))
            return false;

        for (auto& bus : inputBuses)
            bus.clear();
        for (auto& bus : outputBuses)
            bus.fillSentinel();

        for (auto& bus : inputBuses)
        {
            for (size_t c = 0; c < bus.floatStorage.size(); ++c)
                for (int32 n = 0; n < blockSize; ++n)
                {
                    const float value = click[static_cast<size_t> (n)];
                    bus.floatStorage[c][static_cast<size_t> (n)] = value;
                    bus.doubleStorage[c][static_cast<size_t> (n)] = value;
                }
        }

        HostParameterChanges noChanges;

        ProcessData data;
        data.processMode = kRealtime;
        data.symbolicSampleSize = useDoublePrecision ? kSample64 : kSample32;
        data.numSamples = blockSize;
        data.numInputs = numInputBuses;
        data.numOutputs = numOutputBuses;
        data.inputs = numInputBuses > 0 ? &inputBuses[0].buffers : nullptr;
        data.outputs = numOutputBuses > 0 ? &outputBuses[0].buffers : nullptr;
        data.inputParameterChanges = &noChanges;
        data.outputParameterChanges = nullptr;
        data.inputEvents = nullptr;
        data.outputEvents = nullptr;
        data.processContext = nullptr;

        if (processor->process (data) != kResultOk)
        {
            fail (ExitCode::kStep, "the processor failed while the onset was measured");
            return false;
        }

        for (const auto& bus : outputBuses)
            unwrittenOutputSamples += bus.sentinelCount (useDoublePrecision);

        if (! outputBuses.empty())
            impulse = firstChannel (outputBuses[0], useDoublePrecision);
    }

    const int64_t onset = auralis::onsetOf (impulse, Tolerance::kSilence);

    // -- the tail the plug-in really rings for ------------------------------
    //
    // A tone goes in, then silence.  The tail is the time the output keeps
    // going after the input stopped.

    const int32 toneBlocks = 32;
    const int32 silenceBlocks = 64;
    std::vector<float> tail;
    tail.reserve (static_cast<size_t> (toneBlocks + silenceBlocks) * static_cast<size_t> (blockSize));

    const std::vector<float> toneSignal = auralis::makeTone (
        Setup::kToneHz, Setup::kToneAmplitude,
        static_cast<size_t> (toneBlocks) * static_cast<size_t> (blockSize), sampleRate);

    for (int32 block = 0; block < toneBlocks + silenceBlocks; ++block)
    {
        for (auto& bus : inputBuses)
            bus.clear();
        for (auto& bus : outputBuses)
            bus.fillSentinel();

        if (block < toneBlocks)
        {
            const size_t offset = static_cast<size_t> (block) * static_cast<size_t> (blockSize);
            for (auto& bus : inputBuses)
            {
                for (size_t c = 0; c < bus.floatStorage.size(); ++c)
                    for (int32 n = 0; n < blockSize; ++n)
                    {
                        const float value = toneSignal[offset + static_cast<size_t> (n)];
                        bus.floatStorage[c][static_cast<size_t> (n)] = value;
                        bus.doubleStorage[c][static_cast<size_t> (n)] = value;
                    }
            }
        }

        ProcessData data;
        data.processMode = kRealtime;
        data.symbolicSampleSize = useDoublePrecision ? kSample64 : kSample32;
        data.numSamples = blockSize;
        data.numInputs = numInputBuses;
        data.numOutputs = numOutputBuses;
        data.inputs = numInputBuses > 0 ? &inputBuses[0].buffers : nullptr;
        data.outputs = numOutputBuses > 0 ? &outputBuses[0].buffers : nullptr;
        data.inputParameterChanges = &noChanges;
        data.outputParameterChanges = nullptr;
        data.inputEvents = nullptr;
        data.outputEvents = nullptr;
        data.processContext = nullptr;

        if (processor->process (data) != kResultOk)
        {
            fail (ExitCode::kStep, "the processor failed while the tail was measured");
            return false;
        }

        for (const auto& bus : outputBuses)
            unwrittenOutputSamples += bus.sentinelCount (useDoublePrecision);

        if (! outputBuses.empty())
        {
            const std::vector<float> channel = firstChannel (outputBuses[0], useDoublePrecision);
            tail.insert (tail.end(), channel.begin(), channel.end());
        }
    }

    const size_t toneSamples = static_cast<size_t> (toneBlocks) * static_cast<size_t> (blockSize);
    const int64_t measuredTail = auralis::tailAfter (tail, toneSamples, Tolerance::kSilence);

    // A plug-in that delays the signal keeps the signal going for the
    // length of the delay after the input stopped, so the delay is part
    // of what the host has to wait for.  The host waits for the latency
    // and the tail the plug-in reports.
    const int64_t expected = static_cast<int64_t> (reportedLatency)
                             + static_cast<int64_t> (reportedTail);
    const int64_t tailExcess = measuredTail - expected;
    const int64_t latencyExcess = measuredDelay - static_cast<int64_t> (reportedLatency);

    // Is the ring-out a decay, or does the plug-in keep making sound?  A
    // decay falls; a sound the plug-in makes on its own does not, and a
    // host cannot wait for that.  The correlation says whether the
    // ring-out is the input coming back; the level says whether it fades.
    double tailCorrelation = 0.0;
    double tailPeak = 0.0;
    double tailFirst = 0.0;
    double tailLast = 0.0;
    {
        const size_t window = std::min<size_t> (1024, toneSamples);
        if (tail.size() > toneSamples + window && toneSamples >= window)
        {
            const std::vector<float> reference (tail.begin() + static_cast<long> (toneSamples - window),
                                               tail.begin() + static_cast<long> (toneSamples));
            const std::vector<float> answer (tail.begin() + static_cast<long> (toneSamples),
                                             tail.begin() + static_cast<long> (toneSamples + window));
            auralis::correlationLag (reference, answer, 64, &tailCorrelation);
        }

        // The level of the ring-out, so a reader can see how loud it is.
        // The part the host waits for anyway (the delay the plug-in
        // reports) is not part of the ring-out.
        const size_t afterDelay = toneSamples + static_cast<size_t> (reportedLatency);
        for (size_t i = afterDelay; i < tail.size(); ++i)
            tailPeak = std::max (tailPeak, std::fabs (static_cast<double> (tail[i])));

        // The level at the start of the ring-out and at its end.
        const size_t ringStart = afterDelay;
        const size_t ringEnd = tail.size();
        if (ringEnd > ringStart + 8)
        {
            const size_t quarter = (ringEnd - ringStart) / 4;
            for (size_t i = ringStart; i < ringStart + quarter && i < ringEnd; ++i)
                tailFirst = std::max (tailFirst, std::fabs (static_cast<double> (tail[i])));
            for (size_t i = ringEnd - quarter; i < ringEnd; ++i)
                tailLast = std::max (tailLast, std::fabs (static_cast<double> (tail[i])));
        }
    }

    // A ring-out that falls by more than 6 dB is a decay: the plug-in's
    // own memory of the input.  One that does not fall is a sound the
    // plug-in keeps making.
    const double ringFallDb = (tailFirst > 0.0 && tailLast > 0.0)
                                  ? 20.0 * std::log10 (tailLast / tailFirst)
                                  : 0.0;
    const bool tailIsADecay = (ringFallDb < -6.0);

    // The level of the ring-out in dB, against the level of the tone that
    // went in.
    const double tailLevelDb =
        tailPeak > 0.0 ? 20.0 * std::log10 (tailPeak / Setup::kToneAmplitude) : -200.0;

    latencyRecord.set ("reported_samples", json::Value (static_cast<uint32_t> (reportedLatency)));
    latencyRecord.set ("reported_stable", json::Value (latencyStable));
    latencyRecord.set ("measured_delay_samples", json::Value (measuredDelay));
    latencyRecord.set ("delay_excess_samples", json::Value (latencyExcess));
    latencyRecord.set ("correlation", json::Value (correlation));
    latencyRecord.set ("correlation_peak", json::Value (correlationPeak));
    latencyRecord.set ("onset_samples", json::Value (onset));

    // The range the delay was searched in, and where it came from.  A
    // reader has to be able to see that the search was wide enough for
    // every delay the product can declare, and how much the wider search
    // cost.
    latencyRecord.set ("search_bound_samples", json::Value (searchBound));
    latencyRecord.set ("search_margin_samples", json::Value (Setup::kLatencySearchMargin));
    latencyRecord.set ("search_ms", json::Value (correlationMilliseconds));
    latencyRecord.set ("catalogue_loaded", json::Value (catalogue.loaded));
    latencyRecord.set ("catalogue_path", json::Value (catalogue.path));
    latencyRecord.set ("catalogue_max_latency_samples", json::Value (catalogueMax));
    latencyRecord.set ("catalogue_module", json::Value (catalogueEntry != nullptr ? catalogueEntry->code : std::string()));
    latencyRecord.set ("catalogue_declared_samples", json::Value (catalogueDeclared));
    latencyRecord.set ("catalogue_delay_error_samples", json::Value (measuredDelay - catalogueDeclared));
    latencyRecord.set ("latency_tolerance_samples", json::Value (latencyTolerance));

    latencyRecord.set ("tail_reported_samples", json::Value (static_cast<uint32_t> (reportedTail)));
    latencyRecord.set ("tail_measured_samples", json::Value (measuredTail));
    latencyRecord.set ("tail_expected_samples", json::Value (expected));
    latencyRecord.set ("tail_excess_samples", json::Value (tailExcess));
    latencyRecord.set ("tail_correlation", json::Value (tailCorrelation));
    latencyRecord.set ("tail_is_a_decay", json::Value (tailIsADecay));
    latencyRecord.set ("tail_fall_db", json::Value (ringFallDb));
    latencyRecord.set ("tail_level_db", json::Value (tailLevelDb));
    latencyRecord.set ("tail_threshold", json::Value (Tolerance::kSilence));

    note ("latency         : reported " + std::to_string (reportedLatency) + " samples, measured "
              + std::to_string (measuredDelay) + " samples (correlation " + std::to_string (correlation)
              + "), onset " + std::to_string (onset) + " samples");
    note ("delay search    : " + std::to_string (searchBound) + " samples (the catalogue declares "
              + std::to_string (catalogueMax) + ", the margin is "
              + std::to_string (Setup::kLatencySearchMargin) + "), the search took "
              + std::to_string (correlationMilliseconds) + " ms");
    note ("catalogue       : "
              + (catalogueEntry != nullptr
                     ? (catalogueEntry->code + " declares " + std::to_string (catalogueEntry->declaredLatency)
                        + " samples, the tolerance is " + std::to_string (catalogueEntry->latencyTolerance)
                        + " samples, the measurement is off by "
                        + std::to_string (measuredDelay - catalogueEntry->declaredLatency))
                     : std::string ("the module is not in the catalogue")));
    note ("tail            : reported " + std::to_string (reportedTail) + " samples, measured "
              + std::to_string (measuredTail) + " samples (correlation "
              + std::to_string (tailCorrelation) + ", level " + std::to_string (tailLevelDb) + " dB)");

    if (! latencyStable)
    {
        fail (ExitCode::kStep, "the plug-in reports a different latency every time it is asked");
        return false;
    }

    // The cross correlation only means something when the output still
    // looks like the input.  When it does not, the onset is the witness:
    // a plug-in that holds the output back starts late.
    if (correlation >= Tolerance::kCorrelation)
    {
        if (latencyExcess > Tolerance::kLatency)
        {
            fail (ExitCode::kStep,
                  "the plug-in delays the audio by " + std::to_string (measuredDelay)
                      + " samples but reports a latency of " + std::to_string (reportedLatency)
                      + ": the host cannot compensate for a delay it was not told about");
            return false;
        }
    }
    else if (onset > static_cast<int64_t> (reportedLatency) + Tolerance::kLatency)
    {
        fail (ExitCode::kStep,
              "the plug-in holds its output back for " + std::to_string (onset)
                  + " samples but reports a latency of " + std::to_string (reportedLatency)
                  + ": the host cannot compensate for a delay it was not told about");
        return false;
    }
    else
    {
        // The output does not look like the input any more, so the cross
        // correlation says nothing about the delay.  The onset was used
        // instead, and the reader is told that the delay was not measured.
        json::Value finding = json::Value::makeObject();
        finding.set ("kind", json::Value ("latency_not_measured"));
        finding.set ("text", json::Value ("the delay could not be measured: the output does not "
                                          "look like the input (the correlation is "
                                          + std::to_string (correlation) + ")"));
        finding.set ("reported_samples", json::Value (static_cast<uint32_t> (reportedLatency)));
        finding.set ("onset_samples", json::Value (onset));
        findings.push (finding);
    }

    // -- the delay against the catalogue ------------------------------------
    //
    // The catalogue says how long the module of the product declares its
    // signal to be delayed, and how far the measurement may be from that.
    // The host does not fail on it: the gate is what holds the product to
    // its own declaration.  The host says what it measured, and the gate
    // reads it.
    if (catalogueEntry != nullptr && correlation >= Tolerance::kCorrelation)
    {
        const int64_t error = measuredDelay - catalogueEntry->declaredLatency;
        if (std::llabs (error) > latencyTolerance)
        {
            const std::string text =
                "the module delays the signal by " + std::to_string (measuredDelay)
                + " samples but the catalogue declares " + std::to_string (catalogueEntry->declaredLatency)
                + " samples (the difference is " + std::to_string (error)
                + " samples, the tolerance is " + std::to_string (latencyTolerance) + ")";

            json::Value finding = json::Value::makeObject();
            finding.set ("kind", json::Value ("latency_disagrees_with_catalogue"));
            finding.set ("text", json::Value (text));
            finding.set ("measured_samples", json::Value (measuredDelay));
            finding.set ("declared_samples", json::Value (catalogueEntry->declaredLatency));
            finding.set ("error_samples", json::Value (error));
            finding.set ("tolerance_samples", json::Value (latencyTolerance));
            findings.push (finding);

            note ("latency finding : " + text);
        }
    }

    if (tailExcess > 0)
    {
        const std::string text =
            "the plug-in rings for " + std::to_string (measuredTail)
            + " samples after its input stopped but reports a latency of "
            + std::to_string (reportedLatency) + " and a tail of "
            + std::to_string (reportedTail) + " (the ring-out is at "
            + std::to_string (tailLevelDb) + " dB)";

        json::Value finding = json::Value::makeObject();
        finding.set ("kind", json::Value ("tail_not_reported"));
        finding.set ("text", json::Value (text));
        finding.set ("measured_samples", json::Value (measuredTail));
        finding.set ("expected_samples", json::Value (expected));
        finding.set ("excess_samples", json::Value (tailExcess));
        finding.set ("level_db", json::Value (tailLevelDb));
        finding.set ("is_a_decay", json::Value (tailIsADecay));
        findings.push (finding);

        if (tailExcess > Tolerance::kTail)
        {
            if (tailIsADecay)
            {
                fail (ExitCode::kStep,
                      text + ": the host cuts the tail off and the result clicks");
                return false;
            }

            // The plug-in makes a sound of its own, so the output does not
            // stop when the input does.  That is not a tail a host can wait
            // for, but the plug-in should still say so.
            note ("tail finding    : " + text
                      + ": a host that stops the stream cuts that sound off");
        }
        else
        {
            note ("tail note       : " + text);
        }
    }

    return true;
}

// ---------------------------------------------------------------------------
//  the surface the host sees
// ---------------------------------------------------------------------------
//
//  Three things a host can do with a plug-in that the exercise above does
//  not cover:
//
//    * read the help text of a parameter,
//    * load one of the presets of the program list,
//    * see the choices of a parameter that is drawn as a list.
//
//  The help is published through an interface of the plug-in's own, because
//  the format has no field for it.  The program list is published through
//  the unit information interface, and the choices through the flags of the
//  parameter info.

bool Host::inspectSurface()
{
    json::Value record = json::Value::makeObject();

    // -- the help text of the parameters ------------------------------------
    FUnknownPtr<IParameterHelp> help (controller);

    int32 helpCount = 0;
    int32 shortestLength = -1;
    std::string shortestText;
    json::Value helpTexts = json::Value::makeArray();

    if (help != nullptr)
    {
        helpCount = help->getParameterHelpCount ();

        const int32 count = controller->getParameterCount ();

        for (int32 i = 0; i < count; ++i)
        {
            ParameterInfo info;
            std::memset (&info, 0, sizeof (info));
            if (controller->getParameterInfo (i, info) != kResultOk)
                continue;

            String128 text;
            std::memset (text, 0, sizeof (text));
            if (help->getParameterHelp (info.id, text) != kResultOk)
                continue;

            const std::string sentence = fromString128 (text);

            if (sentence.empty())
                continue;

            helpTexts.push (json::Value (sentence));

            if (shortestLength < 0 || static_cast<int32> (sentence.size ()) < shortestLength)
            {
                shortestLength = static_cast<int32> (sentence.size ());
                shortestText = sentence;
            }
        }
    }

    record.set ("help_interface", json::Value (help != nullptr));
    record.set ("help_count", json::Value (helpCount));
    record.set ("help_shortest_length", json::Value (shortestLength));
    record.set ("help_shortest", json::Value (shortestText));
    record.set ("help_texts", helpTexts);

    note ("help            : " + std::to_string (helpCount) + " parameter(s) carry a sentence, "
              + "the shortest is " + std::to_string (shortestLength) + " character(s)");

    // -- the program list ---------------------------------------------------
    //
    // The program change parameter is what a host drives to load a preset.
    // Every one of its steps is tried, and the host watches the other
    // parameters: a preset that changes nothing is a preset that was not
    // loaded.

    const ParameterRecord* programParameter = nullptr;

    for (const auto& parameter : parameters)
    {
        if (! parameter.programChange)
            continue;

        programParameter = &parameter;
        break;
    }

    int32 entriesThatChange = 0;
    int32 parametersThatChange = 0;

    if (programParameter != nullptr)
    {
        std::map<ParamID, ParamValue> before;

        for (const auto& parameter : parameters)
            before[parameter.id] = controller->getParamNormalized (parameter.id);

        const int32 steps = programParameter->stepCount;

        for (int32 step = 1; step <= steps; ++step)
        {
            const ParamValue target = steps > 0
                                        ? static_cast<ParamValue> (step) / static_cast<ParamValue> (steps)
                                        : ParamValue (1.0);

            controller->setParamNormalized (programParameter->id, target);

            int32 changed = 0;

            for (const auto& parameter : parameters)
            {
                const ParamValue now = controller->getParamNormalized (parameter.id);

                if (std::fabs (now - before[parameter.id]) > 1.0e-9)
                    ++changed;
            }

            if (changed > 0)
            {
                ++entriesThatChange;

                if (changed > parametersThatChange)
                    parametersThatChange = changed;
            }
        }

        // The program change parameter is put back where it was.
        controller->setParamNormalized (programParameter->id,
                                        before[programParameter->id]);
    }

    record.set ("program_change_parameter", json::Value (programParameter != nullptr));
    record.set ("program_entries_that_change", json::Value (entriesThatChange));
    record.set ("program_parameters_that_change", json::Value (parametersThatChange));

    note ("program list    : " + std::to_string (entriesThatChange)
              + " entr(y/ies) changed the parameters when they were loaded");

    surfaceRecord = record;
    return true;
}

// ---------------------------------------------------------------------------
//  the run
// ---------------------------------------------------------------------------

int Host::run (const std::string& argument, bool tone)
{
    moduleArgument = argument;
    wantTone = tone;

    // The plug-in says things while it is exercised; the host catches them
    // and decides at the end whether the plug-in is broken.  The capture
    // can be switched off (AURALIS_VST3HOST_CAPTURE=0), which is what a
    // developer does when the plug-in dies and the message it wrote is
    // then lost with it.
    const char* capture = std::getenv ("AURALIS_VST3HOST_CAPTURE");
    if (capture == nullptr || std::strcmp (capture, "0") != 0)
        startErrorCapture();

    // A module that has no console of its own writes its log to the debug
    // output of the operating system.  The host takes that place too, so
    // that the report can say what the module said.
    debugCaptureAvailable = debugCapture.start();
if (! debugCaptureAvailable)
    debugCaptureReason = debugCapture.getReason();

    note ("=== Auralis VST3 host ================================================");

    std::string resolveError;
    binaryPath = resolveModuleBinary (fs::path (argument), resolveError);
    if (binaryPath.empty())
    {
        fail (ExitCode::kLoad, resolveError);
        return exitCode;
    }
    note ("binary          : " + binaryPath.string());

    // -- the catalogue of the product --------------------------------------
    //
    // The range the delay is searched in comes from the catalogue: it is the
    // largest delay the modules of the product declare, plus a margin.  The
    // catalogue is read before the exercise starts, so that the report can
    // say where it came from and what it said.
    {
        std::vector<fs::path> roots;
        roots.push_back (binaryPath.parent_path());
        const fs::path own = Catalogue::executableDirectory();
        if (! own.empty())
            roots.push_back (own);

        catalogue = Catalogue::load (Catalogue::locate (roots));

        if (catalogue.loaded)
        {
            note ("catalogue       : " + catalogue.path + ", " + std::to_string (catalogue.entries.size())
                      + " module(s), the largest declared delay is "
                      + std::to_string (catalogue.maxDeclaredLatency) + " samples");
        }
        else
        {
            note ("catalogue       : not read (" + catalogue.problem
                      + "): the delay is searched over the delay the plug-in reports");
        }
    }

    // The layout: the folder the module was found in, and the files that
    // travel with it.  A module may ship a payload next to itself, and the
    // report says what was found.
    {
        const fs::path folder = binaryPath.parent_path();
        layoutRecord.set ("folder", json::Value (folder.string()));

        // The two shapes a bundle can have: the binary sits in a folder
        // that carries the name of the bundle, or it sits in an
        // architecture folder inside the contents of the bundle.
        const fs::path parent = folder.parent_path();
        const bool isBundleLayout = (parent.filename().string() == "Contents");
        layoutRecord.set ("shape", json::Value (isBundleLayout ? "bundle" : "flat"));

        json::Value companions = json::Value::makeArray();
        std::error_code listError;
        std::vector<std::string> names;
        for (const auto& entry : fs::directory_iterator (folder, listError))
        {
            if (! entry.is_regular_file (listError))
                continue;
            if (fs::equivalent (entry.path(), binaryPath, listError))
                continue;
            names.push_back (entry.path().filename().string());
        }
        std::sort (names.begin(), names.end());
        for (const auto& name : names)
            companions.push (json::Value (name));
        layoutRecord.set ("companions", companions);
        layoutRecord.set ("companion_count", json::Value (static_cast<int32> (names.size())));

        note ("layout          : " + std::string (isBundleLayout ? "bundle" : "flat")
                  + ", folder \"" + folder.string() + "\", "
                  + std::to_string (names.size()) + " file(s) travel with it");
    }

    std::string loadError;
    std::error_code bundleError;
    const fs::path bundle = fs::is_directory (fs::path (argument), bundleError) ? fs::path (argument) : binaryPath.parent_path();
    if (! module.open (binaryPath, bundleLibraryDirectories (bundle), loadError))
    {
        fail (ExitCode::kLoad, loadError);
        return exitCode;
    }

    factory = module.getFactory();
    if (factory == nullptr)
    {
        fail (ExitCode::kLoad, "the module returned no plug-in factory");
        return exitCode;
    }

    // Every step is timed, so the report says where the time went.
    //
    // In the report mode the exercise does not stop at the first step that
    // failed: it goes on with the steps that can still run, so that the
    // report holds the whole picture.  A step that failed is still a
    // failure, and the exit code says so.
    auto step = [this] (const std::string& name, bool (Host::*action) (), bool canRun) -> bool {
        if (! canRun)
        {
            note ("step            : " + name + " SKIPPED, the exercise could not get there");
            stepRecords.push_back (std::make_pair (name, std::string ("skipped")));
            return true;
        }

        startStep (name);
        const bool ok = (this->*action) ();
        endStep();
        if (! ok)
        {
            note ("step            : " + name + " FAILED after "
                      + std::to_string (stepTimes.back().second) + " ms");
            stepRecords.push_back (std::make_pair (name, std::string ("failed")));
            if (reportOnly)
                return true;
        }
        else
        {
            stepRecords.push_back (std::make_pair (name, std::string ("ok")));
        }
        return ok;
    };

    // The plug-in's own words are known when the exercise is over, and they
    // decide whether a plug-in that does not touch the audio is broken.
    auto finish = [this] () -> int {
        plugInMessages = stopErrorCapture();
        plugInDebugOutput = debugCapture.stop();

        if (! plugInMessages.empty())
            writeToStderr (plugInMessages);
        if (! plugInDebugOutput.empty())
            writeToStderr ("the module wrote to the debug output:\n" + plugInDebugOutput);

        if (doesNotProcess)
        {
            const std::string complaint = firstComplaint (plugInMessages);
            if (! complaint.empty())
            {
                fail (ExitCode::kStep,
                      "the plug-in does not process the audio and says so: " + complaint);
            }
            else
            {
                const std::string said = firstComplaint (plugInDebugOutput);
                if (! said.empty())
                {
                    fail (ExitCode::kStep,
                          "the plug-in does not process the audio and says so: " + said);
                }
            }
        }

        return exitCode;
    };

    const bool haveFactory = (factory != nullptr);

    if (! step ("classes", &Host::enumerateClasses, haveFactory))
        return finish();
    if (! step ("component", &Host::createComponent, haveFactory))
        return finish();
    if (! step ("controller", &Host::createController, component != nullptr))
        return finish();
    if (! step ("connect", &Host::connectComponents, component != nullptr && controller != nullptr))
        return finish();
    if (! step ("connection", &Host::checkConnection, component != nullptr && controller != nullptr))
        return finish();
    if (! step ("buses", &Host::inspectBuses, component != nullptr))
        return finish();
    if (! step ("formats", &Host::probeFormats, processor != nullptr))
        return finish();
    if (! step ("parameters", &Host::enumerateParameters, controller != nullptr))
        return finish();
    if (! step ("parameter", &Host::exerciseParameter, controller != nullptr && processor != nullptr))
        return finish();
    if (! step ("setup", &Host::setupAudio, component != nullptr && processor != nullptr))
        return finish();
    if (! step ("processing", &Host::processAudio, processor != nullptr && ! inputBuses.empty()))
        return finish();
    if (! step ("liveness", &Host::checkParameterLiveness,
                controller != nullptr && processor != nullptr && ! parameters.empty()))
        return finish();
    if (! step ("effects", &Host::measureParameterEffects,
                controller != nullptr && processor != nullptr && ! parameters.empty()))
        return finish();
    if (! step ("parameter step", &Host::measureParameterStep,
                controller != nullptr && processor != nullptr && ! parameters.empty()))
        return finish();
    if (! step ("agreement", &Host::checkAgreement,
                controller != nullptr && processor != nullptr && ! parameters.empty()))
        return finish();
    if (! step ("latency", &Host::measureLatencyAndTail, processor != nullptr && ! inputBuses.empty()))
        return finish();
    if (! step ("state", &Host::exerciseState, component != nullptr && controller != nullptr))
        return finish();
    if (! step ("surface", &Host::inspectSurface, controller != nullptr))
        return finish();
    if (! step ("dump", &Host::dumpAudio, processor != nullptr))
        return finish();

    // The time each step took, so a reader can see where the exercise
    // spends its time.
    {
        std::string summary;
        int64_t total = 0;
        for (const auto& entry : stepTimes)
        {
            total += entry.second;
            summary += entry.first + "=" + std::to_string (entry.second) + "ms ";
        }
        note ("timing          : " + summary + "total=" + std::to_string (total) + "ms");
    }

    note ("result          : OK");
    return finish();
}

bool Host::writeAudioDump (const std::string& path) const
{
    if (path.empty())
        return true;

    std::ofstream file (path, std::ios::binary);
    if (! file)
    {
        writeToStderr ("! the audio could not be written to " + path + "\n");
        return false;
    }

    if (! dumpedAudio.empty())
    {
        file.write (reinterpret_cast<const char*> (dumpedAudio.data()),
                    static_cast<std::streamsize> (dumpedAudio.size() * sizeof (float)));
    }

    return true;
}

/* The dump step: a tone goes in and the output is kept, so that it can be
   compared with a reference.  It runs at the end of the exercise, when the
   plug-in's own delay has passed: what comes out is the signal the chain
   made, at the rate the host works at. */
bool Host::dumpAudio()
{
    if (dumpPath.empty())
        return true;

    const int32 blocks = 16;
    HostParameterChanges noChanges;
    const std::vector<float> tone = auralis::makeTone (
        Setup::kToneHz, Setup::kToneAmplitude,
        static_cast<size_t> (blocks) * static_cast<size_t> (blockSize), sampleRate);

    for (int32 block = 0; block < blocks; ++block)
    {
        for (auto& bus : inputBuses)
            bus.clear();
        for (auto& bus : outputBuses)
            bus.fillSentinel();

        const size_t offset = static_cast<size_t> (block) * static_cast<size_t> (blockSize);
        for (auto& bus : inputBuses)
        {
            for (size_t c = 0; c < bus.floatStorage.size(); ++c)
                for (int32 n = 0; n < blockSize; ++n)
                {
                    const float value = tone[offset + static_cast<size_t> (n)];
                    bus.floatStorage[c][static_cast<size_t> (n)] = value;
                    bus.doubleStorage[c][static_cast<size_t> (n)] = value;
                }
        }

        ProcessData data;
        data.processMode = kRealtime;
        data.symbolicSampleSize = useDoublePrecision ? kSample64 : kSample32;
        data.numSamples = blockSize;
        data.numInputs = numInputBuses;
        data.numOutputs = numOutputBuses;
        data.inputs = numInputBuses > 0 ? &inputBuses[0].buffers : nullptr;
        data.outputs = numOutputBuses > 0 ? &outputBuses[0].buffers : nullptr;
        data.inputParameterChanges = &noChanges;
        data.outputParameterChanges = nullptr;
        data.inputEvents = nullptr;
        data.outputEvents = nullptr;
        data.processContext = nullptr;

        if (processor->process (data) != kResultOk)
        {
            fail (ExitCode::kStep, "the processor failed while the audio was dumped");
            return false;
        }

        if (! outputBuses.empty())
        {
            const std::vector<float> channel = firstChannel (outputBuses[0], useDoublePrecision);
            dumpedAudio.insert (dumpedAudio.end(), channel.begin(), channel.end());
        }
    }

    note ("audio dump      : " + std::to_string (dumpedAudio.size()) + " sample(s) to "
              + dumpPath);

    processingInfo.set ("audio_dump", json::Value (dumpPath));
    processingInfo.set ("dumped_frames", json::Value (static_cast<int64_t> (dumpedAudio.size())));
    processingInfo.set ("dumped_sample_rate", json::Value (sampleRate));

    return true;
}

void Host::teardown()
{
    // Leave the plug-in the way a host does: stop processing, deactivate,
    // disconnect, terminate, release.  This runs whatever happened during
    // the exercise.
    note ("teardown        : stopping processing");
    if (processor != nullptr)
        processor->setProcessing (false);
    note ("teardown        : deactivating");
    if (component != nullptr && componentActivated)
    {
        // A host deactivates what it activated, and nothing else: a
        // component that was never activated has no engine to switch off.
        component->setActive (false);
        componentActivated = false;
    }
    if (component != nullptr && busesActivated)
    {
        // The buses are let go after the component, the reverse of the order
        // they were activated in.  A host that leaves a bus activated tells
        // the plug-in that the bus is still in use.
        activateBuses (component, false);
        busesActivated = false;
    }

    note ("teardown        : disconnecting");
    if (component != nullptr)
    {
        FUnknownPtr<IConnectionPoint> componentPoint (component);
        FUnknownPtr<IConnectionPoint> controllerPoint (controller);
        if (componentPoint != nullptr && controllerPoint != nullptr)
        {
            componentPoint->disconnect (controllerPoint.getInterface());
            controllerPoint->disconnect (componentPoint.getInterface());
        }
    }

    note ("teardown        : terminating the component");
    if (! guardedTerminate (component))
        recordFault ("the module faulted while the component was being terminated");

    note ("teardown        : terminating the controller");
    if (! guardedTerminate (controller))
        recordFault ("the module faulted while the controller was being terminated");

    note ("teardown        : releasing");
    if (processor != nullptr)
    {
        if (! guardedRelease (processor))
            recordFault ("the module faulted while the audio processor was being released");
        processor = nullptr;
    }
    if (controller != nullptr)
    {
        if (! guardedRelease (controller))
            recordFault ("the module faulted while the controller was being released");
        controller = nullptr;
    }
    if (component != nullptr)
    {
        if (! guardedRelease (component))
            recordFault ("the module faulted while the component was being released");
        component = nullptr;
    }
    if (! module.releaseFactory())
        recordFault ("the module faulted while the plug-in factory was being released");

    // The shared library is let go here, not in the destructor, so that a
    // module that does not let go of itself cannot keep the host alive
    // after the report has been written.
    note ("teardown        : unloading the module");
    if (! module.unload (5000))
        recordFault ("the module did not let go of its library within 5 s");

    note ("teardown        : done");
}

void Host::recordFault (const std::string& message)
{
    faults.push_back (message);
    writeToStderr ("! " + message + "\n");
}

void Host::report()
{
    // The JSON goes to stdout, the human report to stderr.
    json::Value root = json::Value::makeObject();
    root.set ("ok", json::Value (exitCode == ExitCode::kOk));
    root.set ("host", json::Value ("auralis-vst3host"));
    root.set ("host_version", json::Value ("1.0.0"));
    root.set ("exit_code", json::Value (exitCode));

    json::Value moduleInfo = json::Value::makeObject();
    moduleInfo.set ("argument", json::Value (moduleArgument));
    moduleInfo.set ("binary", json::Value (binaryPath.string()));
    root.set ("module", moduleInfo);

    root.set ("factory", factoryRecord);

    json::Value classes = json::Value::makeArray();
    for (const auto& record : classRecords)
        classes.push (record);
    root.set ("class_count", json::Value (static_cast<int32_t> (classRecords.size())));
    root.set ("classes", classes);

    json::Value componentInfo = json::Value::makeObject();
    componentInfo.set ("created", json::Value (componentCreated));
    componentInfo.set ("controller_created", json::Value (controllerCreated));
    componentInfo.set ("selected_class_index", json::Value (selectedClassIndex));
    componentInfo.set ("selected_class_name", json::Value (selectedClassName));
    root.set ("component", componentInfo);
    root.set ("units", unitRecord);
    root.set ("connection", connectionRecord);
    root.set ("buses", busRecord);

    root.set ("audio", audioInfo);
    root.set ("parameters", parameterRecords);
    root.set ("parameter_catalogue", parameterCatalogue);
    root.set ("parameter_roundtrip", parameterRoundTrip);
    root.set ("parameter_liveness", livenessRecord);
    root.set ("parameter_step", stepRecord);
    root.set ("agreement", agreementRecord);
    root.set ("latency", latencyRecord);
    // The count over the whole exercise: every block the host pushed through
    // the plug-in, not only the required one.
    processingInfo.set ("unwritten_output_samples_total", json::Value (unwrittenOutputSamples));
    root.set ("processing", processingInfo);
    root.set ("state", stateInfo);
    root.set ("surface", surfaceRecord);

    // The time each step took.
    {
        json::Value timings = json::Value::makeObject();
        int64_t total = 0;
        for (const auto& entry : stepTimes)
        {
            timings.set (entry.first, json::Value (entry.second));
            total += entry.second;
        }
        timings.set ("total_ms", json::Value (total));
        root.set ("timings", timings);
    }

    if (errorMessage.empty())
        root.set ("error", json::Value());
    else
        root.set ("error", json::Value (errorMessage));

    root.set ("findings", findings);

    json::Value faultList = json::Value::makeArray();
    for (const auto& fault : faults)
        faultList.push (json::Value (fault));
    root.set ("faults", faultList);

    // What the plug-in said while it was exercised.
    {
        json::Value messages = json::Value::makeArray();
        std::istringstream stream (plugInMessages);
        std::string line;
        while (std::getline (stream, line))
        {
            if (line.empty())
                continue;
            messages.push (json::Value (line));
        }
        root.set ("plug_in_messages", messages);
    }

    // What the plug-in wrote to the debug output of the operating system.
    {
        json::Value lines = json::Value::makeArray();
        std::istringstream stream (plugInDebugOutput);
        std::string line;
        while (std::getline (stream, line))
        {
            if (line.empty())
                continue;
            lines.push (json::Value (line));
        }
        root.set ("plug_in_debug_output", lines);

        json::Value capture = json::Value::makeObject();
        capture.set ("available", json::Value (debugCaptureAvailable));
        capture.set ("line_count", json::Value (static_cast<int32> (lines.getMemberCount())));
        if (! debugCaptureReason.empty())
            capture.set ("reason", json::Value (debugCaptureReason));
        root.set ("debug_capture", capture);
    }

    // The layout the module was found in.
    root.set ("layout", layoutRecord);

    // What happened to every step of the exercise.
    {
        json::Value steps = json::Value::makeArray();
        for (const auto& entry : stepRecords)
        {
            json::Value record = json::Value::makeObject();
            record.set ("step", json::Value (entry.first));
            record.set ("outcome", json::Value (entry.second));
            steps.push (record);
        }
        root.set ("steps", steps);
    }

    root.set ("report_only", json::Value (reportOnly));

    std::cout << root.serialise (2) << "\n";
    std::cout.flush();

    if (! faults.empty())
    {
        writeToStderr ("module faults   : " + std::to_string (faults.size()) + "\n");
    }
}

// ---------------------------------------------------------------------------
//  stdout protection
// ---------------------------------------------------------------------------
//
//  The module is third party code: it may print.  To keep stdout parseable
//  it is pointed at stderr while the module is loaded and exercised, and
//  restored afterwards.

class StdoutGuard
{
public:
    StdoutGuard()
    {
        std::fflush (stdout);
#if defined (_WIN32)
        saved = _dup (_fileno (stdout));
        _dup2 (_fileno (stderr), _fileno (stdout));
#else
        saved = dup (fileno (stdout));
        dup2 (fileno (stderr), fileno (stdout));
#endif
    }

    ~StdoutGuard()
    {
        std::fflush (stdout);
#if defined (_WIN32)
        if (saved != -1)
            _dup2 (saved, _fileno (stdout));
        _close (saved);
#else
        if (saved != -1)
            dup2 (saved, fileno (stdout));
        close (saved);
#endif
    }

private:
    int saved {-1};
};

// ---------------------------------------------------------------------------
//  crash diagnostics
// ---------------------------------------------------------------------------
//
//  The module is third party code.  When it faults, the host should say
//  where, instead of dying silently.

#if defined (_WIN32)
static LPTOP_LEVEL_EXCEPTION_FILTER previousFilter = nullptr;

static std::string describeAddress (void* address)
{
    HMODULE module = nullptr;
    if (GetModuleHandleEx (GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           static_cast<LPCWSTR> (address),
                           &module)
        == 0)
        return "an unknown module";

    char path[MAX_PATH] = {0};
    GetModuleFileNameA (module, path, MAX_PATH);

    const size_t base = reinterpret_cast<size_t> (module);
    const size_t moduleOffset = reinterpret_cast<size_t> (address) - base;

    char buf[32];
    std::snprintf (buf, sizeof (buf), "%zx", moduleOffset);
    return std::string (path) + " +0x" + std::string (buf);
}

static LONG WINAPI crashFilter (EXCEPTION_POINTERS* info)
{
    if (info != nullptr && info->ExceptionRecord != nullptr)
    {
        const auto code = info->ExceptionRecord->ExceptionCode;
        const auto address = info->ExceptionRecord->ExceptionAddress;
        std::cerr << "!! the module faulted: exception 0x" << std::hex << code << std::dec << " at "
                  << describeAddress (address) << "\n";
        std::cerr.flush();
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

static void installCrashFilter()
{
    SetUnhandledExceptionFilter (crashFilter);
}
#else
static void installCrashFilter()
{
}
#endif

// ---------------------------------------------------------------------------
//  entry point
// ---------------------------------------------------------------------------

static void printUsage (std::ostream& out)
{
    out << "usage: vst3host <module> [--tone] [--report-only]\n"
           "       vst3host --list <folder>\n"
           "\n"
           "  <module>       a .vst3 bundle, a .vst3.dll, a module file, or a\n"
           "                 folder holding one\n"
           "  --tone         additionally process a 1 kHz tone and report its peak\n"
           "  --report-only  do not stop at the first step that failed: report what\n"
           "                 the module says instead of stopping the exercise\n"
           "  --search-bound N\n"
           "                 search the delay up to N samples instead of up to the\n"
           "                 bound of the catalogue of the product (a diagnostic)\n"
           "  --list         only list the modules found in the given folder\n";
}

static std::vector<fs::path> listModules (const fs::path& root)
{
    std::vector<fs::path> found;
    std::error_code ec;
    if (! fs::exists (root, ec))
        return found;

    for (fs::recursive_directory_iterator it (root, fs::directory_options::skip_permission_denied, ec), end;
         it != end;
         it.increment (ec))
    {
        if (ec)
            break;
        const fs::path& p = it->path();
        if (p.extension() == ".vst3")
        {
            if (fs::is_directory (p, ec))
            {
                // Only report a bundle that actually holds a binary.
                for (const auto& arch : bundleArchNames())
                {
                    const fs::path archDir = p / "Contents" / arch;
                    if (fs::is_directory (archDir, ec) && ! findBinaryIn (archDir, p.filename().string()).empty())
                    {
                        found.push_back (p);
                        break;
                    }
                }
                if (! found.empty() && found.back() == p)
                    continue;
            }
            else
            {
                found.push_back (p);
            }
        }
    }
    return found;
}

int main (int argc, char** argv)
{
    installCrashFilter();

    std::vector<std::string> args;
    for (int i = 1; i < argc; ++i)
        args.emplace_back (argv[i]);

    bool wantTone = false;
    bool reportOnly = false;
    std::string moduleArgument;
    bool listMode = false;
    std::string listRoot;
    int32 blockSize = 0;
    double sampleRate = 0.0;
    std::string dumpPath;
    int64_t searchBound = 0;
    ParamID forcedParamId = 0;
    double forcedParamValue = 0.0;
    bool haveForcedParam = false;

    for (size_t i = 0; i < args.size(); ++i)
    {
        const std::string& a = args[i];
        if (a == "--tone")
            wantTone = true;
        else if (a == "--report-only")
            reportOnly = true;
        else if (a == "--block-size" && i + 1 < args.size())
            blockSize = std::atoi (args[++i].c_str());
        else if (a == "--rate" && i + 1 < args.size())
            sampleRate = std::atof (args[++i].c_str());
        else if (a == "--search-bound" && i + 1 < args.size())
            searchBound = std::strtoll (args[++i].c_str(), nullptr, 10);
        else if (a == "--dump-audio" && i + 1 < args.size())
            dumpPath = args[++i];
        else if (a == "--parameter" && i + 2 < args.size())
        {
            forcedParamId = static_cast<ParamID> (std::atoi (args[++i].c_str()));
            forcedParamValue = std::atof (args[++i].c_str());
            haveForcedParam = true;
        }
        else if (a == "--list")
        {
            listMode = true;
            if (i + 1 < args.size())
                listRoot = args[++i];
        }
        else if (a == "--help" || a == "-h")
        {
            printUsage (std::cout);
            return ExitCode::kOk;
        }
        else if (moduleArgument.empty())
            moduleArgument = a;
    }

    if (listMode)
    {
        const auto modules = listModules (listRoot.empty() ? fs::path (".") : fs::path (listRoot));
        json::Value root = json::Value::makeObject();
        root.set ("ok", json::Value (true));
        root.set ("root", json::Value (listRoot));
        json::Value list = json::Value::makeArray();
        for (const auto& m : modules)
            list.push (json::Value (m.string()));
        root.set ("modules", list);
        std::cout << root.serialise (2) << "\n";
        for (const auto& m : modules)
            std::cerr << m.string() << "\n";
        return ExitCode::kOk;
    }

    if (moduleArgument.empty())
    {
        // The clear "no module given" error.  The JSON is still printed, so
        // a test can assert on the failure.
        json::Value root = json::Value::makeObject();
        root.set ("ok", json::Value (false));
        root.set ("host", json::Value ("auralis-vst3host"));
        root.set ("host_version", json::Value ("1.0.0"));
        root.set ("exit_code", json::Value (ExitCode::kLoad));
        root.set ("error", json::Value ("no module given: pass the path of a VST3 module to load"));
        std::cout << root.serialise (2) << "\n";
        std::cout.flush();

        std::cerr << "no module given: pass the path of a VST3 module to load\n";
        std::cerr << "usage: vst3host <module> [--tone]\n";
        std::cerr.flush();
        return ExitCode::kLoad;
    }

    Host host;
    int code = ExitCode::kOk;

    if (blockSize > 0)
        host.setBlockSize (blockSize);
    if (sampleRate > 0.0)
        host.setSampleRate (sampleRate);
    if (haveForcedParam)
        host.setForcedParameter (forcedParamId, forcedParamValue);
    if (searchBound > 0)
        host.setSearchBound (searchBound);
    host.setAudioDump (dumpPath);
    host.setReportOnly (reportOnly);

    {
        // The module may print; keep stdout clean for the JSON.  stdout is
        // restored when this scope ends, before the report is written.
        StdoutGuard stdoutGuard;

        code = host.run (moduleArgument, wantTone);
        host.teardown();
    }

    if (! dumpPath.empty())
        host.writeAudioDump (dumpPath);

    host.report();

    // A module that did not let go of its library has a thread of its own
    // still inside the operating system.  The report is written, so the
    // process ends here: the destructors would wait for that thread.
    if (host.moduleIsAbandoned())
    {
        std::cout.flush();
        std::cerr.flush();
        std::_Exit (code);
    }

    return code;
}
