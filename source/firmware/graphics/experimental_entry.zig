// No new hardware compatibility. Reuses the accepted RAM-only ACPI operation.
pub fn main() @import("std").os.uefi.Status { return @import("rom_test.zig").main(); }
test { _ = @import("experimental_paths.zig"); _ = @import("rom_test.zig"); _ = @import("namespace_snapshot.zig"); }
