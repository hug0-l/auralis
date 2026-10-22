/*
    AuralisAAX.h - the Auralis AAX adapter, written directly against the AAX
    SDK.

    See plugins/aax/README.md for the architecture and for what this adapter
    does and does not prove.  The short version:

      * this file is ONE template; it implements any Auralis module;
      * the module is chosen by the identity record that the build stamped
        into the reserved slot below;
      * the page tables and the parameter list come from
        AuralisAAXGenerated.h, which scripts/build_aax.py writes from
        docs/aax_page_tables.json - the file scripts/aax_tables.py writes
        from the canonical parameter table, so the two plug-in formats
        cannot drift apart;
      * the core is reached through the C ABI in aurcore_c_api.h and is
        addressed by parameter NAME;
      * the state chunk is the shared "auralis.state.v1" convention, so a
        session written by one format loads here and the other way round.

    Nothing in this file knows the name of a module, a parameter or a
    preset.  Adding a module to the registry therefore produces a plug-in
    for every format without touching this file.

    THE INTERFACES
    --------------
    The SDK's own reference implementation (AAX_VComponentDescriptor.cpp and
    AAX_VPageTable.cpp) shows what a plug-in has to provide.  The set is:

      * AAX_IEffectParameters  - the data model.  Implemented here by
        AuralisAAX_Processor, which derives from the SDK's
        AAX_CEffectParameters and therefore carries the parameter manager,
        the chunk machinery and the page table hook.
      * AAX_IEffectGUI         - the controller.  Implemented here by
        AuralisAAX_Controller, which derives from the SDK's AAX_CEffectGUI.
      * AAX_IParameter         - one per parameter.  The SDK's AAX_CParameter
        supplies them; the adapter supplies the taper and display delegates.
      * AAX_IPageTable         - the pages and their controls.  The host owns
        the table object, the plug-in fills it; the adapter fills it in
        AuralisAAX_Processor::UpdatePageTable.
      * AAX_IComponentDescriptor, AAX_IEffectDescriptor, AAX_IPropertyMap
        and AAX_IAutomationDelegate are HOST interfaces: the host creates
        the objects, the plug-in fills the descriptor in, reads the property
        map and calls the automation delegate.  The SDK's AAX_V* classes
        are the wrappers that do that; see plugins/aax/README.md.
*/

#ifndef AURALIS_AAX_H
#define AURALIS_AAX_H

#include <AAX.h>
#include <AAX_Callbacks.h>
#include <AAX_CEffectGUI.h>
#include <AAX_CEffectParameters.h>
#include <AAX_ICollection.h>
#include <AAX_IEffectDescriptor.h>
#include <AAX_IComponentDescriptor.h>
#include <AAX_IPropertyMap.h>
#include <AAX_IPageTable.h>

#include <string>
#include <vector>

// ===========================================================================
//  the description: what the plug-in is, in plain data
// ===========================================================================
//
//  These are the structures the generated header fills in.  They carry no
//  behaviour on purpose: the description is data, and the same data is
//  handed to the host (through the AAX descriptor objects) and to the
//  verification probe (through its report), so the two cannot disagree.

/** One parameter of one module, as the canonical table states it. */
struct AuralisAAXParameter
{
    int         id;             //!< the registry id: the position inside the module
    const char* name;           //!< the canonical core name
    const char* label;          //!< the short name that is drawn on the control
    const char* type;           //!< "float", "int", "bool" or "enum"
    const char* group;          //!< the group the parameter belongs to
    const char* unit;           //!< the unit the value is in ("" when there is none)
    double      minimum;        //!< the lower bound, in natural units
    double      maximum;        //!< the upper bound, in natural units
    double      defaultValue;   //!< the default, in natural units
    const char* const* choices; //!< the choices of an enum, NULL terminated
    int         choiceCount;    //!< the number of choices
    const char* help;           //!< the sentence that explains the parameter
};

/** One bound control of a page. */
struct AuralisAAXControl
{
    const char* slot;      //!< "switch", "knob1", "knob2" or "knob3"
    int         parameter; //!< the registry id of the bound parameter
};

/** One page of controls. */
struct AuralisAAXPage
{
    int                        index;    //!< the page number, counted from one
    const AuralisAAXControl*   controls; //!< the controls of the page
    int                        controlCount;
};

