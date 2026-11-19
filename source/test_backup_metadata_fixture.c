/* SPDX-License-Identifier: GPL-3.0-or-later
 * Test-only: mark a known synthetic file in a regular NTFS image. Never devices.
 * These are deliberately unsupported-metadata fixtures, not usable EFS/WOF files.
 */
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <ntfs-3g/attrib.h>
#include <ntfs-3g/dir.h>
#include <ntfs-3g/endians.h>
#include <ntfs-3g/inode.h>
#include <ntfs-3g/volume.h>

int main(int argc,char **argv) {
    struct stat observed;
    if(argc!=3 || lstat(argv[1],&observed) || !S_ISREG(observed.st_mode) ||
        observed.st_size!=64*1024*1024 ||
        (strcmp(argv[2],"ea") && strcmp(argv[2],"encrypted") && strcmp(argv[2],"wof"))) return 2;
    ntfs_volume *volume=ntfs_mount(argv[1],0);
    if(!volume)return 3;
    ntfs_inode *inode=ntfs_pathname_to_inode(volume,NULL,"/fixture.bin");
    if(!inode){ntfs_umount(volume,FALSE);return 4;}
    int result=0;
    if(!strcmp(argv[2],"encrypted")) {
        inode->flags|=FILE_ATTR_ENCRYPTED;NInoSetDirty(inode);
    } else if(!strcmp(argv[2],"ea")) {
        unsigned char info[8]={0};
        result=ntfs_attr_add(inode,AT_EA_INFORMATION,AT_UNNAMED,0,info,sizeof(info));
    } else {
        le32 reparse[2]={cpu_to_le32(0x80000017u),0};
        result=ntfs_attr_add(inode,AT_REPARSE_POINT,AT_UNNAMED,0,(unsigned char *)reparse,sizeof(reparse));
        inode->flags|=FILE_ATTR_REPARSE_POINT;NInoSetDirty(inode);
    }
    if(ntfs_inode_close(inode))result=1;
    if(ntfs_umount(volume,FALSE))result=1;
    return result?5:0;
}
