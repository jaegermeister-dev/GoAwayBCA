// Developer-only variant. Refuses anything except the one synthetic VM GUID.
// Never package this auto-selection binary in the app or on physical EFI.
pub fn main() @import("std").os.uefi.Status {
    return @import("windows_chooser.zig").entry(true);
}
