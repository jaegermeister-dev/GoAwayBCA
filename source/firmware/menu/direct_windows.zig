// Pure parsing for diagnostic-only original-loader selection. No disk writes.
const std = @import("std");
pub const Kind = enum { stock, helper };
pub const helper_sha = "e3f0584dca446d4926749087de19b24ae2bfc74727ef3eb1e6342fffd1f41d5f";

pub fn kind(path: []const u16) !Kind {
    var name: [256]u8 = undefined;
    if (path.len >= name.len) return error.UnsupportedDiagnosticLoader;
    for (path, 0..) |c, i| {
        if (c < 32 or c > 126) return error.UnsupportedDiagnosticLoader;
        name[i] = @intCast(c);
    }
    const text = name[0..path.len];
    if (std.ascii.endsWithIgnoreCase(text, "\\bootmgfw.efi")) return .stock;
    if (std.ascii.endsWithIgnoreCase(text, "\\Fix.efi")) return .helper;
    return error.UnsupportedDiagnosticLoader;
}

pub fn sibling(path: []const u16, leaf: []const u8) ![256]u16 {
    var split: ?usize = null;
    for (path, 0..) |c, i| if (c == '\\') { split = i; };
    const at = (split orelse return error.InvalidOriginalLoaderPath) + 1;
    if (at + leaf.len >= 256) return error.InvalidOriginalLoaderPath;
    var out: [256]u16 = @splat(0);
    @memcpy(out[0..at], path[0..at]);
    for (leaf, 0..) |c, i| {
        if (!std.ascii.isAlphanumeric(c) and c != '.') return error.InvalidOriginalLoaderPath;
        out[at+i] = c;
    }
    return out;
}

pub fn originalHash(config: []const u8) ![32]u8 {
    if (config.len != 40 or !std.mem.eql(u8, config[0..8], "NMBFIX01"))
        return error.InvalidOriginalLoaderRecord;
    if (std.mem.allEqual(u8, config[8..40], 0)) return error.InvalidOriginalLoaderRecord;
    return config[8..40].*;
}

pub fn validateLoader(data: []const u8) !void {
    if (data.len < 1024 or !std.mem.eql(u8, data[0..2], "MZ")) return error.InvalidWindowsLoader;
    const offset = std.mem.readInt(u32, data[60..64], .little);
    if (offset > data.len - 96 or !std.mem.eql(u8, data[offset..][0..4], "PE\x00\x00"))
        return error.InvalidWindowsLoader;
    if (std.mem.readInt(u16, data[offset+4..][0..2], .little) != 0x8664 or
        std.mem.readInt(u16, data[offset+24..][0..2], .little) != 0x20b or
        std.mem.readInt(u16, data[offset+24+68..][0..2], .little) != 10)
        return error.WindowsLoaderIsNotX64EfiApplication;
}

test "only stock boot manager or a separately verified helper is resolved" {
    const wide = std.unicode.utf8ToUtf16LeStringLiteral;
    try std.testing.expectEqual(Kind.stock, try kind(wide("\\EFI\\Microsoft\\Boot\\bootmgfw.efi")));
    try std.testing.expectEqual(Kind.helper, try kind(wide("\\EFI\\Microsoft\\Boot\\FIX.EFI")));
    try std.testing.expectError(error.UnsupportedDiagnosticLoader, kind(wide("\\EFI\\Other\\Unknown.efi")));
    const original = try sibling(wide("\\EFI\\Microsoft\\Boot\\Fix.efi"), "Original.efi");
    try std.testing.expectEqualSlices(u16, wide("\\EFI\\Microsoft\\Boot\\Original.efi"), std.mem.sliceTo(&original, 0));
}

test "original record requires exact header size and nonzero hash" {
    var record: [40]u8 = @splat(1);
    @memcpy(record[0..8], "NMBFIX01");
    try std.testing.expectEqual(@as(u8, 1), (try originalHash(&record))[0]);
    record[0] = 0;
    try std.testing.expectError(error.InvalidOriginalLoaderRecord, originalHash(&record));
    try std.testing.expectError(error.InvalidOriginalLoaderRecord, originalHash(record[0..39]));
}

test "executable validation rejects malformed and non-x64 EFI images" {
    var data: [1024]u8 = @splat(0);
    @memcpy(data[0..2], "MZ");
    std.mem.writeInt(u32, data[60..64], 128, .little);
    @memcpy(data[128..132], "PE\x00\x00");
    std.mem.writeInt(u16, data[132..134], 0x8664, .little);
    std.mem.writeInt(u16, data[152..154], 0x20b, .little);
    std.mem.writeInt(u16, data[220..222], 10, .little);
    try validateLoader(&data);
    data[132] = 0;
    try std.testing.expectError(error.WindowsLoaderIsNotX64EfiApplication, validateLoader(&data));
    std.mem.writeInt(u32, data[60..64], 0xffffffff, .little);
    try std.testing.expectError(error.InvalidWindowsLoader, validateLoader(&data));
}
