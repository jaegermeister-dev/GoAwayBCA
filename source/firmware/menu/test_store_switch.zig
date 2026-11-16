// In-memory fault injection for the same engine used by the firmware probe.
const std=@import("std");
const core=@import("store_switch_core.zig");
const w=core.w;
const a=std.testing.allocator;
const File=struct { name: [128]u8, length: usize, data: []u8 };
const Memory=struct {
    files: [64]?File=@splat(null),
    boundary: usize=0, stop: ?usize=null, tear: ?[]const u8=null,
    fn deinit(self: *Memory) void {for (self.files) |optional| if (optional) |f| a.free(f.data);}
    fn index(self: *Memory,name: []const u8) ?usize {
        for (self.files,0..) |optional,i| if (optional) |f| {
            if (std.ascii.eqlIgnoreCase(f.name[0..f.length],name)) return i;
        };
        return null;
    }
    fn point(self: *Memory) !void {
        const at=self.boundary;self.boundary+=1;
        if (self.stop == at) return error.PowerLoss;
    }
    pub fn inspect(self: *Memory,name: []const u8) !?w.Record {
        return if (self.index(name)) |i| w.record(self.files[i].?.data) else null;
    }
    pub fn expect(self: *Memory,name: []const u8,expected: ?w.Record) !void {
        const actual=try self.inspect(name);
        if (expected) |r| {
            if (actual == null or !w.same(actual.?,r)) return error.FileChanged;
        } else if (actual != null) return error.FileAlreadyExists;
    }
    pub fn readAlloc(self: *Memory,allocator: std.mem.Allocator,name: []const u8,limit: usize) !?[]u8 {
        if (self.index(name)) |i| {
            const f=self.files[i].?;
            if (f.data.len > limit) return error.ReadLimitExceeded;
            return try allocator.dupe(u8,f.data);
        }
        return null;
    }
    pub fn create(self: *Memory,name: []const u8,bytes: []const u8) !w.Record {
        if (name.len > 128 or !std.mem.startsWith(u8,name,core.prefix)) return error.UnsafePath;
        try self.expect(name,null);try self.point();
        for (&self.files) |*optional| if (optional.* == null) {
            const torn=if (self.tear) |path| std.mem.eql(u8,path,name) else false;
            var f: File=.{.name=undefined,.length=name.len,.data=try a.dupe(u8,bytes[0..if (torn) bytes.len/2 else bytes.len])};
            @memcpy(f.name[0..name.len],name);optional.*=f;
            if (torn) return error.PowerLoss;
            try self.point();return w.record(bytes);
        };
        return error.MemoryFixtureFull;
    }
    pub fn remove(self: *Memory,name: []const u8,expected: w.Record) !void {
        try self.expect(name,expected);try self.point();
        const i=self.index(name).?;a.free(self.files[i].?.data);self.files[i]=null;try self.point();
    }
    pub fn rename(self: *Memory,source: []const u8,destination: []const u8,expected: w.Record) !void {
        if (destination.len > 128) return error.UnsafePath;
        try self.expect(source,expected);try self.expect(destination,null);try self.point();
        const i=self.index(source).?;@memcpy(self.files[i].?.name[0..destination.len],destination);
        self.files[i].?.length=destination.len;try self.point();
    }
};
const Validation=struct {
    pub fn validate(_: *Validation,bytes: []const u8,index: u32) !void {
        const lead=if (index == 0) "A clean:" else if (index == 1) "B clean:" else return error.WrongIdentity;
        if (!std.mem.startsWith(u8,bytes,lead)) return error.WrongIdentity;
    }
};
const Engine=core.Engine(Memory,Validation);
const PhysicalValidation=struct {
    pub fn validate(_: *PhysicalValidation,bytes: []const u8,index: u32) !void {
        const parser=@import("boot_store_parse.zig");
        const check=@import("boot_store_identity.zig");
        const disk=[16]u8{0x6b,0xc0,0xb3,0xa7,0xbd,0x74,0xc3,0x46,0x8a,0x28,0x29,0x92,0x19,0x64,0xc9,0xee};
        const id: check.Identity=.{.esp_disk=disk,.windows_disk=disk,
            .esp=.{0x10,0xbe,0xf7,0xf5,0xe6,0x32,0x8e,0x41,0xbd,0x32,0x0b,0x56,0x74,0xd7,0xef,0xa7},
            .windows=if (index==0) .{0x13,0x44,0x87,0x64,0x59,0xfe,0xf4,0x47,0xb0,0x73,0xde,0x95,0xc7,0x16,0xf3,0x18}
                else if (index==1) .{0xf0,0x32,0xde,0x57,0x1f,0xa8,0xa8,0x42,0x9d,0xd7,0x84,0x58,0xc5,0xca,0x59,0x36}
                else return error.WrongIdentity};
        var hive=try parser.Hive.init(bytes);
        try check.validate(&hive,&id);
    }
};

