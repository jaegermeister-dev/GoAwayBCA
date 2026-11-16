// NEVER SHIP. Same guarded synthetic store activation, but the unmodified
// Windows loader and its resources are read from its private NTFS boot tree.
// Both synthetic public EFI aliases invoke this test-only dispatcher.
pub const synthetic_canonical_store_test=true;
pub fn main() @import("std").os.uefi.Status {
    const guard=@import("store_lab_guard.zig");
    @import("canonical_store_boot_vm.zig").prepare() catch |err| {
        guard.trace("CANONICAL-NTFS-VM: FAIL ");guard.trace(@errorName(err));guard.trace("\n");
        return .aborted;
    };
    guard.trace("CANONICAL-NTFS-VM: private NTFS loader/resource path\n");
    return @import("windows_chooser.zig").entry(true);
}
