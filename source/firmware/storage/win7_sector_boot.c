/* GoAwayBCA private Windows 7 SP1 x64 4Kn compatibility candidate.
 * Not packaged until the VM and boot-chain integration tests pass.
 * Chain the adjacent SectorNext.efi, preserving the chosen graphics route.
 * Adjust one known hive-sector guard before kernel initialization. The kernel
 * file, driver signing policy and code-integrity settings stay unchanged.
 */
#include <efi.h>
#include <efilib.h>
int _fltused=0;

static EFI_EXIT_BOOT_SERVICES saved_exit;
static UINT8 map_buffer[128*1024] __attribute__((aligned(8)));
static const UINT8 original_rsds[]={0x52,0x53,0x44,0x53,0xb9,0xdb,0x44,0x38,0x17,0x20,0x67,0x49,
    0xbe,0x7a,0xa4,0xa2,0xc2,0x04,0x30,0xfa,0x02,0,0,0,0x6e,0x74,0x6b,0x72,
    0x6e,0x6c,0x6d,0x70,0x2e,0x70,0x64,0x62,0};
static const UINT8 original_guard[]={0x8b,0x84,0x24,0xf4,0,0,0,0x3d,0,0x02,0,0,0x76,0x14,
    0x48,0x8b,0x4c,0x24,0x60,0xe8,0xeb,0x44,0xcb,0xff,0xbf,0x18,0x02,0,0xc0};
static const UINT8 sha2_gdr_rsds[]={0x52,0x53,0x44,0x53,0x62,0xf9,0x37,0x2e,0x99,0xd6,0x2c,0x49,
    0xaa,0xf3,0xf9,0xf4,0xe9,0x77,0x0b,0x1d,0x02,0,0,0,0x6e,0x74,0x6b,0x72,
    0x6e,0x6c,0x6d,0x70,0x2e,0x70,0x64,0x62,0};
static const UINT8 sha2_gdr_guard[]={0x8b,0x84,0x24,0xf4,0,0,0,0x3d,0,0x02,0,0,0x76,0x14,
    0x48,0x8b,0x4c,0x24,0x60,0xe8,0x8b,0x89,0xca,0xff,0xbf,0x18,0x02,0,0xc0};
static const UINT8 sha2_ldr_rsds[]={0x52,0x53,0x44,0x53,0xa4,0x0c,0x28,0x76,0x95,0x9d,0xad,0x49,
    0x90,0x30,0xaa,0xb3,0xb2,0xbb,0x97,0xb3,0x02,0,0,0,0x6e,0x74,0x6b,0x72,
    0x6e,0x6c,0x6d,0x70,0x2e,0x70,0x64,0x62,0};
static const UINT8 sha2_ldr_guard[]={0x8b,0x84,0x24,0xf4,0,0,0,0x3d,0,0x02,0,0,0x76,0x14,
    0x48,0x8b,0x4c,0x24,0x60,0xe8,0xdb,0x6e,0xca,0xff,0xbf,0x18,0x02,0,0xc0};
typedef struct { UINT32 timestamp,size,entry,rsds_rva,guard_rva; const UINT8*rsds; const UINT8*guard; } KERNEL_PROFILE;
static const KERNEL_PROFILE profiles[]={
    {0x4ce7951a,0x5ea000,0x2b66f0,0x1a32cc,0x3c48bd,original_rsds,original_guard},
    {0x54d0317d,0x5e7000,0x2b46f0,0x1a220c,0x3c4c9d,sha2_gdr_rsds,sha2_gdr_guard},
    {0x54d030c3,0x5e6000,0x2b36f0,0x1a1d2c,0x3c570d,sha2_ldr_rsds,sha2_ldr_guard}
};
#define MIN_KERNEL_SIZE 0x5e6000

