/*
    aurcore_abi.cpp -- the Auralis core C ABI.

    This is the implementation of the contract declared in
    plugins/vst3/aurcore_c_api.h.  It embeds the interpreter and drives the
    core package through aurcore.abi_bridge.

    WHY EMBED THE INTERPRETER
    --------------------------
    The DSP core is written in Python/NumPy.  Rather than re-implementing it
    in C (which would fork the algorithm and guarantee that the two drift),
    the adapter talks to the one core through a small, stable C boundary.
    The boundary is deliberately narrow: eighteen entry points, all of them
    documented in the header, all of them derived from it.

    THE RULES THIS FILE KEEPS
    --------------------------
    1. Every failing call leaves a message the host can read, and never
       leaves a Python exception pending.
    2. The interpreter lock is taken after the engine lock and released
       before it, so the two can never deadlock.
    3. A control call (the message thread) and a realtime call (the audio
       thread) never touch the same engine at the same time.
    4. The audio path allocates nothing on the C side once the block size
       has settled: the block buffer belongs to the engine.
    5. The library can be loaded and unloaded repeatedly in one process, and
       it only ends an interpreter that it started itself.

    THE MODE
    --------
    The mode the chain works in ("realtime" or "offline") is a PARAMETER of
    the module, called "Processing Mode", and it needs no entry point of its
    own: an adapter sets it with aur_engine_set_param_text, exactly as it
    sets any other parameter, and the core hands the mode to every module
    of the chain before the chain runs.

    Two things follow for an adapter.  The mode is part of the chain
    document, so the state of the plug-in carries it.  And the latency of
    the chain follows the mode -- a module that uses a longer window in the
    offline mode delays more -- so the adapter has to report the latency
    again when the mode changes.

    BUILD
    -----
    plugins/abi/build_abi.py produces aurcore_abi.dll (or the platform
    equivalent).  The export table is derived from the header, so the
    library exports exactly what the header promises.
*/

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include <atomic>
#include <mutex>
#include <new>
#include <shared_mutex>

#if defined(_WIN32)
  #include <windows.h>
#else
  #include <dlfcn.h>
#endif

#include "aurcore_c_api.h"

/* The build writes the two paths that cannot be known at source level into
   a small generated header, so the same source builds with any compiler:
   a quoted string on a command line is a quoting problem in every compiler
   that is not the one the command line was written for. */
#if defined(__has_include)
  #if __has_include ("aurcore_build_config.h")
    #include "aurcore_build_config.h"
  #endif
#endif

/* The root of the project: the directory the core package is in when the
   library is used from a checkout.  The environment variable wins, and the
   directories above the library are searched for the package as well, so
   an installed package works without an environment variable. */
#ifndef AURALIS_ROOT
#define AURALIS_ROOT "."
#endif

#define AUR_ERROR_TEXT 512

/* ------------------------------------------------------------------ */
/* the engine                                                          */
/* ------------------------------------------------------------------ */

struct AurEngine
{
    AurEngine ()
        : engine (nullptr)
        , channels (0)
        , generation (0)
        , module_count (0)
        , block (nullptr)
        , block_capacity (0)
    {
        last_error[0] = '\0';
    }

    ~AurEngine ()
    {
        free (block);
        block = nullptr;
        block_capacity = 0;
    }

    PyObject* engine;          /* the core's Engine object */
    uint32_t channels;
    uint32_t generation;       /* the interpreter generation it was born in */
    std::atomic<int> module_count;  /* the number of modules in the chain */

    std::mutex lock;           /* the engine lock */
    std::mutex error_lock;     /* guards last_error */
    char last_error[AUR_ERROR_TEXT];

    float* block;              /* the reusable audio block */
    size_t block_capacity;     /* in floats */
};

/* ------------------------------------------------------------------ */
/* the library's own state                                            */
/* ------------------------------------------------------------------ */

/* The interpreter lock of the library: shared while a call is running,
   exclusive while the interpreter is being ended.  Two engines may run at
   the same time; the interpreter itself decides how they interleave. */
static std::shared_mutex g_interpreter_lock;

/* Guards the start of the interpreter and the flag that says whether this
   library started it. */
static std::mutex g_init_lock;

/* True once the interpreter is up. */
static std::atomic<int> g_python_ready (0);

/* True when this library started the interpreter and therefore owns it. */
static bool g_owns_interpreter = false;

/* True when a failure of the start-up has been reported.  The failure is
   reported once: a plug-in asks for an engine again and again, and the
   host reads what the plug-in wrote only at the end of the exercise, so a
   message per attempt fills the pipe of the host and the host waits for a
   reader that only comes at the end. */
static std::atomic<int> g_start_failure_reported (0);

/* Bumped every time the interpreter is ended.  An engine that belongs to an
   older generation is stale and is refused. */
static std::atomic<uint32_t> g_generation (1);

/* The imported bridge module.  Owned by the library while the interpreter
   lives; only touched under the interpreter lock. */
static PyObject* g_bridge = nullptr;

/* The version of the core, read once when the interpreter comes up. */
static char g_version[64] = "0.0.0";

/* The message of the calls that have no engine: aur_engine_create and
   aur_shutdown.  Read with aur_engine_last_error(NULL). */
static char g_global_error[AUR_ERROR_TEXT] = { 0 };
static std::mutex g_global_error_lock;

/* The places the core looked for its package.  A plug-in that does nothing
   has to be able to say why, so the search writes down where it looked. */
static char g_package_search[1024] = { 0 };

/* ------------------------------------------------------------------ */
/* the messages                                                        */
/* ------------------------------------------------------------------ */

static void aur_set_error (AurEngine* e, const char* message)
{
    if (message == nullptr)
        message = "unknown error";

    if (e == nullptr)
    {
        std::lock_guard<std::mutex> guard (g_global_error_lock);
        snprintf (g_global_error, sizeof (g_global_error), "%s", message);
        return;
    }

    std::lock_guard<std::mutex> guard (e->error_lock);
    snprintf (e->last_error, sizeof (e->last_error), "%s", message);
}

static bool aur_error_is_empty (AurEngine* e)
{
    if (e == nullptr)
    {
        std::lock_guard<std::mutex> guard (g_global_error_lock);
        return g_global_error[0] == '\0';
    }

    std::lock_guard<std::mutex> guard (e->error_lock);
    return e->last_error[0] == '\0';
}

/* Turn the failure into a return value.  The message is written when the
   failure has none of its own; the return value carries the classification
   in every case. */
static int aur_fail (AurEngine* e, AurError code, const char* message)
{
    if (message != nullptr)
        aur_set_error (e, message);
    else if (aur_error_is_empty (e))
    {
        char text[AUR_ERROR_TEXT];
        snprintf (text, sizeof (text), "error %d", (int) code);
        aur_set_error (e, text);
    }

    return -(int) code;
}

/* The same, for the calls that return a 64 bit value. */
static int64_t aur_fail64 (AurEngine* e, AurError code, const char* message)
{
    aur_fail (e, code, message);
    return -(int64_t) code;
}


/* Report a failure of the start-up.  The message goes into the library, so
   that the caller can read it, and onto the error stream, so that the host
   can print it.  It is written once: a plug-in asks for an engine again and
   again, and the host reads what the plug-in wrote only at the end of the
   exercise, so a message per attempt fills the pipe of the host and the
   host waits for a reader that only comes at the end. */
static void aur_report_start_failure (const char* message)
{
    aur_set_error (nullptr, message);

    if (g_start_failure_reported.exchange (1, std::memory_order_acq_rel) == 0)
    {
        fprintf (stderr, "auralis: %s\n", message);
        fflush (stderr);
    }
}

