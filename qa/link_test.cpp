/* link_test.cpp -- proves the Auralis core ABI import library is usable by a
   C++ consumer, which is exactly what the plug-in adapter needs. */
#include <cstdio>
#include "aurcore_c_api.h"

int main()
{
    setvbuf (stdout, nullptr, _IONBF, 0);
    printf("abi version: %s\n", aur_engine_version());
    fflush (stdout);

    printf("creating the engine...\n");
    fflush (stdout);
    AurEngine* engine = aur_engine_create (48000, 2);
    printf("engine = %p\n", (void*) engine);
    fflush (stdout);
    if (engine == nullptr)
    {
        printf("FAIL: the engine could not be created\n");
        return 1;
    }
    const int index = aur_engine_add_module (engine, "AUR_DECLICK");
    printf("add module -> %d\n", index);
    if (index < 0)
    {
        printf("FAIL: %s\n", aur_engine_last_error (engine));
        aur_engine_destroy (engine);
        return 1;
    }
    aur_engine_set_param (engine, 0, "Sensitivity", 5.0);
    char buffer[256] = { 0 };
    aur_engine_get_param (engine, 0, "Sensitivity", buffer, sizeof (buffer));
    printf("Sensitivity -> %s\n", buffer);
    printf("module count -> %d\n", aur_engine_module_count (engine));
    aur_engine_destroy (engine);
    printf("OK\n");
    return 0;
}
