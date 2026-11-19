// Independent Windows boot chooser. Default build has no file writes. An
// explicit experimental integration root activates bound individual BCD stores.
// No variant changes NVRAM/default startup settings or the partition layout.
// A selected child may perform its own normal OS boot/servicing operations.
const std = @import("std");
const u = std.os.uefi;
const p = @import("chooser_parse.zig");
const utf16 = std.unicode.utf8ToUtf16LeStringLiteral;
const File = u.protocol.File;
const ConsoleSession = @import("console_session.zig").Session;
const console_scale = @import("console_scale.zig");
var console_session: ConsoleSession = undefined;
const ntfs_hash = "463bf0b24d58f21d3a9d594a7a4fa68310e1b59e376de0bb5b2dcfb1948d1734";
const ntfs_4kn_hash = "be98c861c709fa5db0819337ea1e40e4e11ed9037f8f37497a6e4274973c2ed7";
const synthetic_guid = [_]u8{0x72,0xa3,0x67,0x63,0x20,0x91,0x76,0x41,0xb4,0xbb,0x7e,0x74,0x3e,0x60,0x7a,0x09};
const logical_esp_probe = if (@hasDecl(@import("root"),"use_esp_logical_path")) @import("root").use_esp_logical_path else false;
const activate_stores=if (@hasDecl(@import("root"),"activate_system_stores")) @import("root").activate_system_stores else false;
const vm_store_menu=if (@hasDecl(@import("root"),"system_store_menu_probe")) @import("root").system_store_menu_probe else false;
const vm_preflight=if (@hasDecl(@import("root"),"system_store_preflight_only")) @import("root").system_store_preflight_only else false;
var buffer: [16*1024*1024]u8 = undefined;
var ntfs_drivers = [_]?u.Handle{null,null};
var ntfs_target_driver: [p.max_entries]usize = undefined;
var ntfs_targets: [p.max_entries]u.Handle = undefined;
var ntfs_target_count: usize = 0;

// Only disconnect handles attached by this invocation. Never disconnect a
// filesystem provided by firmware/OpenCore or another application. Leaving
// our NTFS driver connected after Escape exposes private Windows boot paths
// to OpenCore's next scan and creates an extra bypass entry.
pub fn releaseNtfs() void {
    const bs = u.system_table.boot_services orelse return;
    for (ntfs_drivers,0..) |maybe_driver,index| {
        const driver = maybe_driver orelse continue;
        var detached = true;
        for (ntfs_targets[0..ntfs_target_count],0..) |target,i| {
            if (ntfs_target_driver[i] == index) bs.disconnectController(target,driver,null) catch { detached=false; };
        }
        if (detached) _ = bs.unloadImage(driver) catch .aborted;
    }
    ntfs_target_count=0;
    ntfs_drivers=.{null,null};
}

fn say(s: []const u8) void {
    var wide: [512:0]u16 = undefined;
    if (s.len > 511) return;
    for (s,0..) |c,i| wide[i] = c;
    wide[s.len] = 0;
    if (u.system_table.con_out) |con| _ = con.outputString(@ptrCast(&wide)) catch false;
}

fn trace(comptime vm: bool, s: []const u8) void {
    @import("root").diagnosticTrace(s);
    if (vm) for (s) |c| {
        asm volatile ("outb %[byte], %[port]" : : [byte] "{al}" (c), [port] "{dx}" (@as(u16,0xe9)));
    };
}

pub fn read(root: *File, path: [*:0]const u16, limit: usize) ![]const u8 {
    if (limit >= buffer.len) return error.InvalidReadLimit;
    const file = try root.open(path,.read,.{});
    defer file.close() catch {};
    var used: usize = 0;
    while (true) {
        const n = try file.read(buffer[used..limit+1]);
        used += n;
        if (used > limit) return error.FileTooLarge;
        if (n == 0) return buffer[0..used];
    }
}

fn hashMatches(data: []const u8, expected: *const [32]u8) bool {
    var digest: [32]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(data,&digest,.{});
    return std.mem.eql(u8,&digest,expected);
}

