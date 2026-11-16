// Synthetic-ESP-only firmware file-I/O acceptance test. Never distribute.
const std = @import("std");
const u = std.os.uefi;
const io = @import("store_file_io.zig");
const utf16 = std.unicode.utf8ToUtf16LeStringLiteral;
const prefix = "\\EFI\\WinDeploy\\StoreSwitchLab\\";
fn trace(s: []const u8) void {
    for (s) |c| asm volatile ("outb %[byte], %[port]" : : [byte] "{al}" (c), [port] "{dx}" (@as(u16,0xe9)));
}
fn testRun() !void {
    const bs = u.system_table.boot_services orelse return error.NoBootServices;
    const loaded = (try bs.handleProtocol(u.protocol.LoadedImage,u.handle)) orelse return error.NoImage;
    const device = loaded.device_handle orelse return error.NoDevice;
    const dp = (try bs.handleProtocol(u.protocol.DevicePath,device)) orelse return error.NoPath;
    const bytes: [*]const u8 = @ptrCast(dp);
    const guid = [_]u8{0xba,0x3b,0x7c,0x6a,0x80,0x13,0x21,0x4e,0xb6,0x85,0x9d,0x02,0x93,0x77,0xb9,0x6a};
    var offset: usize = 0; var found = false;
    while (offset+4 <= 4096) {
        const length = std.mem.readInt(u16,bytes[offset+2..][0..2],.little);
        if (length < 4 or offset+length > 4096) return error.InvalidPath;
        if (bytes[offset] == 0x7f) break;
        if (bytes[offset] == 4 and bytes[offset+1] == 1) {
            if (found or length != 42 or bytes[offset+40] != 2 or bytes[offset+41] != 2 or
                !std.mem.eql(u8,bytes[offset+24..][0..16],&guid)) return error.NotSyntheticEsp;
            found = true;
        }
        offset += length;
    }
    if (!found) return error.NotSyntheticEsp;
    const fs = (try bs.handleProtocol(u.protocol.SimpleFileSystem,device)) orelse return error.NoFilesystem;
    const root = try fs.openVolume(); defer root.close() catch {};
    const marker = try root.open(utf16("\\EFI\\WinDeploy\\store-io-lab-only.txt"),.read,.{});
    var marker_bytes: [64]u8 = undefined;
    const length = try marker.read(&marker_bytes); try marker.close();
    if (!std.mem.eql(u8,marker_bytes[0..length],"W7DEP DISPOSABLE STORE IO TEST 1\n")) return error.NoLabAuthority;
    const directory = try root.open(utf16("\\EFI\\WinDeploy\\StoreSwitchLab"),.read_write_create,.{.directory=true});
    try directory.close();
    const files: io.IO = .{.root=root};
    const old = try files.create(prefix ++ "current.bcd","old store bytes");
    const next = try files.create(prefix ++ "next.bcd","new store bytes with a different length");
    if (files.create(prefix ++ "current.bcd","should not replace")) |_| return error.ExistingFileOverwritten else |err| {
        if (err != error.FileAlreadyExists) return err;
    }
    try files.expect(prefix ++ "current.bcd",old);
    try files.rename(prefix ++ "current.bcd",prefix ++ "previous.bcd",old);
    try files.rename(prefix ++ "next.bcd",prefix ++ "current.bcd",next);
    try files.expect(prefix ++ "previous.bcd",old);
    const empty = try files.create(prefix ++ "empty.log","");
    try files.remove(prefix ++ "empty.log",empty);
    var wrong = next; wrong.digest[0] ^= 1;
    if (files.remove(prefix ++ "current.bcd",wrong)) |_| return error.ChangedFileDeleted else |err| {
        if (err != error.FileChanged) return err;
    }
    try files.expect(prefix ++ "current.bcd",next);
    _ = try files.create(prefix ++ "RESULT.txt","PASS: create, flush/readback, rename, delete, mismatch refusal\n");
    trace("STORE-IO: PASS; only StoreSwitchLab files were changed\n");
}
pub fn main() u.Status {
    testRun() catch |err| {trace("STORE-IO: FAIL ");trace(@errorName(err));trace("\n");return .aborted;};
    return .success;
}