static BOOLEAN equal(const UINT8*a,const UINT8*b,UINTN n){while(n--)if(*a++!=*b++)return FALSE;return TRUE;}
static UINT32 read32(const UINT8*p){return p[0]|((UINT32)p[1]<<8)|((UINT32)p[2]<<16)|((UINT32)p[3]<<24);}
static const KERNEL_PROFILE* tested_kernel(UINT8*p,UINT64 available){
    if(available<MIN_KERNEL_SIZE || p[0]!='M' || p[1]!='Z')return NULL;
    UINT32 off=read32(p+0x3c);if(off>0x1000)return NULL;
    UINT8*pe=p+off;
    if(read32(pe)!=0x4550 || pe[4]!=0x64 || pe[5]!=0x86 || pe[24]!=0x0b || pe[25]!=0x02)return NULL;
    for(UINTN i=0;i<sizeof(profiles)/sizeof(profiles[0]);i++){
        const KERNEL_PROFILE*k=&profiles[i];
        if(available<k->size || read32(pe+8)!=k->timestamp || read32(pe+24+56)!=k->size || read32(pe+24+16)!=k->entry)continue;
        if(equal(p+k->rsds_rva,k->rsds,sizeof(original_rsds)) && equal(p+k->guard_rva,k->guard,sizeof(original_guard)))return k;
    }
    return NULL;
}
static VOID restore_exit(VOID){
    BS->ExitBootServices=saved_exit;
    BS->Hdr.CRC32=0;BS->CalculateCrc32(BS,BS->Hdr.HeaderSize,&BS->Hdr.CRC32);
}
static EFI_STATUS EFIAPI sector_exit(EFI_HANDLE image,UINTN key){
    UINTN length=sizeof(map_buffer),actual_key=0,size=0;UINT32 version=0;
    EFI_STATUS status=BS->GetMemoryMap(&length,(EFI_MEMORY_DESCRIPTOR*)map_buffer,&actual_key,&size,&version);
    if(EFI_ERROR(status)||size<sizeof(EFI_MEMORY_DESCRIPTOR))return EFI_UNSUPPORTED;
    UINT8*kernel=NULL;const KERNEL_PROFILE*profile=NULL;UINTN count=0;
    for(UINTN off=0;off+size<=length;off+=size){
        EFI_MEMORY_DESCRIPTOR*d=(EFI_MEMORY_DESCRIPTOR*)(map_buffer+off);
        if(d->Type!=EfiLoaderCode && d->Type!=EfiLoaderData && d->Type!=EfiBootServicesCode && d->Type!=EfiBootServicesData)continue;
        if(d->NumberOfPages>(~(UINT64)0)/4096)continue;
        UINT64 bytes=d->NumberOfPages*4096;
        if(bytes<MIN_KERNEL_SIZE || d->PhysicalStart>(~(UINT64)0)-bytes)continue;
        for(UINT64 page=0;page<=bytes-MIN_KERNEL_SIZE;page+=4096){
            UINT8*p=(UINT8*)(UINTN)(d->PhysicalStart+page);
            const KERNEL_PROFILE*found=tested_kernel(p,bytes-page);
            if(found){kernel=p;profile=found;count++;}
        }
    }
    if(count!=1){
        Print(L"GoAwayBCA: this Windows kernel is not covered by the 4Kn test profile.\r\nNo kernel adjustment was applied.\r\n");
        return EFI_UNSUPPORTED;
    }
    /* The accepted branch already computes the hive cluster factor from the
       actual BytesPerSector/512. Change only cmp eax,0x200 -> cmp eax,0x1000. */
    kernel[profile->guard_rva+8]=0;kernel[profile->guard_rva+9]=0x10;
    restore_exit();
    return saved_exit(image,key);
}

static EFI_STATUS next_path(EFI_LOADED_IMAGE*loaded,CHAR16*out,UINTN capacity){
    EFI_DEVICE_PATH*node=loaded->FilePath;
    UINTN used=0,last=0,nodes=0;
    while(node && !IsDevicePathEnd(node)){
        if(++nodes>8 || DevicePathType(node)!=MEDIA_DEVICE_PATH || DevicePathSubType(node)!=MEDIA_FILEPATH_DP)return EFI_UNSUPPORTED;
        UINTN bytes=DevicePathNodeLength(node);
        if(bytes<6 || bytes%2 || bytes>4096)return EFI_UNSUPPORTED;
        FILEPATH_DEVICE_PATH*f=(FILEPATH_DEVICE_PATH*)node;
        UINTN chars=(bytes-4)/2;
        if(f->PathName[chars-1]!=0)return EFI_UNSUPPORTED;
        for(UINTN i=0;i+1<chars;i++){
            CHAR16 c=f->PathName[i];
            if(!c || used+1>=capacity)return EFI_UNSUPPORTED;
            out[used++]=c;
            if(c==L'\\')last=used;
        }
        node=NextDevicePathNode(node);
    }
    if(!used || !last || out[0]!=L'\\')return EFI_UNSUPPORTED;
    static const CHAR16 name[]=L"SectorNext.efi";
    UINTN n=sizeof(name)/sizeof(name[0]);
    if(last+n>capacity)return EFI_UNSUPPORTED;
    for(UINTN i=0;i<n;i++)out[last+i]=name[i];
    return EFI_SUCCESS;
}

EFI_STATUS EFIAPI efi_main(EFI_HANDLE image,EFI_SYSTEM_TABLE*table){
    InitializeLib(image,table);
    EFI_LOADED_IMAGE*loaded=NULL;EFI_BLOCK_IO*block=NULL;
    EFI_GUID image_guid=LOADED_IMAGE_PROTOCOL,block_guid=BLOCK_IO_PROTOCOL;
    EFI_STATUS status=BS->HandleProtocol(image,&image_guid,(VOID**)&loaded);
    if(EFI_ERROR(status))return status;
    status=BS->HandleProtocol(loaded->DeviceHandle,&block_guid,(VOID**)&block);
    if(EFI_ERROR(status)||!block->Media || block->Media->BlockSize!=4096){
        Print(L"GoAwayBCA: the Windows 7 4Kn route requires a 4096-byte logical-sector drive.\r\n");return EFI_UNSUPPORTED;
    }
    CHAR16 file[512];status=next_path(loaded,file,512);
    if(EFI_ERROR(status))return status;
    EFI_DEVICE_PATH*path=FileDevicePath(loaded->DeviceHandle,file);
    if(!path)return EFI_OUT_OF_RESOURCES;
    EFI_HANDLE child=NULL;status=BS->LoadImage(FALSE,image,path,NULL,0,&child);FreePool(path);
    if(EFI_ERROR(status)){Print(L"GoAwayBCA: the next Windows boot component could not be loaded.\r\n");return status;}
    saved_exit=BS->ExitBootServices;BS->ExitBootServices=sector_exit;
    BS->Hdr.CRC32=0;BS->CalculateCrc32(BS,BS->Hdr.HeaderSize,&BS->Hdr.CRC32);
    status=BS->StartImage(child,NULL,NULL);
    restore_exit();
    return status;
}