/* Read the pending exception, turn it into a message and clear it.  A
   message that names the exception is worth far more than "python
   exception", which is what the host used to be told. */
static void aur_set_py_error (AurEngine* e)
{
    if (! PyErr_Occurred ())
    {
        aur_set_error (e, "the core raised an exception that carried no message");
        return;
    }

    PyObject* type = nullptr;
    PyObject* value = nullptr;
    PyObject* trace = nullptr;
    PyErr_Fetch (&type, &value, &trace);
    PyErr_NormalizeException (&type, &value, &trace);

    char text[AUR_ERROR_TEXT];
    text[0] = '\0';

    /* The name of the class of the exception, so the message reads
       "KeyError: 'Sensitivity'" and not "'Sensitivity'". */
    if (value != nullptr)
    {
        PyObject* cls = PyObject_Type (value);
        if (cls != nullptr)
        {
            PyObject* name = PyObject_GetAttrString (cls, "__name__");
            if (name != nullptr)
            {
                const char* utf8 = PyUnicode_AsUTF8 (name);
                if (utf8 != nullptr)
                    snprintf (text, sizeof (text), "%s", utf8);
                Py_DECREF (name);
            }
            else
            {
                PyErr_Clear ();
            }
            Py_DECREF (cls);
        }
        else
        {
            PyErr_Clear ();
        }
    }

    if (text[0] == '\0' && type != nullptr)
    {
        PyObject* name = PyObject_GetAttrString (type, "__name__");
        if (name != nullptr)
        {
            const char* utf8 = PyUnicode_AsUTF8 (name);
            if (utf8 != nullptr)
                snprintf (text, sizeof (text), "%s", utf8);
            Py_DECREF (name);
        }
        else
        {
            PyErr_Clear ();
        }
    }

    if (value != nullptr)
    {
        PyObject* shown = PyObject_Str (value);
        if (shown != nullptr)
        {
            const char* utf8 = PyUnicode_AsUTF8 (shown);
            if (utf8 != nullptr)
            {
                const size_t used = strlen (text);
                snprintf (text + used, sizeof (text) - used, ": %s", utf8);
            }
            Py_DECREF (shown);
        }
        else
        {
            PyErr_Clear ();
        }
    }

    Py_XDECREF (type);
    Py_XDECREF (value);
    Py_XDECREF (trace);

    if (text[0] == '\0')
        snprintf (text, sizeof (text), "the core raised an exception");

    aur_set_error (e, text);
}

/* No entry point may leave a Python exception pending: the interpreter is
   shared with the program that embeds this library, and a pending exception
   would surface there at a moment of the host's choosing. */
static void aur_clear_py_error ()
{
    if (PyErr_Occurred ())
        PyErr_Clear ();
}

/* ------------------------------------------------------------------ */
/* locating the runtime                                                */
/* ------------------------------------------------------------------ */

/* The directory this library was loaded from.  A plug-in bundle keeps the
   core package next to the library, so this is where the interpreter has to
   look for it; the host loads the bundle from wherever the user put it, so
   the path cannot be known at build time. */
static void aur_library_directory (char* out, size_t size)
{
    if (out == nullptr || size == 0)
        return;

    out[0] = '\0';

#if defined(_WIN32)
    HMODULE handle = NULL;

    if (GetModuleHandleExA (GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                                GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            (LPCSTR) (void*) &aur_library_directory,
                            &handle) != 0)
    {
        char path[MAX_PATH];
        const DWORD length = GetModuleFileNameA (handle, path, (DWORD) sizeof (path));

        if (length > 0 && length < sizeof (path))
        {
            char* last = strrchr (path, '\\');
            if (last != NULL)
            {
                *last = '\0';
                snprintf (out, size, "%s", path);
            }
        }
    }
#else
    Dl_info info;

    if (dladdr ((void*) &aur_library_directory, &info) != 0 &&
        info.dli_fname != NULL)
    {
        char* last = strrchr (info.dli_fname, '/');
        if (last != NULL)
        {
            *last = '\0';
            snprintf (out, size, "%s", info.dli_fname);
        }
    }
#endif
}

/* ------------------------------------------------------------------ */
/* the interpreter's home                                             */
/* ------------------------------------------------------------------ */

/* The interpreter needs a standard library to start at all.  Without one
   it stops with "Fatal Python error: Failed to import encodings module",
   and it takes the host down with it: the host dies in the middle of the
   exercise, without a report and without a word about the reason.

   So the home has to be found BEFORE the interpreter starts, and it has to
   be found by the core itself: the user who installs the package does not
   set an environment variable.

   The core is a shared library, so it knows where it was loaded from.  The
   runtime directory holds the interpreter, and the standard library sits
   next to it, so the home is one of:

     1. the directory the core was loaded from - the runtime directory,
        which is the home of the interpreter that sits in it;
     2. the directory next to it, for the arrangement where the runtime
        directory is a sibling of the core;
     3. the "Python" directory next to it, for the arrangement where the
        home is a directory of its own;
     4. the PYTHONHOME of the environment, when the package carries no home
        of its own: a deployment can point the plug-in at another
        installation without rebuilding it.

   A candidate is a home when it holds a standard library OF THIS
   interpreter: "Lib" on Windows, "lib/python3.X" on the other platforms. */

static bool aur_path_is_file (const char* path)
{
    struct stat info;

    if (path == nullptr || path[0] == '\0')
        return false;
    if (stat (path, &info) != 0)
        return false;

    return (info.st_mode & S_IFREG) != 0;
}


/* The standard library of a home.  Windows keeps it in "Lib", the other
   platforms in "lib/python3.X".  A zipped standard library counts too:
   the interpreter reads it out of the archive. */
static bool aur_home_has_stdlib (const char* home)
{
    char path[1200];

    snprintf (path, sizeof (path), "%s/Lib/os.py", home);
    if (aur_path_is_file (path))
        return true;

    snprintf (path, sizeof (path), "%s/lib/python%d.%d/os.py",
              home, PY_MAJOR_VERSION, PY_MINOR_VERSION);
    if (aur_path_is_file (path))
        return true;

    snprintf (path, sizeof (path), "%s/python%d%d.zip",
              home, PY_MAJOR_VERSION, PY_MINOR_VERSION);
    if (aur_path_is_file (path))
        return true;

    return false;
}


/* The name of the interpreter library of a version: "python315.dll" is
   3.15, "libpython3.15.so" is 3.15, and "python3.dll" is the stable ABI,
   which serves every 3.x. */
static bool aur_interpreter_name_is_ours (const char* name)
{
    const char* p = name;

    if (strncmp (p, "libpython", 9) == 0)
        p += 9;
    else if (strncmp (p, "python", 6) == 0)
        p += 6;
    else
        return true;   /* not the interpreter: nothing to disagree with */

    if (p[0] == '3' && p[1] == '.')
    {
        /* "libpython3.15.so" */
        int minor = 0;
        int digits = 0;

        ++p;
        while (p[0] >= '0' && p[0] <= '9')
        {
            minor = minor * 10 + (p[0] - '0');
            ++p;
            ++digits;
        }

        if (digits == 0)
            return true;   /* "libpython3.so": the stable ABI */

        return minor == PY_MINOR_VERSION;
    }

    if (p[0] == '3' && (p[1] == '\0' || p[1] == '_' || p[1] == '.'))
        return true;       /* "python3.dll": the stable ABI */

    if (p[0] == '3')
        ++p;               /* "python315.dll" */

    {
        int minor = 0;
        int digits = 0;

        while (p[0] >= '0' && p[0] <= '9')
        {
            minor = minor * 10 + (p[0] - '0');
            ++p;
            ++digits;
        }

        if (digits == 0)
            return true;
        if (digits == 1)
            return true;   /* "python3.dll": the stable ABI */

        return minor == PY_MINOR_VERSION;
    }
}


