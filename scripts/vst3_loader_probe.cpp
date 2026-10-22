// ============================================================================
//  scripts/vst3_loader_probe.cpp
//
//  A measurement instrument: it answers one question.
//
//      "When a host loads this plug-in, which directory does the Windows
//       loader take the plug-in's dependencies from?"
//
//  The Windows loader does NOT search the directory of a loaded module for
//  that module's dependencies.  A plug-in that carries its runtime inside
//  its bundle is therefore only self contained if the loader happens to
//  find the dependency anyway - through the directory of the process image,
//  through AddDllDirectory, or through a library that was already loaded.
//
//  The probe makes every one of those conditions explicit, so the numbers
//  in the runtime decision can be reproduced.
//
//  usage:
//      vst3_loader_probe <plugin.dll> [options]
//
//      --altered            load with LOAD_WITH_ALTERED_SEARCH_PATH
//      --add-dir <dir>      AddDllDirectory(dir) before the load
//      --preload <dir>      LoadLibraryW every DLL in <dir> before the load
//      --preload-only <d>   load the DLLs of <d> and report what happened
//      --cwd <dir>          change the current directory first
//      --report             print where each named module was loaded from
//      --list <dir>         list the DLLs of <dir> and nothing else
//
//  The exit code is 0 when the plug-in loaded, 1 when it did not.  The
//  Windows error code of the failed load is printed, so the difference
//  between "the file is not there" and "a dependency is missing" is
//  visible.
// ============================================================================

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif

