// PRIVATE isolated boot experiment. Never installed as a default boot loader.
// Adds one data-only SSDT in RAM, then starts the hash-pinned existing Windows
// boot manager. No PCI/GMUX/power/flash/NVRAM/registry/driver writes.
// v2: publish private root-table copies via EFI1 InstallConfigurationTable.
// No EFI_ACPI_TABLE_PROTOCOL required; original firmware tables never written.
const std = @import("std");
const u = std.os.uefi;
const p = @import("acpi_parse.zig");
const clone = @import("acpi_clone.zig");
const utf16 = std.unicode.utf8ToUtf16LeStringLiteral;
const File = u.protocol.File;
const automatic_entry = @import("boot_profile").automatic;
const independent_loader = if (@hasDecl(@import("boot_profile"), "independent_loader")) @import("boot_profile").independent_loader else false;
const experimental = @import("experimental_paths.zig");
const dock_test = if (@hasDecl(@import("boot_profile"), "dock_resource_test")) @import("boot_profile").dock_resource_test else false;
const dock = @import("dock_patch.zig");
pub fn isAutomaticProfile() bool { return automatic_entry; }
const patch = @embedFile("SSDT-NMBROM.aml");
const marker = if (dock_test) "WinDeploy PRIVATE Windows7 PEG1 resource test 20261009 v1\n" else if (automatic_entry) "WinDeploy PRIVATE Windows7 automatic M370X 20261009 v1\n" else "WinDeploy PRIVATE Windows7 ROM test 20261009 v2\n";
const marker_path = if (dock_test) utf16("\\EFI\\W7DEP-DOCKTEST\\ALLOW-DOCKTEST.txt") else if (automatic_entry) utf16("\\EFI\\W7DEP-WIN7-AUTO\\ALLOW-AUTO.txt") else utf16("\\EFI\\W7DEP-ROMTEST2\\ALLOW-WIN7-ROM-TEST2.txt");
const log_path = if (dock_test) utf16("\\EFI\\W7DEP-DOCKTEST") else if (automatic_entry) utf16("\\EFI\\W7DEP-WIN7-AUTO") else utf16("\\EFI\\W7DEP-ROMTEST2");
const loader_path = utf16("\\EFI\\Microsoft\\Boot\\bootmgfw.efi");
// The automatic entry occupies loader_path on disk. Read ONLY the separately
// preserved original bytes, then pass the original logical device path to
// LoadImage with a non-null SourceBuffer. Firmware does not re-read that path.
// This keeps Windows' BCD discovery identical to the proven manual experiment
// and prevents the wrapper recursively loading itself.
const loader_source_path = if (automatic_entry) utf16("\\EFI\\Microsoft\\Boot\\bootmgfw-w7dep-original.efi") else loader_path;
const expected_loader = hex("6470d503fc884f23b515457a03018ece8bf6f6c5bd358f3b5518711ebadb5161");
const expected_config = hex("4929f116eb5d2de4afa8d712601eb39e515ca6685d883d4a3e4a529a14b67746");
const namespace = [_][]const u8{
    @embedFile("collected-capture-000/003-DSDT.dat"),
    @embedFile("collected-capture-000/008-SSDT.dat"),
    @embedFile("collected-capture-000/009-SSDT.dat"),
    @embedFile("collected-capture-000/010-SSDT.dat"),
    @embedFile("collected-capture-000/011-SSDT.dat"),
    @embedFile("collected-capture-000/012-SSDT.dat"),
    @embedFile("collected-capture-000/013-SSDT.dat"),
    @embedFile("collected-capture-000/014-SSDT.dat"),
    @embedFile("collected-capture-000/015-SSDT.dat"),
    @embedFile("collected-capture-000/016-SSDT.dat"),
    @embedFile("collected-capture-000/017-SSDT.dat"),
    @embedFile("collected-capture-000/020-VFCT.dat"),
};
var map_storage: [512 * 1024]u8 align(8) = undefined;
var memory: u.tables.MemoryMapSlice = undefined;
var file_storage: [8 * 1024 * 1024]u8 = undefined;
var log: ?*File = null;
var roots_seen: [8]u64 = undefined;
var roots_count: usize = 0;
var patch_roots: usize = 0;
var matched: [namespace.len]bool = undefined;
var observed_namespace = @import("namespace_snapshot.zig").Snapshot{};

