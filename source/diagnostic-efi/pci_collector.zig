// Private read-only PCI header inventory. NOT a fix, bootloader or driver.
// No PCI writes, BAR sizing, MMIO, driver loading, ACPI/NVRAM/boot-policy changes,
// reset or ExitBootServices. Writes only a NEW report in an explicit marker dir.
const std = @import("std");
const context = @import("diagnostic_context.zig");
const u = std.os.uefi;
const File = u.protocol.File;
const utf16 = std.unicode.utf8ToUtf16LeStringLiteral;
const marker = "WinDeploy readonly PCI inventory 20261009 v1\n";

// ABI prefix from UEFI PCI I/O Protocol / EDK II PciIo.h. Unused methods are
// intentionally integers: there is no callable PCI write method in this type.
const PciIo = extern struct {
    unused_io_methods: [6]usize,
    read_config: *const fn (*PciIo, u32, u32, usize, *anyopaque) callconv(u.cc) u.Status,
    uncallable_write_config: usize,
    unused_dma_methods: [6]usize,
    get_location: *const fn (*PciIo, *usize, *usize, *usize, *usize) callconv(u.cc) u.Status,
    pub const guid align(8) = u.Guid{ .time_low = 0x4cf5b200, .time_mid = 0x68b8, .time_high_and_version = 0x4ca5, .clock_seq_high_and_reserved = 0x9e, .clock_seq_low = 0xec, .node = .{ 0xb2, 0x3e, 0x3f, 0x50, 0x02, 0x9a } };
};
const Location = struct { segment: usize, bus: usize, device: usize, function: usize };
const Snapshot = struct { location: Location, header: [64]u8 };
var log_buffer: [256 * 1024]u8 = undefined;
var log_length: usize = 0;

fn append(comptime fmt: []const u8, args: anytype) !void {
    const text = try std.fmt.bufPrint(log_buffer[log_length..], fmt, args);
    log_length += text.len;
}
fn readHeader(io: *PciIo) !Snapshot {
    var snap: Snapshot = undefined;
    const loc = &snap.location;
    if (io.get_location(io, &loc.segment, &loc.bus, &loc.device, &loc.function) != .success) return error.LocationReadFailed;
    if (loc.segment > 65535 or loc.bus > 255 or loc.device > 31 or loc.function > 7) return error.InvalidLocation;
    // Uint8 width, offset zero, exactly the conventional 64-byte config header.
    if (io.read_config(io, 0, 0, snap.header.len, &snap.header) != .success) return error.ConfigReadFailed;
    if (snap.header[0] == 0xff and snap.header[1] == 0xff) return error.DeviceAbsent;
    return snap;
}
fn word(h: []const u8, at: usize) !u16 {
    if (at > h.len or h.len - at < 2) return error.ShortHeader;
    return @as(u16, h[at]) | (@as(u16, h[at + 1]) << 8);
}
const Window = struct { base: u64, limit: u64, enabled: bool };
fn memoryWindow(h: []const u8) !Window {
    if (h.len != 64 or h[14] & 0x7f != 1) return error.NotBridge;
    const base = @as(u64, (try word(h, 0x20)) & 0xfff0) << 16;
    const limit = (@as(u64, (try word(h, 0x22)) & 0xfff0) << 16) | 0xfffff;
    return .{ .base = base, .limit = limit, .enabled = base <= limit };
}
fn record(s: Snapshot) !void {
    const l = s.location;
    try append("BDF={x}:{x}:{x}.{x} vendor={x:0>4} device={x:0>4} header={x:0>2}\r\n", .{ l.segment, l.bus, l.device, l.function, try word(&s.header, 0), try word(&s.header, 2), s.header[14] });
    if (s.header[14] & 0x7f == 1) {
        const w = try memoryWindow(&s.header);
        try append("bridge primary={d} secondary={d} subordinate={d} nonprefetch={x}-{x} enabled={}\r\n", .{ s.header[24], s.header[25], s.header[26], w.base, w.limit, w.enabled });
    }
    try append("raw=", .{});
    for (s.header) |byte| try append("{x:0>2}", .{byte});
    try append("\r\n", .{});
}
fn message(text: []const u8) void {
    var buf: [256:0]u16 = undefined;
    if (text.len > 256) return;
    for (text, 0..) |b, i| buf[i] = b;
    buf[text.len] = 0;
    if (u.system_table.con_out) |c| _ = c.outputString(@ptrCast(&buf)) catch false;
}
fn missing(dir: *File, name: [*:0]const u16) !bool {
    const f = dir.open(name, .read, .{}) catch |err| {
        if (err == error.NotFound) return true;
        return err;
    };
    try f.close();
    return false;
}
fn save(dir: *File) !void {
    for (0..100) |i| {
        var buf: [32]u8 = undefined;
        const ascii = try std.fmt.bufPrint(&buf, "pci-{d:0>3}.txt", .{i});
        var wide: [32:0]u16 = undefined;
        for (ascii, 0..) |b, j| wide[j] = b;
        wide[ascii.len] = 0;
        const name: [*:0]const u16 = @ptrCast(&wide);
        if (!try missing(dir, name)) continue;
        const f = try dir.open(name, .read_write_create, .{});
        defer f.close() catch {};
        if (try f.write(log_buffer[0..log_length]) != log_length) return error.ShortWrite;
        try f.flush();
        try f.setPosition(0);
        var readback: [1024]u8 = undefined;
        var offset: usize = 0;
        while (offset < log_length) {
            const n = @min(readback.len, log_length - offset);
            if (try f.read(readback[0..n]) != n or !std.mem.eql(u8, readback[0..n], log_buffer[offset..][0..n])) return error.ReadbackMismatch;
            offset += n;
        }
        return;
    }
    return error.NoUnusedReportName;
}
fn run() !void {
    const bs = u.system_table.boot_services orelse return error.NoBootServices;
    const image = (try bs.handleProtocol(u.protocol.LoadedImage, u.handle)) orelse return error.NoLoadedImage;
    const device = image.device_handle orelse return error.NoDevice;
    const fs = (try bs.handleProtocol(u.protocol.SimpleFileSystem, device)) orelse return error.NoFilesystem;
    const root = try fs.openVolume();
    defer root.close() catch {};
    const dir = try context.open(root, image);
    defer dir.close() catch {};
    const allow = try dir.open(utf16("ALLOW-PCI.txt"), .read, .{});
    defer allow.close() catch {};
    var bytes: [128]u8 = undefined;
    const n = try allow.read(&bytes);
    if (!std.mem.eql(u8, bytes[0..n], marker)) return error.WrongMarker;
    const handles = (try bs.locateHandleBuffer(.{ .by_protocol = &PciIo.guid })) orelse return error.NoPciHandles;
    defer bs.freePool(@ptrCast(@alignCast(handles.ptr))) catch {};
    if (handles.len == 0 or handles.len > 512) return error.HandleCount;
    log_length = 0;
    try append("WinDeploy read-only PCI inventory v1. Existing protocol handles only.\r\nNo PCI/MMIO writes, BAR sizing, ACPI/NVRAM/boot changes or resets.\r\n", .{});
    try append("{s}", .{context.reportHeader()});
    var failures: usize = 0;
    var count: usize = 0;
    for (handles, 0..) |handle, index| {
        const io = (try bs.handleProtocol(PciIo, handle)) orelse { failures += 1; continue; };
        const snap = readHeader(io) catch |err| { failures += 1; try append("READ_FAILED index={d} error={s}\r\n", .{ index, @errorName(err) }); continue; };
        try record(snap);
        count += 1;
    }
    try append("END handles={d} read={d} failed={d}. Hardware fix NOT applied.\r\n", .{ handles.len, count, failures });
    try save(dir);
    if (failures != 0) return error.IncompleteInventory;
}
pub fn main() u.Status {
    message("WinDeploy: PCI header inventory only. No hardware changes.\r\n");
    run() catch |err| { message("Inventory stopped: "); message(@errorName(err)); message("\r\nNo hardware fix applied.\r\n"); return .aborted; };
    message("Inventory saved and read back. No patch applied. Boot Windows normally.\r\n");
    return .success;
}

