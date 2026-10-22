/*
    AuralisAAXHostDouble.h - the stand-in host of the verification probe.

    WHY THIS EXISTS
    ---------------
    The objects the plug-in describes itself into - the effect descriptor,
    the component descriptor and the property map - are HOST objects: the
    host creates them, the plug-in fills them in.  There is no host on a
    build machine, so the probe provides the stand-ins below and the
    adapter fills those, the same way it would fill the real ones.

    The stand-ins are the format's own interfaces, not a copy of them: the
    adapter talks to them through AAX_IEffectDescriptor,
    AAX_IComponentDescriptor, AAX_IPropertyMap and AAX_ICollection, so
    what the probe reads back is what the plug-in registered with a host.

    The stand-ins record what they are given; they do not act on it.
*/

#ifndef AURALIS_AAX_HOST_DOUBLE_H
#define AURALIS_AAX_HOST_DOUBLE_H

#include <AAX_ICollection.h>
#include <AAX_IComponentDescriptor.h>
#include <AAX_IDescriptionHost.h>
#include <AAX_IPropertyMap.h>
#include <AAX_Version.h>

#include <map>
#include <string>
#include <vector>

// The host interfaces grew between the two revisions of the format's SDK this
// adapter is built against: revision 2.10 added a typed field to the
// component descriptor and a role to the effect descriptor.  The stand-ins
// below implement the host side of those interfaces, so they have to
// implement the methods of the revision they are compiled against.  The
// guard keeps one source working against both revisions.
#if AAX_SDK_VERSION >= 0x020A
#define AURALIS_AAX_SDK_HAS_TYPED_FIELDS 1
#define AURALIS_AAX_SDK_HAS_PLUG_IN_ROLE 1
#else
#define AURALIS_AAX_SDK_HAS_TYPED_FIELDS 0
#define AURALIS_AAX_SDK_HAS_PLUG_IN_ROLE 0
#endif

// ===========================================================================
//  the property map
// ===========================================================================

/** The host's property map: the numbers the plug-in declares. */
class AuralisAAXHostPropertyMap : public AAX_IPropertyMap
{
public:
    AAX_CBoolean GetProperty (AAX_EProperty inProperty, AAX_CPropertyValue* outValue) const override
    {
        const auto found = mValues.find (inProperty);

        if (found == mValues.end())
            return 0;

        *outValue = found->second;
        return 1;
    }

    AAX_CBoolean GetPointerProperty (AAX_EProperty inProperty, const void** outValue) const override
    {
        const auto found = mPointerValues.find (inProperty);

        if (found == mPointerValues.end())
            return 0;

        *outValue = found->second;
        return 1;
    }

    AAX_Result AddProperty (AAX_EProperty inProperty, AAX_CPropertyValue inValue) override
    {
        mValues[inProperty] = inValue;
        return AAX_SUCCESS;
    }

    AAX_Result AddPointerProperty (AAX_EProperty inProperty, const void* inValue) override
    {
        mPointerValues[inProperty] = inValue;
        return AAX_SUCCESS;
    }

    AAX_Result AddPointerProperty (AAX_EProperty inProperty, const char* inValue) override
    {
        return AddPointerProperty (inProperty, static_cast<const void*> (inValue));
    }

    AAX_Result RemoveProperty (AAX_EProperty inProperty) override
    {
        mValues.erase (inProperty);
        mPointerValues.erase (inProperty);
        return AAX_SUCCESS;
    }

    AAX_Result AddPropertyWithIDArray (AAX_EProperty, const AAX_SPlugInIdentifierTriad*, uint32_t) override
    {
        return AAX_ERROR_UNIMPLEMENTED;
    }

    AAX_CBoolean GetPropertyWithIDArray (AAX_EProperty, const AAX_SPlugInIdentifierTriad**, uint32_t*) const override
    {
        return 0;
    }

    IACFUnknown* GetIUnknown() override { return nullptr; }

    /** The value of a property, or zero when the plug-in did not set it. */
    AAX_CPropertyValue value (AAX_EProperty inProperty) const
    {
        const auto found = mValues.find (inProperty);
        return (found == mValues.end()) ? 0 : found->second;
    }

    bool has (AAX_EProperty inProperty) const
    {
        return mValues.find (inProperty) != mValues.end();
    }

private:
    std::map<AAX_EProperty, AAX_CPropertyValue> mValues;
    std::map<AAX_EProperty, const void*> mPointerValues;
};

// ===========================================================================
//  the component descriptor
// ===========================================================================

