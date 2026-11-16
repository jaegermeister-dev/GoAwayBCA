// NEVER SHIP: shared-chooser integration acceptance on the fixed synthetic ESP.
const u=@import("std").os.uefi;
const guard=@import("store_lab_guard.zig");
pub const activate_system_stores=true;
pub const system_store_menu_probe=true;
pub fn main() u.Status {
    const root=guard.open() catch |err| {
        guard.trace("SYSTEM-CHOOSER-VM: authority refused ");guard.trace(@errorName(err));guard.trace("\n");
        return .aborted;
    };
    root.close() catch return .aborted;
    return @import("windows_chooser.zig").entry(true);
}
