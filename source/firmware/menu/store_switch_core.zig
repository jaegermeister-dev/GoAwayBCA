// EXPERIMENTAL transaction engine; lab namespace by default. Explicit firmware
// root opt-in selects SystemStores, used by the guarded integration VM only.
// Production installer/publication remains locked; this module grants no authority.
// Completed-operation recovery is supported. Unknown/torn files are retained
// and stop recovery; never guess that an unverified file is safe to overwrite.
const std=@import("std");
pub const w=@import("store_switch_wire.zig");
pub const system_store_mode=if (@hasDecl(@import("root"),"activate_system_stores"))
    @import("root").activate_system_stores else false;
pub const prefix=if (system_store_mode) "\\EFI\\WinDeploy\\SystemStores\\" else "\\EFI\\WinDeploy\\StoreSwitchLab\\";
pub const canon=prefix ++ "Canonical.BCD";
pub const state=prefix ++ "Active.bin";
pub const journal=prefix ++ "Pending.bin";
const outgoing=prefix ++ "Outgoing.BCD";
const selected=prefix ++ "Selected.BCD";
const next=prefix ++ "Next.BCD";
const previous=prefix ++ "Previous.BCD";
const private_next=prefix ++ "PrivateNext.BCD";
const private_previous=prefix ++ "PrivatePrevious.BCD";
const state_next=prefix ++ "StateNext.bin";
const state_previous=prefix ++ "StatePrevious.bin";
const log_names=[3][]const u8{canon ++ ".LOG",canon ++ ".LOG1",canon ++ ".LOG2"};
const transient=[8][]const u8{outgoing,selected,next,previous,private_next,private_previous,state_next,state_previous};
pub fn privateName(index: u32,buffer: *[128]u8) ![]const u8 {
    if (index >= 32) return error.InvalidStoreIndex;
    return std.fmt.bufPrint(buffer,prefix ++ "Private-{d}.BCD",.{index});
}
fn logName(generation: u64,index: u32,number: usize,buffer: *[128]u8) ![]const u8 {
    if (index >= 32 or number >= 3) return error.InvalidStoreIndex;
    return std.fmt.bufPrint(buffer,prefix ++ "Log-{x:0>16}-{d}-{d}.bin",.{generation,index,number});
}