/* A home that holds the interpreter library of another version is not the
   home of this core: the standard library of one version is not the
   standard library of another. */
static bool aur_home_has_no_foreign_interpreter (const char* home)
{
#if defined(_WIN32)
    char pattern[1200];
    WIN32_FIND_DATAA found;
    HANDLE handle;

    snprintf (pattern, sizeof (pattern), "%s\\python3*.dll", home);

    handle = FindFirstFileA (pattern, &found);
    if (handle == INVALID_HANDLE_VALUE)
        return true;

    bool ours = true;

    do
    {
        if (! aur_interpreter_name_is_ours (found.cFileName))
            ours = false;
    }
    while (FindNextFileA (handle, &found));

    FindClose (handle);
    return ours;
#else
    (void) home;
    return true;
#endif
}


/* The directory above the given one.  Returns false when there is none. */
static bool aur_parent_directory (char* path)
{
    char* last = strrchr (path, '\\');
    char* slash = strrchr (path, '/');

    if (slash != NULL && (last == NULL || slash > last))
        last = slash;

    if (last == NULL)
        return false;

    *last = '\0';
    return path[0] != '\0';
}


/* A directory is a home when it holds a standard library this core's
   interpreter can use. */
static bool aur_home_is_usable (const char* home)
{
    if (! aur_home_has_stdlib (home))
        return false;

    return aur_home_has_no_foreign_interpreter (home);
}


/* The home of the interpreter, found from where the core is running.
   Returns false when there is none: the caller then says so instead of
   starting an interpreter that cannot live. */
static bool aur_find_python_home (char* out, size_t size)
{
    char library_dir[1024];

    out[0] = '\0';
    aur_library_directory (library_dir, sizeof (library_dir));

    /* 1. the home the package carries: the directory the core was loaded
          from holds the interpreter, so it is the home. */
    if (library_dir[0] != '\0' && aur_home_is_usable (library_dir))
    {
        snprintf (out, size, "%s", library_dir);
        return true;
    }

    /* 2. the directory next to the core: the arrangement where the runtime
          directory is a sibling of the library. */
    if (library_dir[0] != '\0')
    {
        char sibling[1200];

        snprintf (sibling, sizeof (sibling), "%s", library_dir);
        if (aur_parent_directory (sibling) && aur_home_is_usable (sibling))
        {
            snprintf (out, size, "%s", sibling);
            return true;
        }
    }

    /* 3. the home in a directory of its own next to the core. */
    if (library_dir[0] != '\0')
    {
        char beside[1200];

        snprintf (beside, sizeof (beside), "%s/Python", library_dir);
        if (aur_home_is_usable (beside))
        {
            snprintf (out, size, "%s", beside);
            return true;
        }
    }

    /* 4. the environment, when the package carries no home of its own. */
    {
        const char* const env = getenv ("PYTHONHOME");

        if (env != nullptr && env[0] != '\0' && aur_home_is_usable (env))
        {
            snprintf (out, size, "%s", env);
            return true;
        }
    }

    return false;
}


/* Turn a path into a wide string, the way the platform spells it. */
static bool aur_widen (const char* text, wchar_t* out, size_t count)
{
    if (text == nullptr || out == nullptr || count == 0)
        return false;

#if defined(_WIN32)
    {
        const int written = MultiByteToWideChar (CP_UTF8, 0, text, -1, out, (int) count);
        return written > 0;
    }
#else
    {
        /* every path the package ships is ASCII */
        const size_t length = strlen (text);

        if (length + 1 > count)
            return false;

        for (size_t i = 0; i < length; ++i)
            out[i] = (wchar_t) (unsigned char) text[i];

        out[length] = 0;
        return true;
    }
#endif
}


/* Start the interpreter with the home the core found.

   Returns 0 when the interpreter is up and -1 when it could not be
   started.  When there is no home the interpreter is NOT started at all:
   it would stop with "Failed to import encodings module" and take the
   host down with it, silently.  The core says what is missing instead,
   on the error stream and in the message of the library, so the host can
   print it. */
static int aur_start_interpreter (void)
{
    char home[1024];

    if (! aur_find_python_home (home, sizeof (home)))
    {
        char library_dir[1024];
        char sibling[1200];
        char beside[1200];
        char message[AUR_ERROR_TEXT * 2];
        const char* const env = getenv ("PYTHONHOME");

        aur_library_directory (library_dir, sizeof (library_dir));
        snprintf (beside, sizeof (beside), "%s/Python", library_dir);
        snprintf (sibling, sizeof (sibling), "%s", library_dir);
        aur_parent_directory (sibling);

        if (env != nullptr && env[0] != '\0')
        {
            snprintf (message, sizeof (message),
                      "the interpreter could not be started: its standard library was "
                      "not found.  The core looked in \"%s\", in \"%s\", in \"%s\" and "
                      "in the PYTHONHOME of the environment (\"%s\").  The package has "
                      "to carry the standard library of the interpreter next to it.",
                      library_dir, sibling, beside, env);
        }
        else
        {
            snprintf (message, sizeof (message),
                      "the interpreter could not be started: its standard library was "
                      "not found.  The core looked in \"%s\", in \"%s\" and in \"%s\", "
                      "and there is no PYTHONHOME in the environment.  The package has "
                      "to carry the standard library of the interpreter next to it.",
                      library_dir, sibling, beside);
        }

        aur_report_start_failure (message);
        return -1;
    }

    /* The home is handed to the interpreter itself, so the environment of
       the host is left alone: a program that embeds this library must not
       find a PYTHONHOME it did not set.  The buffer is static, because
       the configuration keeps the pointer until the interpreter is up. */
    {
        static wchar_t wide_home[1024];

        if (! aur_widen (home, wide_home, sizeof (wide_home) / sizeof (wide_home[0])))
        {
            aur_report_start_failure (
                "the interpreter could not be started: the home of the interpreter "
                "could not be turned into a wide string");
            return -1;
        }

        PyConfig config;

        PyConfig_InitPythonConfig (&config);

        /* What Py_InitializeEx(0) does: the host keeps its own handlers. */
        config.install_signal_handlers = 0;
        config.home = wide_home;

        const PyStatus status = Py_InitializeFromConfig (&config);

        if (PyStatus_Exception (status))
        {
            char message[AUR_ERROR_TEXT];

            snprintf (message, sizeof (message),
                      "the interpreter could not be started: %s",
                      status.err_msg != nullptr ? status.err_msg
                                                : "the configuration was refused");

            aur_report_start_failure (message);
            return -1;
        }
    }

    return 0;
}

/* The core package.  The release tree keeps it next to the application and
   the runtime directory sits a few directories below it, so the search
   walks up from the directory the core was loaded from and takes the
   closest directory that holds the package.  A user who installs the
   package therefore does not have to point the plug-in at it with an
   environment variable.

   THE WALK HAS TO CARRY ITS DIRECTORY OVER FROM ONE LEVEL TO THE NEXT.
   The directory the core was loaded from is copied into the local buffer
   ONCE, and the buffer is then shortened by one level per round.  A copy
   that is made again in every round throws the walk away: the search
   looks in the same directory six times and never finds the package, and
   the core is then only importable when the host happens to run in the
   directory of the package -- which a host that loads a plug-in from an
   installation never does. */