pub fn matches(dp: *const u.protocol.DevicePath, guid: *const [16]u8) bool {
    const bytes: [*]const u8 = @ptrCast(dp);
    var offset: usize = 0;
    var found = false;
    while (offset+4 <= 4096) {
        const len = std.mem.readInt(u16,bytes[offset+2..][0..2],.little);
        if (len < 4 or offset+len > 4096) return false;
        if (bytes[offset] == 0x7f) return bytes[offset+1] == 0xff and len == 4 and found;
        if (bytes[offset] == 4 and bytes[offset+1] == 1) {
            if (found or len != 42 or bytes[offset+40] != 2 or bytes[offset+41] != 2) return false;
            if (!std.mem.eql(u8,bytes[offset+24..][0..16],guid)) return false;
            found = true;
        }
        offset += len;
    }
    return false;
}

pub fn partition(guid: *const [16]u8) !u.Handle {
    const bs = u.system_table.boot_services orelse return error.NoBootServices;
    const handles = (try bs.locateHandleBuffer(.{.by_protocol=&u.protocol.BlockIo.guid})) orelse return error.NoBlocks;
    // locateHandleBuffer allocates directly through firmware, not Zig's
    // metadata-prefixed pool_allocator. Free it through the matching API.
    defer bs.freePool(@alignCast(@ptrCast(handles.ptr))) catch {};
    var found: ?u.Handle = null;
    for (handles) |h| {
        const dp = (try bs.handleProtocol(u.protocol.DevicePath,h)) orelse continue;
        if (matches(dp,guid)) {
            if (found != null) return error.AmbiguousPartitionGuid;
            found = h;
        }
    }
    return found orelse error.PartitionUnavailable;
}

pub fn load(device: u.Handle, path: [*:0]const u16, expected: ?*const [32]u8) !u.Handle {
    return loadLogical(device,path,expected,device,path);
}

fn loadLogical(device: u.Handle, path: [*:0]const u16, expected: ?*const [32]u8,
    logical_device: u.Handle, logical_path: [*:0]const u16) !u.Handle {
    const bs = u.system_table.boot_services orelse return error.NoBootServices;
    const dp = (try bs.handleProtocol(u.protocol.DevicePath,logical_device)) orelse return error.NoPath;
    const fs = (try bs.handleProtocol(u.protocol.SimpleFileSystem,device)) orelse return error.NoFilesystem;
    const root = try fs.openVolume();
    defer root.close() catch {};
    const data = try read(root,path,15*1024*1024);
    if (data.len < 1024 or !std.mem.eql(u8,data[0..2],"MZ")) return error.InvalidLoader;
    if (expected) |digest| if (!hashMatches(data,digest)) return error.LoaderHashChanged;
    const file_dp = try dp.createFileDevicePath(u.pool_allocator,std.mem.sliceTo(logical_path,0));
    // Keep its device path alive for the loaded child's lifetime.
    var child: u.Handle = undefined;
    try bs._loadImage(false,u.handle,file_dp,data.ptr,data.len,&child).err();
    errdefer _ = bs.unloadImage(child) catch .aborted;
    const loaded = (try bs.handleProtocol(u.protocol.LoadedImage,child)) orelse return error.NoChildInfo;
    if (loaded.device_handle != logical_device) return error.WrongChildVolume;
    return child;
}

