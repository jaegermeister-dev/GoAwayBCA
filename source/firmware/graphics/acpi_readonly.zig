// Stage 1 ONLY: read firmware tables into new diagnostic files, then return.
// No ACPI installation/evaluation, runtime variables, boot selection, driver
// loading, chipset/PCI writes, reset, or calls to ExitBootServices exist here.
const std = @import("std");
const u = std.os.uefi;
const p = @import("acpi_parse.zig");
const utf16 = std.unicode.utf8ToUtf16LeStringLiteral;
const File = u.protocol.File;
const marker = "WinDeploy readonly ACPI collection 20261008 v1\n";
var map_storage: [512 * 1024]u8 align(8) = undefined;
var memory: u.tables.MemoryMapSlice = undefined;
var seen: [512]u64 = undefined;
var seen_count: usize = 0;
var saved_count: usize = 0;
var ssdt_count: usize = 0;
var dsdt_count: usize = 0;
var total_saved: usize = 0;
var out: *File = undefined;
var inventory: *File = undefined;

fn message(s: []const u8) void {
    var buf: [256:0]u16 = undefined;
    if (s.len > 256) return;
    for (s, 0..) |c, i| buf[i] = c;
    buf[s.len] = 0;
    if (u.system_table.con_out) |con| _ = con.outputString(@ptrCast(&buf)) catch false;
}
fn readMemory(address: u64, length: usize) ![]const u8 {
    if (length == 0 or length > 1024 * 1024) return error.BadLength;
    var it = memory.iterator();
    while (it.next()) |d| {
        if (d.attribute.rp) continue;
        switch (d.type) {
            .acpi_reclaim_memory, .acpi_memory_nvs, .boot_services_code, .boot_services_data, .runtime_services_code, .runtime_services_data, .loader_code, .loader_data, .reserved_memory_type => {},
            else => continue,
        }
        if (p.rangeInside(address, length, d.physical_start, d.number_of_pages)) {
            const ptr: [*]const u8 = @ptrFromInt(address);
            return ptr[0..length];
        }
    }
    return error.UnmappedTable;
}
fn table(address: u64) ![]const u8 {
    const header = try readMemory(address, 36);
    const data = try readMemory(address, try p.tableLength(header));
    try p.validateTable(data);
    return data;
}
fn missing(dir: *File, name: [*:0]const u16) !bool {
    const f = dir.open(name, .read, .{}) catch |err| {
        if (err == error.NotFound) return true;
        return err;
    };
    try f.close();
    return false;
}
fn newFile(dir: *File, name: []const u8) !*File {
    var path: [96:0]u16 = undefined;
    if (name.len > 96) return error.BadName;
    for (name, 0..) |c, i| {
        if (!std.ascii.isAlphanumeric(c) and c != '-' and c != '.' and c != '_') return error.BadName;
        path[i] = c;
    }
    path[name.len] = 0;
    const z: [*:0]const u16 = @ptrCast(&path);
    if (!try missing(dir, z)) return error.WouldOverwrite;
    return dir.open(z, .read_write_create, .{});
}
fn writeAll(f: *File, data: []const u8) !void {
    if (try f.write(data) != data.len) return error.ShortWrite;
}
fn save(name: []const u8, data: []const u8) !void {
    if (data.len > 16 * 1024 * 1024 - total_saved) return error.TotalLimit;
    const f = try newFile(out, name);
    defer f.close() catch {};
    try writeAll(f, data);
    try f.flush();
    // Read back every byte on the same handle before declaring a saved table.
    try f.setPosition(0);
    var buf: [4096]u8 = undefined;
    var pos: usize = 0;
    while (pos < data.len) {
        const size = @min(buf.len, data.len - pos);
        if (try f.read(buf[0..size]) != size or !std.mem.eql(u8, buf[0..size], data[pos..][0..size])) return error.ReadbackMismatch;
        pos += size;
    }
    var hash: [32]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(data, &hash, .{});
    var hex: [64]u8 = undefined;
    const digits = "0123456789abcdef";
    for (hash, 0..) |v, i| {
        hex[i * 2] = digits[v >> 4];
        hex[i * 2 + 1] = digits[v & 15];
    }
    var line: [200]u8 = undefined;
    const text = try std.fmt.bufPrint(&line, "saved\t{s}\t{d}\t{s}\r\n", .{ name, data.len, hex });
    try writeAll(inventory, text);
    total_saved += data.len;
    saved_count += 1;
}
fn visit(address: u64) anyerror!void {
    for (seen[0..seen_count]) |a| if (a == address) return;
    if (seen_count == seen.len) return error.TooManyTables;
    const data = try table(address);
    seen[seen_count] = address;
    seen_count += 1;
    var line: [160]u8 = undefined;
    try writeAll(inventory, try std.fmt.bufPrint(&line, "table\t{d}\t{s}\t0x{x}\t{d}\r\n", .{ seen_count, data[0..4], address, data.len }));
    if (p.retain(data[0..4])) {
        var name: [40]u8 = undefined;
        try save(try std.fmt.bufPrint(&name, "{d:0>3}-{s}.dat", .{ seen_count, data[0..4] }), data);
    }
    if (std.mem.eql(u8, data[0..4], "SSDT")) ssdt_count += 1;
    if (std.mem.eql(u8, data[0..4], "DSDT")) dsdt_count += 1;
    if (std.mem.eql(u8, data[0..4], "FACP")) {
        var dsdt: u64 = 0;
        if (data.len >= 148) dsdt = try p.u64le(data, 140);
        if (dsdt == 0) dsdt = try p.u32le(data, 40);
        if (dsdt == 0) return error.NoDsdt;
        const d = try table(dsdt);
        if (!std.mem.eql(u8, d[0..4], "DSDT")) return error.WrongDsdt;
        try visit(dsdt);
    }
}
fn walkRoot(address: u64, width: usize) !void {
    const data = try table(address);
    const n = try p.rootEntryCount(data, width);
    try visit(address);
    for (0..n) |i| {
        const child = if (width == 8) try p.u64le(data, 36 + i * 8) else try p.u32le(data, 36 + i * 4);
        if (child == 0) return error.NullTable;
        try visit(child);
    }
}
fn captureRsdp(address: u64, index: usize) !void {
    var data = try readMemory(address, 20);
    if (!std.mem.eql(u8, data[0..8], "RSD PTR ")) return error.BadRsdp;
    if (data[15] >= 2) {
        const header = try readMemory(address, 36);
        const n = try p.u32le(header, 20);
        if (n < 36 or n > 4096) return error.BadLength;
        data = try readMemory(address, n);
    }
    try p.validateRsdp(data);
    var name: [40]u8 = undefined;
    try save(try std.fmt.bufPrint(&name, "RSDP-{d}.dat", .{index}), data);
    const rsdt = try p.u32le(data, 16);
    const xsdt = if (data[15] >= 2) try p.u64le(data, 24) else 0;
    if (rsdt == 0 and xsdt == 0) return error.NoRoot;
    if (xsdt != 0) try walkRoot(xsdt, 8);
    if (rsdt != 0) try walkRoot(rsdt, 4);
}
fn run() !void {
    const bs = u.system_table.boot_services orelse return error.NoBootServices;
    const loaded = (try bs.handleProtocol(u.protocol.LoadedImage, u.handle)) orelse return error.NoLoadedImage;
    const device = loaded.device_handle orelse return error.NoDevice;
    const fs = (try bs.handleProtocol(u.protocol.SimpleFileSystem, device)) orelse return error.NoFilesystem;
    const root = try fs.openVolume();
    defer root.close() catch {};
    const dir = try root.open(utf16("\\EFI\\W7DEP-DIAG"), .read, .{});
    defer dir.close() catch {};
    const allow = try dir.open(utf16("ALLOW-READONLY.txt"), .read, .{});
    var allow_bytes: [128]u8 = undefined;
    const got = allow.read(&allow_bytes) catch |err| {
        allow.close() catch {};
        return err;
    };
    try allow.close();
    if (!std.mem.eql(u8, allow_bytes[0..got], marker)) return error.WrongMarker;
    var made = false;
    for (0..100) |i| {
        var ascii: [32]u8 = undefined;
        const name = try std.fmt.bufPrint(&ascii, "capture-{d:0>3}", .{i});
        var path: [32:0]u16 = undefined;
        for (name, 0..) |c, n| path[n] = c;
        path[name.len] = 0;
        const z: [*:0]const u16 = @ptrCast(&path);
        if (try missing(dir, z)) {
            out = try dir.open(z, .read_write_create, .{ .directory = true });
            made = true;
            break;
        }
    }
    if (!made) return error.NoUnusedDirectory;
    defer out.close() catch {};
    inventory = try newFile(out, "inventory.txt");
    defer inventory.close() catch {};
    try writeAll(inventory, "WinDeploy ACPI readonly collector v1\r\nNo firmware changes; no method evaluation.\r\n");
    memory = try bs.getMemoryMap(&map_storage);
    if (memory.info.descriptor_size < @sizeOf(u.tables.MemoryDescriptor)) return error.BadMemoryMap;
    const cfg = u.system_table.configuration_table;
    if (u.system_table.number_of_table_entries > 1024) return error.TooManyConfigEntries;
    var count: usize = 0;
    for (cfg[0..u.system_table.number_of_table_entries]) |entry| {
        const ct = u.tables.ConfigurationTable;
        if (entry.vendor_guid.eql(ct.acpi_20_table_guid) or entry.vendor_guid.eql(ct.acpi_10_table_guid)) {
            try captureRsdp(@intFromPtr(entry.vendor_table), count);
            count += 1;
        }
    }
    if (count == 0 or dsdt_count == 0 or ssdt_count == 0) return error.IncompleteNamespace;
    var end: [160]u8 = undefined;
    try writeAll(inventory, try std.fmt.bufPrint(&end, "COMPLETE\troot-pointers={d}\tunique-tables={d}\tSSDT={d}\tDSDT={d}\tfiles={d}\r\n", .{ count, seen_count, ssdt_count, dsdt_count, saved_count }));
    try inventory.flush();
}
pub fn main() u.Status {
    message("WinDeploy: collecting ACPI tables only. No firmware changes.\r\n");
    run() catch |err| {
        message("Collection STOPPED: ");
        message(@errorName(err));
        message("\r\nNo compatibility patch was applied. Return to normal Windows boot.\r\n");
        return .aborted;
    };
    message("Collection complete. No patch applied. Type exit, then boot Windows normally.\r\n");
    return .success;
}

