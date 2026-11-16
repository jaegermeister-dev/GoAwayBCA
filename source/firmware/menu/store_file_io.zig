// EXPERIMENTAL file adapter. StoreSwitchLab-only by default. The explicitly
// opted-in integration root uses SystemStores plus exactly four canonical BCD
// paths, after the caller verifies its ESP, parent GPT and configuration.
const std = @import("std");
const u = std.os.uefi;
const File = u.protocol.File;
pub const Digest = @import("store_switch_wire.zig").Digest;
pub const Record = @import("store_switch_wire.zig").Record;
const core=@import("store_switch_core.zig");
const prefix = core.prefix;
const canonical_test = if (@hasDecl(@import("root"),"synthetic_canonical_store_test"))
    @import("root").synthetic_canonical_store_test else false;

fn mappedPath(name: []const u8) []const u8 {
    // Only explicit opt-in roots enable these exact translations. Ordinary
    // read-only chooser builds and default scratch probes do not.
    if (canonical_test or core.system_store_mode) {
        inline for (.{"", ".LOG", ".LOG1", ".LOG2"}) |suffix| {
            if (std.mem.eql(u8,name,prefix ++ "Canonical.BCD" ++ suffix))
                return "\\EFI\\Microsoft\\Boot\\BCD" ++ suffix;
        }
    }
    return name;
}

