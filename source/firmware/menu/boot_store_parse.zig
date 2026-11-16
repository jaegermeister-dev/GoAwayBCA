// Read-only bounded clean-REGF parser for the experimental BCD activation code.
// No filesystem, firmware, log replay or registry writes. Not yet in the chooser.
const std = @import("std");
const max_cells = 16384;
const max_children = 512;
pub const Value = struct { kind: u32, data: []const u8 };
const Span = struct { offset: u32, end: u32 };
pub const Children = struct { count: usize = 0, offsets: [max_children]u32 = undefined };
pub const Hive = struct {
    data: []const u8,
    spans: [max_cells]Span = undefined,
    used: usize = 0,
    root: u32,

    pub fn init(data: []const u8) !Hive {
        var hive: Hive = undefined;
        try hive.initInto(data);
        return hive;
    }

    // The bounded cell index is 128 KiB. Firmware callers allocate Hive from
    // their pool and initialize it in place rather than consume the small
    // firmware stack with a large return-value temporary.
    pub fn initInto(hive: *Hive, data: []const u8) !void {
        if (data.len < 8192 or data.len > 2*1024*1024 or data.len % 4096 != 0 or
            !std.mem.eql(u8,data[0..4],"regf")) return error.InvalidHive;
        if (try n(data,4) != try n(data,8)) return error.DirtyHive;
        if (try n(data,20) != 1 or try n(data,28) != 0 or try n(data,32) != 1)
            return error.UnsupportedHive;
        var sum: u32 = 0;
        for (0..127) |i| sum ^= try n(data,i*4);
        if (sum == 0) sum = 1 else if (sum == 0xffffffff) sum = 0xfffffffe;
        if (sum != try n(data,508)) return error.BadHiveChecksum;
        if (try n(data,40) != data.len - 4096) return error.InvalidHiveLength;
        hive.data=data;
        hive.root=try n(data,36);
        hive.used=0;
        var offset: usize = 4096;
        while (offset < data.len) {
            if (!std.mem.eql(u8,data[offset..offset+4],"hbin") or try n(data,offset+4) != offset-4096)
                return error.InvalidBin;
            const size = try n(data,offset+8);
            if (size < 4096 or size % 4096 != 0 or size > data.len-offset) return error.InvalidBin;
            var cell_offset = offset+32;
            while (cell_offset < offset+size) {
                const signed: i32 = @bitCast(try n(data,cell_offset));
                const length: usize = @intCast(if (signed < 0) -@as(i64,signed) else signed);
                if (length < 8 or length % 8 != 0 or length > offset+size-cell_offset) return error.InvalidCell;
                if (signed < 0) {
                    if (hive.used == max_cells) return error.TooManyCells;
                    hive.spans[hive.used] = .{.offset=@intCast(cell_offset-4096),.end=@intCast(cell_offset+length)};
                    hive.used += 1;
                }
                cell_offset += length;
            }
            offset += size;
        }
        _ = try hive.node(hive.root);
    }

    pub fn cell(self: *const Hive, offset: u32, minimum: usize) ![]const u8 {
        // Cells are sorted; binary search also rejects interior/free pointers.
        var lo: usize = 0;
        var hi = self.used;
        while (lo < hi) {
            const mid = lo+(hi-lo)/2;
            const span = self.spans[mid];
            if (span.offset < offset) lo = mid+1 else if (span.offset > offset) hi = mid else {
                const start: usize = @as(usize,offset)+4100;
                if (span.end < start or span.end-start < minimum) return error.ShortCell;
                return self.data[start..span.end];
            }
        }
        return error.InvalidCellReference;
    }

    fn node(self: *const Hive, offset: u32) ![]const u8 {
        const cell_data = try self.cell(offset,76);
        if (!std.mem.eql(u8,cell_data[0..2],"nk") or try s(cell_data,2) & 0x10 != 0 or
            try n(cell_data,24) != 0) return error.InvalidKey;
        if (76+@as(usize,try s(cell_data,72)) > cell_data.len) return error.InvalidKeyName;
        return cell_data;
    }

    pub fn nodeName(self: *const Hive, offset: u32, out: *[256]u8) ![]const u8 {
        const node_data = try self.node(offset);
        return name(node_data[76..][0..try s(node_data,72)],try s(node_data,2) & 0x20 != 0,out);
    }

    fn index(self: *const Hive, offset: u32, depth: usize, result: *Children,
        visited: *[64]u32, visit_count: *usize) anyerror!void {
        if (depth >= 16 or visit_count.* >= visited.len) return error.IndexLimit;
        for (visited[0..visit_count.*]) |prior| if (prior == offset) return error.CyclicIndex;
        visited[visit_count.*] = offset; visit_count.* += 1;
        const data = try self.cell(offset,4);
        const count = try s(data,2);
        const indirect = std.mem.eql(u8,data[0..2],"ri");
        const hashed = std.mem.eql(u8,data[0..2],"lf") or std.mem.eql(u8,data[0..2],"lh");
        if (!indirect and !hashed and !std.mem.eql(u8,data[0..2],"li")) return error.InvalidIndex;
        const stride: usize = if (hashed) 8 else 4;
        if (count == 0 or count > max_children or 4+count*stride > data.len) return error.IndexLimit;
        for (0..count) |i| {
            const target = try n(data,4+i*stride);
            if (indirect) try self.index(target,depth+1,result,visited,visit_count) else {
                if (result.count == max_children) return error.IndexLimit;
                for (result.offsets[0..result.count]) |prior| if (prior == target) return error.DuplicateKey;
                result.offsets[result.count] = target; result.count += 1;
            }
        }
    }

    pub fn children(self: *const Hive, offset: u32) !Children {
        const node_data = try self.node(offset);
        const count = try n(node_data,20);
        var result: Children = .{};
        if (count == 0) return result;
        if (count > max_children) return error.IndexLimit;
        var visited: [64]u32 = undefined; var visits: usize = 0;
        try self.index(try n(node_data,28),0,&result,&visited,&visits);
        if (count != result.count) return error.InconsistentIndex;
        for (result.offsets[0..result.count]) |child_offset| {
            if (try n(try self.node(child_offset),16) != offset) return error.WrongKeyParent;
        }
        return result;
    }

    pub fn child(self: *const Hive, parent: u32, wanted: []const u8) !u32 {
        const list = try self.children(parent);
        var found: ?u32 = null;
        for (list.offsets[0..list.count]) |offset| {
            var buffer: [256]u8 = undefined;
            if (std.ascii.eqlIgnoreCase(try self.nodeName(offset,&buffer),wanted)) {
                if (found != null) return error.DuplicateKey;
                found = offset;
            }
        }
        return found orelse error.MissingKey;
    }

    pub fn value(self: *const Hive, offset: u32, wanted: []const u8) !Value {
        const node_data = try self.node(offset);
        const count = try n(node_data,36);
        if (count == 0) return error.MissingValue;
        if (count > max_children) return error.ValueLimit;
        const list = try self.cell(try n(node_data,40),count*4);
        var found: ?Value = null;
        for (0..count) |i| {
            const data = try self.cell(try n(list,i*4),20);
            if (!std.mem.eql(u8,data[0..2],"vk")) return error.InvalidValue;
            const length = try s(data,2); const flags = try s(data,16);
            if (flags & ~@as(u16,1) != 0 or 20+@as(usize,length) > data.len) return error.InvalidValue;
            var buffer: [256]u8 = undefined;
            const key = try name(data[20..][0..length],flags & 1 != 0,&buffer);
            const raw_length = try n(data,4); const size = raw_length & 0x7fffffff;
            if (size > 16344) return error.ValueLimit;
            const content = if (raw_length & 0x80000000 != 0) blk: {
                if (size > 4) return error.InvalidInlineValue;
                break :blk data[8..][0..size];
            } else if (size == 0) "" else (try self.cell(try n(data,8),size))[0..size];
            if (std.ascii.eqlIgnoreCase(key,wanted)) {
                if (found != null) return error.DuplicateValue;
                found = .{.kind=try n(data,12),.data=content};
            }
        }
        return found orelse error.MissingValue;
    }

    pub fn element(self: *const Hive, object: u32, field: []const u8) !Value {
        return self.value(try self.child(try self.child(object,"Elements"),field),"Element");
    }
};

