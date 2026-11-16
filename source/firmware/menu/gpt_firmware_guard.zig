// READ ONLY. Verify the loaded ESP's actual parent GPT, not just path labels.
// No reset, writeBlocks, NVRAM, filesystem or partition-changing operation.
const std=@import("std");
const u=std.os.uefi;
const gpt=@import("gpt_readonly.zig");
const Block=u.protocol.BlockIo;
const Aligned=[]align(4096)u8;
const Path=struct { raw: []const u8, parent_length: usize, number: u32, first: u64, sectors: u64, guid: gpt.Guid };
fn pathInfo(dp: *const u.protocol.DevicePath) !Path {
    const raw: [*]const u8=@ptrCast(dp);
    var offset: usize=0;var result: ?Path=null;
    while (offset+4 <= 4096) {
        const size=std.mem.readInt(u16,raw[offset+2..][0..2],.little);
        if (size < 4 or size > 4096-offset) return error.InvalidDiskDevicePath;
        if (raw[offset] == 0x7f) {
            if (raw[offset+1] != 0xff or size != 4) return error.InvalidDiskDevicePath;
            var found=result orelse return error.NoPartitionDevicePath;
            found.raw=raw[0..offset+4];return found;
        }
        if (result != null) return error.TrailingPartitionDevicePath;
        if (raw[offset] == 4 and raw[offset+1] == 1) {
            if (size != 42 or raw[offset+40] != 2 or raw[offset+41] != 2) return error.NotGptDevicePath;
            result=.{.raw=undefined,.parent_length=offset,
                .number=std.mem.readInt(u32,raw[offset+4..][0..4],.little),
                .first=std.mem.readInt(u64,raw[offset+8..][0..8],.little),
                .sectors=std.mem.readInt(u64,raw[offset+16..][0..8],.little),
                .guid=raw[offset+24..][0..16].*};
        }
        offset+=size;
    }
    return error.InvalidDiskDevicePath;
}
fn parentMatches(dp: *const u.protocol.DevicePath,child: *const Path) bool {
    const raw: [*]const u8=@ptrCast(dp);
    var offset: usize=0;
    while (offset+4 <= 4096) {
        const size=std.mem.readInt(u16,raw[offset+2..][0..2],.little);
        if (size < 4 or size > 4096-offset) return false;
        if (raw[offset] == 0x7f) return raw[offset+1] == 0xff and size == 4 and
            offset == child.parent_length and std.mem.eql(u8,raw[0..offset],child.raw[0..offset]);
        if (offset >= child.parent_length) return false;
        offset+=size;
    }
    return false;
}
fn read(block: *Block,media_id: u32,lba: u64,bytes: usize) !Aligned {
    const media=block.media;
    const sector: usize=media.block_size;
    if ((sector != 512 and sector != 4096) or bytes == 0 or bytes % sector != 0 or
        media.media_id != media_id or !media.media_present or lba > media.last_block or
        bytes/sector > media.last_block-lba+1 or media.io_align > 4096 or
        (media.io_align > 1 and !std.math.isPowerOfTwo(media.io_align))) return error.UnsafeGptRead;
    const data=try u.pool_allocator.alignedAlloc(u8,.fromByteUnits(4096),bytes);
    errdefer u.pool_allocator.free(data);
    try block.readBlocks(media_id,lba,data);
    if (block.media.media_id != media_id or block.media.block_size != sector or !block.media.media_present) return error.GptMediaChanged;
    return data;
}
pub const esp_type=gpt.Guid{0x28,0x73,0x2a,0xc1,0x1f,0xf8,0xd2,0x11,0xba,0x4b,0x00,0xa0,0xc9,0x3e,0xc9,0x3b};
pub const basic_type=gpt.Guid{0xa2,0xa0,0xd0,0xeb,0xe5,0xb9,0x33,0x44,0x87,0xc0,0x68,0xb6,0xb7,0x26,0x99,0xc7};
pub fn verifyLoadedEsp(disk: *const gpt.Guid,part: *const gpt.Guid) !void {
    const bs=u.system_table.boot_services orelse return error.NoBootServices;
    const loaded=(try bs.handleProtocol(u.protocol.LoadedImage,u.handle)) orelse return error.NoImage;
    const own=loaded.device_handle orelse return error.NoDevice;
    return verifyPartition(own,disk,part,&esp_type);
}
pub fn verifyPartition(own: u.Handle,disk: *const gpt.Guid,part: *const gpt.Guid,kind: *const gpt.Guid) !void {
    const bs=u.system_table.boot_services orelse return error.NoBootServices;
    const own_block=(try bs.handleProtocol(Block,own)) orelse return error.NoPartitionBlock;
    if (!own_block.media.logical_partition or (own_block.media.block_size != 512 and own_block.media.block_size != 4096)) return error.NotPartitionBlock;
    const dp=(try bs.handleProtocol(u.protocol.DevicePath,own)) orelse return error.NoDiskDevicePath;
    const child=try pathInfo(dp);
    if (!std.mem.eql(u8,&child.guid,part)) return error.WrongEspDevicePath;
    const handles=(try bs.locateHandleBuffer(.{.by_protocol=&Block.guid})) orelse return error.NoBlocks;
    defer bs.freePool(@alignCast(@ptrCast(handles.ptr))) catch {};
    var parent: ?*Block=null;
    for (handles) |handle| {
        const block=(try bs.handleProtocol(Block,handle)) orelse continue;
        if (block.media.logical_partition) continue;
        const parent_path=(try bs.handleProtocol(u.protocol.DevicePath,handle)) orelse continue;
        if (parentMatches(parent_path,&child)) {
            if (parent != null) return error.AmbiguousParentDisk;
            parent=block;
        }
    }
    const block=parent orelse return error.NoParentDisk;
    const media_id=block.media.media_id;const last=block.media.last_block;
    const sector: usize=block.media.block_size;
    if (sector != own_block.media.block_size) return error.PartitionSectorSizeMismatch;
    if (last < 67 or last > std.math.maxInt(u64)/sector-1) return error.InvalidDiskLength;
    const mbr=try read(block,media_id,0,sector);defer u.pool_allocator.free(mbr);
    try gpt.protective(mbr,last);
    const primary=try read(block,media_id,1,sector);defer u.pool_allocator.free(primary);
    const backup=try read(block,media_id,last,sector);defer u.pool_allocator.free(backup);
    const a=try gpt.header(primary,1,last);const b=try gpt.header(backup,last,last);
    const entries=try read(block,media_id,a.table_lba,(a.tableBytes()+sector-1)/sector*sector);
    defer u.pool_allocator.free(entries);
    const back_entries=try read(block,media_id,b.table_lba,(b.tableBytes()+sector-1)/sector*sector);
    defer u.pool_allocator.free(back_entries);
    const found=try gpt.validate(mbr,primary,entries[0..a.tableBytes()],backup,
        back_entries[0..b.tableBytes()],last,disk,part);
    if (!std.mem.eql(u8,&found.kind,kind) or found.number != child.number or
        found.first != child.first or found.last-found.first+1 != child.sectors or
        own_block.media.last_block != child.sectors-1) return error.EspGeometryMismatch;
    const fresh=try read(block,media_id,1,sector);defer u.pool_allocator.free(fresh);
    if (!std.mem.eql(u8,primary,fresh) or block.media.last_block != last or
        block.media.block_size != sector or own_block.media.block_size != sector) return error.GptChangedDuringRead;
}