fn ensureNtfs(own: u.Handle, target: u.Handle) !void {
    const bs = u.system_table.boot_services orelse return error.NoBootServices;
    // An already-working firmware/OpenCore filesystem is sufficient. Do not
    // replace it or connect a second driver to unrelated controllers.
    if ((try bs.handleProtocol(u.protocol.SimpleFileSystem,target)) != null) return;
    const block = (try bs.handleProtocol(u.protocol.BlockIo,target)) orelse return error.NoBlocks;
    const index: usize = switch (block.media.block_size) { 512 => 0,4096 => 1,else => return error.UnsupportedSectorSize };
    if (ntfs_drivers[index] == null) {
        var expected: [32]u8 = undefined;
        _ = try std.fmt.hexToBytes(&expected,if (index == 0) ntfs_hash else ntfs_4kn_hash);
        const driver = try load(own,if (index == 0) utf16("\\EFI\\WinDeploy\\OpenNtfsDxe.efi") else utf16("\\EFI\\WinDeploy\\Ntfs4Kn.efi"),&expected);
        errdefer _ = bs.unloadImage(driver) catch .aborted;
        const result = try bs.startImage(driver);
        try result.code.err();
        ntfs_drivers[index] = driver;
    }
    if (ntfs_target_count >= ntfs_targets.len) return error.TooManyNtfsTargets;
    // Remember even a partially failed connection so the return path can
    // attempt cleanup. No pre-existing SimpleFileSystem reaches this point.
    ntfs_targets[ntfs_target_count]=target;
    ntfs_target_driver[ntfs_target_count]=index;
    ntfs_target_count+=1;
    var drivers = [_:null]?u.Handle{ntfs_drivers[index].?};
    try bs.connectController(target,&drivers,null,false);
    if ((try bs.handleProtocol(u.protocol.SimpleFileSystem,target)) == null) return error.NtfsUnavailable;
}

pub fn launch(comptime vm: bool, own: u.Handle, item: *const p.Entry,menu_bytes: []const u8,index: usize) !void {
    if (!item.available()) return error.EntryUnavailable;
    if (vm and (!std.mem.eql(u8,&item.boot_guid,&synthetic_guid) or !item.hashLocked())) return error.NotVmFixture;
    trace(vm,"CHOOSER: finding GPT partition\n");
    const target = try partition(&item.boot_guid);
    trace(vm,"CHOOSER: GPT partition found\n");
    if (target == own) {
        var narrow: [256]u8 = undefined;
        var length: usize = 0;
        for (item.path) |c| {
            if (c == 0) break;
            narrow[length] = @intCast(c); length += 1;
        }
        if (std.ascii.eqlIgnoreCase(narrow[0..length],"\\EFI\\Boot\\BOOTX64.EFI") or
            std.ascii.eqlIgnoreCase(narrow[0..length],"\\EFI\\Microsoft\\Boot\\bootmgfw.efi")) return error.PublicEntryRecursion;
    }
    if (item.ntfs()) {
        trace(vm,"CHOOSER: preparing NTFS access\n");
        try ensureNtfs(own,target);
        trace(vm,"CHOOSER: NTFS access ready\n");
    }
    trace(vm,"CHOOSER: loading selected EFI image\n");
    // Developer-only experiment: source remains the pinned stock NTFS loader,
    // but its logical system device is the synthetic ESP containing its BCD.
    // No store activation/writes are implemented or enabled in production.
    const child = if (vm and logical_esp_probe) blk: {
        trace(vm,"CHOOSER: diagnostic ESP logical boot path\n");
        break :blk try loadLogical(target,@ptrCast(&item.path),&item.digest,
            own,utf16("\\EFI\\Microsoft\\Boot\\bootmgfw.efi"));
    } else try load(target,@ptrCast(&item.path),if (item.hashLocked()) &item.digest else null);
    const bs = u.system_table.boot_services orelse return error.NoBootServices;
    defer _ = bs.unloadImage(child) catch .aborted;
    if (activate_stores) {
        trace(vm,"DIAGNOSTIC: opening and validating BCD configuration\n");
        var stores=try @import("store_activation.zig").Context.open(own,menu_bytes);
        defer stores.close();
        const windows=try partition(&item.windows_guid);
        trace(vm,"DIAGNOSTIC: activating selected Windows BCD store\n");
        try stores.activate(windows,@intCast(index));
        trace(vm,"CHOOSER: bound per-OS canonical store activated; both GPT identities verified\n");
    }
    if (vm and vm_preflight) {
        trace(vm,"SYSTEM-CHOOSER-PREFLIGHT: PASS; loaded child is unloaded WITHOUT executing Windows\n");
        return;
    }
    trace(vm,"CHOOSER: selected partition and loader verified; starting Windows\n");
    try @import("root").requireFlushedLog();
    const result = try bs.startImage(child);
    try result.code.err();
}
