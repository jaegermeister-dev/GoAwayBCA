// Pure GPT validation for a future boot-store write guard. No disk I/O/writes.
// Uses actual 512-byte or 4096-byte logical sectors, never bus/model guesses.
// Both GPT copies and the protective MBR must agree; hybrid MBR is never valid.
const std=@import("std");
pub const Guid=[16]u8;
pub const Header=struct {
    current: u64, other: u64, first: u64, last: u64, disk: Guid,
    table_lba: u64, count: u32, entry_size: u32, table_crc: u32,
    pub fn tableBytes(self: Header) usize { return @as(usize,self.count)*self.entry_size; }
};
pub const Partition=struct { number: u32, first: u64, last: u64, kind: Guid, guid: Guid };
fn n32(bytes: []const u8,at: usize) u32 { return std.mem.readInt(u32,bytes[at..][0..4],.little); }
fn n64(bytes: []const u8,at: usize) u64 { return std.mem.readInt(u64,bytes[at..][0..8],.little); }
pub fn protective(mbr: []const u8,last_block: u64) !void {
    if ((mbr.len != 512 and mbr.len != 4096) or !std.mem.eql(u8,mbr[510..512],&.{0x55,0xaa}) or last_block < 67)
        return error.InvalidProtectiveMbr;
    var count: usize=0;
    for (0..4) |i| {
        const e=mbr[446+i*16..][0..16];
        if (e[4] == 0xee) {
            count+=1;
            if (e[0] != 0 or n32(e,8) != 1 or n32(e,12) != @min(last_block,0xffffffff))
                return error.InvalidProtectiveCoverage;
        } else if (!std.mem.allEqual(u8,e,0)) return error.HybridMbrNotSupported;
    }
    if (count != 1) return error.HybridMbrNotSupported;
}
pub fn header(bytes: []const u8,lba: u64,last_block: u64) !Header {
    if ((bytes.len != 512 and bytes.len != 4096) or !std.mem.eql(u8,bytes[0..8],"EFI PART") or
        n32(bytes,8) != 0x10000 or n32(bytes,20) != 0) return error.InvalidGptHeader;
    const size=n32(bytes,12);
    if (size < 92 or size > bytes.len) return error.InvalidGptHeader;
    var body: [4096]u8=undefined;@memcpy(body[0..bytes.len],bytes);@memset(body[16..20],0);
    if (std.hash.Crc32.hash(body[0..size]) != n32(bytes,16)) return error.GptHeaderChecksum;
    const result: Header=.{.current=n64(bytes,24),.other=n64(bytes,32),.first=n64(bytes,40),
        .last=n64(bytes,48),.disk=bytes[56..72].*,.table_lba=n64(bytes,72),
        .count=n32(bytes,80),.entry_size=n32(bytes,84),.table_crc=n32(bytes,88)};
    if (result.current != lba or result.first < 2 or result.first > result.last or result.last >= last_block or
        std.mem.allEqual(u8,&result.disk,0) or last_block > std.math.maxInt(u64)/bytes.len-1)
        return error.InvalidGptBounds;
    if (result.count == 0 or result.count > 4096 or result.entry_size < 128 or result.entry_size > 1024 or
        !std.math.isPowerOfTwo(result.entry_size)) return error.InvalidGptTable;
    const sectors=(result.tableBytes()+bytes.len-1)/bytes.len;
    if (result.table_lba > last_block or sectors > last_block-result.table_lba+1) return error.InvalidGptTable;
    const end=result.table_lba+sectors;
    if (lba == 1) {
        if (result.other != last_block or result.table_lba < 2 or end > result.first) return error.GptMetadataOverlap;
    } else {
        if (lba != last_block or result.other != 1 or result.table_lba <= result.last or end > lba)
            return error.GptMetadataOverlap;
    }
    return result;
}
pub fn validate(mbr: []const u8,primary: []const u8,entries: []const u8,
    backup: []const u8,backup_entries: []const u8,last_block: u64,disk: *const Guid,part: *const Guid) !Partition {
    if (primary.len != backup.len or mbr.len != primary.len) return error.GptSectorSizeMismatch;
    try protective(mbr,last_block);
    const a=try header(primary,1,last_block);const b=try header(backup,last_block,last_block);
    if (!std.mem.eql(u8,&a.disk,disk)) return error.WrongGptDisk;
    if (!std.mem.eql(u8,&a.disk,&b.disk) or a.first != b.first or a.last != b.last or
        a.count != b.count or a.entry_size != b.entry_size or entries.len != a.tableBytes() or
        !std.mem.eql(u8,entries,backup_entries)) return error.GptCopiesDisagree;
    if (std.hash.Crc32.hash(entries) != a.table_crc or std.hash.Crc32.hash(backup_entries) != b.table_crc)
        return error.GptTableChecksum;
    var selected: ?Partition=null;
    for (0..a.count) |i| {
        const row=entries[i*a.entry_size..][0..128];
        if (std.mem.allEqual(u8,row[0..16],0)) continue;
        if (std.mem.allEqual(u8,row[16..32],0) or std.mem.eql(u8,row[16..32],&a.disk)) return error.DuplicateGptIdentity;
        const first=n64(row,32);const last=n64(row,40);
        if (first < a.first or first > last or last > a.last) return error.InvalidPartitionBounds;
        for (0..i) |j| {
            const prior=entries[j*a.entry_size..][0..128];
            if (std.mem.allEqual(u8,prior[0..16],0)) continue;
            if (std.mem.eql(u8,row[16..32],prior[16..32])) return error.DuplicateGptIdentity;
            if (first <= n64(prior,40) and last >= n64(prior,32)) return error.OverlappingGptPartitions;
        }
        if (std.mem.eql(u8,row[16..32],part)) selected=.{.number=@intCast(i+1),.first=first,.last=last,
            .kind=row[0..16].*,.guid=row[16..32].*};
    }
    return selected orelse error.MissingGptPartition;
}

