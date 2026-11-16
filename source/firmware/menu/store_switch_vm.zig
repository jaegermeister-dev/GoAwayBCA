// Synthetic-ESP-only REAL file transaction and REAL BCD parser acceptance.
// Never writes EFI/Microsoft, never boots Windows, never distribute this binary.
const std=@import("std");
const u=std.os.uefi;
const core=@import("store_switch_core.zig");
const io=@import("store_file_io.zig");
const guard=@import("store_lab_guard.zig");
const parser=@import("boot_store_parse.zig");
const semantics=@import("boot_store_identity.zig");
const utf16=std.unicode.utf8ToUtf16LeStringLiteral;
pub const Validation=struct {
    pub fn validate(_: *Validation,bytes: []const u8,index: u32) !void {
        if (index > 1) return error.UnknownLabStore;
        const hive=try u.pool_allocator.create(parser.Hive);
        defer u.pool_allocator.destroy(hive);
        try hive.initInto(bytes);
        const a=[_]u8{0x11,0x11,0x11,0x11,0xaa,0xaa,0xbb,0x4b,0x8c,0xcc,0x11,0x11,0x11,0x11,0x11,0x11};
        const b=[_]u8{0x72,0xa3,0x67,0x63,0x20,0x91,0x76,0x41,0xb4,0xbb,0x7e,0x74,0x3e,0x60,0x7a,0x09};
        const id: semantics.Identity=.{
            .esp_disk=.{0x1c,0x42,0x0d,0xcf,0xa1,0xe6,0x50,0x49,0x93,0xbe,0xcc,0xdd,0x81,0x1a,0xcd,0x0b},
            .esp=.{0xba,0x3b,0x7c,0x6a,0x80,0x13,0x21,0x4e,0xb6,0x85,0x9d,0x02,0x93,0x77,0xb9,0x6a},
            .windows_disk=.{0x1e,0x57,0xab,0xe5,0x20,0x53,0xc8,0x4d,0x84,0x28,0x78,0x7e,0x5d,0x55,0x9e,0x3f},
            .windows=if (index == 0) a else b};
        try semantics.validate(hive,&id);
    }
};
fn run() !void {
    const root=try guard.open();defer root.close() catch {};
    var files: io.IO=.{.root=root};var validator: Validation=.{};
    try exercise(Validation,&files,&validator,core.w.hash("synthetic two-store firmware acceptance v1"));
}
pub fn exercise(comptime V: type, files: *io.IO, validator: *V, config: core.w.Digest) !void {
    const allocator=u.pool_allocator;
    // Fixtures and identities are created by the lab builder, not derived from
    // a detected physical machine. State initialization is new-file only.
    const a=(try files.readAlloc(allocator,core.prefix ++ "Private-0.BCD",2*1024*1024)) orelse return error.NoLabA;
    defer allocator.free(a);
    const b=(try files.readAlloc(allocator,core.prefix ++ "Private-1.BCD",2*1024*1024)) orelse return error.NoLabB;
    defer allocator.free(b);
    guard.trace("STORE-SWITCH: validating original stores\n");
    try validator.validate(a,0);try validator.validate(b,1);
    guard.trace("STORE-SWITCH: original references verified\n");
    _=try files.create(core.state,&core.w.encodeState(.{.generation=0,.active=0,.config=config}));
    _=try files.create(core.canon,a);
    _=try files.create(core.canon ++ ".LOG","synthetic outgoing log evidence");
    _=try files.create(core.canon ++ ".LOG1","");
    var engine: core.Engine(io.IO,V)=.{.io=files,.validation=validator,.allocator=allocator,.config=config,.count=2};
    try engine.activate(1);try files.expect(core.canon,core.w.record(b));
    guard.trace("STORE-SWITCH: A -> B verified\n");
    try engine.activate(0);try files.expect(core.canon,core.w.record(a));
    guard.trace("STORE-SWITCH: B -> A verified\n");
    try files.expect(core.prefix ++ "Private-0.BCD",core.w.record(a));
    try files.expect(core.prefix ++ "Private-1.BCD",core.w.record(b));
    try files.expect(core.journal,null);
    try files.expect(core.state,core.w.record(&core.w.encodeState(.{.generation=2,.active=0,.config=config})));
    _=try files.create(core.prefix ++ "SWITCH-RESULT.txt","PASS: real BCD semantics, A -> B -> A, log preservation, flush/readback, separate stores\n");
    guard.trace("STORE-SWITCH: PASS; canonical system BCD untouched, lab only\n");
}
pub fn main() u.Status {
    run() catch |err| {guard.trace("STORE-SWITCH: FAIL ");guard.trace(@errorName(err));guard.trace("\n");return .aborted;};
    return .success;
}