/** The host's component descriptor: the shape of one channel configuration. */
class AuralisAAXHostComponentDescriptor : public AAX_IComponentDescriptor
{
public:
    AAX_Result Clear() override
    {
        mAudioIn.clear();
        mAudioOut.clear();
        mBufferLength.clear();
        mDataIn.clear();
        mPrivateData.clear();
        mProperties = nullptr;
        mProcessProc = nullptr;
        mInstanceInitProc = nullptr;
        return AAX_SUCCESS;
    }

    AAX_Result AddAudioIn (AAX_CFieldIndex inFieldIndex) override
    {
        mAudioIn.push_back (inFieldIndex);
        return AAX_SUCCESS;
    }

    AAX_Result AddAudioOut (AAX_CFieldIndex inFieldIndex) override
    {
        mAudioOut.push_back (inFieldIndex);
        return AAX_SUCCESS;
    }

    AAX_Result AddAudioBufferLength (AAX_CFieldIndex inFieldIndex) override
    {
        mBufferLength.push_back (inFieldIndex);
        return AAX_SUCCESS;
    }

    AAX_Result AddSampleRate (AAX_CFieldIndex) override { return AAX_SUCCESS; }
    AAX_Result AddClock (AAX_CFieldIndex) override { return AAX_SUCCESS; }
    AAX_Result AddSideChainIn (AAX_CFieldIndex) override { return AAX_SUCCESS; }

    AAX_Result AddDataInPort (AAX_CFieldIndex inFieldIndex, uint32_t inPacketSize,
                               AAX_EDataInPortType = AAX_eDataInPortType_Buffered) override
    {
        mDataIn.push_back (inFieldIndex);
        mDataInSize = inPacketSize;
        return AAX_SUCCESS;
    }

    AAX_Result AddAuxOutputStem (AAX_CFieldIndex, int32_t, const char[]) override
    {
        return AAX_SUCCESS;
    }

    AAX_Result AddPrivateData (AAX_CFieldIndex inFieldIndex, int32_t inDataSize,
                                uint32_t = AAX_ePrivateDataOptions_DefaultOptions) override
    {
        mPrivateData.push_back (inFieldIndex);
        mPrivateDataSize = inDataSize;
        return AAX_SUCCESS;
    }

    AAX_Result AddTemporaryData (AAX_CFieldIndex, uint32_t) override { return AAX_SUCCESS; }
    AAX_Result AddDmaInstance (AAX_CFieldIndex, AAX_IDma::EMode) override { return AAX_SUCCESS; }
    AAX_Result AddMeters (AAX_CFieldIndex, const AAX_CTypeID*, const uint32_t) override { return AAX_SUCCESS; }
    AAX_Result AddMIDINode (AAX_CFieldIndex, AAX_EMIDINodeType, const char[], uint32_t) override { return AAX_SUCCESS; }
    AAX_Result AddReservedField (AAX_CFieldIndex, uint32_t) override { return AAX_SUCCESS; }

#if AURALIS_AAX_SDK_HAS_TYPED_FIELDS
    /** The typed field the plug-in added.  The stand-in records it and
        does not act on it, like every other field it is given. */
    AAX_Result AddFieldOfType (AAX_CFieldIndex inFieldIndex, AAX_CTypeID,
                                const void*) override
    {
        mTypedFields.push_back (inFieldIndex);
        return AAX_SUCCESS;
    }
#endif

    AAX_IPropertyMap* NewPropertyMap() const override
    {
        return new AuralisAAXHostPropertyMap();
    }

    AAX_IPropertyMap* DuplicatePropertyMap (AAX_IPropertyMap* inPropertyMap) const override
    {
        return inPropertyMap;
    }

    AAX_Result AddProcessProc_Native (AAX_CProcessProc inProcessProc,
                                      AAX_IPropertyMap* inProperties,
                                      AAX_CInstanceInitProc inInstanceInitProc,
                                      AAX_CBackgroundProc,
                                      AAX_CSelector* outProcID) override
    {
        mProcessProc = inProcessProc;
        mInstanceInitProc = inInstanceInitProc;
        mProperties = inProperties;

        if (outProcID != nullptr)
            *outProcID = 1;

        return AAX_SUCCESS;
    }

    AAX_Result AddProcessProc_TI (const char[], const char[], AAX_IPropertyMap*,
                                  const char[], const char[], AAX_CSelector*) override
    {
        return AAX_ERROR_UNIMPLEMENTED;
    }

