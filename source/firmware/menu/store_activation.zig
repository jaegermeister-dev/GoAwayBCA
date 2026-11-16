// Experimental integration; production installer remains disabled.
// Requires explicit root compile-time opt-in, prepublished bound configuration,
// verified ESP/Windows parent GPT identities and clean per-OS BCD semantics.
// Does NOT initialize missing state, merge Windows entries or write NVRAM.
const std=@import("std");
const u=std.os.uefi;
const core=@import("store_switch_core.zig");
const files=@import("store_file_io.zig");
const binding=@import("store_configuration.zig");
const gpt=@import("gpt_firmware_guard.zig");
pub const Context=struct {
    io: files.IO,
    configuration: binding.Config,
    pub fn open(own: u.Handle,menu_bytes: []const u8) !Context {
        if (!core.system_store_mode) @compileError("System-store activation requires explicit firmware root opt-in");
        const bs=u.system_table.boot_services orelse return error.NoBootServices;
        const fs=(try bs.handleProtocol(u.protocol.SimpleFileSystem,own)) orelse return error.NoEspFilesystem;
        const root=try fs.openVolume();errdefer root.close() catch {};
        var io: files.IO=.{.root=root};
        const bytes=(try io.readAlloc(u.pool_allocator,core.prefix ++ "Configuration.bin",4096)) orelse return error.NoStoreConfiguration;
        defer u.pool_allocator.free(bytes);
        const configuration=try binding.parse(bytes,menu_bytes);
        try gpt.verifyPartition(own,&configuration.identities[0].esp_disk,&configuration.identities[0].esp,&gpt.esp_type);
        return .{.io=io,.configuration=configuration};
    }
    pub fn close(self: *Context) void {self.io.root.close() catch {};}
    pub fn activate(self: *Context,windows_partition: u.Handle,index: u32) !void {
        if (index >= self.configuration.count) return error.UnknownStoreIndex;
        const id=self.configuration.identities[index];
        try gpt.verifyPartition(windows_partition,&id.windows_disk,&id.windows,&gpt.basic_type);
        var validator: binding.Validation=.{.config=&self.configuration,.allocator=u.pool_allocator};
        var engine: core.Engine(files.IO,binding.Validation)=.{.io=&self.io,.validation=&validator,
            .allocator=u.pool_allocator,.config=self.configuration.digest,.count=self.configuration.count};
        try engine.activate(index);
    }
};