static void aur_add_core_package_path (PyObject* sys_path, const char* library_dir)
{
    char directory[1200];
    char candidate[1200];

    if (library_dir == NULL || library_dir[0] == '\0')
        return;

    snprintf (directory, sizeof (directory), "%s", library_dir);

    for (int level = 0; level < 6; ++level)
    {
        snprintf (candidate, sizeof (candidate), "%s/aurcore/__init__.py", directory);

        /* The places the core looked are kept: a plug-in that does
           nothing has to be able to say why, and the host only reads the
           error stream at the end of the exercise. */
        {
            std::lock_guard<std::mutex> guard (g_global_error_lock);
            const size_t used = strlen (g_package_search);

            if (used + strlen (candidate) + 2 < sizeof (g_package_search))
            {
                snprintf (g_package_search + used, sizeof (g_package_search) - used,
                          "%s%s", used > 0 ? ", " : "", candidate);
            }
        }

        if (aur_path_is_file (candidate))
        {
            PyObject* entry = PyUnicode_FromString (directory);
            if (entry != nullptr)
            {
                PyList_Insert (sys_path, 0, entry);
                Py_DECREF (entry);
            }
            return;
        }

        if (! aur_parent_directory (directory))
            return;
    }
}


/* Start the interpreter if it is not up yet, and make the core importable.

   The interpreter MUST be initialised before the thread state is touched.
   On Python 3.13 and later, PyGILState_Ensure() before Py_Initialize()
   deadlocks, so the order here is not negotiable.

   The caller holds the shared interpreter lock, so two threads cannot start
   the interpreter at the same time, and the shutdown cannot run while this
   runs. */
static int aur_init_python (void)
{
    if (g_python_ready.load (std::memory_order_acquire))
        return 0;

    std::lock_guard<std::mutex> guard (g_init_lock);

    if (g_python_ready.load (std::memory_order_relaxed))
        return 0;

    if (! Py_IsInitialized ())
    {
        if (aur_start_interpreter () != 0)
            return -1;

        if (! Py_IsInitialized ())
        {
            aur_set_error (nullptr, "the interpreter could not be started");
            return -1;
        }

        /* This library started the interpreter, so this library owns it and
           may end it. */
        g_owns_interpreter = true;
    }

    PyGILState_STATE gil = PyGILState_Ensure ();

    /* Make the project importable.  AURALIS_ROOT wins, then the directory
       the library was loaded from, then the current directory.  A plug-in
       bundle keeps the core package next to the library, so the library's
       own directory is always a candidate, and so is the "Python" folder
       beside it. */
    const char* root = getenv ("AURALIS_ROOT");
    if (root == nullptr || root[0] == '\0')
        root = AURALIS_ROOT;

    PyObject* sys_path = PySys_GetObject ("path");
    if (sys_path != nullptr)
    {
        char library_dir[1024];
        aur_library_directory (library_dir, sizeof (library_dir));

        const char* candidates[4];
        int count = 0;

        candidates[count++] = root;

        if (library_dir[0] != '\0')
        {
            candidates[count++] = library_dir;

            char beside[1200];
            snprintf (beside, sizeof (beside), "%s/Python", library_dir);
            candidates[count++] = beside;
        }

        candidates[count++] = ".";

        for (int i = 0; i < count; ++i)
        {
            PyObject* entry = PyUnicode_FromString (candidates[i]);
            if (entry != nullptr)
            {
                PyList_Insert (sys_path, 0, entry);
                Py_DECREF (entry);
            }
        }

        /* The package itself: the directories above the runtime directory
           are searched for it, so that an installed package works without
           an environment variable. */
        if (library_dir[0] != '\0')
            aur_add_core_package_path (sys_path, library_dir);
    }

    /* Import the bridge so its failures surface here, not on the first
       call, and keep it: the audio path should not pay for an import on
       every block. */
    PyObject* bridge = PyImport_ImportModule ("aurcore.abi_bridge");
    if (bridge == nullptr)
    {
        /* The host has to be able to see why the core did not come up, so
           the message goes onto the error stream as well as into the
           library.  It is written once: the plug-in asks again and again,
           and the host reads the stream only at the end of the exercise. */
        aur_set_py_error (nullptr);
        {
            std::lock_guard<std::mutex> guard (g_global_error_lock);

            if (g_start_failure_reported.exchange (1, std::memory_order_acq_rel) == 0)
            {
                fprintf (stderr, "auralis: the core could not be imported: %s\n",
                         g_global_error);

                if (g_package_search[0] != '\0')
                {
                    fprintf (stderr, "auralis: the core looked for the package in: %s\n",
                             g_package_search);
                }

                fflush (stderr);
            }
        }
        PyGILState_Release (gil);
        return -1;
    }

    if (g_bridge != nullptr)
        Py_DECREF (g_bridge);
    g_bridge = bridge;

    PyObject* core = PyImport_ImportModule ("aurcore");
    if (core != nullptr)
    {
        PyObject* v = PyObject_GetAttrString (core, "__version__");
        if (v != nullptr)
        {
            const char* s = PyUnicode_AsUTF8 (v);
            if (s != nullptr)
                snprintf (g_version, sizeof (g_version), "%s", s);
            Py_DECREF (v);
        }
        Py_DECREF (core);
    }
    PyErr_Clear ();

    PyGILState_Release (gil);
    g_python_ready.store (1, std::memory_order_release);
    return 0;
}

/* Call `bridge.<name>(engine, ...)` and convert a Python exception into a
   negative AurError. */
static PyObject* aur_call (AurEngine* e, const char* name, PyObject* args)
{
    PyObject* bridge = g_bridge;

    if (bridge == nullptr)
    {
        bridge = PyImport_ImportModule ("aurcore.abi_bridge");
        if (bridge == nullptr)
        {
            aur_set_py_error (e);
            return nullptr;
        }
        if (g_bridge != nullptr)
            Py_DECREF (g_bridge);
        g_bridge = bridge;
    }
    else
    {
        Py_INCREF (bridge);
    }

    PyObject* fn = PyObject_GetAttrString (bridge, name);
    Py_DECREF (bridge);
    if (fn == nullptr)
    {
        aur_set_py_error (e);
        return nullptr;
    }

    PyObject* result = PyObject_CallObject (fn, args);
    Py_DECREF (fn);
    if (result == nullptr)
        aur_set_py_error (e);
    return result;
}

/* ------------------------------------------------------------------ */
/* the guard that keeps the two threads apart                          */
/* ------------------------------------------------------------------ */

/* The chain is addressed by index, and the library keeps the number of
   modules, so an index that is out of range is caught here instead of in
   the core. */
static bool aur_module_index_ok (const AurEngine* e, uint32_t index)
{
    return index < (uint32_t) e->module_count.load (std::memory_order_acquire);
}

/* Every call on an engine takes the engine lock, so a control call and a
   realtime call can never touch the same engine at the same time.  The
   control call waits for the audio thread; the audio thread waits for a
   control call that is already running.  Neither waits for the other to be
   scheduled. */
class EngineGuard
{
public:
    explicit EngineGuard (AurEngine* e) : engine_ (e)
    {
        if (engine_ != nullptr)
            engine_->lock.lock ();
    }

    ~EngineGuard ()
    {
        if (engine_ != nullptr)
            engine_->lock.unlock ();
    }

    EngineGuard (const EngineGuard&) = delete;
    EngineGuard& operator= (const EngineGuard&) = delete;

private:
    AurEngine* engine_;
};

/* ------------------------------------------------------------------ */
/* required entry points                                               */
/* ------------------------------------------------------------------ */

