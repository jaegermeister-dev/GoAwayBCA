/* Win7 native, read-only storage detection for the running Windows volume.
 * No assumptions about C:, the EXE's location, Mac model, or SATA/NVMe.
 * Never reuse a previous session's sector result. */
#include <winioctl.h>
typedef struct {
    wchar_t windows[MAX_PATH], root[MAX_PATH], volume[MAX_PATH];
    DWORD logical, physical, disk;
    ULONGLONG partition_offset;
} WindowsStorageInfo;

static DWORD read_windows_storage(WindowsStorageInfo *info) {
    DWORD result=ERROR_INVALID_DATA, got=0, sectors=0, free_clusters=0, clusters=0;
    HANDLE volume=INVALID_HANDLE_VALUE, disk=INVALID_HANDLE_VALUE;
    wchar_t device[MAX_PATH], physical_path[64];
    VOLUME_DISK_EXTENTS extents;
    DISK_GEOMETRY geometry;
    NTFS_VOLUME_DATA_BUFFER ntfs;
    STORAGE_PROPERTY_QUERY query;
    STORAGE_ACCESS_ALIGNMENT_DESCRIPTOR alignment;
    ZeroMemory(info,sizeof(*info));
    UINT n=GetSystemWindowsDirectoryW(info->windows,MAX_PATH);
    if(!n || n>=MAX_PATH)return ERROR_INVALID_NAME;
    if(!GetVolumePathNameW(info->windows,info->root,MAX_PATH) ||
       !GetVolumeNameForVolumeMountPointW(info->root,info->volume,MAX_PATH) ||
       !GetDiskFreeSpaceW(info->root,&sectors,&info->logical,&free_clusters,&clusters))return GetLastError();
    n=(UINT)lstrlenW(info->volume);
    if(n<5 || n>=MAX_PATH || info->volume[n-1]!=L'\\')return ERROR_INVALID_NAME;
    lstrcpyW(device,info->volume);device[n-1]=0;
    volume=CreateFileW(device,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,NULL,OPEN_EXISTING,0,NULL);
    if(volume==INVALID_HANDLE_VALUE)return GetLastError();
    if(!DeviceIoControl(volume,IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS,NULL,0,&extents,sizeof(extents),&got,NULL)){
        result=GetLastError();goto done;
    }
    if(extents.NumberOfDiskExtents!=1 || got<sizeof(extents) || extents.Extents[0].StartingOffset.QuadPart<0)goto done;
    if(!DeviceIoControl(volume,FSCTL_GET_NTFS_VOLUME_DATA,NULL,0,&ntfs,sizeof(ntfs),&got,NULL)){
        result=GetLastError();goto done;
    }
    if(got<sizeof(ntfs) || ntfs.BytesPerSector!=info->logical)goto done;
    info->disk=extents.Extents[0].DiskNumber;
    info->partition_offset=(ULONGLONG)extents.Extents[0].StartingOffset.QuadPart;
    wsprintfW(physical_path,L"\\\\.\\PhysicalDrive%u",info->disk);
    disk=CreateFileW(physical_path,0,FILE_SHARE_READ|FILE_SHARE_WRITE,NULL,OPEN_EXISTING,0,NULL);
    if(disk==INVALID_HANDLE_VALUE){result=GetLastError();goto done;}
    if(!DeviceIoControl(disk,IOCTL_DISK_GET_DRIVE_GEOMETRY,NULL,0,&geometry,sizeof(geometry),&got,NULL)){
        result=GetLastError();goto done;
    }
    if(got<sizeof(geometry) || geometry.BytesPerSector!=info->logical ||
       (info->logical!=512 && info->logical!=4096) || info->partition_offset%info->logical)goto done;
    /* Some original Win7 storage stacks do not expose physical alignment.
       Logical-sector detection above remains mandatory and independent. */
    ZeroMemory(&query,sizeof(query));ZeroMemory(&alignment,sizeof(alignment));
    query.PropertyId=StorageAccessAlignmentProperty;query.QueryType=PropertyStandardQuery;
    if(DeviceIoControl(disk,IOCTL_STORAGE_QUERY_PROPERTY,&query,sizeof(query),&alignment,sizeof(alignment),&got,NULL)
       && got>=sizeof(alignment) && alignment.Size>=sizeof(alignment)){
        if(alignment.BytesPerLogicalSector!=info->logical)goto done;
        info->physical=alignment.BytesPerPhysicalSector;
    }
    result=ERROR_SUCCESS;
done:
    if(disk!=INVALID_HANDLE_VALUE)CloseHandle(disk);
    CloseHandle(volume);return result;
}
