// DEVELOPER VM PROBE ONLY. No default/NVRAM/ACPI/partition writes.
// This binary refuses to chainload except on its synthetic GPT partition.
const std = @import("std");
const u = std.os.uefi;
const utf16 = std.unicode.utf8ToUtf16LeStringLiteral;
const File = u.protocol.File;
const target_guid = [_]u8{0x72,0xa3,0x67,0x63,0x20,0x91,0x76,0x41,0xb4,0xbb,0x7e,0x74,0x3e,0x60,0x7a,0x09};
const driver_hash = "463bf0b24d58f21d3a9d594a7a4fa68310e1b59e376de0bb5b2dcfb1948d1734";
var buffer: [8*1024*1024]u8 = undefined;

fn say(s: []const u8) void {
    var wide: [512:0]u16 = undefined;
    if (s.len > 511) return;
    for (s, 0..) |c,i| {
        wide[i] = c;
        // QEMU debugcon only; never included in the release chooser.
        asm volatile ("outb %[byte], %[port]" : : [byte] "{al}" (c), [port] "{dx}" (@as(u16,0xe9)));
    }
    wide[s.len] = 0;
    if (u.system_table.con_out) |con| _ = con.outputString(@ptrCast(&wide)) catch false;
}

fn read(root: *File, path: [*:0]const u16) ![]const u8 {
    const file = try root.open(path, .read, .{});
    defer file.close() catch {};
    var used: usize = 0;
    while (true) {
        if (used == buffer.len) return error.FileTooLarge;
        const n = try file.read(buffer[used..]);
        if (n == 0) return buffer[0..used];
        used += n;
    }
}

fn matchPartition(dp: *const u.protocol.DevicePath) bool {
    const bytes: [*]const u8 = @ptrCast(dp);
    var offset: usize = 0;
    var found = false;
    while (offset + 4 <= 4096) {
        const len = std.mem.readInt(u16, bytes[offset+2..][0..2], .little);
        if (len < 4 or offset + len > 4096) return false;
        if (bytes[offset] == 0x7f) return bytes[offset+1] == 0xff and len == 4 and found;
        if (bytes[offset] == 4 and bytes[offset+1] == 1) {
            if (found or len != 42 or bytes[offset+40] != 2 or bytes[offset+41] != 2) return false;
            if (!std.mem.eql(u8, bytes[offset+24..][0..16], &target_guid)) return false;
            found = true;
        }
        offset += len;
    }
    return false;
}

fn load(device: u.Handle, path: [*:0]const u16) !u.Handle {
    const bs = u.system_table.boot_services orelse return error.NoBootServices;
    const dp = (try bs.handleProtocol(u.protocol.DevicePath, device)) orelse return error.NoPath;
    const fs = (try bs.handleProtocol(u.protocol.SimpleFileSystem, device)) orelse return error.NoFilesystem;
    const root = try fs.openVolume();
    defer root.close() catch {};
    const data = try read(root, path);
    const file_dp = try dp.createFileDevicePath(u.pool_allocator, std.mem.sliceTo(path,0));
    var child: u.Handle = undefined;
    try bs._loadImage(false, u.handle, file_dp, data.ptr, data.len, &child).err();
    const info = (try bs.handleProtocol(u.protocol.LoadedImage, child)) orelse return error.NoChildInfo;
    if (info.device_handle != device) return error.ChildDeviceMismatch;
    return child;
}

fn run() !void {
    say("W7DEP NTFS PROBE START\r\n");
    const bs = u.system_table.boot_services orelse return error.NoBootServices;
    const loaded = (try bs.handleProtocol(u.protocol.LoadedImage, u.handle)) orelse return error.NoLoadedImage;
    const device = loaded.device_handle orelse return error.NoDevice;
    const fs = (try bs.handleProtocol(u.protocol.SimpleFileSystem, device)) orelse return error.NoOwnFilesystem;
    const root = try fs.openVolume();
    defer root.close() catch {};
    const driver_path = utf16("\\EFI\\WinDeploy\\OpenNtfsDxe.efi");
    const driver_data = try read(root,driver_path);
    var actual: [32]u8 = undefined;
    var expected: [32]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(driver_data,&actual,.{});
    _ = try std.fmt.hexToBytes(&expected,driver_hash);
    if (!std.mem.eql(u8,&actual,&expected)) return error.DriverHashMismatch;
    const handles = (try bs.locateHandleBuffer(.{.by_protocol=&u.protocol.BlockIo.guid})) orelse return error.NoBlocks;
    var selected: ?u.Handle = null;
    for (handles) |h| {
        const dp = (try bs.handleProtocol(u.protocol.DevicePath,h)) orelse continue;
        if (matchPartition(dp)) {
            if (selected != null) return error.DuplicatePartition;
            selected = h;
        }
    }
    const target = selected orelse return error.NoSyntheticPartition;
    const driver = try load(device,driver_path);
    const driver_exit = try bs.startImage(driver);
    try driver_exit.code.err();
    say("READONLY NTFS DRIVER STARTED\r\n");
    var drivers = [_:null]?u.Handle{driver};
    try bs.connectController(target,&drivers,null,false);
    const ntfs = (try bs.handleProtocol(u.protocol.SimpleFileSystem,target)) orelse return error.NoNtfs;
    const ntfs_root = try ntfs.openVolume();
    defer ntfs_root.close() catch {};
    const marker = try read(ntfs_root,utf16("\\W7DEP-PROBE.txt"));
    if (!std.mem.eql(u8,marker,"Isolated Windows 10 NTFS firmware probe\n")) return error.BadMarker;
    const bcd = try read(ntfs_root,utf16("\\efi\\microsoft\\boot\\bcd"));
    if (bcd.len < 4096 or !std.mem.eql(u8,bcd[0..4],"regf")) return error.InvalidBCD;
    say("TARGET NTFS MARKER AND BCD READ OK\r\n");
    const child = try load(target,utf16("\\efi\\boot\\bootx64.efi"));
    say("START WINDOWS LOADER ON SELECTED NTFS DEVICE\r\n");
    const result = try bs.startImage(child);
    try result.code.err();
    say("WINDOWS LOADER RETURNED\r\n");
}

pub fn main() u.Status {
    run() catch |err| {
        say("W7DEP PROBE FAILED: "); say(@errorName(err)); say("\r\n");
        if (u.system_table.boot_services) |bs| bs.stall(10_000_000) catch {};
        return .aborted;
    };
    return .success;
}