const Mock = struct {
    var pci: PciIo = undefined;
    var header: [64]u8 = @splat(0);
    var bad_location: bool = false;
    var fail_read: bool = false;
    var reads: usize = 0;
    fn init() void { pci = undefined; pci.get_location = location; pci.read_config = read; header = @splat(0); header[0] = 0x86; header[1] = 0x80; bad_location = false; fail_read = false; reads = 0; }
    fn location(_: *PciIo, segment: *usize, bus: *usize, device: *usize, function: *usize) callconv(u.cc) u.Status { segment.* = 0; bus.* = 7; device.* = if (bad_location) 32 else 0; function.* = 0; return .success; }
    fn read(_: *PciIo, width: u32, offset: u32, count: usize, dest: *anyopaque) callconv(u.cc) u.Status { reads += 1; if (width != 0 or offset != 0 or count != 64) return .invalid_parameter; if (fail_read) return .device_error; const dst: *[64]u8 = @ptrCast(dest); dst.* = header; return .success; }
};
test "UEFI x64 ABI read-only prefix offsets" { try std.testing.expectEqual(@as(usize, 48), @offsetOf(PciIo, "read_config")); try std.testing.expectEqual(@as(usize, 112), @offsetOf(PciIo, "get_location")); }
test "exactly one conventional header read and no other methods" { Mock.init(); const s = try readHeader(&Mock.pci); try std.testing.expectEqual(@as(usize, 1), Mock.reads); try std.testing.expectEqual(@as(u16, 0x8086), try word(&s.header, 0)); }
test "invalid BDF rejected before config access" { Mock.init(); Mock.bad_location = true; try std.testing.expectError(error.InvalidLocation, readHeader(&Mock.pci)); try std.testing.expectEqual(@as(usize, 0), Mock.reads); }
test "read failure and absent device stop" { Mock.init(); Mock.fail_read = true; try std.testing.expectError(error.ConfigReadFailed, readHeader(&Mock.pci)); Mock.init(); Mock.header[0] = 255; Mock.header[1] = 255; try std.testing.expectError(error.DeviceAbsent, readHeader(&Mock.pci)); }
test "one and four MiB bridge windows decode correctly" { var h: [64]u8 = @splat(0); h[14] = 1; h[0x20] = 0xe0; h[0x21] = 0xb4; h[0x22] = 0xe0; h[0x23] = 0xb4; var w = try memoryWindow(&h); try std.testing.expect(w.enabled); try std.testing.expectEqual(@as(u64, 0x100000), w.limit - w.base + 1); h[0x20] = 0xc0; h[0x22] = 0xf0; w = try memoryWindow(&h); try std.testing.expectEqual(@as(u64, 0x400000), w.limit - w.base + 1); }
test "disabled bridge and wrong header are not valid ranges" { var h: [64]u8 = @splat(0); try std.testing.expectError(error.NotBridge, memoryWindow(&h)); h[14] = 1; h[0x21] = 0xb5; h[0x23] = 0xb4; try std.testing.expect(!(try memoryWindow(&h)).enabled); try std.testing.expectError(error.ShortHeader, word(&h, 64)); }