    AAX_Result AddProcessProc (AAX_IPropertyMap* inProperties, AAX_CSelector* outProcIDs = nullptr,
                                int32_t inProcIDsSize = 0) override
    {
        return AddProcessProc_Native (mProcessProc, inProperties, mInstanceInitProc, nullptr, outProcIDs);
    }

    // -- what the plug-in declared ---------------------------------------
    const std::vector<AAX_CFieldIndex>& audioIn() const { return mAudioIn; }
    const std::vector<AAX_CFieldIndex>& audioOut() const { return mAudioOut; }
    const std::vector<AAX_CFieldIndex>& bufferLength() const { return mBufferLength; }
    const std::vector<AAX_CFieldIndex>& dataIn() const { return mDataIn; }
    const std::vector<AAX_CFieldIndex>& privateData() const { return mPrivateData; }
#if AURALIS_AAX_SDK_HAS_TYPED_FIELDS
    const std::vector<AAX_CFieldIndex>& typedFields() const { return mTypedFields; }
#endif
    uint32_t dataInSize() const { return mDataInSize; }
    uint32_t privateDataSize() const { return mPrivateDataSize; }
    bool hasProcessProc() const { return mProcessProc != nullptr; }
    bool hasInstanceInitProc() const { return mInstanceInitProc != nullptr; }

    AAX_IPropertyMap* properties() const { return mProperties; }

private:
    std::vector<AAX_CFieldIndex> mAudioIn;
    std::vector<AAX_CFieldIndex> mAudioOut;
    std::vector<AAX_CFieldIndex> mBufferLength;
    std::vector<AAX_CFieldIndex> mDataIn;
    std::vector<AAX_CFieldIndex> mPrivateData;
#if AURALIS_AAX_SDK_HAS_TYPED_FIELDS
    std::vector<AAX_CFieldIndex> mTypedFields;
#endif
    uint32_t mDataInSize = 0;
    uint32_t mPrivateDataSize = 0;
    AAX_CProcessProc mProcessProc = nullptr;
    AAX_CInstanceInitProc mInstanceInitProc = nullptr;
    AAX_IPropertyMap* mProperties = nullptr;
};

// ===========================================================================
//  the effect descriptor
// ===========================================================================

/** The host's effect descriptor: the identity of one effect. */
class AuralisAAXHostEffectDescriptor : public AAX_IEffectDescriptor
{
public:
    AAX_IComponentDescriptor* NewComponentDescriptor() override
    {
        AuralisAAXHostComponentDescriptor* const component = new AuralisAAXHostComponentDescriptor();
        mOwnedComponents.push_back (component);
        return component;
    }

    AAX_Result AddComponent (AAX_IComponentDescriptor* inComponentDescriptor) override
    {
        mComponents.push_back (inComponentDescriptor);
        return AAX_SUCCESS;
    }

    AAX_Result AddName (const char* inPlugInName) override
    {
        mNames.push_back (inPlugInName != nullptr ? inPlugInName : "");
        return AAX_SUCCESS;
    }

    AAX_Result AddCategory (uint32_t inCategory) override
    {
        mCategory = inCategory;
        return AAX_SUCCESS;
    }

    AAX_Result AddCategoryBypassParameter (uint32_t, AAX_CParamID) override
    {
        return AAX_SUCCESS;
    }

    AAX_Result AddProcPtr (void* inProcPtr, AAX_CProcPtrID inProcID) override
    {
        mProcPtrs.push_back (inProcPtr);
        mProcIds.push_back (inProcID);
        return AAX_SUCCESS;
    }

    AAX_IPropertyMap* NewPropertyMap() override
    {
        return new AuralisAAXHostPropertyMap();
    }

    AAX_Result SetProperties (AAX_IPropertyMap*) override { return AAX_SUCCESS; }

    /** The resource the plug-in declared.  The page table is one: a
        document in the bundle, whose name the host reads out of
        Contents/Resources.  The stand-in records what was declared, so the
        report can show it. */
    AAX_Result AddResourceInfo (AAX_EResourceType inResourceType, const char* inInfo) override
    {
        mResourceTypes.push_back (inResourceType);
        mResourceInfos.push_back (inInfo != nullptr ? inInfo : "");
        return AAX_SUCCESS;
    }

    AAX_Result AddMeterDescription (AAX_CTypeID, const char*, AAX_IPropertyMap*) override { return AAX_SUCCESS; }
    AAX_Result AddControlMIDINode (AAX_CTypeID, AAX_EMIDINodeType, const char[], uint32_t) override { return AAX_SUCCESS; }

#if AURALIS_AAX_SDK_HAS_PLUG_IN_ROLE
    /** The role the plug-in declared.  The stand-in records it and does not
        act on it, like every other declaration it is given. */
    AAX_Result SetRole (uint32_t inRole) override
    {
        mRole = inRole;
        return AAX_SUCCESS;
    }
#endif