test "actual serviced Windows 8.1 survives switch to Windows 10 and back" {
    var m: Memory=.{};defer m.deinit();
    const win10=@embedFile("BCD10-physical-20261011.test");
    const win81=@embedFile("BCD81-physical-20261011.test");
    _=try m.create(core.canon,win81);
    _=try m.create(core.state,&w.encodeState(.{.generation=0,.active=1,.config=config}));
    _=try m.create(core.prefix ++ "Private-0.BCD",win10);
    _=try m.create(core.prefix ++ "Private-1.BCD",win81);
    m.boundary=0;
    var v: PhysicalValidation=.{};
    var e: core.Engine(Memory,PhysicalValidation)=.{.io=&m,.validation=&v,.allocator=a,.config=config,.count=2};
    try e.activate(1);
    try std.testing.expectEqual(@as(usize,0),m.boundary);
    try e.activate(0);
    try m.expect(core.canon,w.record(win10));
    try m.expect(core.prefix ++ "Private-1.BCD",w.record(win81));
    try e.activate(1);
    try m.expect(core.canon,w.record(win81));
    try m.expect(core.prefix ++ "Private-0.BCD",w.record(win10));
    try m.expect(core.journal,null);
}
const config=blk: { @setEvalBranchQuota(10000); break :blk w.hash("fixed two-store lab configuration"); };
fn fixture() !Memory {
    var m: Memory= .{};errdefer m.deinit();
    _=try m.create(core.canon,"A clean:serviced");
    _=try m.create(core.state,&w.encodeState(.{.generation=0,.active=0,.config=config}));
    _=try m.create(core.prefix ++ "Private-0.BCD","A clean:prior");
    _=try m.create(core.prefix ++ "Private-1.BCD","B clean:installed");
    _=try m.create(core.canon ++ ".LOG","outgoing registry log");
    _=try m.create(core.canon ++ ".LOG1","");
    _=try m.create(core.prefix ++ "Unrelated.bin","unchanged unrelated evidence");
    m.boundary=0;return m;
}
fn make(m: *Memory,v: *Validation) Engine {return .{.io=m,.validation=v,.allocator=a,.config=config,.count=2};}
fn finalCheck(m: *Memory) !void {
    try m.expect(core.canon,w.record("B clean:installed"));
    try m.expect(core.prefix ++ "Private-0.BCD",w.record("A clean:serviced"));
    try m.expect(core.prefix ++ "Private-1.BCD",w.record("B clean:installed"));
    try m.expect(core.prefix ++ "Unrelated.bin",w.record("unchanged unrelated evidence"));
    try m.expect(core.journal,null);
    try m.expect(core.state,w.record(&w.encodeState(.{.generation=1,.active=1,.config=config})));
    try m.expect(core.prefix ++ "Log-0000000000000000-0-0.bin",w.record("outgoing registry log"));
    try m.expect(core.prefix ++ "Log-0000000000000000-0-1.bin",w.record(""));
    try m.expect(core.canon ++ ".LOG",null);try m.expect(core.canon ++ ".LOG1",null);
}

