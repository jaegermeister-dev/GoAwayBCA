// Lab-only authority guard shared by disposable UEFI probes, never production.
const std=@import("std");
const u=std.os.uefi;
const utf16=std.unicode.utf8ToUtf16LeStringLiteral;
pub fn trace(s: []const u8) void {
    for (s) |c| asm volatile ("outb %[byte], %[port]" : : [byte] "{al}" (c), [port] "{dx}" (@as(u16,0xe9)));
}
pub fn open() !*u.protocol.File {
    const bs=u.system_table.boot_services orelse return error.NoBootServices;
    const loaded=(try bs.handleProtocol(u.protocol.LoadedImage,u.handle)) orelse return error.NoImage;
    const device=loaded.device_handle orelse return error.NoDevice;
    const dp=(try bs.handleProtocol(u.protocol.DevicePath,device)) orelse return error.NoPath;
    const bytes: [*]const u8=@ptrCast(dp);
    const guid=[_]u8{0xba,0x3b,0x7c,0x6a,0x80,0x13,0x21,0x4e,0xb6,0x85,0x9d,0x02,0x93,0x77,0xb9,0x6a};
    var offset: usize=0;var found=false;var ended=false;
    while (offset+4 <= 4096) {
        const length=std.mem.readInt(u16,bytes[offset+2..][0..2],.little);
        if (length < 4 or offset+length > 4096) return error.InvalidPath;
        if (bytes[offset] == 0x7f) {ended=bytes[offset+1] == 0xff and length == 4;break;}
        if (bytes[offset] == 4 and bytes[offset+1] == 1) {
            if (found or length != 42 or bytes[offset+40] != 2 or bytes[offset+41] != 2 or
                !std.mem.eql(u8,bytes[offset+24..][0..16],&guid)) return error.NotSyntheticEsp;
            found=true;
        }
        offset+=length;
    }
    if (!found or !ended) return error.NotSyntheticEsp;
    const fs=(try bs.handleProtocol(u.protocol.SimpleFileSystem,device)) orelse return error.NoFilesystem;
    const root=try fs.openVolume();errdefer root.close() catch {};
    const marker=try root.open(utf16("\\EFI\\WinDeploy\\store-io-lab-only.txt"),.read,.{});
    defer marker.close() catch {};
    var marker_bytes: [64]u8=undefined;
    const length=try marker.read(&marker_bytes);
    if (!std.mem.eql(u8,marker_bytes[0..length],"W7DEP DISPOSABLE STORE IO TEST 1\n")) return error.NoLabAuthority;
    return root;
}