    // -- what the plug-in declared ---------------------------------------
    const std::vector<std::string>& names() const { return mNames; }
    uint32_t category() const { return mCategory; }
    const std::vector<AAX_CProcPtrID>& procIds() const { return mProcIds; }
    const std::vector<void*>& procPtrs() const { return mProcPtrs; }
    const std::vector<AAX_IComponentDescriptor*>& components() const { return mComponents; }
    const std::vector<AAX_EResourceType>& resourceTypes() const { return mResourceTypes; }
    const std::vector<std::string>& resourceInfos() const { return mResourceInfos; }
#if AURALIS_AAX_SDK_HAS_PLUG_IN_ROLE
    uint32_t role() const { return mRole; }
#endif

private:
    std::vector<std::string> mNames;
    uint32_t mCategory = 0;
    std::vector<void*> mProcPtrs;
    std::vector<AAX_CProcPtrID> mProcIds;
    std::vector<AAX_IComponentDescriptor*> mComponents;
    std::vector<AuralisAAXHostComponentDescriptor*> mOwnedComponents;
    std::vector<AAX_EResourceType> mResourceTypes;
    std::vector<std::string> mResourceInfos;
#if AURALIS_AAX_SDK_HAS_PLUG_IN_ROLE
    uint32_t mRole = 0;
#endif
};

// ===========================================================================
//  the collection
// ===========================================================================

/** The host's collection: the effects the plug-in registers. */
class AuralisAAXHostCollection : public AAX_ICollection
{
public:
    AAX_IEffectDescriptor* NewDescriptor() override
    {
        AuralisAAXHostEffectDescriptor* const descriptor = new AuralisAAXHostEffectDescriptor();
        mOwnedDescriptors.push_back (descriptor);
        return descriptor;
    }

    AAX_Result AddEffect (const char* inEffectID, AAX_IEffectDescriptor* inEffectDescriptor) override
    {
        mEffectIds.push_back (inEffectID != nullptr ? inEffectID : "");
        mDescriptors.push_back (inEffectDescriptor);
        return AAX_SUCCESS;
    }

    AAX_Result SetManufacturerName (const char* inPackageName) override
    {
        mManufacturerName = (inPackageName != nullptr) ? inPackageName : "";
        return AAX_SUCCESS;
    }

    AAX_Result AddPackageName (const char* inPackageName) override
    {
        mPackageNames.push_back ((inPackageName != nullptr) ? inPackageName : "");
        return AAX_SUCCESS;
    }

    AAX_Result SetPackageVersion (uint32_t inVersion) override
    {
        mPackageVersion = inVersion;
        return AAX_SUCCESS;
    }

    AAX_IPropertyMap* NewPropertyMap() override { return new AuralisAAXHostPropertyMap(); }
    AAX_Result SetProperties (AAX_IPropertyMap*) override { return AAX_SUCCESS; }
    AAX_Result GetHostVersion (uint32_t* outVersion) const override
    {
        *outVersion = 0;
        return AAX_SUCCESS;
    }

    AAX_IDescriptionHost* DescriptionHost() override { return nullptr; }
    const AAX_IDescriptionHost* DescriptionHost() const override { return nullptr; }
    IACFDefinition* HostDefinition() const override { return nullptr; }

    // -- what the plug-in registered -------------------------------------
    const std::vector<std::string>& effectIds() const { return mEffectIds; }
    const std::vector<AAX_IEffectDescriptor*>& descriptors() const { return mDescriptors; }
    const std::string& manufacturerName() const { return mManufacturerName; }
    const std::vector<std::string>& packageNames() const { return mPackageNames; }
    uint32_t packageVersion() const { return mPackageVersion; }

    ~AuralisAAXHostCollection() override
    {
        for (AuralisAAXHostEffectDescriptor* descriptor : mOwnedDescriptors)
            delete descriptor;
    }

private:
    std::vector<std::string> mEffectIds;
    std::vector<AAX_IEffectDescriptor*> mDescriptors;
    std::vector<AuralisAAXHostEffectDescriptor*> mOwnedDescriptors;
    std::string mManufacturerName;
    std::vector<std::string> mPackageNames;
    uint32_t mPackageVersion = 0;
};

#endif // AURALIS_AAX_HOST_DOUBLE_H
