// Conservative, read-only identity checks. No firmware writes or activation.
const std = @import("std");
const r = @import("boot_store_parse.zig");
const bootmgr_id = "{9dea862c-5cdd-4e70-acc1-f32b344d4795}";
pub const Identity = struct { esp_disk: [16]u8, esp: [16]u8, windows_disk: [16]u8, windows: [16]u8 };

fn eqText(value: r.Value, expected: []const u8) !void {
    var buffer: [512]u8 = undefined;
    if (!std.ascii.eqlIgnoreCase(try r.text(value,&buffer),expected)) return error.WrongBootPath;
}
fn qualified(data: []const u8, disk: *const [16]u8, part: *const [16]u8) bool {
    if (data.len != 72) return false;
    return std.mem.readInt(u32,data[0..4],.little) == 6 and
        std.mem.allEqual(u8,data[4..8],0) and std.mem.readInt(u32,data[8..12],.little) == 72 and
        std.mem.allEqual(u8,data[12..16],0) and std.mem.eql(u8,data[16..32],part) and
        std.mem.allEqual(u8,data[32..40],0) and std.mem.eql(u8,data[40..56],disk) and
        std.mem.allEqual(u8,data[56..72],0);
}
fn device(value: r.Value, disk: *const [16]u8, part: *const [16]u8) bool {
    return value.kind == 3 and value.data.len == 88 and std.mem.allEqual(u8,value.data[0..16],0)
        and qualified(value.data[16..],disk,part);
}
fn guidText(raw: *const [16]u8, out: *[38]u8) void {
    const order = [_]usize{3,2,1,0,5,4,7,6,8,9,10,11,12,13,14,15};
    const hex = "0123456789abcdef";
    out[0] = '{'; var at: usize = 1;
    for (order,0..) |index,i| {
        if (i == 4 or i == 6 or i == 8 or i == 10) {out[at] = '-'; at += 1;}
        out[at] = hex[raw[index] >> 4]; out[at+1] = hex[raw[index] & 15]; at += 2;
    }
    out[37] = '}';
}
fn objectType(hive: *const r.Hive, object: u32) !u32 {
    const value = try hive.value(try hive.child(object,"Description"),"Type");
    if (value.kind != 4 or value.data.len != 4) return error.InvalidObjectType;
    return r.n(value.data,0);
}
fn recoveryDirectory(path: []const u8) ![]const u8 {
    // Windows setup can register either of these observed layouts. This is
    // not a wildcard: still reject traversal, arbitrary directories and files.
    if (std.ascii.eqlIgnoreCase(path,"\\Recovery\\WindowsRE\\Winre.wim"))
        return path[0..path.len-9];
    if (path.len != 56 or !std.ascii.startsWithIgnoreCase(path,"\\Recovery\\") or
        !std.ascii.endsWithIgnoreCase(path,"\\Winre.wim")) return error.UnknownRecoveryPath;
    for (path[10..46],0..) |c,i| {
        if (i == 8 or i == 13 or i == 18 or i == 23) {
            if (c != '-') return error.UnknownRecoveryPath;
        } else if (!std.ascii.isHex(c)) return error.UnknownRecoveryPath;
    }
    return path[0..47];
}
fn recovery(hive: *const r.Hive, objects: u32, value: r.Value, id: *const Identity) !void {
    const data = value.data;
    if (value.kind != 3 or data.len < 142 or data.len > 1024 or data.len % 2 != 0)
        return error.UnknownRecoveryDevice;
    if (try r.n(data,16) != 0 or try r.n(data,20) != 1 or try r.n(data,24) != data.len-16 or
        try r.n(data,28) != 0 or try r.n(data,32) != 3 or !std.mem.allEqual(u8,data[36..56],0) or
        try r.n(data,56) != 1 or try r.n(data,60) != data.len-56 or try r.n(data,64) != 5)
        return error.UnknownRecoveryDevice;
    if (!qualified(data[68..140],&id.windows_disk,&id.windows)) return error.ForeignRecoveryVolume;
    var buffer: [512]u8 = undefined;
    const path = try r.text(.{.kind=1,.data=data[140..]},&buffer);
    const directory = try recoveryDirectory(path);
    var name: [38]u8 = undefined; guidText(data[0..16],&name);
    const options = try hive.child(objects,&name);
    if (try objectType(hive,options) != 0x30000000 or
        !device(try hive.element(options,"31000003"),&id.windows_disk,&id.windows))
        return error.ForeignRecoverySdi;
    var expected: [55]u8 = undefined;
    @memcpy(expected[0..directory.len],directory);
    @memcpy(expected[directory.len..][0..8],"boot.sdi");
    try eqText(try hive.element(options,"32000004"),expected[0..directory.len+8]);
}