// Host-only mock tests exercise the same file-write/readback and memory guards.
// They never call real firmware and never open host filesystem files.
const Mock = struct {
    var dir: File = undefined;
    var file: File = undefined;
    var log: File = undefined;
    var bytes: [4096]u8 = undefined;
    var log_bytes: [4096]u8 = undefined;
    var length: usize = 0;
    var position: usize = 0;
    var log_length: usize = 0;
    var exists: bool = false;
    var corrupt: bool = false;
    var short_write: bool = false;
    var denied: bool = false;
    fn init() void {
        length = 0;
        position = 0;
        log_length = 0;
        exists = false;
        corrupt = false;
        short_write = false;
        denied = false;
        dir = undefined;
        file = undefined;
        log = undefined;
        dir._open = open;
        file._close = close;
        file._write = write;
        file._read = read;
        file._set_position = seek;
        file._flush = flush;
        log._write = logWrite;
        out = &dir;
        inventory = &log;
        total_saved = 0;
        saved_count = 0;
    }
    fn open(_: *const File, result: **File, _: [*:0]const u16, mode: File.OpenMode, _: File.Attributes) callconv(u.cc) u.Status {
        if (denied) return .access_denied;
        if (mode == .read and !exists) return .not_found;
        exists = true;
        result.* = &file;
        return .success;
    }
    fn close(_: *File) callconv(u.cc) u.Status {
        return .success;
    }
    fn flush(_: *File) callconv(u.cc) u.Status {
        return .success;
    }
    fn seek(_: *File, pos: u64) callconv(u.cc) u.Status {
        if (pos > length) return .unsupported;
        position = @intCast(pos);
        return .success;
    }
    fn write(_: *File, count: *usize, src: [*]const u8) callconv(u.cc) u.Status {
        if (short_write and count.* > 0) count.* -= 1;
        if (count.* > bytes.len - position) return .volume_full;
        @memcpy(bytes[position..][0..count.*], src[0..count.*]);
        position += count.*;
        length = @max(length, position);
        return .success;
    }
    fn read(_: *File, count: *usize, dst: [*]u8) callconv(u.cc) u.Status {
        count.* = @min(count.*, length - position);
        @memcpy(dst[0..count.*], bytes[position..][0..count.*]);
        if (corrupt and count.* > 0) dst[0] ^= 1;
        position += count.*;
        return .success;
    }
    fn logWrite(_: *File, count: *usize, src: [*]const u8) callconv(u.cc) u.Status {
        if (count.* > log_bytes.len - log_length) return .volume_full;
        @memcpy(log_bytes[log_length..][0..count.*], src[0..count.*]);
        log_length += count.*;
        return .success;
    }
};
test "new capture file readback and manifest" {
    Mock.init();
    try save("001-SSDT.dat", "mock firmware table");
    try std.testing.expectEqualStrings("mock firmware table", Mock.bytes[0..Mock.length]);
    try std.testing.expectEqual(@as(usize, 1), saved_count);
    try std.testing.expect(std.mem.startsWith(u8, Mock.log_bytes[0..Mock.log_length], "saved\t001-SSDT.dat\t19\t"));
}
test "existing files are refused without mutation" {
    Mock.init();
    Mock.exists = true;
    try std.testing.expectError(error.WouldOverwrite, save("001-SSDT.dat", "new"));
    try std.testing.expectEqual(@as(usize, 0), Mock.length);
}
test "readback mismatch cannot be reported saved" {
    Mock.init();
    Mock.corrupt = true;
    try std.testing.expectError(error.ReadbackMismatch, save("001-SSDT.dat", "data"));
    try std.testing.expectEqual(@as(usize, 0), saved_count);
}
test "short write and access errors stop" {
    Mock.init();
    Mock.short_write = true;
    try std.testing.expectError(error.ShortWrite, save("001-SSDT.dat", "data"));
    Mock.init();
    Mock.denied = true;
    try std.testing.expectError(error.AccessDenied, save("001-SSDT.dat", "data"));
}
test "path traversal and aggregate size limit refused" {
    Mock.init();
    try std.testing.expectError(error.BadName, save("../BOOTX64.EFI", "data"));
    try std.testing.expectError(error.BadName, save("x\\bootmgfw.efi", "data"));
    total_saved = 16 * 1024 * 1024;
    try std.testing.expectError(error.TotalLimit, save("001-SSDT.dat", "data"));
}
test "memory reader rejects MMIO and read-protected regions" {
    var data: [4096]u8 align(4096) = @splat(0);
    var desc: u.tables.MemoryDescriptor = .{
        .type = .acpi_reclaim_memory,
        .physical_start = @intFromPtr(&data),
        .virtual_start = 0,
        .number_of_pages = 1,
        .attribute = @bitCast(@as(u64, 0)),
    };
    memory = .{ .ptr = @ptrCast(&desc), .info = .{ .key = @fromBackingInt(0), .descriptor_size = @sizeOf(u.tables.MemoryDescriptor), .descriptor_version = 1, .len = 1 } };
    try std.testing.expectEqual(@as(usize, 36), (try readMemory(@intFromPtr(&data), 36)).len);
    desc.type = .memory_mapped_io;
    try std.testing.expectError(error.UnmappedTable, readMemory(@intFromPtr(&data), 36));
    desc.type = .acpi_reclaim_memory;
    desc.attribute.rp = true;
    try std.testing.expectError(error.UnmappedTable, readMemory(@intFromPtr(&data), 36));
    desc.attribute.rp = false;
    try std.testing.expectError(error.UnmappedTable, readMemory(@intFromPtr(&data) + 4090, 36));
}
