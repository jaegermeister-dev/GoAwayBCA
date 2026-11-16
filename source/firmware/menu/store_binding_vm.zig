// NEVER SHIP. Configuration-bound A -> B -> A on the fixed synthetic ESP only.
// All mutations remain in StoreSwitchLab. Does not launch Windows or touch the
// real canonical BCD. The menu is data for this test, not an executable target.
const std=@import("std");
const u=std.os.uefi;
const core=@import("store_switch_core.zig");
const io=@import("store_file_io.zig");
const guard=@import("store_lab_guard.zig");
const configuration=@import("store_configuration.zig");
fn run() !void {
    const root=try guard.open();defer root.close() catch {};
    var files: io.IO=.{.root=root};const allocator=u.pool_allocator;
    const menu=(try files.readAlloc(allocator,core.prefix ++ "BindingMenu.bin",16384)) orelse return error.NoBindingMenu;
    defer allocator.free(menu);
    const bytes=(try files.readAlloc(allocator,core.prefix ++ "BindingConfig.bin",4096)) orelse return error.NoBindingConfiguration;
    defer allocator.free(bytes);
    const config=try configuration.parse(bytes,menu);
    if (config.count != 2) return error.WrongLabStoreCount;
    try @import("gpt_firmware_guard.zig").verifyLoadedEsp(&config.identities[0].esp_disk,&config.identities[0].esp);
    guard.trace("STORE-BINDING: both parent GPT copies, protective MBR and actual ESP geometry verified\n");
    var validator: configuration.Validation=.{.config=&config,.allocator=allocator};
    guard.trace("STORE-BINDING: exact menu and ordered disk/partition identities validated\n");
    try @import("store_switch_vm.zig").exercise(configuration.Validation,&files,&validator,config.digest);
}
pub fn main() u.Status {
    run() catch |err| {guard.trace("STORE-BINDING: FAIL ");guard.trace(@errorName(err));guard.trace("\n");return .aborted;};
    return .success;
}
