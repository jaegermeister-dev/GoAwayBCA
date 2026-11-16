// Pure, host-testable builders: never write any original firmware buffer.
const std = @import("std");
const p = @import("acpi_parse.zig");

fn fixSum(b: []u8, at: usize) void {
    b[at] = 0;
    var sum: u8 = 0;
    for (b) |v| sum +%= v;
    b[at] = 0 -% sum;
}

pub fn root(src: []const u8, dst: []u8, width: usize, addition: u64) !void {
    const n = try p.rootEntryCount(src, width);
    if (n == 0 or n >= 512) return error.BadEntryCount;
    if (addition == 0 or addition > std.math.maxInt(u32)) return error.BadAddress;
    if (dst.len != src.len + width) return error.BadDestination;
    if (@intFromPtr(src.ptr) < @intFromPtr(dst.ptr) + dst.len and
        @intFromPtr(dst.ptr) < @intFromPtr(src.ptr) + src.len) return error.OverlappingBuffers;
    for (0..n) |i| {
        const a = if (width == 8) try p.u64le(src, 36 + i * width) else try p.u32le(src, 36 + i * width);
        if (a == 0 or a == addition) return error.BadExistingEntry;
    }
    @memcpy(dst[0..src.len], src);
    std.mem.writeInt(u32, dst[4..8], @intCast(dst.len), .little);
    if (width == 8) std.mem.writeInt(u64, dst[src.len..][0..8], addition, .little)
    else std.mem.writeInt(u32, dst[src.len..][0..4], @intCast(addition), .little);
    fixSum(dst, 9);
    try p.validateTable(dst);
}

pub fn rsdp(src: []const u8, dst: []u8, rsdt: u64, xsdt: u64) !void {
    try p.validateRsdp(src);
    if (src.len != dst.len or (src.len != 20 and src.len != 36)) return error.BadDestination;
    if (@intFromPtr(src.ptr) < @intFromPtr(dst.ptr) + dst.len and
        @intFromPtr(dst.ptr) < @intFromPtr(src.ptr) + src.len) return error.OverlappingBuffers;
    if (rsdt > std.math.maxInt(u32) or xsdt > std.math.maxInt(u32)) return error.BadAddress;
    // Do not invent or drop root tables, including the distinct ACPI1 FADT.
    if ((try p.u32le(src, 16) == 0) != (rsdt == 0)) return error.RootPresenceChanged;
    if (src.len == 20) {
        if (xsdt != 0) return error.RootPresenceChanged;
    } else if ((try p.u64le(src, 24) == 0) != (xsdt == 0)) return error.RootPresenceChanged;
    @memcpy(dst, src);
    std.mem.writeInt(u32, dst[16..20], @intCast(rsdt), .little);
    fixSum(dst[0..20], 8);
    if (dst.len == 36) {
        std.mem.writeInt(u64, dst[24..32], xsdt, .little);
        fixSum(dst, 32);
    }
    try p.validateRsdp(dst);
}

// The journal is incremented BEFORE each firmware call: a call can fail after
// changing a pointer. The same state machine is used by the real EFI backend
// and tests. rollback must not return unless restoration is verified.
pub fn boot(ctx: anytype, count: usize) !void {
    if (count == 0 or count > 2) return error.BadPublicationCount;
    var attempted: usize = 0;
    defer ctx.rollback(attempted);
    for (0..count) |i| {
        try ctx.checkOriginal(i);
        attempted = i + 1;
        try ctx.publish(i);
        try ctx.checkPublished(i);
    }
    try ctx.verify();
    try ctx.start();
    return error.WindowsLoaderReturned;
}

pub fn restore(ctx: anytype, count: usize) !void {
    if (count > 2) return error.BadPublicationCount;
    var failed = false;
    var i = count;
    while (i > 0) {
        i -= 1;
        ctx.restoreOne(i) catch { failed = true; };
    }
    // Verify the full configuration and original bytes even after an error.
    ctx.verifyRestored() catch { failed = true; };
    if (failed) return error.RollbackUnverified;
}