pub fn validate(hive: *const r.Hive, id: *const Identity) !void {
    const objects = try hive.child(hive.root,"Objects");
    const bootmgr = try hive.child(objects,bootmgr_id);
    if (try objectType(hive,bootmgr) != 0x10100002) return error.WrongBootManager;
    var buffer: [512]u8 = undefined;
    const default_name = try r.text(try hive.element(bootmgr,"23000003"),&buffer);
    if (default_name.len != 38) return error.InvalidDefault;
    const loader = try hive.child(objects,default_name);
    if (try objectType(hive,loader) != 0x10200003) return error.InvalidDefault;
    try @import("boot_store_refs.zig").validate(hive,objects,bootmgr,loader);
    const order = try hive.element(bootmgr,"24000001");
    if (order.kind != 7 or order.data.len != 80) return error.MultipleWindowsEntries;
    for (default_name,0..) |c,i| {
        if (order.data[i*2+1] != 0 or std.ascii.toLower(order.data[i*2]) != std.ascii.toLower(c))
            return error.MultipleWindowsEntries;
    }
    if (!std.mem.allEqual(u8,order.data[76..],0)) return error.MultipleWindowsEntries;
    if (!device(try hive.element(bootmgr,"11000001"),&id.esp_disk,&id.esp)) return error.WrongSystemVolume;
    if (!device(try hive.element(loader,"11000001"),&id.windows_disk,&id.windows) or
        !device(try hive.element(loader,"21000001"),&id.windows_disk,&id.windows)) return error.ForeignWindowsVolume;
    try eqText(try hive.element(bootmgr,"12000002"),"\\EFI\\Microsoft\\Boot\\bootmgfw.efi");
    try eqText(try hive.element(loader,"12000002"),"\\Windows\\System32\\winload.efi");
    try eqText(try hive.element(loader,"22000002"),"\\Windows");
    const object_list = try hive.children(objects);
    for (object_list.offsets[0..object_list.count]) |obj| {
        const kind = try objectType(hive,obj);
        if (kind == 0x10100002 and obj != bootmgr) return error.MultipleBootManagers;
        if (kind != 0x10100001 and kind != 0x10100002 and kind != 0x101fffff and kind != 0x10200003 and
            kind != 0x10200004 and kind != 0x30000000 and kind >> 28 != 2) return error.UnknownBootApplication;
        if (kind == 0x10200003 or kind == 0x10200004) {
            const a = try hive.element(obj,"11000001"); const b = try hive.element(obj,"21000001");
            if (a.kind != b.kind or !std.mem.eql(u8,a.data,b.data)) return error.DifferentOsDevice;
            if (device(a,&id.windows_disk,&id.windows)) {
                if (kind == 0x10200003 and obj != loader) return error.MultipleWindowsEntries;
                if (kind == 0x10200004) try eqText(try hive.element(obj,"12000002"),"\\Windows\\System32\\winresume.efi");
            } else {
                if (kind != 0x10200003 or obj == loader) return error.ForeignWindowsVolume;
                try recovery(hive,objects,a,id);
                const pe = try hive.element(obj,"26000022");
                if (pe.kind != 3 or !std.mem.eql(u8,pe.data,&.{1})) return error.RecoveryNotWinPE;
            }
        }
        // Firmware mirrors are preserved opaque metadata, never Windows targets.
        // They may refer to another OS or a disk that is no longer connected.
        if (kind == 0x10100001 or kind == 0x101fffff) continue;
        const elements = hive.child(obj,"Elements") catch |err| {
            if (err == error.MissingKey) continue;
            return err;
        };
        const list = try hive.children(elements);
        for (list.offsets[0..list.count]) |field| {
            var field_buffer: [256]u8 = undefined;
            const field_name = try hive.nodeName(field,&field_buffer);
            if (field_name.len != 8) return error.InvalidElementNumber;
            const number = std.fmt.parseInt(u32,field_name,16) catch return error.InvalidElementNumber;
            if ((number >> 24) & 15 == 1) {
                const val = try hive.value(field,"Element");
                if (!device(val,&id.esp_disk,&id.esp) and !device(val,&id.windows_disk,&id.windows)) {
                    if (kind != 0x10200003 or obj == loader) return error.ForeignDeviceElement;
                    try recovery(hive,objects,val,id);
                }
            }
        }
    }
}

