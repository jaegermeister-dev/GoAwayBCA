// NEVER SHIP. Same isolated guard and integration, but do not execute Windows.
pub const activate_system_stores=true;
pub const system_store_menu_probe=true;
pub const system_store_preflight_only=true;
pub fn main() @import("std").os.uefi.Status {
    return @import("system_store_chooser_vm.zig").main();
}