test "captured roots keep every original byte except length/checksum; append once" {
    inline for (.{ .{ "001-RSDT.dat", 4 }, .{ "021-XSDT.dat", 8 }, .{ "023-RSDT.dat", 4 } }) |entry| {
        const src = @embedFile("collected-capture-000/" ++ entry[0]);
        const before = src.*;
        var dst: [src.len + entry[1]]u8 = undefined;
        try root(src, &dst, entry[1], 0x12345000);
        try std.testing.expectEqualSlices(u8, &before, src);
        try std.testing.expectEqualSlices(u8, src[0..4], dst[0..4]);
        try std.testing.expectEqual(src[8], dst[8]);
        try std.testing.expectEqualSlices(u8, src[10..], dst[10..src.len]);
        try std.testing.expectEqual((try p.rootEntryCount(src, entry[1])) + 1, try p.rootEntryCount(&dst, entry[1]));
        const last = if (entry[1] == 8) try p.u64le(&dst, src.len) else try p.u32le(&dst, src.len);
        try std.testing.expectEqual(@as(u64, 0x12345000), last);
    }
}

test "both captured RSDP versions retain revision, OEM and zero/nonzero roots" {
    inline for (.{ "RSDP-0.dat", "RSDP-1.dat" }) |name| {
        const src = @embedFile("collected-capture-000/" ++ name);
        const before = src.*;
        var dst: [src.len]u8 = undefined;
        try rsdp(src, &dst, 0x12340000, if (src.len == 36) 0x23450000 else 0);
        try std.testing.expectEqualSlices(u8, &before, src);
        try std.testing.expectEqualSlices(u8, src[0..8], dst[0..8]);
        try std.testing.expectEqualSlices(u8, src[9..16], dst[9..16]);
        try std.testing.expectEqual(@as(u32, 0x12340000), try p.u32le(&dst, 16));
        if (src.len == 36) {
            try std.testing.expectEqualSlices(u8, src[20..24], dst[20..24]);
            try std.testing.expectEqualSlices(u8, src[33..36], dst[33..36]);
            try std.testing.expectEqual(@as(u64, 0x23450000), try p.u64le(&dst, 24));
        }
    }
}

test "builders reject bad checksums, size, width, addresses, duplicate and overlap" {
    const src = @embedFile("collected-capture-000/001-RSDT.dat");
    var dst: [src.len + 4]u8 = undefined;
    try std.testing.expectError(error.BadAddress, root(src, &dst, 4, 0));
    try std.testing.expectError(error.BadAddress, root(src, &dst, 4, 0x100000000));
    try std.testing.expectError(error.BadWidth, root(src, &dst, 6, 0x1000));
    try std.testing.expectError(error.BadDestination, root(src, dst[0..src.len], 4, 0x1000));
    try std.testing.expectError(error.BadExistingEntry, root(src, &dst, 4, try p.u32le(src, 36)));
    @memcpy(dst[0..src.len], src);
    try std.testing.expectError(error.OverlappingBuffers, root(dst[0..src.len], &dst, 4, 0x1000));
    dst[9] +%= 1;
    try std.testing.expectError(error.BadChecksum, root(dst[0..src.len], &dst, 4, 0x1000));
    const r = @embedFile("collected-capture-000/RSDP-1.dat");
    var rdst: [36]u8 = undefined;
    try std.testing.expectError(error.BadAddress, rsdp(r, &rdst, 0x100000000, 1));
    try std.testing.expectError(error.RootPresenceChanged, rsdp(r, &rdst, 0, 0));
    const r1 = @embedFile("collected-capture-000/RSDP-0.dat");
    try std.testing.expectError(error.RootPresenceChanged, rsdp(r1, rdst[0..20], 1, 1));
}

