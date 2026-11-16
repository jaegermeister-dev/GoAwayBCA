// Fixed-size, checksummed lab transaction records. No filesystem operations.
// Explicit byte layout, never a compiler-dependent serialized struct.
const std = @import("std");
pub const Digest = [32]u8;
pub const Record = struct { digest: Digest, size: u64 };
pub const state_size = 88;
pub const journal_size = 352;
pub fn hash(bytes: []const u8) Digest {
    var result: Digest = undefined;
    std.crypto.hash.sha2.Sha256.hash(bytes,&result,.{}); return result;
}
pub fn same(a: Record,b: Record) bool {
    return a.size == b.size and std.mem.eql(u8,&a.digest,&b.digest);
}
pub fn record(bytes: []const u8) Record { return .{.digest=hash(bytes),.size=bytes.len}; }
pub const State = struct { generation: u64, active: u32, config: Digest };
pub const Journal = struct {
    generation: u64, old: u32, new: u32, config: Digest,
    outgoing: Record, selected: Record, old_private: Record,
    logs: [3]?Record,
};
fn seal(bytes: []u8) void { @memcpy(bytes[bytes.len-32..],&hash(bytes[0..bytes.len-32])); }
fn check(bytes: []const u8, size: usize, magic: *const [8]u8) !void {
    if (bytes.len != size or !std.mem.eql(u8,bytes[0..8],magic) or
        !std.mem.eql(u8,bytes[size-32..],&hash(bytes[0..size-32]))) return error.InvalidTransactionRecord;
}
fn putRecord(bytes: *[40]u8,r: Record) void {
    std.mem.writeInt(u64,bytes[0..8],r.size,.little); @memcpy(bytes[8..],&r.digest);
}
fn getRecord(bytes: *const [40]u8) !Record {
    const size=std.mem.readInt(u64,bytes[0..8],.little);
    if (size > 16*1024*1024) return error.InvalidTransactionFileSize;
    return .{.size=size,.digest=bytes[8..40].*};
}
pub fn encodeState(s: State) [state_size]u8 {
    var bytes: [state_size]u8=@splat(0);
    @memcpy(bytes[0..8],"W7DEPS01");
    std.mem.writeInt(u64,bytes[8..16],s.generation,.little);
    std.mem.writeInt(u32,bytes[16..20],s.active,.little);
    @memcpy(bytes[24..56],&s.config); seal(&bytes); return bytes;
}
pub fn decodeState(bytes: []const u8,config: Digest, count: usize) !State {
    try check(bytes,state_size,"W7DEPS01");
    if (!std.mem.allEqual(u8,bytes[20..24],0) or !std.mem.eql(u8,bytes[24..56],&config)) return error.DifferentStoreConfiguration;
    const result: State=.{.generation=std.mem.readInt(u64,bytes[8..16],.little),
        .active=std.mem.readInt(u32,bytes[16..20],.little),.config=config};
    if (result.active >= count or result.generation >= std.math.maxInt(u64)-1) return error.InvalidActiveStore;
    return result;
}
pub fn encodeJournal(j: Journal) [journal_size]u8 {
    var bytes: [journal_size]u8=@splat(0);
    @memcpy(bytes[0..8],"NM BCAJ1");
    std.mem.writeInt(u64,bytes[8..16],j.generation,.little);
    std.mem.writeInt(u32,bytes[16..20],j.old,.little);
    std.mem.writeInt(u32,bytes[20..24],j.new,.little);
    @memcpy(bytes[24..56],&j.config);
    putRecord(bytes[56..96],j.outgoing); putRecord(bytes[96..136],j.selected); putRecord(bytes[136..176],j.old_private);
    var mask: u32=0;
    for (j.logs,0..) |optional,i| if (optional) |r| {
        mask |= @as(u32,1) << @intCast(i); putRecord(bytes[176+i*40..][0..40],r);
    };
    std.mem.writeInt(u32,bytes[296..300],mask,.little); seal(&bytes); return bytes;
}
pub fn decodeJournal(bytes: []const u8,config: Digest,count: usize) !Journal {
    try check(bytes,journal_size,"NM BCAJ1");
    if (!std.mem.eql(u8,bytes[24..56],&config) or !std.mem.allEqual(u8,bytes[300..320],0)) return error.DifferentStoreConfiguration;
    var j: Journal=.{.generation=std.mem.readInt(u64,bytes[8..16],.little),
        .old=std.mem.readInt(u32,bytes[16..20],.little),.new=std.mem.readInt(u32,bytes[20..24],.little),.config=config,
        .outgoing=try getRecord(bytes[56..96]),.selected=try getRecord(bytes[96..136]),
        .old_private=try getRecord(bytes[136..176]),.logs=.{null,null,null}};
    if (j.old >= count or j.new >= count or j.old == j.new or j.generation >= std.math.maxInt(u64)-2)
        return error.InvalidSwitchIdentity;
    const mask=std.mem.readInt(u32,bytes[296..300],.little);
    if (mask & ~@as(u32,7) != 0) return error.InvalidLogMask;
    for (0..3) |i| {
        const raw=bytes[176+i*40..][0..40];
        if (mask & (@as(u32,1) << @intCast(i)) != 0) j.logs[i]=try getRecord(raw)
        else if (!std.mem.allEqual(u8,raw,0)) return error.UnusedLogRecord;
    }
    return j;
}

test "state and journal round trip, every single byte corruption is refused" {
    const config=hash("synthetic configuration");
    const state=encodeState(.{.generation=12,.active=0,.config=config});
    try std.testing.expectEqual(@as(u64,12),(try decodeState(&state,config,2)).generation);
    const journal=encodeJournal(.{.generation=12,.old=0,.new=1,.config=config,
        .outgoing=record("old"),.selected=record("new"),.old_private=record("prior"),
        .logs=.{record("log"),record(""),null}});
    const j=try decodeJournal(&journal,config,2);
    try std.testing.expect(same(j.logs[1].?,record("")) and j.logs[2] == null);
    for (0..state.len) |i| {var bad=state;bad[i] ^= 1;try std.testing.expectError(error.InvalidTransactionRecord,decodeState(&bad,config,2));}
    for (0..journal.len) |i| {var bad=journal;bad[i] ^= 1;try std.testing.expectError(error.InvalidTransactionRecord,decodeJournal(&bad,config,2));}
    try std.testing.expectError(error.DifferentStoreConfiguration,decodeJournal(&journal,hash("different"),2));
    try std.testing.expectError(error.InvalidSwitchIdentity,decodeJournal(&journal,config,1));
}
