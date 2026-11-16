// Exact-profile, RAM-only experiment. NOT a generic Thunderbolt patch.
// Preserve firmware PCI boot resources below PEG1 by changing only its
// _DSM(UUID e5c937d0..., function 5) return from 1 to 0. PCI0._DSM is untouched.
// Offsets independently mapped by iasl -l with the captured DSDT externals.
const std = @import("std");
const p = @import("acpi_parse.zig");
pub const original = @embedFile("collected-capture-000/011-SSDT.dat");
pub const method_start = 0x55ef;
pub const method_end = 0x5621;
pub const changed_at = 0x561d;
pub const changed = blk: {
    var data = original.*;
    if (data.len != 34522 or data[changed_at] != 1 or
        data[changed_at - 1] != 0x0a or data[changed_at - 2] != 0xa4)
        @compileError("Unexpected Thunderbolt fixture");
    data[changed_at] = 0;
    data[9] +%= 1;
    break :blk data;
};

pub fn replaceRootLink(root: []u8, width: usize, old: u64, replacement: u64) !void {
    const n = try p.rootEntryCount(root, width);
    if (old == 0 or replacement == 0 or old == replacement or
        replacement > std.math.maxInt(u32)) return error.BadReplacement;
    var at: ?usize = null;
    for (0..n) |i| {
        const offset = 36 + width * i;
        const addr = if (width == 8) try p.u64le(root, offset) else try p.u32le(root, offset);
        if (addr == replacement) return error.DuplicateReplacement;
        if (addr == old) {
            if (at != null) return error.DuplicateOriginal;
            at = offset;
        }
    }
    const offset = at orelse return error.MissingOriginal;
    if (width == 8) std.mem.writeInt(u64, root[offset..][0..8], replacement, .little)
    else std.mem.writeInt(u32, root[offset..][0..4], @intCast(replacement), .little);
    root[9] = 0;
    var sum: u8 = 0;
    for (root) |v| sum +%= v;
    root[9] = 0 -% sum;
    try p.validateTable(root);
}

test "only PEG1 function5 return and ACPI checksum change" {
    try p.validateTable(original);
    try p.validateTable(&changed);
    try std.testing.expectEqualStrings("TbtPEG11", original[16..24]);
    var count: usize = 0;
    for (original, changed, 0..) |a, b, i| {
        if (a != b) {
            count += 1;
            try std.testing.expect(i == 9 or i == changed_at);
        }
    }
    try std.testing.expectEqual(@as(usize, 2), count);
    try std.testing.expectEqual(@as(u8, 1), original[0x2c6e]); // PCI0 remains 1.
    try std.testing.expectEqual(@as(u8, 1), changed[0x2c6e]);
    try std.testing.expectEqualSlices(u8, original[0x2c40..0x2c72], original[method_start..method_end]);
    try std.testing.expectEqual(@as(u8, 0), changed[changed_at]);
}

test "private root link replacement preserves all other links and rejects duplicates" {
    inline for (.{ .{ "001-RSDT.dat", 4 }, .{ "021-XSDT.dat", 8 }, .{ "023-RSDT.dat", 4 } }) |entry| {
        const src = @embedFile("collected-capture-000/" ++ entry[0]);
        const width = entry[1];
        const old = if (width == 8) try p.u64le(src, 36) else try p.u32le(src, 36);
        var dst = src.*;
        try replaceRootLink(&dst, width, old, 0x12345000);
        try std.testing.expectEqualSlices(u8, src[36 + width ..], dst[36 + width ..]);
        try std.testing.expectEqualSlices(u8, src[0..9], dst[0..9]);
        try std.testing.expectEqualSlices(u8, src[10..36], dst[10..36]);
        try std.testing.expectError(error.DuplicateReplacement, replaceRootLink(&dst, width, old, 0x12345000));
        dst = src.*;
        try std.testing.expectError(error.MissingOriginal, replaceRootLink(&dst, width, 0x76543000, 0x12345000));
        try std.testing.expectEqualSlices(u8, src, &dst);
        try std.testing.expectError(error.BadReplacement, replaceRootLink(&dst, width, old, 0x100000000));
    }
}
