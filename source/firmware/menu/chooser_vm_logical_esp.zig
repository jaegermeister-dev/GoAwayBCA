// Diagnostic only: fixed synthetic VM GUID, no writes/NVRAM/network.
// NEVER package this automatic-selection entry point for an owner installation.
pub const use_esp_logical_path = true;
pub fn main() @import("std").os.uefi.Status {
    return @import("windows_chooser.zig").entry(true);
}
