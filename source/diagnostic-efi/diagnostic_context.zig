// Read-only session binding. Reports stay beside the launched collector.
// No variables, boot policy, hardware registers or Windows volumes are written.
const std = @import("std");
const u = std.os.uefi;
const File = u.protocol.File;
const utf16 = std.unicode.utf8ToUtf16LeStringLiteral;
var context: [2048]u8 = undefined;
var context_len: usize = 0;
var header: [4096]u8 = undefined;
var header_len: usize = 0;

pub fn value(data: []const u8, key: []const u8) ![]const u8 {
    var lines = std.mem.splitScalar(u8, data, '\n');
    var found: ?[]const u8 = null;
    while (lines.next()) |line| {
        if (line.len > key.len and std.mem.eql(u8, line[0..key.len], key) and line[key.len] == '=') {
            if (found != null) return error.DuplicateField;
            found = line[key.len + 1 ..];
        }
    }
    const result = found orelse return error.MissingField;
    if (result.len == 0 or result.len > 180) return error.BadField;
    for (result) |c| if (c < 32 or c > 126) return error.BadField;
    return result;
}
fn hexID(id: []const u8, count: usize) !void {
    if (id.len != count) return error.BadID;
    for (id) |c| if (!std.ascii.isHex(c)) return error.BadID;
}
fn shortName(name: []const u8) bool {
    if(name.len!=8 or !std.ascii.eqlIgnoreCase(name[0..5],"Test-")) return false;
    for(name[5..]) |c| if(c<'0' or c>'9') return false;
    return !std.mem.eql(u8,name[5..],"000");
}
fn validateSession(data: []const u8, name: []const u8) !void {
    const id=try value(data,"session_id"); try hexID(id,32);
    if(shortName(name)) {
        if(!std.ascii.eqlIgnoreCase(name,try value(data,"folder_name"))) return error.SessionMismatch;
    } else if(!std.ascii.eqlIgnoreCase(name,id)) return error.SessionMismatch;
}
pub fn directory(path: []const u8, out: []u16) ![]const u16 {
    const prefix = "\\EFI\\WinDeploy\\Diagnostics\\";
    if (path.len <= prefix.len or !std.ascii.eqlIgnoreCase(path[0..prefix.len], prefix)) return error.WrongDirectory;
    const length = std.mem.indexOfScalar(u8,path[prefix.len..],'\\') orelse return error.WrongDirectory;
    const end = prefix.len + length;
    const name = path[prefix.len..end];
    if (!shortName(name)) hexID(name,32) catch return error.WrongDirectory;
    const leaf = path[end + 1 ..];
    if (!std.ascii.eqlIgnoreCase(leaf, "ACPI.efi") and !std.ascii.eqlIgnoreCase(leaf, "PCI.efi")) return error.WrongCollector;
    if (out.len <= end) return error.PathTooLong;
    for (path[0..end], 0..) |c, i| out[i] = c;
    out[end] = 0;
    return out[0..end];
}
fn imagePath(dp: *const u.protocol.DevicePath, output: []u8) ![]const u8 {
    const bytes: [*]const u8 = @ptrCast(dp);
    var offset: usize = 0;
    var used: usize = 0;
    while (offset + 4 <= 4096) {
        const size = std.mem.readInt(u16, bytes[offset + 2 ..][0..2], .little);
        if (size < 4 or offset + size > 4096) return error.BadDevicePath;
        if (bytes[offset] == 0x7f) {
            if (bytes[offset + 1] != 0xff or size != 4 or used == 0) return error.BadDevicePath;
            return output[0..used];
        }
        if (bytes[offset] != 4 or bytes[offset + 1] != 4 or size < 6 or size % 2 != 0) return error.BadDevicePath;
        var at = offset + 4;
        while (at < offset + size - 2) : (at += 2) {
            const c = std.mem.readInt(u16, bytes[at..][0..2], .little);
            if (c < 32 or c > 126 or used == output.len) return error.BadDevicePath;
            output[used] = @intCast(c); used += 1;
        }
        if (bytes[at] != 0 or bytes[at + 1] != 0) return error.BadDevicePath;
        offset += size;
    }
    return error.BadDevicePath;
}
fn matchesEsp(dp: *const u.protocol.DevicePath, expected: []const u8) !bool {
    if (expected.len != 36) return error.BadGuid;
    var compact: [32]u8 = undefined; var at: usize = 0;
    for (expected, 0..) |c, i| {
        if (i == 8 or i == 13 or i == 18 or i == 23) { if (c != '-') return error.BadGuid; }
        else { if (!std.ascii.isHex(c)) return error.BadGuid; compact[at] = c; at += 1; }
    }
    var canonical: [16]u8 = undefined; _ = try std.fmt.hexToBytes(&canonical, &compact);
    const order = [_]usize{3,2,1,0,5,4,7,6,8,9,10,11,12,13,14,15};
    var wanted: [16]u8 = undefined; for (order, 0..) |i, j| wanted[j] = canonical[i];
    const bytes: [*]const u8 = @ptrCast(dp); var offset: usize = 0; var found = false;
    while (offset + 4 <= 4096) {
        const len = std.mem.readInt(u16, bytes[offset + 2 ..][0..2], .little);
        if (len < 4 or offset + len > 4096) return error.BadDevicePath;
        if (bytes[offset] == 0x7f) return found and bytes[offset + 1] == 0xff and len == 4;
        if (bytes[offset] == 4 and bytes[offset + 1] == 1) {
            if (found or len != 42 or bytes[offset + 40] != 2 or bytes[offset + 41] != 2) return error.NotGPT;
            if (!std.mem.eql(u8, bytes[offset + 24 ..][0..16], &wanted)) return false;
            found = true;
        }
        offset += len;
    }
    return error.BadDevicePath;
}
pub fn open(root: *File, image: *u.protocol.LoadedImage) !*File {
    var ascii: [512]u8 = undefined; const path = try imagePath(image.file_path, &ascii);
    var wide: [512]u16 = undefined; const parent = try directory(path, &wide);
    const dir = try root.open(@ptrCast(parent.ptr), .read, .{});
    errdefer dir.close() catch {};
    const file = try dir.open(utf16("context.txt"), .read, .{});
    defer file.close() catch {};
    context_len = try file.read(&context);
    if (context_len == 0 or context_len == context.len) return error.BadContextSize;
    var extra: [1]u8 = undefined; if (try file.read(&extra) != 0) return error.BadContextSize;
    const data = context[0..context_len];
    if (!std.mem.startsWith(u8, data, "WinDeploy read-only hardware report v1\n")) return error.BadContext;
    const prefix="\\EFI\\WinDeploy\\Diagnostics\\";
    try validateSession(data,path[prefix.len..parent.len]);
    const bs = u.system_table.boot_services orelse return error.NoBootServices;
    const dp = (try bs.handleProtocol(u.protocol.DevicePath, image.device_handle orelse return error.NoDevice)) orelse return error.NoDevicePath;
    if (!try matchesEsp(dp, try value(data, "esp_guid"))) return error.EspMismatch;
    const inherited = try value(data, "macos_model");
    header_len = (try std.fmt.bufPrint(&header, "{s}efi_model_observation=unavailable; inherited model is {s}\n", .{data, inherited})).len;
    if (u.system_table.runtime_services.getTime()) |time| {
        const t = time[0];
        header_len += (try std.fmt.bufPrint(header[header_len..], "efi_capture_firmware_clock={d}-{d:0>2}-{d:0>2}T{d:0>2}:{d:0>2}:{d:0>2}; timezone not assumed UTC\n", .{t.year,t.month,t.day,t.hour,t.minute,t.second})).len;
    } else |_| { header_len += (try std.fmt.bufPrint(header[header_len..], "efi_capture_firmware_clock=unavailable\n", .{})).len; }
    return dir;
}
pub fn reportHeader() []const u8 { return header[0..header_len]; }

