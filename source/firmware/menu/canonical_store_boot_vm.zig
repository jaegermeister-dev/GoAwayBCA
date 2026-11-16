// NEVER SHIP. Activates the synthetic Win10 store on the synthetic ESP only,
// then starts the existing synthetic logical-ESP boot probe. No host devices,
// physical-Mac GUIDs, default-boot writes or networking are permitted here.
const std=@import("std");
const u=std.os.uefi;
const core=@import("store_switch_core.zig");
const io=@import("store_file_io.zig");
const guard=@import("store_lab_guard.zig");
const validation=@import("store_switch_vm.zig").Validation;
pub const synthetic_canonical_store_test=true;
pub const use_esp_logical_path=true;

pub fn prepare() !void {
    // This checks the fixed synthetic GPT ESP GUID and exact lab marker before
    // obtaining any writable file handle. It cannot run on the owner's ESP.
    const root=try guard.open();defer root.close() catch {};
    var files: io.IO=.{.root=root};var validator: validation=.{};
    const allocator=u.pool_allocator;
    const config=core.w.hash("synthetic two-store firmware acceptance v1");
    const a=(try files.readAlloc(allocator,core.prefix ++ "Private-0.BCD",2*1024*1024)) orelse return error.NoLabA;
    defer allocator.free(a);
    const b=(try files.readAlloc(allocator,core.prefix ++ "Private-1.BCD",2*1024*1024)) orelse return error.NoLabB;
    defer allocator.free(b);
    try validator.validate(a,0);try validator.validate(b,1);
    if (try files.inspect(core.state)) |_| {
        // A retry/reboot must validate persisted state and the serviced BCD;
        // it must not initialize again or replace Windows' own changes.
        guard.trace("CANONICAL-VM: existing transaction state\n");
    } else {
        try files.expect(core.journal,null);
        try files.expect(core.canon,core.w.record(a));
        _=try files.create(core.state,&core.w.encodeState(.{.generation=0,.active=0,.config=config}));
    }
    var engine: core.Engine(io.IO,validation)=.{.io=&files,.validation=&validator,
        .allocator=allocator,.config=config,.count=2};
    try engine.activate(1);
    const current=(try files.readAlloc(allocator,core.canon,2*1024*1024)) orelse return error.MissingActiveBCD;
    defer allocator.free(current);
    try validator.validate(current,1);
    guard.trace("CANONICAL-VM: Win10 identity/reference validation passed; starting child\n");
}
pub fn main() u.Status {
    prepare() catch |err| {
        guard.trace("CANONICAL-VM: FAIL ");guard.trace(@errorName(err));guard.trace("\n");
        return .aborted;
    };
    return @import("windows_chooser.zig").entry(true);
}