const Mock = struct {
    pointers: [2]bool = .{ false, false },
    fail_before: ?usize = null,
    fail_after: ?usize = null,
    fail_readback: ?usize = null,
    fail_verify: bool = false,
    fail_start: bool = false,
    started: bool = false,
    restored_count: usize = 0,
    fn checkOriginal(self: *@This(), i: usize) !void {
        if (self.pointers[i]) return error.ChangedOriginal;
    }
    fn publish(self: *@This(), i: usize) !void {
        if (self.fail_before == i) return error.MockPublish;
        self.pointers[i] = true;
        if (self.fail_after == i) return error.MockPublish;
    }
    fn checkPublished(self: *@This(), i: usize) !void {
        if (self.fail_readback == i) return error.MockReadback;
        if (!self.pointers[i]) return error.MissingPublication;
    }
    fn verify(self: *@This()) !void { if (self.fail_verify) return error.MockVerify; }
    fn start(self: *@This()) !void {
        self.started = true;
        if (self.fail_start) return error.MockStart;
    }
    fn rollback(self: *@This(), count: usize) void {
        self.restored_count = count;
        for (0..count) |i| self.pointers[i] = false;
    }
};

test "publication failures before and after mutation restore every attempted pointer" {
    for (0..2) |i| {
        for (0..2) |when| {
            var m = Mock{};
            if (when == 0) m.fail_before = i else m.fail_after = i;
            try std.testing.expectError(error.MockPublish, boot(&m, 2));
            try std.testing.expectEqual(i + 1, m.restored_count);
            try std.testing.expect(!m.started and !m.pointers[0] and !m.pointers[1]);
        }
        var m = Mock{ .fail_readback = i };
        try std.testing.expectError(error.MockReadback, boot(&m, 2));
        try std.testing.expect(!m.started and !m.pointers[0] and !m.pointers[1]);
    }
}

test "failed verification and returned Windows always restore both pointers" {
    var m = Mock{ .fail_verify = true };
    try std.testing.expectError(error.MockVerify, boot(&m, 2));
    try std.testing.expect(!m.started and !m.pointers[0] and !m.pointers[1]);
    m = .{ .fail_start = true };
    try std.testing.expectError(error.MockStart, boot(&m, 2));
    try std.testing.expect(m.started and !m.pointers[0] and !m.pointers[1]);
    m = .{};
    try std.testing.expectError(error.WindowsLoaderReturned, boot(&m, 2));
    try std.testing.expect(m.started and !m.pointers[0] and !m.pointers[1]);
}

const RestoreMock = struct {
    pointers: [2]bool = .{ true, true },
    calls: [2]bool = .{ false, false },
    fail_before: ?usize = null,
    fail_after: ?usize = null,
    verified: bool = false,
    fn restoreOne(self: *@This(), i: usize) !void {
        self.calls[i] = true;
        if (self.fail_before == i) return error.MockRestore;
        self.pointers[i] = false;
        if (self.fail_after == i) return error.MockRestore;
    }
    fn verifyRestored(self: *@This()) !void {
        self.verified = true;
        if (self.pointers[0] or self.pointers[1]) return error.NotRestored;
    }
};

test "rollback attempts every pointer and refuses any unverified restoration" {
    for (0..2) |i| {
        for (0..2) |when| {
            var m = RestoreMock{};
            if (when == 0) m.fail_before = i else m.fail_after = i;
            try std.testing.expectError(error.RollbackUnverified, restore(&m, 2));
            try std.testing.expect(m.calls[0] and m.calls[1] and m.verified);
        }
    }
    var m = RestoreMock{};
    try restore(&m, 2);
    try std.testing.expect(m.verified and !m.pointers[0] and !m.pointers[1]);
}

test "invalid publication counts never start or modify anything" {
    var m = Mock{};
    try std.testing.expectError(error.BadPublicationCount, boot(&m, 0));
    try std.testing.expectError(error.BadPublicationCount, boot(&m, 3));
    try std.testing.expect(!m.started and !m.pointers[0] and !m.pointers[1]);
}