fn hex(comptime value: []const u8) [32]u8 {
    comptime {
        var result: [32]u8 = undefined;
        _ = std.fmt.hexToBytes(&result, value) catch @compileError("bad hash");
        return result;
    }
}
fn digest(data: []const u8) [32]u8 {
    var result: [32]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(data, &result, .{});
    return result;
}
fn message(s: []const u8) void {
    var buf: [256:0]u16 = undefined;
    if (s.len > 256) return;
    for (s, 0..) |c, i| buf[i] = c;
    buf[s.len] = 0;
    if (u.system_table.con_out) |con| _ = con.outputString(@ptrCast(&buf)) catch false;
}
fn record(s: []const u8) !void {
    message(s);
    if (log) |f| {
        if (try f.write(s) != s.len) return error.ShortLogWrite;
        try f.flush();
    }
}
fn closeLog() void {
    if (log) |f| { f.close() catch {}; log = null; }
}
fn readFile(root: *File, path: [*:0]const u16) ![]const u8 {
    const f = try root.open(path, .read, .{});
    defer f.close() catch {};
    var n: usize = 0;
    while (n < file_storage.len) {
        const got = try f.read(file_storage[n..@min(file_storage.len, n + 65536)]);
        if (got == 0) return file_storage[0..n];
        n += got;
    }
    return error.FileTooLarge;
}
fn newLog(dir: *File) !*File {
    for (0..100) |i| {
        var ascii: [24]u8 = undefined;
        const name = try std.fmt.bufPrint(&ascii, "test-{d:0>3}.txt", .{i});
        var path: [24:0]u16 = undefined;
        for (name, 0..) |c, n| path[n] = c;
        path[name.len] = 0;
        const z: [*:0]const u16 = @ptrCast(&path);
        const existing = dir.open(z, .read, .{}) catch |err| {
            if (err == error.NotFound) return dir.open(z, .read_write_create, .{});
            return err;
        };
        try existing.close();
    }
    return error.NoUnusedLog;
}
fn readMemory(address: u64, length: usize) ![]const u8 {
    if (length == 0 or length > 1024 * 1024) return error.BadLength;
    var it = memory.iterator();
    while (it.next()) |d| {
        if (d.attribute.rp) continue;
        switch (d.type) {
            .acpi_reclaim_memory, .acpi_memory_nvs, .boot_services_code, .boot_services_data,
            .runtime_services_code, .runtime_services_data, .loader_code, .loader_data, .reserved_memory_type => {},
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
fn matchOriginal(data: []const u8) !void {
    if (independent_loader) return observed_namespace.check(data);
    // Byte-exact matching deliberately refuses changed firmware/unknown tables.
    for (namespace, 0..) |want, i| {
        if (std.mem.eql(u8, data, want)) { matched[i] = true; return; }
    }
    try record("Unknown/changed namespace or VFCT table; no blind override.\r\n");
    return error.FirmwareProfileChanged;
}
fn rootTable(address: u64, width: usize, expect_patch: bool) !void {
    for (roots_seen[0..roots_count]) |a| if (a == address) return;
    if (roots_count == roots_seen.len) return error.TooManyRoots;
    roots_seen[roots_count] = address;
    roots_count += 1;
    const root = try table(address);
    const n = try p.rootEntryCount(root, width);
    var additions: usize = 0;
    var replacements: usize = 0;
    for (0..n) |i| {
        const child = if (width == 8) try p.u64le(root, 36 + i * 8) else try p.u32le(root, 36 + i * 4);
        const data = try table(child);
        if (std.mem.eql(u8, data, patch)) { additions += 1; continue; }
        if (dock_test) {
            if (std.mem.eql(u8, data, &dock.changed)) {
                if (!expect_patch) return error.UnexpectedDockPatch;
                replacements += 1;
                try matchOriginal(dock.original);
                continue;
            }
            if (expect_patch and std.mem.eql(u8, data, dock.original)) return error.DockPatchMissing;
        }
        const sig = data[0..4];
        if (std.mem.eql(u8, sig, "PSDT")) return error.UnexpectedPsdt;
        if (std.mem.eql(u8, sig, "SSDT") or std.mem.eql(u8, sig, "DSDT") or std.mem.eql(u8, sig, "VFCT")) try matchOriginal(data);
        if (std.mem.eql(u8, sig, "FACP")) {
            var dsdt = if (data.len >= 148) try p.u64le(data, 140) else 0;
            if (dsdt == 0) dsdt = try p.u32le(data, 40);
            const d = try table(dsdt);
            if (!std.mem.eql(u8, d[0..4], "DSDT")) return error.WrongDsdt;
            try matchOriginal(d);
        }
    }
    if (additions != @as(usize, if (expect_patch) 1 else 0)) return error.PatchLinkMismatch;
    if (dock_test and replacements != @as(usize, if (expect_patch) 1 else 0)) return error.DockLinkMismatch;
    if (additions == 1) patch_roots += 1;
}
fn inspectFirmware(expect_patch: bool) !void {
    const bs = u.system_table.boot_services orelse return error.NoBootServices;
    memory = try bs.getMemoryMap(&map_storage);
    if (memory.info.descriptor_size < @sizeOf(u.tables.MemoryDescriptor)) return error.BadMemoryMap;
    roots_count = 0;
    patch_roots = 0;
    matched = @splat(false);
    if (independent_loader) observed_namespace.begin();
    const cfg = u.system_table.configuration_table;
    if (u.system_table.number_of_table_entries > 1024) return error.TooManyConfigEntries;
    for (cfg[0..u.system_table.number_of_table_entries]) |entry| {
        const ct = u.tables.ConfigurationTable;
        if (!entry.vendor_guid.eql(ct.acpi_20_table_guid) and !entry.vendor_guid.eql(ct.acpi_10_table_guid)) continue;
        const address = @intFromPtr(entry.vendor_table);
        var data = try readMemory(address, 20);
        if (data[15] >= 2) {
            const head = try readMemory(address, 36);
            const len = try p.u32le(head, 20);
            if (len < 36 or len > 4096) return error.BadLength;
            data = try readMemory(address, len);
        }
        try p.validateRsdp(data);
        const rsdt = try p.u32le(data, 16);
        const xsdt = if (data[15] >= 2) try p.u64le(data, 24) else 0;
        if (xsdt != 0) try rootTable(xsdt, 8, expect_patch);
        if (rsdt != 0) try rootTable(rsdt, 4, expect_patch);
    }
    if (roots_count == 0) return error.NoRoots;
    if (independent_loader) {
        try observed_namespace.finish();
    } else {
        for (matched) |found| if (!found) return error.MissingExpectedTable;
    }
    if (expect_patch and patch_roots != roots_count) return error.PatchLinkMismatch;
}
fn validDevicePath(dp: *const u.protocol.DevicePath) !void {
    var cursor = dp;
    var total: usize = 0;
    while (total < 4096) {
        const n = cursor.length;
        if (n < 4 or n > 4096 - total) return error.BadDevicePath;
        if (cursor.type == .end) {
            if (cursor.subtype != 0xff or n != 4) return error.BadDevicePath;
            return;
        }
        total += n;
        cursor = @ptrFromInt(@intFromPtr(cursor) + n);
    }
    return error.BadDevicePath;
}
fn holdForPowerOff() noreturn {
    message("ROLLBACK COULD NOT BE VERIFIED. Do NOT boot another OS this session.\r\n");
    message(if (automatic_entry) "Power off; use the saved original loader or restore the backup.\r\n" else "Power off, then use your unchanged normal boot entry.\r\n");
    // Never return to the picker with an unverified live ACPI alteration.
    while (true) { if (u.system_table.boot_services) |bs| bs.stall(1_000_000) catch {}; }
}
const Blob = struct { original: []const u8, hash: [32]u8, copy: []u8 };
const Publication = struct { guid: u.Guid, blob: Blob };
const RamPlan = struct {
    bs: *u.tables.BootServices,
    pages: [8][]align(4096) u.Page = undefined,
    pages_count: usize = 0,
    roots: [4]Blob = undefined,
    root_count: usize = 0,
    pubs: [2]Publication = undefined,
    pub_count: usize = 0,
    config: [1024]u.tables.ConfigurationTable = undefined,
    config_count: usize = 0,
    patch_copy: []u8 = undefined,
    dock_blob: ?Blob = null,

    fn allocate(self: *@This(), length: usize) ![]u8 {
        if (length == 0 or length > 1024 * 1024 or self.pages_count == self.pages.len) return error.BadAllocation;
        // Below 4GB for RSDT. NVS survives ExitBootServices and resume; same
        // memory class used by OpenCore's ACPI root-table allocations.
        const pages = try self.bs.allocatePages(.{ .max_address = @ptrFromInt(0xfffff000) }, .acpi_memory_nvs, (length + 4095) / 4096);
        self.pages[self.pages_count] = pages;
        self.pages_count += 1;
        const bytes = std.mem.sliceAsBytes(pages);
        if (@intFromPtr(bytes.ptr) == 0 or @intFromPtr(bytes.ptr) + bytes.len > 0x100000000) return error.BadAllocation;
        @memset(bytes, 0);
        return bytes[0..length];
    }
    fn release(self: *@This()) void {
        while (self.pages_count > 0) {
            self.pages_count -= 1;
            // Only reached after verified restoration or before publication.
            self.bs.freePages(self.pages[self.pages_count]) catch {};
        }
    }
    fn copyRoot(self: *@This(), address: u64, width: usize) !u64 {
        if (address == 0) return 0;
        for (self.roots[0..self.root_count]) |r| {
            if (@intFromPtr(r.original.ptr) == address) {
                _ = try p.rootEntryCount(r.original, width);
                return @intFromPtr(r.copy.ptr);
            }
        }
        if (self.root_count == self.roots.len) return error.TooManyRoots;
        const src = try table(address);
        _ = try p.rootEntryCount(src, width);
        const dst = try self.allocate(src.len + width);
        try clone.root(src, dst, width, @intFromPtr(self.patch_copy.ptr));
        if (dock_test) {
            const n = try p.rootEntryCount(src, width);
            var original_address: ?u64 = null;
            for (0..n) |i| {
                const addr = if (width == 8) try p.u64le(src, 36 + i * width) else try p.u32le(src, 36 + i * width);
                const data = try table(addr);
                if (std.mem.eql(u8, data, dock.original)) {
                    if (original_address != null) return error.DuplicateDockTable;
                    original_address = addr;
                    if (self.dock_blob) |b| {
                        if (@intFromPtr(b.original.ptr) != addr) return error.DifferentDockOriginal;
                    } else {
                        const replacement = try self.allocate(dock.changed.len);
                        @memcpy(replacement, &dock.changed);
                        self.dock_blob = .{ .original = data, .hash = digest(data), .copy = replacement };
                    }
                }
            }
            const old = original_address orelse return error.MissingDockTable;
            try dock.replaceRootLink(dst, width, old, @intFromPtr(self.dock_blob.?.copy.ptr));
        }
        self.roots[self.root_count] = .{ .original = src, .hash = digest(src), .copy = dst };
        self.root_count += 1;
        return @intFromPtr(dst.ptr);
    }
    fn prepare(self: *@This()) !void {
        const count = u.system_table.number_of_table_entries;
        if (count == 0 or count > self.config.len) return error.BadConfigCount;
        @memcpy(self.config[0..count], u.system_table.configuration_table[0..count]);
        self.config_count = count;
        self.patch_copy = try self.allocate(patch.len);
        @memcpy(self.patch_copy, patch);
        var saw1 = false;
        var saw2 = false;
        const ct = u.tables.ConfigurationTable;
        for (self.config[0..count]) |entry| {
            const is1 = entry.vendor_guid.eql(ct.acpi_10_table_guid);
            const is2 = entry.vendor_guid.eql(ct.acpi_20_table_guid);
            if (!is1 and !is2) continue;
            if ((is1 and saw1) or (is2 and saw2) or self.pub_count == 2) return error.DuplicateAcpiGuid;
            if (is1) saw1 = true else saw2 = true;
            const addr = @intFromPtr(entry.vendor_table);
            const head = try readMemory(addr, 20);
            // Private fixture is specifically this Mac's 20-byte ACPI1 plus
            // 36-byte ACPI2. Do not coerce revisions or guess unusual layouts.
            if ((is1 and head[15] != 0) or (is2 and head[15] != 2)) return error.UnexpectedRsdpVersion;
            const src = try readMemory(addr, if (is1) 20 else 36);
            try p.validateRsdp(src);
            const dst = try self.allocate(src.len);
            const rsdt = try self.copyRoot(try p.u32le(src, 16), 4);
            const xsdt = if (is2) try self.copyRoot(try p.u64le(src, 24), 8) else 0;
            try clone.rsdp(src, dst, rsdt, xsdt);
            self.pubs[self.pub_count] = .{ .guid = entry.vendor_guid, .blob = .{ .original = src, .hash = digest(src), .copy = dst } };
            self.pub_count += 1;
        }
        if (independent_loader) {
            if (self.pub_count==0 or self.root_count==0) return error.UnexpectedRootLayout;
        } else if (!saw1 or !saw2 or self.pub_count != 2 or self.root_count != 3) return error.UnexpectedRootLayout;
        try self.verifyOriginalBytes();
        try self.verifyConfig(false);
        try record("Prepared private ACPI root pointers and ROM SSDT below 4GB. Original tables retained.\r\n");
        if (dock_test) try record("Private TbtPEG11 copy: PEG1 _DSM function5 return 1 -> 0 only; PCI0 unchanged.\r\n");
    }
    fn verifyOriginalBytes(self: *@This()) !void {
        if (self.dock_blob) |b| if (!std.mem.eql(u8, &digest(b.original), &b.hash)) return error.OriginalDockChanged;
        for (self.roots[0..self.root_count]) |r| if (!std.mem.eql(u8, &digest(r.original), &r.hash)) return error.OriginalRootChanged;
        for (self.pubs[0..self.pub_count]) |pub_entry| if (!std.mem.eql(u8, &digest(pub_entry.blob.original), &pub_entry.blob.hash)) return error.OriginalRsdpChanged;
    }
    fn lookup(guid: u.Guid) !usize {
        const count = u.system_table.number_of_table_entries;
        if (count == 0 or count > 1024) return error.BadConfigCount;
        var found: ?usize = null;
        for (u.system_table.configuration_table[0..count]) |entry| {
            if (entry.vendor_guid.eql(guid)) {
                if (found != null) return error.DuplicateConfigGuid;
                found = @intFromPtr(entry.vendor_table);
            }
        }
        return found orelse error.MissingConfigGuid;
    }
    fn verifyConfig(self: *@This(), published: bool) !void {
        if (u.system_table.number_of_table_entries != self.config_count) return error.ConfigCountChanged;
        for (self.config[0..self.config_count]) |entry| {
            var want = @intFromPtr(entry.vendor_table);
            if (published) {
                for (self.pubs[0..self.pub_count]) |pub_entry| if (entry.vendor_guid.eql(pub_entry.guid)) { want = @intFromPtr(pub_entry.blob.copy.ptr); break; };
            }
            if (try lookup(entry.vendor_guid) != want) return error.ConfigPointerChanged;
        }
    }
};
const BootContext = struct {
    plan: *RamPlan,
    bs: *u.tables.BootServices,
    child: u.Handle,
    pub fn checkOriginal(self: *@This(), i: usize) !void {
        const e = self.plan.pubs[i];
        if (try RamPlan.lookup(e.guid) != @intFromPtr(e.blob.original.ptr)) return error.ConfigPointerChanged;
        try self.plan.verifyOriginalBytes();
    }
    pub fn publish(self: *@This(), i: usize) !void {
        const e = self.plan.pubs[i];
        try record("Publishing private ACPI root pointer.\r\n");
        try self.bs.installConfigurationTable(&e.guid, @ptrCast(e.blob.copy.ptr));
    }
    pub fn checkPublished(self: *@This(), i: usize) !void {
        const e = self.plan.pubs[i];
        if (try RamPlan.lookup(e.guid) != @intFromPtr(e.blob.copy.ptr)) return error.PublicationNotVisible;
    }
    pub fn rollback(self: *@This(), attempted: usize) void {
        clone.restore(self, attempted) catch { holdForPowerOff(); };
        record("Original ACPI pointers and bytes restored and verified.\r\n") catch {};
    }
    pub fn restoreOne(self: *@This(), i: usize) !void {
        const e = self.plan.pubs[i];
        const current = RamPlan.lookup(e.guid) catch 0;
        if (current != @intFromPtr(e.blob.original.ptr)) try self.bs.installConfigurationTable(&e.guid, @ptrCast(@constCast(e.blob.original.ptr)));
    }
    pub fn verifyRestored(self: *@This()) !void {
        var failed = false;
        self.plan.verifyConfig(false) catch { failed = true; };
        self.plan.verifyOriginalBytes() catch { failed = true; };
        inspectFirmware(false) catch { failed = true; };
        if (failed) return error.RollbackUnverified;
    }
    pub fn verify(self: *@This()) !void {
        try self.plan.verifyConfig(true);
        try self.plan.verifyOriginalBytes();
        try inspectFirmware(true);
        try record("One temporary data-only SSDT verified in every ACPI root.\r\n");
        if (dock_test) try record("One byte plus checksum changed in private Thunderbolt SSDT; original preserved.\r\n");
    }
    pub fn start(self: *@This()) !void {
        try record(if (automatic_entry) "Starting saved original Windows7 loader with its original device path.\r\n" else "Starting existing Windows7 boot manager now. No persistent boot change.\r\n");
        const result = try self.bs.startImage(self.child);
        try result.code.err();
    }
};
fn run() !void {
    try p.validateTable(patch);
    if (!std.mem.eql(u8, patch[0..4], "SSDT") or !std.mem.eql(u8, patch[16..24], "NMBROM01")) return error.WrongPatch;
    const bs = u.system_table.boot_services orelse return error.NoBootServices;
    const loaded = (try bs.handleProtocol(u.protocol.LoadedImage, u.handle)) orelse return error.NoLoadedImage;
    const device = loaded.device_handle orelse return error.NoDevice;
    const fs = (try bs.handleProtocol(u.protocol.SimpleFileSystem, device)) orelse return error.NoFilesystem;
    const root = try fs.openVolume();
    defer root.close() catch {};
    var folder_path: [256:0]u16 = undefined;
    if (independent_loader) folder_path = try experimental.ownFolder(loaded.file_path);
    if (!independent_loader and !std.mem.eql(u8, try readFile(root, marker_path), marker)) return error.WrongMarker;
    const dir = try root.open(if (independent_loader) @ptrCast(&folder_path) else log_path, .read, .{});
    defer dir.close() catch {};
    // The stock 8.1/10 overlay can be reached through a read-only NTFS EFI
    // driver. Diagnostic-file creation is optional ONLY in experimental mode;
    // failures remain visible on the console, never silently bypass a check.
    log = if (independent_loader) newLog(dir) catch null else try newLog(dir);
    if (independent_loader and log == null) message("EFI diagnostic file unavailable; messages are shown on screen.\r\n");
    const selected_hash = if (independent_loader) try experimental.loaderHash(try readFile(dir, utf16("Entry.bin"))) else expected_loader;
    if (independent_loader) try record("EXPERIMENTAL M370X ROM table, not a verified Iris/other-GPU fix. Original namespace snapshot verified for this boot only.\r\n");
    try record(if (dock_test) "WinDeploy PRIVATE one-boot PEG1 resource test v1; proven ROM SSDT retained\r\n" else if (automatic_entry) "WinDeploy PRIVATE automatic Windows7 M370X entry v1\r\n" else "WinDeploy PRIVATE Windows7 RAM-only ROM test v2 (private roots)\r\n");
    if (!independent_loader and !std.mem.eql(u8, &digest(try readFile(root, utf16("\\EFI\\OC\\config.plist"))), &expected_config)) return error.OpenCoreConfigChanged;
    try inspectFirmware(false);
    try record(if (independent_loader) "Original ACPI namespace captured; no model-specific changes beyond the selected ROM-table experiment.\r\n" else "Captured DSDT/10 SSDTs/VFCT byte-match; OpenCore config hash matches.\r\n");
    const dp = (try bs.handleProtocol(u.protocol.DevicePath, device)) orelse return error.NoDevicePath;
    try validDevicePath(dp);
    const win_dp = try dp.createFileDevicePath(u.pool_allocator, loader_path);
    // Retain path storage for the full loaded-image lifetime (small, reclaimed by firmware).
    const win_data = if (independent_loader) try readFile(dir, utf16("Original.efi")) else try readFile(root, loader_source_path);
    if (!std.mem.eql(u8, &digest(win_data), &selected_hash)) return error.WindowsLoaderChanged;
    var child: u.Handle = undefined;
    // Both verified bytes AND full same-volume path: preserves BCD discovery and
    // avoids a second unverified file read by LoadImage. Never selects a new loader.
    try bs._loadImage(false, u.handle, win_dp, win_data.ptr, win_data.len, &child).err();
    defer _ = bs.unloadImage(child) catch .aborted;
    const child_info = (try bs.handleProtocol(u.protocol.LoadedImage, child)) orelse return error.NoChildInfo;
    if (child_info.device_handle != device) return error.ChildWrongVolume;
    try inspectFirmware(false);
    try record("Existing hash-pinned Windows loader loaded from same EFI volume.\r\n");
    var plan = RamPlan{ .bs = bs };
    defer plan.release();
    try plan.prepare();
    var context = BootContext{ .plan = &plan, .bs = bs, .child = child };
    try clone.boot(&context, plan.pub_count);
}
pub fn main() u.Status {
    defer closeLog();
    message(if (independent_loader) "WinDeploy experimental M370X ROM table. Selected Windows boot only; RAM only.\r\n" else if (dock_test) "WinDeploy private dock resource test; this Windows boot only.\r\n" else if (automatic_entry) "WinDeploy Windows7 M370X helper. Applying RAM-only ACPI fix.\r\n" else "WinDeploy PRIVATE ROM experiment: Windows only, this boot only.\r\n");
    run() catch |err| {
        record("Test stopped: ") catch {}; record(@errorName(err)) catch {}; record("\r\n") catch {};
        message(if (automatic_entry and !dock_test) "Helper stopped safely. Use saved original loader or restore backup.\r\n" else "No persistent boot change. Type exit and use normal Windows entry.\r\n");
        return .aborted;
    };
    return .success;
}

test "embedded patch and firmware profile are valid and exact" {
    try p.validateTable(patch);
    try std.testing.expectEqualStrings("NMBROM01", patch[16..24]);
    for (namespace) |data| try p.validateTable(data);
    matched = @splat(false);
    observed_namespace = .{};
    for (namespace) |data| try matchOriginal(data);
    if (!independent_loader) { for (matched) |m| try std.testing.expect(m); }
    try std.testing.expectEqual(@as(usize, 12), namespace.len);
}
test "loader fingerprint is fixed" {
    try std.testing.expect(!std.mem.eql(u8, &digest("not Windows"), &expected_loader));
    try std.testing.expectEqual(@as(u8, 0x64), expected_loader[0]);
}

test "automatic route reads separate original bytes but retains proven Windows path" {
    try std.testing.expectEqualSlices(u16, utf16("\\EFI\\Microsoft\\Boot\\bootmgfw.efi"), loader_path);
    if (automatic_entry) {
        try std.testing.expect(!std.mem.eql(u16, loader_source_path, loader_path));
        try std.testing.expectEqualSlices(u16, utf16("\\EFI\\Microsoft\\Boot\\bootmgfw-w7dep-original.efi"), loader_source_path);
        try std.testing.expectEqualStrings(if (dock_test) "WinDeploy PRIVATE Windows7 PEG1 resource test 20261009 v1\n" else "WinDeploy PRIVATE Windows7 automatic M370X 20261009 v1\n", marker);
    } else try std.testing.expectEqualSlices(u16, loader_source_path, loader_path);
}
test "bounded single-instance EFI device path" {
    var valid: [8]u8 = .{ 4, 1, 4, 0, 0x7f, 0xff, 4, 0 };
    try validDevicePath(@ptrCast(&valid));
    valid[2] = 0;
    try std.testing.expectError(error.BadDevicePath, validDevicePath(@ptrCast(&valid)));
    valid[2] = 4;
    valid[5] = 1;
    try std.testing.expectError(error.BadDevicePath, validDevicePath(@ptrCast(&valid)));
}
test "RAM publication tests are included in EFI test suite" { _ = clone; }

test "dock profile remains manual and includes exact-table tests" {
    if (dock_test) {
        try std.testing.expect(automatic_entry); // saved-loader source, no recursive wrapper
        try std.testing.expectEqualSlices(u16, utf16("\\EFI\\W7DEP-DOCKTEST"), log_path);
        try std.testing.expectEqualSlices(u16, utf16("\\EFI\\W7DEP-DOCKTEST\\ALLOW-DOCKTEST.txt"), marker_path);
        _ = dock;
    }
}
