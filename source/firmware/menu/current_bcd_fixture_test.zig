// Local read-only acceptance evidence; never imported into shipped firmware.
const std = @import("std");
const registry = @import("boot_store_parse.zig");
const semantics = @import("boot_store_identity.zig");

test "current physical Win7 BCD snapshot accepted without modification" {
    const data = @embedFile("real-efi-readonly-20261010/canonical-win7-readonly.bcd");
    var digest: [32]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(data, &digest, .{});
    var expected: [32]u8 = undefined;
    _ = try std.fmt.hexToBytes(&expected, "b7b394069e7a5b2cf26570fcae7767d7d4e031bc31b2c0cc1bfc0e8c63616053");
    try std.testing.expectEqualSlices(u8, &expected, &digest);
    const hive = try std.testing.allocator.create(registry.Hive);
    defer std.testing.allocator.destroy(hive);
    try hive.initInto(data);
    var id: semantics.Identity = .{
        .esp_disk=.{0x6b,0xc0,0xb3,0xa7,0xbd,0x74,0xc3,0x46,0x8a,0x28,0x29,0x92,0x19,0x64,0xc9,0xee},
        .esp=.{0x10,0xbe,0xf7,0xf5,0xe6,0x32,0x8e,0x41,0xbd,0x32,0x0b,0x56,0x74,0xd7,0xef,0xa7},
        .windows_disk=undefined,
        .windows=.{0x85,0xf4,0x76,0x1f,0x80,0x25,0x2b,0x4d,0x92,0x4a,0x2c,0x8e,0xa0,0x68,0x66,0x20}};
    id.windows_disk = id.esp_disk;
    try semantics.validate(hive, &id);
    id.windows[0] ^= 1;
    try std.testing.expectError(error.ForeignWindowsVolume, semantics.validate(hive, &id));
}
