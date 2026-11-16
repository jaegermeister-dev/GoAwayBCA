// Data-only counterpart of store_configuration.py. Never opens or writes files.
// Exact menu and ordered disk/partition identities bind transaction state, so a
// new menu cannot accidentally reuse an active index from the previous order.
const std=@import("std");
const menu_parser=@import("chooser_parse.zig");
const registry=@import("boot_store_parse.zig");
const semantics=@import("boot_store_identity.zig");
pub const header_size=112;
pub const record_size=32;
pub const Config=struct {
    count: usize,
    menu_digest: [32]u8,
    digest: [32]u8,
    identities: [menu_parser.max_entries]semantics.Identity,
};
fn hash(bytes: []const u8) [32]u8 {
    var value: [32]u8=undefined;
    std.crypto.hash.sha2.Sha256.hash(bytes,&value,.{});return value;
}
fn checksum(bytes: []const u8) [32]u8 {
    var h=std.crypto.hash.sha2.Sha256.init(.{});
    h.update(bytes[0..80]);h.update(bytes[header_size..]);return h.finalResult();
}
pub fn parse(bytes: []const u8, menu_bytes: []const u8) !Config {
    if (bytes.len < header_size or !std.mem.eql(u8,bytes[0..8],"W7DEPC01")) return error.BadStoreHeader;
    const version=std.mem.readInt(u32,bytes[8..12],.little);
    const count=std.mem.readInt(u32,bytes[12..16],.little);
    if (version != 1 or count == 0 or count > menu_parser.max_entries or
        bytes.len != header_size + count*record_size) return error.BadStoreLength;
    if (!std.mem.eql(u8,bytes[80..112],&checksum(bytes))) return error.BadStoreChecksum;
    if (!std.mem.eql(u8,bytes[48..80],&hash(menu_bytes))) return error.DifferentStoreMenu;
    const menu=try menu_parser.parse(menu_bytes);
    if (menu.count != count) return error.StoreCountMismatch;
    if (std.mem.allEqual(u8,bytes[16..32],0) or std.mem.allEqual(u8,bytes[32..48],0)) return error.ZeroStoreGuid;
    var result: Config=.{.count=count,.menu_digest=bytes[48..80].*,.digest=hash(bytes),.identities=undefined};
    for (0..count) |i| {
        const row=bytes[header_size+i*record_size..][0..record_size];
        if (std.mem.allEqual(u8,row[0..16],0) or std.mem.allEqual(u8,row[16..32],0)) return error.ZeroStoreGuid;
        const id: semantics.Identity=.{.esp_disk=bytes[16..32].*,.esp=bytes[32..48].*,
            .windows_disk=row[0..16].*,.windows=row[16..32].*};
        const entry=menu.entries[i];
        if (std.mem.eql(u8,&id.windows,&id.esp)) return error.WindowsIsEsp;
        if (!std.mem.eql(u8,&entry.windows_guid,&id.windows)) return error.StoreOrderMismatch;
        const on_windows=std.mem.eql(u8,&entry.boot_guid,&id.windows);
        if ((!on_windows and !std.mem.eql(u8,&entry.boot_guid,&id.esp)) or
            entry.ntfs() != on_windows) return error.ForeignStoreLoader;
        for (result.identities[0..i]) |prior| {
            if (std.mem.eql(u8,&prior.windows,&id.windows)) return error.DuplicateStoreGuid;
        }
        result.identities[i]=id;
    }
    return result;
}

// Heap allocation matters in firmware: the registry cell map is 128 KiB.
// Validate current BCD contents, not a saved hash; Windows legitimately services
// its own store. Validation still confines every device/reference to its OS.
pub const Validation=struct {
    config: *const Config,
    allocator: std.mem.Allocator,
    pub fn validate(self: *Validation,bytes: []const u8,index: u32) !void {
        if (index >= self.config.count) return error.UnknownStoreIndex;
        const hive=try self.allocator.create(registry.Hive);
        defer self.allocator.destroy(hive);
        try hive.initInto(bytes);
        try semantics.validate(hive,&self.config.identities[index]);
    }
};

test "Python serializer fixture and real BCD share exact identity" {
    const config=try parse(@embedFile("store-config-fixture.bin"),@embedFile("store-menu-fixture.bin"));
    try std.testing.expectEqual(@as(usize,1),config.count);
    var validator: Validation=.{.config=&config,.allocator=std.testing.allocator};
    try validator.validate(@embedFile("BCD10.test"),0);
    try std.testing.expectError(error.UnknownStoreIndex,validator.validate(@embedFile("BCD10.test"),1));
    var different=config;different.identities[0].windows[0] ^= 1;
    validator.config=&different;
    try std.testing.expectError(error.ForeignWindowsVolume,validator.validate(@embedFile("BCD10.test"),0));
}

test "all configuration bytes and lengths are checked" {
    const original=@embedFile("store-config-fixture.bin");
    const menu=@embedFile("store-menu-fixture.bin");
    for (0..original.len) |i| {
        var changed=original.*;changed[i] ^= 1;
        if (parse(&changed,menu)) |_| return error.AcceptedCorruption else |_| {}
        if (parse(original[0..i],menu)) |_| return error.AcceptedTruncation else |_| {}
    }
    var other_menu=menu.*;other_menu[other_menu.len-1] ^= 1;
    try std.testing.expectError(error.DifferentStoreMenu,parse(original,&other_menu));
}

test "resealed wrong identity and zero GUID still fail semantics" {
    const original=@embedFile("store-config-fixture.bin");
    const menu=@embedFile("store-menu-fixture.bin");
    var changed=original.*;changed[header_size+16] ^= 1;
    @memcpy(changed[80..112],&checksum(&changed));
    try std.testing.expectError(error.StoreOrderMismatch,parse(&changed,menu));
    changed=original.*;@memset(changed[header_size..header_size+16],0);
    @memcpy(changed[80..112],&checksum(&changed));
    try std.testing.expectError(error.ZeroStoreGuid,parse(&changed,menu));
}
