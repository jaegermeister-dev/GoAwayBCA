// Non-VM engineering integration. No automatic selection, test bypass,
// NVRAM/default change or synthetic identity. All menu/configuration, disk
// identity and boot-store checks are performed by the shared implementation.
// Hardware publication remains locked in the macOS installer pending acceptance.
const std = @import("std");
pub const activate_system_stores = true;
pub fn main() std.os.uefi.Status {
    return @import("windows_chooser.zig").entry(false);
}
