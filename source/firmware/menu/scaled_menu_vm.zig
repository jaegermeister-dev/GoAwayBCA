// Networkless VM menu-only diagnostics. Never a distributable EFI entry point.
const std = @import("std");
pub const activate_system_stores = true;
pub const scaling_trace = true;
pub fn main() std.os.uefi.Status {
    return @import("windows_chooser.zig").entry(false);
}