/** The three channel configurations of a module. */
enum AuralisAAXVariant
{
    AuralisAAXVariant_Mono = 0,   //!< mono in, mono out
    AuralisAAXVariant_MonoToStereo = 1, //!< mono in, stereo out
    AuralisAAXVariant_Stereo = 2  //!< stereo in, stereo out
};

/** The number of channel configurations every module has. */
const int kAuralisAAXVariantCount = 3;

/** One module of the family. */
struct AuralisAAXModule
{
    const char* code;         //!< the canonical module code, e.g. "AUR_DECLICK"
    const char* displayName;  //!< the name the host shows
    const char* category;     //!< the registry category
    const char* manufacturerId; //!< the four character manufacturer id
    const char* productId;    //!< the four character product id
    const char* plugId[kAuralisAAXVariantCount]; //!< the plug id of each variant
    const char* effectId[kAuralisAAXVariantCount]; //!< the effect id of each variant
    int         inputChannels[kAuralisAAXVariantCount];
    int         outputChannels[kAuralisAAXVariantCount];

    const AuralisAAXParameter* parameters;
    int                        parameterCount;

    const AuralisAAXPage* pages;
    int             pageCount;

    /* The bypass switch.  The id is the same in every plug-in format of
       the family, so a host's automation map lines up with ours. */
    const char* bypassId;      //!< the id of the bypass parameter
    const char* bypassLabel;   //!< the name the host shows
    const char* bypassHelp;    //!< the sentence that explains it
};

/** The whole family: every module of the registry. */
struct AuralisAAXFamily
{
    int                     moduleCount;
    const AuralisAAXModule* modules;
};

/** The generated family.  AuralisAAXGenerated.h defines it; the header is
    written by scripts/build_aax.py from docs/aax_page_tables.json. */
extern const AuralisAAXFamily auralisAAXFamily;

// ===========================================================================
//  the identity record
// ===========================================================================

/** The identity of the module a binary implements.

    The record is stamped into the binary after it has been linked; see
    plugins/identity.py, which owns the format. */
struct AuralisAAXIdentity
{
    bool                     valid;
    std::string              moduleCode;   //!< the canonical module code
    std::string              displayName;
    std::string              category;
    std::string              productName;
    std::vector<std::string> effectIds;    //!< one per channel variant
};

/** Read the identity record out of this binary. */
AuralisAAXIdentity auralisAAXReadIdentity();

/** Parse an identity record out of a JSON document.

    The record is the one the build stamps into the binary; the format of
    the document belongs to plugins/identity.py. */
AuralisAAXIdentity auralisAAXParseIdentity (const char* text, size_t size);

/** Return the module the identity record names, or NULL. */
const AuralisAAXModule* auralisAAXModuleForCode (const std::string& code);

/** Return the module the identity record names, or NULL. */
const AuralisAAXModule* auralisAAXIdentityModule (const AuralisAAXIdentity& identity);

// ===========================================================================
//  the state chunk
// ===========================================================================

/** The marker that opens the shared state format.  The VST3 adapter writes
    the very same bytes, so a session moves between the two formats. */
extern const char* const kAuralisAAXStateMarker;

/** The id of the chunk the plug-in writes.

    A four character code, packed the way the SDK packs its own chunk ids
    (see CONTROLS_CHUNK_ID in AAX_CEffectParameters.cpp). */
AAX_CTypeID auralisAAXChunkId();

/** Pack four characters into the format's four character code.

    The packing is the one the SDK uses: the first character is the most
    significant byte. */
AAX_CTypeID auralisAAXFourCC (const char* text);

/** The base the AAX parameter id is offset by.

    The SDK's own reserved control ids are strings ("PreviewID",
    "MasterBypassID"), not a numeric range, so a numeric base cannot
    collide with them.  The base is ours and it is the number the
    application package publishes. */
const int kAuralisAAXParamIdBase = 256;

/** Return the AAX parameter id of a registry id. */
std::string auralisAAXParamId (int registryId);

// ===========================================================================
//  the data model
// ===========================================================================

/** The Auralis data model: the parameters, the state and the page table.

    The class derives from the SDK's AAX_CEffectParameters, so the parameter
    manager, the chunk machinery and the page table hook are the SDK's. */
class AuralisAAX_Processor : public AAX_CEffectParameters
{
public:
    /** Build the data model for the module the identity record names. */
    AuralisAAX_Processor();