pub fn hash(bytes: []const u8) Digest {
    var result: Digest = undefined;
    std.crypto.hash.sha2.Sha256.hash(bytes,&result,.{});
    return result;
}
fn path(name: []const u8, out: *[256:0]u16) ![*:0]const u16 {
    if (!std.mem.startsWith(u8,name,prefix) or name.len <= prefix.len or name.len > 240)
        return error.OutsideLabDirectory;
    const leaf = name[prefix.len..];
    if (leaf[leaf.len-1] == '.' or leaf[leaf.len-1] == ' ') return error.InvalidLabName;
    for (leaf) |c| if (!(std.ascii.isAlphanumeric(c) or c == '-' or c == '_' or c == '.'))
        return error.InvalidLabName;
    if (std.mem.indexOf(u8,leaf,"..") != null) return error.InvalidLabName;
    const actual=mappedPath(name);
    for (actual,0..) |c,i| out[i] = c;
    out[actual.len] = 0;
    return @ptrCast(out);
}
pub const IO = struct {
    root: *File,
    fn open(self: IO, name: []const u8, mode: File.OpenMode) !*File {
        var wide: [256:0]u16 = undefined;
        return self.root.open(try path(name,&wide),mode,.{});
    }
    fn describe(file: *File) !Record {
        var metadata: [1024]u8 align(8) = undefined;
        const info = try file.getInfo(.file,&metadata);
        if (info.attribute.directory or info.file_size > 16*1024*1024) return error.InvalidLabFile;
        try file.setPosition(0);
        var bytes: [8192]u8 = undefined;
        var total: u64 = 0;
        var digest = std.crypto.hash.sha2.Sha256.init(.{});
        while (true) {
            const got = try file.read(&bytes);
            if (got == 0) break;
            if (got > bytes.len or total+got > info.file_size) return error.ChangedFileLength;
            total += got; digest.update(bytes[0..got]);
        }
        if (total != info.file_size) return error.ChangedFileLength;
        var result: Digest = undefined; digest.final(&result);
        return .{.digest=result,.size=total};
    }
    pub fn inspect(self: IO, name: []const u8) !?Record {
        const file = self.open(name,.read) catch |err| {
            if (err == error.NotFound) return null;
            return err;
        };
        defer file.close() catch {};
        return try describe(file);
    }
    pub fn readAlloc(self: IO, allocator: std.mem.Allocator, name: []const u8, limit: usize) !?[]u8 {
        const file = self.open(name,.read) catch |err| {
            if (err == error.NotFound) return null;
            return err;
        };
        defer file.close() catch {};
        const record = try describe(file);
        if (record.size > limit) return error.ReadLimitExceeded;
        const bytes = try allocator.alloc(u8,@intCast(record.size));
        errdefer allocator.free(bytes);
        try file.setPosition(0);
        var offset: usize = 0;
        while (offset < bytes.len) {
            const count = try file.read(bytes[offset..]);
            if (count == 0 or count > bytes.len-offset) return error.ShortRead;
            offset += count;
        }
        var extra: [1]u8 = undefined;
        if (try file.read(&extra) != 0 or !std.mem.eql(u8,&hash(bytes),&record.digest)) return error.FileChanged;
        return bytes;
    }
    pub fn expect(self: IO, name: []const u8, expected: ?Record) !void {
        const actual = try self.inspect(name);
        if (expected) |record| {
            if (actual == null or record.size != actual.?.size or
                !std.mem.eql(u8,&record.digest,&actual.?.digest)) return error.FileChanged;
        } else if (actual != null) return error.FileAlreadyExists;
    }
    pub fn create(self: IO, name: []const u8, bytes: []const u8) !Record {
        if (bytes.len > 16*1024*1024) return error.InvalidLabFile;
        try self.expect(name,null);
        const file = try self.open(name,.read_write_create);
        var closed = false; defer if (!closed) file.close() catch {};
        const initial = try describe(file);
        if (initial.size != 0) return error.FileAppeared;
        var offset: usize = 0;
        while (offset < bytes.len) {
            const count = try file.write(bytes[offset..]);
            if (count == 0 or count > bytes.len-offset) return error.ShortWrite;
            offset += count;
        }
        try file.flush(); try file.close(); closed = true;
        const record: Record = .{.size=bytes.len,.digest=hash(bytes)};
        try self.expect(name,record); return record;
    }
    pub fn rename(self: IO, source: []const u8, destination: []const u8, expected: Record) !void {
        if (std.ascii.eqlIgnoreCase(source,destination)) return error.SamePath;
        try self.expect(source,expected); try self.expect(destination,null);
        const file = try self.open(source,.read_write);
        var closed = false; defer if (!closed) file.close() catch {};
        const checked = try describe(file);
        if (checked.size != expected.size or !std.mem.eql(u8,&checked.digest,&expected.digest)) return error.FileChanged;
        var data: [1024]u8 align(8) = undefined;
        const info = try file.getInfo(.file,&data);
        var wide: [256:0]u16 = undefined;
        _ = try path(destination,&wide);
        const name_bytes = std.mem.sliceAsBytes(wide[0..std.mem.len(@as([*:0]const u16,@ptrCast(&wide)))+1]);
        const start = @offsetOf(File.Info.File,"_file_name");
        const size = start+name_bytes.len;
        @memcpy(data[start..size],name_bytes); info.size = size;
        try file._set_info(file,&File.Info.File.guid,size,&data).err();
        try file.flush(); try file.close(); closed = true;
        try self.expect(source,null); try self.expect(destination,expected);
    }
    pub fn remove(self: IO, name: []const u8, expected: Record) !void {
        try self.expect(name,expected);
        const file = try self.open(name,.read_write);
        var consumed = false; defer if (!consumed) file.close() catch {};
        const actual = try describe(file);
        if (actual.size != expected.size or !std.mem.eql(u8,&actual.digest,&expected.digest)) return error.FileChanged;
        // EFI_FILE_PROTOCOL.Delete consumes the handle even on a warning.
        consumed = true;
        if (!try file.delete()) return error.DeleteNotCompleted;
        try self.expect(name,null);
    }
};

test "path policy never allows canonical BCD or unrelated EFI" {
    var out: [256:0]u16 = undefined;
    _ = try path(prefix ++ "staged.bcd",&out);
    for ([_][]const u8{"\\EFI\\Microsoft\\Boot\\BCD","\\EFI\\OC\\OpenCore.efi",
        prefix ++ "..\\BCD",prefix ++ "a/b",prefix ++ "file.",prefix ++ "a..b"}) |invalid| {
        if (path(invalid,&out)) |_| return error.UnsafePathAccepted else |_| {}
    }
}
