// Experimental mode: snapshot this boot's namespace, then permit only the
// separately checked added table. No model-specific compatibility changes.
const std = @import("std");
pub const Snapshot = struct {
    hashes: [128][32]u8 = undefined,
    seen: [128]bool = @splat(false),
    count: usize = 0,
    sealed: bool = false,
    pub fn begin(self: *@This()) void { self.seen = @splat(false); }
    pub fn check(self: *@This(), bytes: []const u8) !void {
        var hash: [32]u8 = undefined;
        std.crypto.hash.sha2.Sha256.hash(bytes, &hash, .{});
        for (self.hashes[0..self.count], 0..) |old,i| {
            if (std.mem.eql(u8, &old, &hash)) { self.seen[i]=true; return; }
        }
        if (self.sealed) return error.NamespaceChanged;
        if (self.count == self.hashes.len) return error.TooManyNamespaceTables;
        self.hashes[self.count]=hash; self.seen[self.count]=true; self.count+=1;
    }
    pub fn finish(self: *@This()) !void {
        if (self.count==0) return error.NoNamespaceTables;
        for (self.seen[0..self.count]) |found| if (!found) return error.NamespaceTableMissing;
        self.sealed=true;
    }
};
test "experimental snapshot rejects changed or missing original namespace" {
    var s=Snapshot{};s.begin();try s.check("DSDT");try s.check("SSDT");try s.check("DSDT");try s.finish();
    try std.testing.expectEqual(@as(usize,2),s.count);
    s.begin();try s.check("SSDT");try s.check("DSDT");try s.finish();
    try std.testing.expectError(error.NamespaceChanged,s.check("different"));
    s.begin();try s.check("DSDT");try std.testing.expectError(error.NamespaceTableMissing,s.finish());
}
