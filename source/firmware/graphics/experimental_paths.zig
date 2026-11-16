const std = @import("std");
const u = std.os.uefi;
pub const prefix = "\\EFI\\WinDeploy\\Installs\\";
pub const suffix = "\\Fix.efi";
pub fn folder(path: []const u16) ![256:0]u16 {
    const ntfs=std.unicode.utf8ToUtf16LeStringLiteral("\\EFI\\Microsoft\\Boot\\Fix.efi");
    if (std.mem.eql(u16,path,ntfs)) {
        var out: [256:0]u16=@splat(0);
        @memcpy(out[0..ntfs.len-suffix.len],ntfs[0..ntfs.len-suffix.len]);
        return out;
    }
    if (path.len != prefix.len + 36 + suffix.len) return error.UnexpectedHelperPath;
    for (prefix, 0..) |c,i| if (path[i] != c) return error.UnexpectedHelperPath;
    for (suffix, 0..) |c,i| if (path[prefix.len+36+i] != c) return error.UnexpectedHelperPath;
    for (0..36) |i| {
        const c=path[prefix.len+i];
        if (i==8 or i==13 or i==18 or i==23) {
            if (c!='-') return error.InvalidPartitionGuid;
        } else if (!((c>='0' and c<='9') or (c>='a' and c<='f'))) return error.InvalidPartitionGuid;
    }
    var out: [256:0]u16 = @splat(0);
    @memcpy(out[0..prefix.len+36],path[0..prefix.len+36]);
    return out;
}
pub fn ownFolder(dp: *const u.protocol.DevicePath) ![256:0]u16 {
    // LoadedImage.FilePath for our exact FilePath node followed by EndEntire.
    if (@intFromEnum(dp.type)!=4 or dp.subtype!=4 or dp.length<6 or dp.length>512 or dp.length%2!=0)
        return error.UnexpectedHelperDevicePath;
    const bytes: [*]const u8=@ptrCast(dp);
    const end=bytes[dp.length..][0..4];
    if (!std.mem.eql(u8,end,&.{0x7f,0xff,4,0})) return error.UnexpectedHelperDevicePath;
    const count=(dp.length-4)/2;
    var text: [254]u16=undefined;
    for (0..count) |i| text[i]=std.mem.readInt(u16,bytes[4+i*2..][0..2],.little);
    if (text[count-1]!=0) return error.UnterminatedPath;
    return folder(text[0..count-1]);
}
pub fn loaderHash(config: []const u8) ![32]u8 {
    if (config.len!=40 or !std.mem.eql(u8,config[0..8],"NMBFIX01")) return error.InvalidHelperConfiguration;
    return config[8..40].*;
}
test "only the installation-scoped helper path is accepted" {
    const path=std.unicode.utf8ToUtf16LeStringLiteral("\\EFI\\WinDeploy\\Installs\\bbbbbbbb-2222-4222-8222-222222222222\\Fix.efi");
    const out=try folder(path);
    try std.testing.expectEqual(@as(u16,0),out[prefix.len+36]);
    try std.testing.expectError(error.UnexpectedHelperPath,folder(std.unicode.utf8ToUtf16LeStringLiteral("\\EFI\\OC\\OpenCore.efi")));
    var wrong=path.*;wrong[prefix.len]='z';
    try std.testing.expectError(error.InvalidPartitionGuid,folder(&wrong));
}
test "configuration binds only original loader bytes" {
    var config: [40]u8=@splat(1);@memcpy(config[0..8],"NMBFIX01");
    const hash=try loaderHash(&config);try std.testing.expectEqual(@as(u8,1),hash[0]);
    config[0]=0;try std.testing.expectError(error.InvalidHelperConfiguration,loaderHash(&config));
}
test "stock NTFS boot overlay helper stays on its own partition" {
    const out=try folder(std.unicode.utf8ToUtf16LeStringLiteral("\\EFI\\Microsoft\\Boot\\Fix.efi"));
    try std.testing.expectEqualSlices(u16,std.unicode.utf8ToUtf16LeStringLiteral("\\EFI\\Microsoft\\Boot"),out[0..19]);
}