#include <windows.h>

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace {

struct DirectoryEntry
{
    std::string name;
    std::string path;
};

std::vector<DirectoryEntry> listDlls (const std::string& directory)
{
    std::vector<DirectoryEntry> found;
    WIN32_FIND_DATAA data;
    const std::string pattern = directory + "\\*";

    HANDLE handle = FindFirstFileA (pattern.c_str(), &data);

    if (handle == INVALID_HANDLE_VALUE)
        return found;

    do
    {
        if (data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
            continue;

        const std::string name = data.cFileName;

        if (name.size() < 4)
            continue;

        std::string tail = name.substr (name.size() - 4);
        for (char& c : tail)
            c = static_cast<char> (::tolower (c));

        if (tail != ".dll" && tail != ".so" && tail != ".dylib")
            continue;

        DirectoryEntry entry;
        entry.name = name;
        entry.path = directory + "\\" + name;
        found.push_back (entry);
    }    while (FindNextFileA (handle, &data));

    FindClose (handle);
    return found;
}

std::string modulePath (HMODULE module)
{
    if (module == nullptr)
        return std::string();

    char buffer[MAX_PATH * 2] = { 0 };
    const DWORD length = GetModuleFileNameA (module, buffer, sizeof (buffer));

    if (length == 0)
        return std::string();

    return std::string (buffer, length);
}

void reportModule (const char* label, const char* name)
{
    HMODULE module = GetModuleHandleA (name);

    if (module == nullptr)
    {
        std::printf ("  %-22s not loaded\n", label);
        return;
    }

    std::printf ("  %-22s %s\n", label, modulePath (module).c_str());
}

} // namespace

int main (int argc, char** argv)
{
    if (argc < 2)
    {
        std::printf ("usage: vst3_loader_probe <plugin.dll> [options]\n");
        return 2;
    }

    const std::string plugin = argv[1];

    bool altered = false;
    bool report = false;
    std::string addDir;
    std::string addDllDir;
    std::string preloadDir;
    std::string cwd;
    std::string listDir;
    std::vector<DLL_DIRECTORY_COOKIE> addedDirectories;

    for (int i = 2; i < argc; ++i)
    {
        const std::string option = argv[i];

        if (option == "--altered")
        {
            altered = true;
        }
        else if (option == "--add-dir" && i + 1 < argc)
        {
            addDir = argv[++i];
        }
        else if (option == "--add-dll-dir" && i + 1 < argc)
        {
            addDllDir = argv[++i];
        }
        else if (option == "--preload" && i + 1 < argc)
        {
            preloadDir = argv[++i];
        }
        else if (option == "--cwd" && i + 1 < argc)
        {
            cwd = argv[++i];
        }
        else if (option == "--report")
        {
            report = true;
        }
        else if (option == "--list" && i + 1 < argc)
        {
            listDir = argv[++i];
        }
    }

    if (! listDir.empty())
    {
        for (const DirectoryEntry& entry : listDlls (listDir))
            std::printf ("%-40s\n", entry.name.c_str());

        return 0;
    }

    if (! cwd.empty())
    {
        if (! SetCurrentDirectoryA (cwd.c_str()))
        {
            std::printf ("could not change to '%s' (%lu)\n", cwd.c_str(), GetLastError());
            return 2;
        }
    }

    if (! addDir.empty())
    {
        // A host that tells the loader where its dependencies are.  The
        // directory replaces the current directory in the search order.
        if (! SetDllDirectoryA (addDir.c_str()))
            std::printf ("SetDllDirectory('%s') failed (%lu)\n", addDir.c_str(), GetLastError());
    }

    if (! addDllDir.empty())
    {
        // The other way a host tells the loader: a directory that is added
        // to the search order, without replacing the current directory.
        typedef DLL_DIRECTORY_COOKIE (WINAPI * PfnAddDllDirectory) (LPCSTR);

        HMODULE kernel32 = GetModuleHandleA ("kernel32.dll");
        PfnAddDllDirectory add = kernel32 != nullptr
            ? reinterpret_cast<PfnAddDllDirectory> (
                  GetProcAddress (kernel32, "AddDllDirectoryA"))
            : nullptr;

        if (add == nullptr)
            std::printf ("AddDllDirectoryA is not available\n");
        else
        {
            const DLL_DIRECTORY_COOKIE cookie = add (addDllDir.c_str());

            if (cookie == nullptr)
                std::printf ("AddDllDirectory('%s') failed (%lu)\n", addDllDir.c_str(), GetLastError());
            else
                addedDirectories.push_back (cookie);
        }
    }

    if (! preloadDir.empty())
    {
        // What a host that "preloads the libraries that travel with the
        // module" does.
        for (const DirectoryEntry& entry : listDlls (preloadDir))
        {
            const HMODULE module = LoadLibraryA (entry.path.c_str());

            if (module == nullptr)
                std::printf ("  preload %-36s failed (%lu)\n", entry.name.c_str(), GetLastError());
            else
                std::printf ("  preload %-36s ok\n", entry.name.c_str());
        }
    }

    SetLastError (0);

    HMODULE handle = nullptr;

    if (altered)
        handle = LoadLibraryExA (plugin.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    else
        handle = LoadLibraryA (plugin.c_str());

    const DWORD error = GetLastError();

    if (handle == nullptr)
    {
        std::printf ("load   : FAILED (%lu)\n", error);

        if (error == ERROR_MOD_NOT_FOUND)
            std::printf ("         a shared library the module depends on was not found\n");
        else if (error == ERROR_PROC_NOT_FOUND)
            std::printf ("         a procedure the module imports was not found\n");

        if (report)
        {
            reportModule ("plug-in", "Auralis De-Click.vst3.dll");
            reportModule ("core", "aurcore_abi.dll");
            reportModule ("interpreter", "python315.dll");
        }

        return 1;
    }

    std::printf ("load   : ok\n");
    std::printf ("  plug-in               %s\n", modulePath (handle).c_str());

    if (report)
    {
        reportModule ("core", "aurcore_abi.dll");
        reportModule ("interpreter", "python315.dll");
    }

    for (DLL_DIRECTORY_COOKIE cookie : addedDirectories)
        RemoveDllDirectory (cookie);

    FreeLibrary (handle);
    return 0;
}