const fixture=@embedFile("gpt-synthetic-headers.bin");
const fixture_disk: Guid=.{0x1c,0x42,0x0d,0xcf,0xa1,0xe6,0x50,0x49,0x93,0xbe,0xcc,0xdd,0x81,0x1a,0xcd,0x0b};
const fixture_part: Guid=.{0xba,0x3b,0x7c,0x6a,0x80,0x13,0x21,0x4e,0xb6,0x85,0x9d,0x02,0x93,0x77,0xb9,0x6a};
fn checkFixture(bytes: []const u8) !Partition {
    return validate(bytes[0..512],bytes[512..1024],bytes[1024..17408],
        bytes[17408..17920],bytes[17920..34304],528383,&fixture_disk,&fixture_part);
}
test "both GPT copies and protective MBR from synthetic disk" {
    const entry=try checkFixture(fixture);
    try std.testing.expectEqual(@as(u32,1),entry.number);
    try std.testing.expectEqual(@as(u64,2048),entry.first);
    try std.testing.expectEqual(@as(u64,526335),entry.last);
}
test "4096-byte GPT uses logical sector units and retains corruption guards" {
    var mbr: [4096]u8=@splat(0);@memcpy(mbr[0..512],fixture[0..512]);
    var primary: [4096]u8=@splat(0);@memcpy(primary[0..512],fixture[512..1024]);
    var backup: [4096]u8=@splat(0);@memcpy(backup[0..512],fixture[17408..17920]);
    std.mem.writeInt(u64,backup[72..80],528383-4,.little);sealTestHeader(&backup);
    // A four-sector table fits exactly before first usable LBA 6 on 4Kn.
    for ([_]*[4096]u8{&primary,&backup}) |h| {
        std.mem.writeInt(u64,h[40..48],6,.little);sealTestHeader(h);
    }
    const entries=fixture[1024..17408];
    const part=try validate(&mbr,&primary,entries,&backup,entries,528383,&fixture_disk,&fixture_part);
    try std.testing.expectEqual(@as(u64,2048),part.first);
    try std.testing.expectError(error.GptSectorSizeMismatch,validate(mbr[0..512],&primary,entries,&backup,entries,528383,&fixture_disk,&fixture_part));
    mbr[466]=7;
    try std.testing.expectError(error.HybridMbrNotSupported,validate(&mbr,&primary,entries,&backup,entries,528383,&fixture_disk,&fixture_part));
    mbr[466]=0;primary[24]^=1;
    try std.testing.expectError(error.GptHeaderChecksum,validate(&mbr,&primary,entries,&backup,entries,528383,&fixture_disk,&fixture_part));
}
test "legacy hybrid entry, stale coverage and corrupt tables rejected" {
    var changed=fixture.*;changed[466]=7;
    try std.testing.expectError(error.HybridMbrNotSupported,checkFixture(&changed));
    changed=fixture.*;changed[458]^=1;
    try std.testing.expectError(error.InvalidProtectiveCoverage,checkFixture(&changed));
    changed=fixture.*;changed[1024]^=1;
    try std.testing.expectError(error.GptCopiesDisagree,checkFixture(&changed));
    changed=fixture.*;changed[1024]^=1;changed[17920]^=1;
    try std.testing.expectError(error.GptTableChecksum,checkFixture(&changed));
    changed=fixture.*;changed[512+72]^=1;
    try std.testing.expectError(error.GptHeaderChecksum,checkFixture(&changed));
}
fn sealTestHeader(bytes: []u8) void {
    @memset(bytes[16..20],0);
    std.mem.writeInt(u32,bytes[16..20],std.hash.Crc32.hash(bytes[0..n32(bytes,12)]),.little);
}
fn sealTestTables(bytes: []u8) void {
    @memcpy(bytes[17920..34304],bytes[1024..17408]);
    const crc=std.hash.Crc32.hash(bytes[1024..17408]);
    for ([_]usize{512,17408}) |offset| {
        std.mem.writeInt(u32,bytes[offset+88..][0..4],crc,.little);
        sealTestHeader(bytes[offset..][0..512]);
    }
}
test "valid checksums do not hide duplicate GUIDs, overlaps or metadata bounds" {
    var changed=fixture.*;
    @memcpy(changed[1152..1280],changed[1024..1152]);sealTestTables(&changed);
    try std.testing.expectError(error.DuplicateGptIdentity,checkFixture(&changed));
    changed[1152+16]^=1;sealTestTables(&changed);
    try std.testing.expectError(error.OverlappingGptPartitions,checkFixture(&changed));
    changed=fixture.*;std.mem.writeInt(u64,changed[512+72..][0..8],2048,.little);sealTestHeader(changed[512..1024]);
    try std.testing.expectError(error.GptMetadataOverlap,checkFixture(&changed));
    changed=fixture.*;std.mem.writeInt(u32,changed[512+80..][0..4],0xffffffff,.little);sealTestHeader(changed[512..1024]);
    try std.testing.expectError(error.InvalidGptTable,checkFixture(&changed));
}