test "generated template identity accepted, other Windows target refused" {
    var hive = try r.Hive.init(@embedFile("BCD10.test"));
    var id: Identity = .{.esp_disk=.{0x22,0x22,0x22,0x22,0xaa,0xaa,0xbb,0x4b,0x8c,0xcc,0x22,0x22,0x22,0x22,0x22,0x22},
        .esp=.{0x33,0x33,0x33,0x33,0xaa,0xaa,0xbb,0x4b,0x8c,0xcc,0x33,0x33,0x33,0x33,0x33,0x33},
        .windows_disk=undefined,.windows=.{0x44,0x44,0x44,0x44,0xaa,0xaa,0xbb,0x4b,0x8c,0xcc,0x44,0x44,0x44,0x44,0x44,0x44}};
    id.windows_disk = id.esp_disk;
    try validate(&hive,&id);
    id.windows[0] ^= 1;
    try std.testing.expectError(error.ForeignWindowsVolume,validate(&hive,&id));
}

test "saved serviced Windows 7 store retains valid recovery and firmware entries" {
    var hive = try r.Hive.init(@embedFile("BCD7-serviced.test"));
    var id: Identity = .{
        .esp_disk=.{0x6b,0xc0,0xb3,0xa7,0xbd,0x74,0xc3,0x46,0x8a,0x28,0x29,0x92,0x19,0x64,0xc9,0xee},
        .esp=.{0x10,0xbe,0xf7,0xf5,0xe6,0x32,0x8e,0x41,0xbd,0x32,0x0b,0x56,0x74,0xd7,0xef,0xa7},
        .windows_disk=undefined,
        .windows=.{0x85,0xf4,0x76,0x1f,0x80,0x25,0x2b,0x4d,0x92,0x4a,0x2c,0x8e,0xa0,0x68,0x66,0x20}};
    id.windows_disk=id.esp_disk;
    try validate(&hive,&id);
    // In-place data-only mutations must not crash the parser, including when
    // values, lengths or tree links become corrupt. No mutated hive is saved.
    var random = std.Random.DefaultPrng.init(71320261010);
    for (0..1024) |_| {
        var changed = @embedFile("BCD7-serviced.test").*;
        const index = random.random().uintLessThan(usize,changed.len);
        changed[index] ^= random.random().int(u8) | 1;
        var candidate = r.Hive.init(&changed) catch continue;
        validate(&candidate,&id) catch {};
    }
}

test "saved synthetic Windows 10 servicing permits null recovery references" {
    var hive=try r.Hive.init(@embedFile("BCD10-serviced.test"));
    const id: Identity=.{
        .esp_disk=.{0x1c,0x42,0x0d,0xcf,0xa1,0xe6,0x50,0x49,0x93,0xbe,0xcc,0xdd,0x81,0x1a,0xcd,0x0b},
        .esp=.{0xba,0x3b,0x7c,0x6a,0x80,0x13,0x21,0x4e,0xb6,0x85,0x9d,0x02,0x93,0x77,0xb9,0x6a},
        .windows_disk=.{0x1e,0x57,0xab,0xe5,0x20,0x53,0xc8,0x4d,0x84,0x28,0x78,0x7e,0x5d,0x55,0x9e,0x3f},
        .windows=.{0x72,0xa3,0x67,0x63,0x20,0x91,0x76,0x41,0xb4,0xbb,0x7e,0x74,0x3e,0x60,0x7a,0x09}};
    try validate(&hive,&id);
}