// Storage exposes inspect/readAlloc/create/rename/remove/expect. Validation
// checks a clean BCD and its actual expected partition identities, not labels.
pub fn Engine(comptime Storage: type,comptime Validation: type) type {
    return struct {
        const Self=@This();
        io: *Storage, validation: *Validation, allocator: std.mem.Allocator,
        config: w.Digest, count: usize,
        fn read(self: *Self,name: []const u8,limit: usize) ![]u8 {
            return (try self.io.readAlloc(self.allocator,name,limit)) orelse error.MissingFile;
        }
        fn matches(self: *Self,name: []const u8,expected: w.Record) !bool {
            const actual=try self.io.inspect(name);
            return if (actual) |r| w.same(r,expected) else false;
        }
        fn verified(self: *Self,name: []const u8,index: u32) ![]u8 {
            const bytes=try self.read(name,2*1024*1024);
            errdefer self.allocator.free(bytes);
            try self.validation.validate(bytes,index); return bytes;
        }
        fn ensure(self: *Self,name: []const u8,bytes: []const u8) !void {
            if (try self.io.inspect(name)) |_| try self.io.expect(name,w.record(bytes))
            else _=try self.io.create(name,bytes);
        }
        fn discard(self: *Self,name: []const u8,expected: w.Record) !void {
            if (try self.io.inspect(name)) |_| try self.io.remove(name,expected);
        }
        fn stage(self: *Self,name: []const u8,expected: w.Record,sources: []const []const u8,index: u32) ![]u8 {
            if (try self.io.inspect(name)) |_| {
                try self.io.expect(name,expected);return self.verified(name,index);
            }
            for (sources) |source| {
                if (try self.matches(source,expected)) {
                    const bytes=try self.verified(source,index); errdefer self.allocator.free(bytes);
                    _=try self.io.create(name,bytes);return bytes;
                }
            }
            return error.NoVerifiedRecoverySource;
        }
        // No truncating writes. Complete verified siblings precede renames.
        fn replace(self: *Self,destination: []const u8,staged: []const u8,prior: []const u8,
            old: w.Record,bytes: []const u8) !void {
            const replacement=w.record(bytes);
            if (try self.matches(destination,replacement)) {
                try self.io.expect(staged,null);
                if (try self.io.inspect(prior)) |_| try self.io.expect(prior,old);
                return;
            }
            if (try self.matches(destination,old)) {
                try self.io.expect(prior,null);try self.ensure(staged,bytes);
                try self.io.rename(destination,prior,old);
            }
            try self.io.expect(destination,null);try self.io.expect(prior,old);
            try self.io.expect(staged,replacement);
            try self.io.rename(staged,destination,replacement);
        }
        pub fn activate(self: *Self,index: u32) !void {
            if (self.count < 1 or self.count > 32 or index >= self.count) return error.InvalidStoreIndex;
            if (try self.io.inspect(journal)) |_| try self.recover();
            const state_bytes=try self.read(state,w.state_size);defer self.allocator.free(state_bytes);
            const s=try w.decodeState(state_bytes,self.config,self.count);
            const old=try self.verified(canon,s.active);defer self.allocator.free(old);
            if (s.active == index) return;
            for (transient) |name| try self.io.expect(name,null);
            var old_buffer: [128]u8=undefined;var new_buffer: [128]u8=undefined;
            const old_name=try privateName(s.active,&old_buffer);
            const new_name=try privateName(index,&new_buffer);
            const new=try self.verified(new_name,index);defer self.allocator.free(new);
            const old_private=try self.verified(old_name,s.active);defer self.allocator.free(old_private);
            var j: w.Journal=.{.generation=s.generation,.old=s.active,.new=index,.config=self.config,
                .outgoing=w.record(old),.selected=w.record(new),.old_private=w.record(old_private),.logs=.{null,null,null}};
            for (log_names,0..) |name,i| {
                j.logs[i]=try self.io.inspect(name);
                var buffer: [128]u8=undefined;
                if (j.logs[i] != null) try self.io.expect(try logName(j.generation,j.old,i,&buffer),null);
            }
            // This is the FIRST mutation. A torn journal stops subsequent work;
            // all canonical/private originals still exist at this boundary.
            _=try self.io.create(journal,&w.encodeJournal(j));
            try self.recover();
        }
        pub fn recover(self: *Self) !void {
            const journal_bytes=try self.read(journal,w.journal_size);defer self.allocator.free(journal_bytes);
            const j=try w.decodeJournal(journal_bytes,self.config,self.count);
            const before=w.encodeState(.{.generation=j.generation,.active=j.old,.config=self.config});
            const after=w.encodeState(.{.generation=j.generation+1,.active=j.new,.config=self.config});
            var old_buffer: [128]u8=undefined;var new_buffer: [128]u8=undefined;
            const old_name=try privateName(j.old,&old_buffer);
            const new_name=try privateName(j.new,&new_buffer);
            if (try self.matches(state,w.record(&after))) {
                try self.io.expect(old_name,j.outgoing);
                const current=try self.verified(canon,j.new);self.allocator.free(current);
                // Windows may have serviced its new canonical hive after a
                // completed state commit; cleanup must not discard that change.
                try self.cleanup(j,journal_bytes,&before);return;
            }
            if (try self.io.inspect(state)) |_| try self.io.expect(state,w.record(&before))
            else try self.io.expect(state_previous,w.record(&before));
            const old=try self.stage(outgoing,j.outgoing,&.{canon,previous,old_name},j.old);
            defer self.allocator.free(old);
            const new=try self.stage(selected,j.selected,&.{new_name,next,canon},j.new);
            defer self.allocator.free(new);
            // The source of the next store is never rewritten by activation.
            try self.io.expect(new_name,j.selected);
            if (!try self.matches(old_name,j.old_private) and !try self.matches(old_name,j.outgoing)) {
                try self.io.expect(old_name,null);try self.io.expect(private_previous,j.old_private);
            }
            for (log_names,0..) |live,i| {
                if (j.logs[i]) |expected| {
                    var name_buffer: [128]u8=undefined;
                    const archive=try logName(j.generation,j.old,i,&name_buffer);
                    if (try self.io.inspect(archive)) |_| try self.io.expect(archive,expected) else {
                        try self.io.expect(live,expected);
                        const bytes=try self.read(live,16*1024*1024);defer self.allocator.free(bytes);
                        _=try self.io.create(archive,bytes);
                    }
                    try self.discard(live,expected);
                } else try self.io.expect(live,null);
            }
            try self.replace(canon,next,previous,j.outgoing,new);
            try self.replace(old_name,private_next,private_previous,j.old_private,old);
            try self.replace(state,state_next,state_previous,w.record(&before),&after);
            try self.cleanup(j,journal_bytes,&before);
        }
        fn cleanup(self: *Self,j: w.Journal,raw: []const u8,before: []const u8) !void {
            for (j.logs,0..) |optional,i| if (optional) |expected| {
                var buffer: [128]u8=undefined;
                try self.io.expect(try logName(j.generation,j.old,i,&buffer),expected);
            };
            try self.discard(outgoing,j.outgoing);try self.discard(selected,j.selected);
            try self.discard(previous,j.outgoing);try self.discard(private_previous,j.old_private);
            try self.discard(state_previous,w.record(before));
            for ([_][]const u8{next,private_next,state_next}) |name| try self.io.expect(name,null);
            try self.io.remove(journal,w.record(raw)); // LAST mutation
        }
    };
}