test "session directory cannot select boot, traversal or arbitrary outputs" {
    var out: [256]u16 = undefined;
    const base = "\\EFI\\WinDeploy\\Diagnostics\\0123456789abcdef0123456789abcdef";
    try std.testing.expectEqual(@as(usize, base.len), (try directory(base ++ "\\PCI.efi", &out)).len);
    for ([_][]const u8{"\\EFI\\Boot\\PCI.efi",base ++ "\\..\\PCI.efi",base ++ "\\WIN7AUTO.efi"}) |path|
        try std.testing.expectError(if (std.mem.startsWith(u8,path,base)) error.WrongCollector else error.WrongDirectory, directory(path,&out));
}
test "context duplicates and control characters fail" {
    try std.testing.expectEqualStrings("abc",try value("id=abc\n", "id"));
    try std.testing.expectError(error.DuplicateField,value("id=abc\nid=def\n","id"));
    try std.testing.expectError(error.BadField,value("id=x\r\n","id"));
}
test "report destination must be the exact GPT ESP GUID" {
    var path: [46]u8 align(8) = @splat(0);
    path[0] = 4; path[1] = 1; path[2] = 42;
    path[40] = 2; path[41] = 2;
    path[42] = 0x7f; path[43] = 0xff; path[44] = 4;
    const id = "33333333-3333-4333-8333-333333333333";
    const bytes = [_]u8{0x33,0x33,0x33,0x33,0x33,0x33,0x33,0x43,0x83,0x33,0x33,0x33,0x33,0x33,0x33,0x33};
    @memcpy(path[24..40], &bytes);
    try std.testing.expect(try matchesEsp(@ptrCast(&path),id));
    try std.testing.expect(!try matchesEsp(@ptrCast(&path),"22222222-2222-4222-8222-222222222222"));
    path[41] = 1;
    try std.testing.expectError(error.NotGPT,matchesEsp(@ptrCast(&path),id));
    try std.testing.expectError(error.BadGuid,matchesEsp(@ptrCast(&path),"bad-guid"));
}



test "short numbered folders preserve session and destination binding" {
    var out: [256]u16=undefined;
    const base="\\EFI\\WinDeploy\\Diagnostics\\Test-001";
    try std.testing.expectEqual(@as(usize,base.len),(try directory(base++"\\PCI.efi",&out)).len);
    for([_] []const u8{"Test-000","Test-01","Test-0010","Test-../x"}) |name| try std.testing.expect(!shortName(name));
    const data="session_id=0123456789abcdef0123456789abcdef\nfolder_name=Test-001\n";
    try validateSession(data,"TEST-001");
    try std.testing.expectError(error.SessionMismatch,validateSession(data,"Test-002"));
    try std.testing.expectError(error.MissingField,validateSession("session_id=0123456789abcdef0123456789abcdef\n","Test-001"));
    try validateSession("session_id=0123456789abcdef0123456789abcdef\n","0123456789abcdef0123456789abcdef");
}