test "physical Windows 10 serviced firmware mirror is not a merged Windows menu" {
    const data=@embedFile("BCD10-physical-20261011.test");
    var hive=try r.Hive.init(data);
    var id: Identity=.{
        .esp_disk=.{0x6b,0xc0,0xb3,0xa7,0xbd,0x74,0xc3,0x46,0x8a,0x28,0x29,0x92,0x19,0x64,0xc9,0xee},
        .esp=.{0x10,0xbe,0xf7,0xf5,0xe6,0x32,0x8e,0x41,0xbd,0x32,0x0b,0x56,0x74,0xd7,0xef,0xa7},
        .windows_disk=undefined,
        .windows=.{0x13,0x44,0x87,0x64,0x59,0xfe,0xf4,0x47,0xb0,0x73,0xde,0x95,0xc7,0x16,0xf3,0x18}};
    id.windows_disk=id.esp_disk;
    try validate(&hive,&id);
    id.windows[0]^=1;
    try std.testing.expectError(error.ForeignWindowsVolume,validate(&hive,&id));
}

fn win81Identity() Identity {
    const disk = [16]u8{0x6b,0xc0,0xb3,0xa7,0xbd,0x74,0xc3,0x46,0x8a,0x28,0x29,0x92,0x19,0x64,0xc9,0xee};
    return .{.esp_disk=disk,.windows_disk=disk,
        .esp=.{0x10,0xbe,0xf7,0xf5,0xe6,0x32,0x8e,0x41,0xbd,0x32,0x0b,0x56,0x74,0xd7,0xef,0xa7},
        .windows=.{0xf0,0x32,0xde,0x57,0x1f,0xa8,0xa8,0x42,0x9d,0xd7,0x84,0x58,0xc5,0xca,0x59,0x36}};
}

test "physical Windows 8.1 after first reboot keeps its named recovery layout" {
    var hive = try r.Hive.init(@embedFile("BCD81-physical-20261011.test"));
    var id = win81Identity();
    try validate(&hive,&id);
    id.windows[0] ^= 1;
    try std.testing.expectError(error.ForeignWindowsVolume,validate(&hive,&id));
}

test "recovery directory recognition is exact and case insensitive" {
    try std.testing.expectEqualStrings("\\Recovery\\WindowsRE\\",try recoveryDirectory("\\Recovery\\WindowsRE\\Winre.wim"));
    try std.testing.expectEqualStrings("\\RECOVERY\\WINDOWSRE\\",try recoveryDirectory("\\RECOVERY\\WINDOWSRE\\WINRE.WIM"));
    for ([_][]const u8{"\\Recovery\\..\\Winre.wim","\\Recovery\\WindowsXX\\Winre.wim",
        "\\Recovery\\WindowsRE\\..\\Winre.wim","\\Recovery\\WindowsRE\\Winre.wim:stream",
        "\\Recovery\\------------------------------------\\Winre.wim",
        "C:\\Recovery\\WindowsRE\\Winre.wim","\\Recovery\\WindowsRE\\Winre.wim\\",
        "\\Recovery\\WindowsRE\\other.wim","/Recovery/WindowsRE/Winre.wim"}) |path|
        try std.testing.expectError(error.UnknownRecoveryPath,recoveryDirectory(path));
}

test "named recovery still rejects altered SDI and unknown directory" {
    const id=win81Identity();
    inline for (.{"boot.sdi","WindowsRE","Winre.wim"}) |text| {
        var changed=@embedFile("BCD81-physical-20261011.test").*;
        const needle=std.mem.sliceAsBytes(std.unicode.utf8ToUtf16LeStringLiteral(text));
        const offset=std.mem.indexOf(u8,&changed,needle) orelse return error.MissingTestPath;
        changed[offset] ^= 1;
        var hive=try r.Hive.init(&changed);
        if (validate(&hive,&id)) |_| return error.AcceptedBadRecoveryPath else |_| {}
    }
}

test "Windows 8.1 recovery volume cannot be reassigned to another partition" {
    var changed=@embedFile("BCD81-physical-20261011.test").*;
    var hive=try r.Hive.init(&changed);
    const id=win81Identity();
    const objects=try hive.children(try hive.child(hive.root,"Objects"));
    var modified: usize=0;
    for (objects.offsets[0..objects.count]) |obj| {
        const value=hive.element(obj,"11000001") catch continue;
        if (value.data.len <= 140) continue;
        for ([_][]const u8{"11000001","21000001"}) |number| {
            const field=try hive.element(obj,number);
            const offset=@intFromPtr(field.data.ptr)-@intFromPtr(&changed);
            changed[offset+84] ^= 1;
            modified+=1;
        }
    }
    try std.testing.expectEqual(@as(usize,2),modified);
    try std.testing.expectError(error.ForeignRecoveryVolume,validate(&hive,&id));
}