test "switch and return retain each OS servicing, no-op writes nothing" {
    var m=try fixture();defer m.deinit();var v: Validation=.{};var e=make(&m,&v);
    try e.activate(0);try std.testing.expectEqual(@as(usize,0),m.boundary);
    try e.activate(1);try finalCheck(&m);
    try m.remove(core.canon,w.record("B clean:installed"));_=try m.create(core.canon,"B clean:Windows servicing");
    try e.activate(0);
    try m.expect(core.canon,w.record("A clean:serviced"));
    try m.expect(core.prefix ++ "Private-1.BCD",w.record("B clean:Windows servicing"));
}
test "single remaining installation validates without switching or writing" {
    var m=try fixture();defer m.deinit();var v: Validation=.{};var e=make(&m,&v);
    e.count=1;try e.activate(0);try std.testing.expectEqual(@as(usize,0),m.boundary);
    try std.testing.expectError(error.InvalidStoreIndex,e.activate(1));
    e.count=0;try std.testing.expectError(error.InvalidStoreIndex,e.activate(0));
    try std.testing.expectEqual(@as(usize,0),m.boundary);
}

test "every completed write rename and deletion boundary recovers" {
    var initial=try fixture();defer initial.deinit();var v: Validation=.{};var first=make(&initial,&v);
    try first.activate(1);const count=initial.boundary;
    for (0..count) |stop| {
        var m=try fixture();defer m.deinit();m.stop=stop;var e=make(&m,&v);
        try std.testing.expectError(error.PowerLoss,e.activate(1));m.stop=null;
        try e.activate(1);try finalCheck(&m);
    }
}

test "torn writes retain original contents or verified complete recovery copies" {
    for ([_][]const u8{core.journal,core.prefix ++ "Outgoing.BCD",core.prefix ++ "Selected.BCD",
        core.prefix ++ "Next.BCD",core.prefix ++ "PrivateNext.BCD",core.prefix ++ "StateNext.bin",
        core.prefix ++ "Log-0000000000000000-0-0.bin"}) |path| {
        var m=try fixture();defer m.deinit();var v: Validation=.{};var e=make(&m,&v);m.tear=path;
        try std.testing.expectError(error.PowerLoss,e.activate(1));m.tear=null;
        if (e.activate(1)) |_| return error.TornWriteAccepted else |_| {}
        // Neither OS's only complete BCD may have been lost; original B is
        // immutable and A is canonical, private, previous or outgoing snapshot.
        try m.expect(core.prefix ++ "Private-1.BCD",w.record("B clean:installed"));
        var preserved=false;
        for ([_][]const u8{core.canon,core.prefix ++ "Private-0.BCD",core.prefix ++ "Previous.BCD",core.prefix ++ "Outgoing.BCD"}) |candidate| {
            if (try m.inspect(candidate)) |r| if (w.same(r,w.record("A clean:serviced"))) {preserved=true;};
        }
        try std.testing.expect(preserved);
        try m.expect(core.prefix ++ "Unrelated.bin",w.record("unchanged unrelated evidence"));
    }
}

test "foreign canonical, different config and unknown remnants cause zero mutations" {
    for (0..3) |scenario| {
        var m=try fixture();defer m.deinit();var v: Validation=.{};var e=make(&m,&v);
        if (scenario == 0) {try m.remove(core.canon,w.record("A clean:serviced"));_=try m.create(core.canon,"foreign data");}
        if (scenario == 1) e.config=w.hash("unrelated configuration");
        if (scenario == 2) _=try m.create(core.prefix ++ "Previous.BCD","unattributed file");
        m.boundary=0;if (e.activate(1)) |_| return error.UnsafeStateAccepted else |_| {}
        try std.testing.expectEqual(@as(usize,0),m.boundary);
    }
}