pub fn n(data: []const u8, offset: usize) !u32 {
    if (offset > data.len or data.len-offset < 4) return error.Bounds;
    return std.mem.readInt(u32,data[offset..][0..4],.little);
}
fn s(data: []const u8, offset: usize) !u16 {
    if (offset > data.len or data.len-offset < 2) return error.Bounds;
    return std.mem.readInt(u16,data[offset..][0..2],.little);
}
fn name(data: []const u8, compressed: bool, output: *[256]u8) ![]const u8 {
    const stride: usize = if (compressed) 1 else 2;
    if (data.len % stride != 0 or data.len/stride > 255) return error.InvalidName;
    for (0..data.len/stride) |i| {
        const char: u16 = if (compressed) data[i] else try s(data,i*2);
        if (char < 32 or char >= 127 or char == '\\') return error.UnsupportedName;
        output[i] = @intCast(char);
    }
    return output[0..data.len/stride];
}
pub fn text(value: Value, output: *[512]u8) ![]const u8 {
    if (value.kind != 1 or value.data.len < 2 or value.data.len % 2 != 0 or value.data.len/2 > output.len)
        return error.InvalidString;
    var used: usize = 0; var ended = false;
    for (0..value.data.len/2) |i| {
        const char = try s(value.data,i*2);
        if (char == 0) ended = true else {
            if (ended or char < 32 or char >= 127) return error.InvalidString;
            output[used] = @intCast(char); used += 1;
        }
    }
    if (!ended) return error.InvalidString;
    return output[0..used];
}

test "parse generated Windows BCD with actual cell references" {
    var hive = try Hive.init(@embedFile("BCD10.test"));
    const objects = try hive.child(hive.root,"Objects");
    const bootmgr = try hive.child(objects,"{9dea862c-5cdd-4e70-acc1-f32b344d4795}");
    var buffer: [512]u8 = undefined;
    try std.testing.expectEqualStrings("{7c10d1ea-8239-4bc7-8234-efdb85bc913a}",
        try text(try hive.element(bootmgr,"23000003"),&buffer));
    try std.testing.expectEqual(@as(usize,2),(try hive.children(objects)).count);
    try std.testing.expectError(error.InvalidCellReference,hive.cell(hive.root+8,4));
    try std.testing.expectError(error.MissingKey,hive.child(objects,"Not an object"));
}

test "reject dirty and truncated hives and embedded string terminators" {
    var data = @embedFile("BCD10.test").*;
    data[4] ^= 1;
    try std.testing.expectError(error.DirtyHive,Hive.init(&data));
    try std.testing.expectError(error.InvalidHive,Hive.init(data[0..200]));
    var buffer: [512]u8 = undefined;
    try std.testing.expectError(error.InvalidString,text(.{.kind=1,.data="A\x00\x00\x00B\x00\x00\x00"},&buffer));
}
