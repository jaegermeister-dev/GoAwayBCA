const d=@import("boot_diagnostic.zig");
pub const diagnostic_variant: u8 = 3;
pub const activate_system_stores=true;
pub const diagnosticTrace=d.diagnosticTrace;
pub const requireFlushedLog=d.requireFlushedLog;
pub const record=d.record;
pub const startLogged=d.startLogged;
pub fn main() @import("std").os.uefi.Status { return d.main(); }
