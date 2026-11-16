// Data-only parser shared by host unit tests and the firmware chooser.
const std = @import("std");
pub const header_size = 44;
pub const record_size = 772;
pub const max_entries = 16;
pub const Entry = struct {
    flags: u32,
    boot_guid: [16]u8,
    windows_guid: [16]u8,
    digest: [32]u8,
    title: [96]u16,
    path: [256]u16,
    pub fn available(self: *const Entry) bool { return self.flags & 1 != 0; }
    pub fn ntfs(self: *const Entry) bool { return self.flags & 2 != 0; }
    pub fn hashLocked(self: *const Entry) bool { return self.flags & 4 != 0; }
};
pub const Menu = struct { count: usize, entries: [max_entries]Entry };

fn text(comptime count: usize, source: []const u8) ![count]u16 {
    var result: [count]u16 = undefined;
    var ended = false;
    for (&result, 0..) |*dest, i| {
        const value = std.mem.readInt(u16,source[i*2..][0..2],.little);
        if (value == 0) ended = true else {
            if (ended or value < 32 or value >= 127) return error.InvalidText;
        }
        dest.* = value;
    }
    if (!ended or result[0] == 0) return error.InvalidText;
    return result;
}

fn pathValid(path: *const [256]u16) bool {
    var ascii: [256]u8 = undefined;
    var len: usize = 0;
    for (path) |c| {
        if (c == 0) break;
        if (c >= 127 or c < 32) return false;
        ascii[len] = @intCast(c); len += 1;
    }
    const s = ascii[0..len];
    if (!std.mem.startsWith(u8,s,"\\EFI\\") or !std.ascii.endsWithIgnoreCase(s,".efi")) return false;
    if (std.ascii.startsWithIgnoreCase(s,"\\EFI\\OC\\") or
        std.ascii.startsWithIgnoreCase(s,"\\EFI\\Apple\\") or
        std.ascii.eqlIgnoreCase(s,"\\EFI\\WinDeploy\\Windows.efi")) return false;
    var parts = std.mem.splitScalar(u8,s[1..],'\\');
    var count: usize = 0;
    while (parts.next()) |part| {
        if (part.len == 0 or std.mem.eql(u8,part,".") or std.mem.eql(u8,part,"..") or
            part[part.len-1] == '.' or part[part.len-1] == ' ') return false;
        for (part) |c| if (std.mem.indexOfScalar(u8,":/*?<>|\"",c) != null) return false;
        count += 1;
    }
    return count >= 3;
}

fn pathEqual(a: *const [256]u16, b: *const [256]u16) bool {
    for (a,b) |x,y| {
        if (std.ascii.toLower(@intCast(x)) != std.ascii.toLower(@intCast(y))) return false;
        if (x == 0) return true;
    }
    return false;
}

pub fn parse(data: []const u8) !Menu {
    if (data.len < header_size or !std.mem.eql(u8,data[0..8],"W7DEP001")) return error.BadHeader;
    const count = std.mem.readInt(u32,data[8..12],.little);
    if (count > max_entries or data.len != header_size + count*record_size) return error.BadLength;
    var digest: [32]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(data[header_size..],&digest,.{});
    if (!std.mem.eql(u8,&digest,data[12..44])) return error.BadChecksum;
    var result: Menu = .{.count=count,.entries=undefined};
    for (0..count) |i| {
        const row = data[header_size+i*record_size..][0..record_size];
        const flags = std.mem.readInt(u32,row[0..4],.little);
        if (flags & ~@as(u32,7) != 0) return error.BadFlags;
        const entry: Entry = .{ .flags=flags, .boot_guid=row[4..20].*,
            .windows_guid=row[20..36].*, .digest=row[36..68].*,
            .title=try text(96,row[68..260]), .path=try text(256,row[260..772]) };
        if (std.mem.allEqual(u8,&entry.boot_guid,0) or std.mem.allEqual(u8,&entry.windows_guid,0)) return error.ZeroGuid;
        if (entry.ntfs() and !std.mem.eql(u8,&entry.boot_guid,&entry.windows_guid)) return error.NtfsPartitionMismatch;
        if (!pathValid(&entry.path)) return error.BadPath;
        for (result.entries[0..i]) |prior| {
            if (std.mem.eql(u8,&entry.boot_guid,&prior.boot_guid) and pathEqual(&entry.path,&prior.path)) return error.DuplicateEntry;
        }
        result.entries[i] = entry;
    }
    return result;
}

pub fn nextSelection(current: usize, count: usize, scan: u16) usize {
    if (count == 0) return 0;
    if (scan == 1) return if (current == 0) count-1 else current-1;
    if (scan == 2) return (current+1)%count;
    return current;
}

test "empty checksum and length validation" {
    var buffer: [header_size]u8 = @splat(0);
    @memcpy(buffer[0..8],"W7DEP001");
    std.crypto.hash.sha2.Sha256.hash("",buffer[12..44],.{});
    try std.testing.expectEqual(@as(usize,0),(try parse(&buffer)).count);
    buffer[12] ^= 1;
    try std.testing.expectError(error.BadChecksum,parse(&buffer));
    try std.testing.expectError(error.BadHeader,parse(buffer[0..20]));
}

test "selection wraps and empty menu stays safe" {
    try std.testing.expectEqual(@as(usize,2),nextSelection(0,3,1));
    try std.testing.expectEqual(@as(usize,0),nextSelection(2,3,2));
    try std.testing.expectEqual(@as(usize,0),nextSelection(0,0,2));
}

test "Python-generated full record and invalid paths/flags" {
    const original = @embedFile("menu-fixture.bin");
    const menu = try parse(original);
    try std.testing.expectEqual(@as(usize,1),menu.count);
    try std.testing.expect(menu.entries[0].ntfs() and menu.entries[0].hashLocked());
    try std.testing.expectEqual(@as(u16,'W'),menu.entries[0].title[0]);
    var altered = original.*;
    altered[header_size] |= 8;
    std.crypto.hash.sha2.Sha256.hash(altered[header_size..],altered[12..44],.{});
    try std.testing.expectError(error.BadFlags,parse(&altered));
    altered = original.*;
    altered[header_size+260] = '/';
    std.crypto.hash.sha2.Sha256.hash(altered[header_size..],altered[12..44],.{});
    try std.testing.expectError(error.BadPath,parse(&altered));
    altered = original.*;
    @memset(altered[header_size+4..header_size+20],0);
    std.crypto.hash.sha2.Sha256.hash(altered[header_size..],altered[12..44],.{});
    try std.testing.expectError(error.ZeroGuid,parse(&altered));
}