AurEngine* aur_engine_create (uint32_t sample_rate, uint32_t channels)
{
    if (sample_rate == 0 || channels == 0)
    {
        aur_set_error (nullptr, "the sample rate and the channel count must be greater than zero");
        return nullptr;
    }

    std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

    if (aur_init_python () != 0)
        return nullptr;

    PyGILState_STATE gil = PyGILState_Ensure ();

    AurEngine* e = new (std::nothrow) AurEngine ();
    if (e == nullptr)
    {
        PyGILState_Release (gil);
        aur_set_error (nullptr, "the engine could not be allocated");
        return nullptr;
    }

    e->channels = channels;
    e->generation = g_generation.load (std::memory_order_acquire);

    PyObject* args = Py_BuildValue ("(ii)", (int) sample_rate, (int) channels);
    PyObject* result = aur_call (e, "create_engine", args);
    Py_XDECREF (args);

    if (result == nullptr)
    {
        /* The engine does not exist, so the message moves to the library. */
        char message[AUR_ERROR_TEXT];
        {
            std::lock_guard<std::mutex> guard (e->error_lock);
            snprintf (message, sizeof (message), "%s", e->last_error);
        }
        delete e;
        if (message[0] == '\0')
            snprintf (message, sizeof (message), "the engine could not be created");
        aur_set_error (nullptr, message);
        aur_clear_py_error ();
        PyGILState_Release (gil);
        return nullptr;
    }

    e->engine = result;
    aur_clear_py_error ();
    PyGILState_Release (gil);
    return e;
}

