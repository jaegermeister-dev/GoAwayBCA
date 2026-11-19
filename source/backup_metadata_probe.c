/* SPDX-License-Identifier: GPL-3.0-or-later
 * Read-only NTFS metadata preflight for conversion through a WIM image.
 * Raw ntfsclone backup does not need this conversion or lose these attributes.
 * No writes, repair, forced mount, Windows installation or boot changes.
 */
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ntfs-3g/attrib.h>
#include <ntfs-3g/endians.h>
#include <ntfs-3g/inode.h>
#include <ntfs-3g/volume.h>

int main(int argc, char **argv) {
    ntfs_volume *volume = NULL;
    unsigned char bitmap[8192];
    uint64_t inspected=0, encrypted=0, ea=0, wof=0;
    int result=1;
    if (argc != 2) { fprintf(stderr,"Usage: backup-metadata-probe NTFS_VOLUME_IMAGE\n"); return 2; }
    volume=ntfs_mount(argv[1], NTFS_MNT_RDONLY);
    if (!volume) { perror("Cannot inspect NTFS read-only"); return 1; }
    if (!volume->mft_na || !volume->mftbmp_na || !volume->mft_record_size ||
        volume->mft_na->data_size<=0 || volume->mftbmp_na->data_size<=0) goto done;
    uint64_t records=(uint64_t)volume->mft_na->data_size/volume->mft_record_size;
    if (records>UINT64_C(1)<<32 || (records+7)/8>(uint64_t)volume->mftbmp_na->data_size) goto done;
    for (uint64_t offset=0; offset<(records+7)/8; offset+=sizeof(bitmap)) {
        s64 amount=(s64)((records+7)/8-offset);
        if (amount>(s64)sizeof(bitmap)) amount=sizeof(bitmap);
        if (ntfs_attr_pread(volume->mftbmp_na,(s64)offset,amount,bitmap)!=amount) goto done;
        for (s64 byte=0; byte<amount; byte++) for (unsigned bit=0; bit<8; bit++) {
            uint64_t number=(offset+(uint64_t)byte)*8+bit;
            if (number>=records || !(bitmap[byte]&(1u<<bit))) continue;
            ntfs_inode *inode=ntfs_inode_open(volume,number);
            if (!inode) goto done;
            /* Attribute extension records are visited via their base inode. */
            if (inode->mrec->base_mft_record) { ntfs_inode_close(inode); continue; }
            ntfs_attr_search_ctx *ctx=ntfs_attr_get_search_ctx(inode,NULL);
            if (!ctx) { ntfs_inode_close(inode); goto done; }
            int has_ea=0,has_encryption=(inode->flags&FILE_ATTR_ENCRYPTED)!=0,has_wof=0;
            errno=0;
            while (ntfs_attrs_walk(ctx)==0) {
                if (ctx->attr->type==AT_EA || ctx->attr->type==AT_EA_INFORMATION) has_ea=1;
                if (ctx->attr->flags&ATTR_IS_ENCRYPTED) has_encryption=1;
                if (ctx->attr->type==AT_REPARSE_POINT) {
                    ntfs_attr *attribute=ntfs_attr_open(inode,AT_REPARSE_POINT,AT_UNNAMED,0);
                    le32 tag;
                    if (!attribute || ntfs_attr_pread(attribute,0,sizeof(tag),&tag)!=sizeof(tag)) {
                        if(attribute)ntfs_attr_close(attribute);
                        ntfs_attr_put_search_ctx(ctx);ntfs_inode_close(inode);goto done;
                    }
                    if(le32_to_cpu(tag)==0x80000017u)has_wof=1;
                    ntfs_attr_close(attribute);
                }
            }
            int ended=errno==ENOENT;
            ntfs_attr_put_search_ctx(ctx);
            if(ntfs_inode_close(inode) || !ended)goto done;
            inspected++;ea+=has_ea;encrypted+=has_encryption;wof+=has_wof;
        }
    }
    printf("{\"schema\":1,\"read_only\":true,\"records_inspected\":%" PRIu64
           ",\"encrypted_records\":%" PRIu64 ",\"extended_attribute_records\":%" PRIu64
           ",\"externally_backed_wof_records\":%" PRIu64 ",\"conversion_allowed\":%s}\n",
           inspected,encrypted,ea,wof,(!encrypted&&!ea&&!wof)?"true":"false");
    result=0;
done:
    if(result)fprintf(stderr,"NTFS metadata inspection did not complete; do not convert this backup.\n");
    if(ntfs_umount(volume,FALSE))result=1;
    return result;
}
