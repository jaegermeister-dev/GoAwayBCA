// Private model/firmware-pinned automatic Windows 7 entry, 2026-10-09.
// All ACPI operations and rollback are shared with the hardware-proven v2.
const helper = @import("rom_test.zig");
pub fn main() @import("std").os.uefi.Status { return helper.main(); }
test { try @import("std").testing.expect(helper.isAutomaticProfile()); }