int aur_engine_add_module (AurEngine* engine, const char* module_code)
{
    if (engine == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the engine handle is NULL");
    if (module_code == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the module code is NULL");

    EngineGuard guard (engine);

    if (engine->generation != g_generation.load (std::memory_order_acquire))
        return aur_fail (engine, AUR_ERR_UNSUPPORTED, "the engine belongs to an interpreter that has ended");

    std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

    if (aur_init_python () != 0)
        return aur_fail (engine, AUR_ERR_INTERNAL, nullptr);

    PyGILState_STATE gil = PyGILState_Ensure ();

    int rc = AUR_OK;
    PyObject* args = Py_BuildValue ("(Os)", engine->engine, module_code);
    if (args == nullptr)
    {
        aur_set_py_error (engine);
        rc = aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, nullptr);
    }
    else
    {
        PyObject* result = aur_call (engine, "add_module", args);
        Py_DECREF (args);

        if (result == nullptr)
        {
            rc = aur_fail (engine, AUR_ERR_UNKNOWN_MODULE, nullptr);
        }
        else
        {
            const long index = PyLong_AsLong (result);
            Py_DECREF (result);

            if (index < 0 || PyErr_Occurred ())
            {
                PyErr_Clear ();
                rc = aur_fail (engine, AUR_ERR_UNKNOWN_MODULE, nullptr);
            }
            else
            {
                engine->module_count.store ((int) index + 1, std::memory_order_release);
                rc = (int) index;
            }
        }
    }

    aur_clear_py_error ();
    PyGILState_Release (gil);
    return rc;
}

int aur_engine_set_param (AurEngine* engine, uint32_t module_index, const char* param_name, double value)
{
    if (engine == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the engine handle is NULL");
    if (param_name == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the parameter name is NULL");

    EngineGuard guard (engine);

    if (engine->generation != g_generation.load (std::memory_order_acquire))
        return aur_fail (engine, AUR_ERR_UNSUPPORTED, "the engine belongs to an interpreter that has ended");

    if (! aur_module_index_ok (engine, module_index))
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the module index is out of range");

    std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

    if (aur_init_python () != 0)
        return aur_fail (engine, AUR_ERR_INTERNAL, nullptr);

    PyGILState_STATE gil = PyGILState_Ensure ();

    int rc = AUR_OK;
    /* O = the engine, i = the module index, s = the parameter name,
       d = the value in natural units */
    PyObject* args = Py_BuildValue ("(Oisd)", engine->engine, (int) module_index, param_name, value);
    if (args == nullptr)
    {
        aur_set_py_error (engine);
        rc = aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, nullptr);
    }
    else
    {
        PyObject* result = aur_call (engine, "set_param", args);
        Py_DECREF (args);

        if (result == nullptr)
            rc = aur_fail (engine, AUR_ERR_UNKNOWN_PARAMETER, nullptr);
        else
            Py_DECREF (result);
    }

    aur_clear_py_error ();
    PyGILState_Release (gil);
    return rc;
}

/* Set one parameter, applied over a ramp instead of as a step.

   The value moves from the one the parameter had to the one given here, in
   natural units, over `ramp_ms` milliseconds.  The parameter holds the
   value it is going to have right away, so the automation reaches its
   target even while the ramp runs, and a change that arrives while a ramp
   runs re-targets the ramp from the value the parameter has reached. */
int aur_engine_set_param_ramped (AurEngine* engine, uint32_t module_index,
                                 const char* param_name, double value, double ramp_ms)
{
    if (engine == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the engine handle is NULL");
    if (param_name == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the parameter name is NULL");

    /* A ramp of no length is a step: the plain call does it. */
    if (!(ramp_ms > 0.0))
        return aur_engine_set_param (engine, module_index, param_name, value);

    EngineGuard guard (engine);

    if (engine->generation != g_generation.load (std::memory_order_acquire))
        return aur_fail (engine, AUR_ERR_UNSUPPORTED, "the engine belongs to an interpreter that has ended");

    if (! aur_module_index_ok (engine, module_index))
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the module index is out of range");

    std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

    if (aur_init_python () != 0)
        return aur_fail (engine, AUR_ERR_INTERNAL, nullptr);

    PyGILState_STATE gil = PyGILState_Ensure ();

    int rc = AUR_OK;
    /* O = the engine, i = the module index, s = the parameter name,
       d = the value in natural units, d = the length of the ramp in
       milliseconds */
    PyObject* args = Py_BuildValue ("(Oisdd)", engine->engine, (int) module_index,
                                    param_name, value, ramp_ms);
    if (args == nullptr)
    {
        aur_set_py_error (engine);
        rc = aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, nullptr);
    }
    else
    {
        PyObject* result = aur_call (engine, "set_param_ramped", args);
        Py_DECREF (args);

        if (result == nullptr)
            rc = aur_fail (engine, AUR_ERR_UNKNOWN_PARAMETER, nullptr);
        else
            Py_DECREF (result);
    }

    aur_clear_py_error ();
    PyGILState_Release (gil);
    return rc;
}

/* Fill the output planes with silence.  The host must never play stale
   audio after a failed block. */
static void aur_zero_output (const AurEngine* engine, float* const* output, uint32_t frames)
{
    if (engine == nullptr || output == nullptr || frames == 0)
        return;

    for (uint32_t c = 0; c < engine->channels; ++c)
        memset (output[c], 0, (size_t) frames * sizeof (float));
}

int aur_engine_process (AurEngine* engine, const float* const* input, float* const* output,
                        uint32_t frames, uint32_t output_capacity, uint32_t output_rate,
                        AurBlockCounts* counts)
{
    if (counts != nullptr)
    {
        counts->consumed = 0;
        counts->produced = 0;
    }

    if (engine == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the engine handle is NULL");
    if (input == nullptr || output == nullptr)
    {
        if (output != nullptr)
            aur_zero_output (engine, output, output_capacity);
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the input or the output buffer is NULL");
    }
    if (frames == 0)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the block has no frames");
    if (output_capacity == 0)
    {
        aur_zero_output (engine, output, 0);
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the output buffer has no room");
    }

    EngineGuard guard (engine);

    if (engine->generation != g_generation.load (std::memory_order_acquire))
    {
        aur_zero_output (engine, output, output_capacity);
        return aur_fail (engine, AUR_ERR_UNSUPPORTED, "the engine belongs to an interpreter that has ended");
    }

    std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

    if (aur_init_python () != 0)
    {
        aur_zero_output (engine, output, output_capacity);
        return aur_fail (engine, AUR_ERR_INTERNAL, nullptr);
    }

    PyGILState_STATE gil = PyGILState_Ensure ();

    const uint32_t channels = engine->channels;
    const size_t samples = (size_t) frames * channels;
    const size_t bytes = samples * sizeof (float);
    int rc = AUR_OK;

    /* The block buffer belongs to the engine: once the block size has
       settled the audio path allocates nothing on this side. */
    if (engine->block_capacity < samples)
    {
        float* grown = (float*) realloc (engine->block, bytes);
        if (grown == nullptr)
        {
            aur_clear_py_error ();
            PyGILState_Release (gil);
            return aur_fail (engine, AUR_ERR_OUT_OF_MEMORY, "the audio block could not be allocated");
        }
        engine->block = grown;
        engine->block_capacity = samples;
    }

    /* gather the planar input into one contiguous block */
    for (uint32_t c = 0; c < channels; ++c)
        memcpy (engine->block + (size_t) c * frames, input[c], (size_t) frames * sizeof (float));

    PyObject* data = PyBytes_FromStringAndSize ((const char*) engine->block, (Py_ssize_t) bytes);
    if (data == nullptr)
    {
        aur_set_py_error (engine);
        rc = aur_fail (engine, AUR_ERR_INTERNAL, nullptr);
    }
    else
    {
        /* O = the engine, O = the raw block (borrowed; 'y' would want a C
           string plus a length, and the payload is binary), i = channels,
           i = frames, i = the rate the result is wanted at (0 = the rate
           the chain works at) */
        PyObject* args = Py_BuildValue ("(OOiii)", engine->engine, data, (int) channels,
                                        (int) frames, (int) output_rate);
        if (args == nullptr)
        {
            aur_set_py_error (engine);
            rc = aur_fail (engine, AUR_ERR_INTERNAL, nullptr);
        }
        else
        {
            /* the bridge hands back (the block, the samples the chain
               took, the samples the chain made) */
            PyObject* result = aur_call (engine, "process", args);
            Py_DECREF (args);

            if (result == nullptr)
            {
                rc = aur_fail (engine, AUR_ERR_INTERNAL, nullptr);
            }
            else
            {
                PyObject* block = nullptr;
                PyObject* consumed = nullptr;
                PyObject* produced = nullptr;

                if (! PyTuple_Check (result) || PyTuple_Size (result) != 3)
                {
                    PyErr_Clear ();
                    rc = aur_fail (engine, AUR_ERR_INTERNAL, "the core did not report the block it made");
                }
                else
                {
                    block = PyTuple_GetItem (result, 0);
                    consumed = PyTuple_GetItem (result, 1);
                    produced = PyTuple_GetItem (result, 2);
                }

                if (rc == AUR_OK)
                {
                    char* out_bytes = nullptr;
                    Py_ssize_t out_len = 0;

                    if (PyBytes_AsStringAndSize (block, &out_bytes, &out_len) < 0)
                    {
                        PyErr_Clear ();
                        rc = aur_fail (engine, AUR_ERR_INTERNAL, nullptr);
                    }
                    else
                    {
                        const long long took = PyLong_AsLongLong (consumed);
                        const long long made = PyLong_AsLongLong (produced);

                        if (PyErr_Occurred ())
                        {
                            PyErr_Clear ();
                            rc = aur_fail (engine, AUR_ERR_INTERNAL, nullptr);
                        }
                        else
                        {
                            /* The number of samples the chain made is the
                               number of samples in each plane of the block
                               it handed back.  The planes of the caller's
                               buffer hold `output_capacity` samples each,
                               and the ones the chain did not fill are
                               silenced: the host must never play stale
                               audio. */
                            const uint32_t made_frames = (made > 0 && out_len > 0)
                                ? (uint32_t) ((size_t) out_len / (sizeof (float) * channels))
                                : 0;
                            const uint32_t written = (made_frames < output_capacity)
                                ? made_frames : output_capacity;

                            for (uint32_t c = 0; c < channels; ++c)
                            {
                                const size_t offset = (size_t) c * made_frames * sizeof (float);
                                size_t available = 0;

                                if ((size_t) made_frames * sizeof (float) > 0 &&
                                    (size_t) out_len > offset)
                                {
                                    available = (size_t) out_len - offset;
                                    if (available > (size_t) made_frames * sizeof (float))
                                        available = (size_t) made_frames * sizeof (float);
                                }

                                if (available > (size_t) written * sizeof (float))
                                    available = (size_t) written * sizeof (float);

                                if (available > 0)
                                    memcpy (output[c], out_bytes + offset, available);

                                if (available < (size_t) output_capacity * sizeof (float))
                                    memset ((char*) output[c] + available, 0,
                                            (size_t) output_capacity * sizeof (float) - available);
                            }

                            if (counts != nullptr)
                            {
                                counts->consumed = (took > 0) ? (uint32_t) took : 0;
                                counts->produced = made_frames;
                            }
                        }
                    }
                }

                Py_DECREF (result);
            }
        }
        Py_DECREF (data);
    }

    if (rc != AUR_OK)
    {
        /* The host must never play stale audio after a failed block. */
        aur_zero_output (engine, output, output_capacity);
    }

    aur_clear_py_error ();
    PyGILState_Release (gil);
    return rc;
}

void aur_engine_destroy (AurEngine* engine)
{
    if (engine == nullptr)
        return;

    {
        EngineGuard guard (engine);

        /* The Python object only exists while the interpreter that made it
           is alive.  After a shutdown there is nothing to release: the
           interpreter took the object with it. */
        if (engine->generation == g_generation.load (std::memory_order_acquire) &&
            engine->engine != nullptr && Py_IsInitialized ())
        {
            std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

            PyGILState_STATE gil = PyGILState_Ensure ();
            Py_XDECREF (engine->engine);
            engine->engine = nullptr;
            aur_clear_py_error ();
            PyGILState_Release (gil);
        }

        engine->engine = nullptr;
    }

    delete engine;
}

/* ------------------------------------------------------------------ */
/* the interpreter itself                                              */
/* ------------------------------------------------------------------ */

/*
    aur_shutdown() ends the interpreter, but only the one this library
    started.

    A host unloads the module at a moment of its own choosing, and the
    interpreter's own exit handler then runs during that unload, where it
    cannot do anything sensible: the program is already half unloaded, and
    the handler walks structures that are being torn down.  A plug-in
    therefore ends the interpreter itself, while everything is still alive,
    and the exit handler finds nothing left to do.

    The call is explicit because the same library is used inside a program
    that embeds it (the application, the test suite): there the interpreter
    belongs to that program, and ending it would take the program down with
    it.  So the library remembers whether it started the interpreter, and
    only ends an interpreter that is its own.
*/
void aur_shutdown (void)
{
    /* The exclusive lock waits for the calls that are running, and keeps
       the new ones out until the interpreter is gone. */
    std::unique_lock<std::shared_mutex> exclusive (g_interpreter_lock);

    bool owned = false;

    {
        std::lock_guard<std::mutex> guard (g_init_lock);
        owned = g_owns_interpreter;
        g_owns_interpreter = false;
        g_python_ready.store (0, std::memory_order_release);
    }

    if (! owned)
    {
        /* The host owns the interpreter: leave it alone. */
        return;
    }

    g_bridge = nullptr;

    if (Py_IsInitialized ())
        Py_FinalizeEx ();

    /* Every engine that exists now belongs to an interpreter that is gone. */
    g_generation.fetch_add (1, std::memory_order_acq_rel);

    snprintf (g_version, sizeof (g_version), "0.0.0");
}

/* ------------------------------------------------------------------ */
/* extensions                                                          */
/* ------------------------------------------------------------------ */

int aur_engine_set_param_text (AurEngine* engine, uint32_t module_index, const char* param_name, const char* text)
{
    if (engine == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the engine handle is NULL");
    if (param_name == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the parameter name is NULL");
    if (text == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the parameter value is NULL");

    EngineGuard guard (engine);

    if (engine->generation != g_generation.load (std::memory_order_acquire))
        return aur_fail (engine, AUR_ERR_UNSUPPORTED, "the engine belongs to an interpreter that has ended");

    if (! aur_module_index_ok (engine, module_index))
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the module index is out of range");

    std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

    if (aur_init_python () != 0)
        return aur_fail (engine, AUR_ERR_INTERNAL, nullptr);

    PyGILState_STATE gil = PyGILState_Ensure ();

    int rc = AUR_OK;
    PyObject* args = Py_BuildValue ("(Oiss)", engine->engine, (int) module_index, param_name, text);
    if (args == nullptr)
    {
        aur_set_py_error (engine);
        rc = aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, nullptr);
    }
    else
    {
        PyObject* result = aur_call (engine, "set_param_text", args);
        Py_DECREF (args);

        if (result == nullptr)
            rc = aur_fail (engine, AUR_ERR_UNKNOWN_PARAMETER, nullptr);
        else
            Py_DECREF (result);
    }

    aur_clear_py_error ();
    PyGILState_Release (gil);
    return rc;
}

int aur_engine_get_param (AurEngine* engine, uint32_t module_index, const char* param_name, char* out, size_t out_size)
{
    if (engine == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the engine handle is NULL");
    if (param_name == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the parameter name is NULL");
    if (out == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the output buffer is NULL");
    if (out_size == 0)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the output buffer has no room");

    out[0] = '\0';

    EngineGuard guard (engine);

    if (engine->generation != g_generation.load (std::memory_order_acquire))
        return aur_fail (engine, AUR_ERR_UNSUPPORTED, "the engine belongs to an interpreter that has ended");

    if (! aur_module_index_ok (engine, module_index))
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the module index is out of range");

    std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

    if (aur_init_python () != 0)
        return aur_fail (engine, AUR_ERR_INTERNAL, nullptr);

    PyGILState_STATE gil = PyGILState_Ensure ();

    int rc = AUR_OK;
    PyObject* args = Py_BuildValue ("(Ois)", engine->engine, (int) module_index, param_name);
    if (args == nullptr)
    {
        aur_set_py_error (engine);
        rc = aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, nullptr);
    }
    else
    {
        PyObject* result = aur_call (engine, "get_param", args);
        Py_DECREF (args);

        if (result == nullptr)
        {
            rc = aur_fail (engine, AUR_ERR_UNKNOWN_PARAMETER, nullptr);
        }
        else
        {
            const char* text = PyUnicode_AsUTF8 (result);
            if (text != nullptr)
                snprintf (out, out_size, "%s", text);
            else
                PyErr_Clear ();
            Py_DECREF (result);
        }
    }

    aur_clear_py_error ();
    PyGILState_Release (gil);
    return rc;
}

int aur_engine_set_bypass (AurEngine* engine, uint32_t module_index, int bypass)
{
    if (engine == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the engine handle is NULL");

    EngineGuard guard (engine);

    if (engine->generation != g_generation.load (std::memory_order_acquire))
        return aur_fail (engine, AUR_ERR_UNSUPPORTED, "the engine belongs to an interpreter that has ended");

    if (! aur_module_index_ok (engine, module_index))
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the module index is out of range");

    std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

    if (aur_init_python () != 0)
        return aur_fail (engine, AUR_ERR_INTERNAL, nullptr);

    PyGILState_STATE gil = PyGILState_Ensure ();

    int rc = AUR_OK;
    PyObject* args = Py_BuildValue ("(Oii)", engine->engine, (int) module_index, bypass ? 1 : 0);
    if (args == nullptr)
    {
        aur_set_py_error (engine);
        rc = aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, nullptr);
    }
    else
    {
        PyObject* result = aur_call (engine, "set_bypass", args);
        Py_DECREF (args);

        if (result == nullptr)
            rc = aur_fail (engine, AUR_ERR_INTERNAL, nullptr);
        else
            Py_DECREF (result);
    }

    aur_clear_py_error ();
    PyGILState_Release (gil);
    return rc;
}

int aur_engine_module_count (const AurEngine* engine)
{
    if (engine == nullptr)
        return aur_fail (nullptr, AUR_ERR_INVALID_ARGUMENT, "the engine handle is NULL");

    if (engine->generation != g_generation.load (std::memory_order_acquire))
        return aur_fail ((AurEngine*) engine, AUR_ERR_UNSUPPORTED,
                         "the engine belongs to an interpreter that has ended");

    /* The count is kept by the library, so the call takes no lock and
       cannot block.  It is safe from the audio thread. */
    return engine->module_count.load (std::memory_order_acquire);
}

int64_t aur_engine_latency_samples (const AurEngine* engine)
{
    if (engine == nullptr)
        return aur_fail64 (nullptr, AUR_ERR_INVALID_ARGUMENT, "the engine handle is NULL");

    AurEngine* e = (AurEngine*) engine;

    EngineGuard guard (e);

    if (e->generation != g_generation.load (std::memory_order_acquire))
        return aur_fail64 (e, AUR_ERR_UNSUPPORTED, "the engine belongs to an interpreter that has ended");

    std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

    if (aur_init_python () != 0)
        return aur_fail64 (e, AUR_ERR_INTERNAL, nullptr);

    PyGILState_STATE gil = PyGILState_Ensure ();

    int64_t latency = 0;
    PyObject* args = Py_BuildValue ("(O)", e->engine);
    if (args == nullptr)
    {
        aur_set_py_error (e);
        aur_fail (e, AUR_ERR_INTERNAL, nullptr);
    }
    else
    {
        PyObject* result = aur_call (e, "latency_samples", args);
        Py_DECREF (args);

        if (result == nullptr)
        {
            aur_fail (e, AUR_ERR_INTERNAL, nullptr);
        }
        else
        {
            latency = (int64_t) PyLong_AsLongLong (result);
            Py_DECREF (result);
            if (PyErr_Occurred ())
            {
                PyErr_Clear ();
                latency = 0;
                aur_fail (e, AUR_ERR_INTERNAL, nullptr);
            }
        }
    }

    aur_clear_py_error ();
    PyGILState_Release (gil);
    return latency;
}

int64_t aur_engine_tail_samples (const AurEngine* engine)
{
    if (engine == nullptr)
        return aur_fail64 (nullptr, AUR_ERR_INVALID_ARGUMENT, "the engine handle is NULL");

    AurEngine* e = (AurEngine*) engine;

    EngineGuard guard (e);

    if (e->generation != g_generation.load (std::memory_order_acquire))
        return aur_fail64 (e, AUR_ERR_UNSUPPORTED, "the engine belongs to an interpreter that has ended");

    std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

    if (aur_init_python () != 0)
        return aur_fail64 (e, AUR_ERR_INTERNAL, nullptr);

    PyGILState_STATE gil = PyGILState_Ensure ();

    int64_t tail = 0;
    PyObject* args = Py_BuildValue ("(O)", e->engine);
    if (args == nullptr)
    {
        aur_set_py_error (e);
        aur_fail (e, AUR_ERR_INTERNAL, nullptr);
    }
    else
    {
        PyObject* result = aur_call (e, "tail_samples", args);
        Py_DECREF (args);

        if (result == nullptr)
        {
            aur_fail (e, AUR_ERR_INTERNAL, nullptr);
        }
        else
        {
            tail = (int64_t) PyLong_AsLongLong (result);
            Py_DECREF (result);
            if (PyErr_Occurred ())
            {
                PyErr_Clear ();
                tail = 0;
                aur_fail (e, AUR_ERR_INTERNAL, nullptr);
            }
        }
    }

    aur_clear_py_error ();
    PyGILState_Release (gil);
    return tail;
}

int64_t aur_engine_output_sample_rate (const AurEngine* engine)
{
    if (engine == nullptr)
        return aur_fail64 (nullptr, AUR_ERR_INVALID_ARGUMENT, "the engine handle is NULL");

    AurEngine* e = (AurEngine*) engine;

    EngineGuard guard (e);

    if (e->generation != g_generation.load (std::memory_order_acquire))
        return aur_fail64 (e, AUR_ERR_UNSUPPORTED, "the engine belongs to an interpreter that has ended");

    std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

    if (aur_init_python () != 0)
        return aur_fail64 (e, AUR_ERR_INTERNAL, nullptr);

    PyGILState_STATE gil = PyGILState_Ensure ();

    int64_t rate = 0;
    PyObject* args = Py_BuildValue ("(O)", e->engine);
    if (args == nullptr)
    {
        aur_set_py_error (e);
        aur_fail (e, AUR_ERR_INTERNAL, nullptr);
    }
    else
    {
        PyObject* result = aur_call (e, "output_sample_rate", args);
        Py_DECREF (args);

        if (result == nullptr)
        {
            aur_fail (e, AUR_ERR_INTERNAL, nullptr);
        }
        else
        {
            rate = (int64_t) PyLong_AsLongLong (result);
            Py_DECREF (result);
            if (PyErr_Occurred ())
            {
                PyErr_Clear ();
                rate = 0;
                aur_fail (e, AUR_ERR_INTERNAL, nullptr);
            }
        }
    }

    aur_clear_py_error ();
    PyGILState_Release (gil);
    return rate;
}

int64_t aur_engine_serialize (const AurEngine* engine, char** out)
{
    if (engine == nullptr)
        return aur_fail64 (nullptr, AUR_ERR_INVALID_ARGUMENT, "the engine handle is NULL");
    if (out == nullptr)
        return aur_fail64 ((AurEngine*) engine, AUR_ERR_INVALID_ARGUMENT, "the output pointer is NULL");

    *out = nullptr;

    AurEngine* e = (AurEngine*) engine;

    EngineGuard guard (e);

    if (e->generation != g_generation.load (std::memory_order_acquire))
        return aur_fail64 (e, AUR_ERR_UNSUPPORTED, "the engine belongs to an interpreter that has ended");

    std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

    if (aur_init_python () != 0)
        return aur_fail64 (e, AUR_ERR_INTERNAL, nullptr);

    PyGILState_STATE gil = PyGILState_Ensure ();

    int64_t length = -(int64_t) AUR_ERR_INTERNAL;
    PyObject* args = Py_BuildValue ("(O)", e->engine);
    if (args == nullptr)
    {
        aur_set_py_error (e);
        aur_fail (e, AUR_ERR_INTERNAL, nullptr);
    }
    else
    {
        PyObject* result = aur_call (e, "serialize", args);
        Py_DECREF (args);

        if (result == nullptr)
        {
            aur_fail (e, AUR_ERR_INTERNAL, nullptr);
        }
        else
        {
            const char* text = PyUnicode_AsUTF8 (result);
            if (text == nullptr)
            {
                PyErr_Clear ();
                aur_fail (e, AUR_ERR_INTERNAL, nullptr);
            }
            else
            {
                const size_t len = strlen (text);
                char* buffer = (char*) malloc (len + 1);
                if (buffer == nullptr)
                {
                    length = aur_fail64 (e, AUR_ERR_OUT_OF_MEMORY,
                                         "the chain document could not be allocated");
                }
                else
                {
                    memcpy (buffer, text, len + 1);
                    *out = buffer;
                    length = (int64_t) len;
                }
            }
            Py_DECREF (result);
        }
    }

    aur_clear_py_error ();
    PyGILState_Release (gil);
    return length;
}

int aur_engine_deserialize (AurEngine* engine, const char* chain_json)
{
    if (engine == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the engine handle is NULL");
    if (chain_json == nullptr)
        return aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, "the chain document is NULL");

    EngineGuard guard (engine);

    if (engine->generation != g_generation.load (std::memory_order_acquire))
        return aur_fail (engine, AUR_ERR_UNSUPPORTED, "the engine belongs to an interpreter that has ended");

    std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);

    if (aur_init_python () != 0)
        return aur_fail (engine, AUR_ERR_INTERNAL, nullptr);

    PyGILState_STATE gil = PyGILState_Ensure ();

    int rc = AUR_OK;
    PyObject* args = Py_BuildValue ("(Os)", engine->engine, chain_json);
    if (args == nullptr)
    {
        aur_set_py_error (engine);
        rc = aur_fail (engine, AUR_ERR_INVALID_ARGUMENT, nullptr);
    }
    else
    {
        PyObject* result = aur_call (engine, "deserialize", args);
        Py_DECREF (args);

        if (result == nullptr)
        {
            rc = aur_fail (engine, AUR_ERR_INTERNAL, nullptr);
        }
        else
        {
            Py_DECREF (result);
            /* the chain was replaced, so the count the library keeps has to
               follow it */
            PyObject* count_args = Py_BuildValue ("(O)", engine->engine);
            if (count_args != nullptr)
            {
                PyObject* count = aur_call (engine, "module_count", count_args);
                Py_DECREF (count_args);
                if (count != nullptr)
                {
                    const long value = PyLong_AsLong (count);
                    Py_DECREF (count);
                    if (! PyErr_Occurred () && value >= 0)
                        engine->module_count.store ((int) value, std::memory_order_release);
                    else
                        PyErr_Clear ();
                }
            }
        }
    }

    aur_clear_py_error ();
    PyGILState_Release (gil);
    return rc;
}

void aur_engine_free (char* text)
{
    free (text);
}

const char* aur_engine_last_error (const AurEngine* engine)
{
    /* The message is copied into a buffer that belongs to the calling
       thread, so a message that is written while it is read cannot tear,
       and the pointer stays valid until the next call on this thread. */
    static thread_local char buffer[AUR_ERROR_TEXT];

    if (engine == nullptr)
    {
        std::lock_guard<std::mutex> guard (g_global_error_lock);
        snprintf (buffer, sizeof (buffer), "%s", g_global_error);
        return buffer;
    }

    /* The lock is taken on a message that is written, so the const of the
       handle is only about the chain, not about the message. */
    std::lock_guard<std::mutex> guard (const_cast<AurEngine*> (engine)->error_lock);
    snprintf (buffer, sizeof (buffer), "%s", engine->last_error);
    return buffer;
}

const char* aur_engine_version (void)
{
    /* the version comes from the core, so the interpreter has to be up */
    {
        std::shared_lock<std::shared_mutex> shared (g_interpreter_lock);
        if (aur_init_python () != 0)
            return g_version;
    }

    return g_version;
}