    /** Build the data model for a named module.

        The verification probe uses this: it runs the very same code the
        plug-in runs, but names the module instead of reading it out of the
        stamped slot. */
    explicit AuralisAAX_Processor (const AuralisAAXModule* inModule);

    ~AuralisAAX_Processor() override;

    /** The factory the host calls; see kAAX_ProcPtrID_Create_EffectParameters. */
    static IACFUnknown* AAX_CALLBACK Create();

    /** The module this data model implements. */
    const AuralisAAXModule* module() const { return mModule; }

    /** The identity record this binary carries. */
    const AuralisAAXIdentity& identity() const { return mIdentity; }

    // -- AAX_CEffectParameters ------------------------------------------
    AAX_Result EffectInit() override;

    AAX_Result GetNumberOfChunks (int32_t* oNumChunks) const override;
    AAX_Result GetChunkIDFromIndex (int32_t iIndex, AAX_CTypeID* oChunkID) const override;
    AAX_Result GetChunkSize (AAX_CTypeID inChunkID, uint32_t* oSize) const override;
    AAX_Result GetChunk (AAX_CTypeID inChunkID, AAX_SPlugInChunk* oChunk) const override;
    AAX_Result SetChunk (AAX_CTypeID inChunkID, const AAX_SPlugInChunk* iChunk) override;
    AAX_Result CompareActiveChunk (const AAX_SPlugInChunk* iChunk, AAX_CBoolean* oIsEqual) const override;

    AAX_Result UpdatePageTable (uint32_t inTableType, int32_t inTablePageSize,
                                AAX_IPageTable& ioPageTable) const override;

    // The overload of the base class is hidden by the one above, and the
    // host calls that one.
    using AAX_CEffectParameters::UpdatePageTable;

    // -- the state ------------------------------------------------------
    /** The state blob: the bytes the chunk carries. */
    std::string stateBlob() const;

    /** The parameter state: the values of the plug-in's parameters. */
    std::string parameterState() const;

    /** Restore the parameter state. */
    bool restoreParameterState (const std::string& text);

private:
    void buildParameters();
    void buildChain();
    void publishHostState();

    const AuralisAAXModule* mModule;
    AuralisAAXIdentity      mIdentity;
    std::string             mParameterState;
};

// ===========================================================================
//  the controller
// ===========================================================================

/** The Auralis controller.

    The plug-in draws no editor of its own: the host shows the parameters
    of the data model, and the page table says how they are laid out.  The
    controller therefore exists to own the view container and to answer the
    host's questions about the plug-in. */
class AuralisAAX_Controller : public AAX_CEffectGUI
{
public:
    AuralisAAX_Controller() = default;
    ~AuralisAAX_Controller() override { DeleteViewContainer(); }

    /** The factory the host calls; see kAAX_ProcPtrID_Create_EffectGUI. */
    static AAX_IEffectGUI* AAX_CALLBACK Create() { return new AuralisAAX_Controller(); }

protected:
    void CreateViewContents() override {}
    void CreateViewContainer() override {}
    void DeleteViewContainer() override {}
};

// ===========================================================================
//  the description the host asks for
// ===========================================================================

/** The entry point the SDK calls; see AAX_Init.cpp.

    The SDK's AAXRegisterPlugin() builds a collection and hands it to this
    function; the function describes the plug-in into the collection. */
AAX_Result GetEffectDescriptions (AAX_ICollection* inCollection);

/** Describe a module into a collection.

    GetEffectDescriptions() names the module the identity record names and
    hands it to this function.  The verification probe names a module
    itself and hands it to the same function, so the two register exactly
    the same thing. */
AAX_Result AuralisAAXDescribe (AAX_ICollection* inCollection, const AuralisAAXModule* inModule);

/** The component descriptors of a module: one per channel configuration.

    The plug-in registers one component descriptor per variant and the
    verification probe reports the same values, so the two agree by
    construction. */
struct AuralisAAXComponent
{
    const char* plugId;
    int         inputChannels;
    int         outputChannels;
    const char* manufacturerId;
    const char* productId;
    std::string effectId;  //!< the "manufacturer:product:plug" form
};

/** Return the component descriptors of a module. */
std::vector<AuralisAAXComponent> auralisAAXComponents (const AuralisAAXModule& module);

#endif // AURALIS_AAX_H
