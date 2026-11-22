// Selected-partition diagnostic application. No automatic repairs or NVRAM writes.
const std=@import("std");
const u=std.os.uefi;
const File=u.protocol.File;
const utf16=std.unicode.utf8ToUtf16LeStringLiteral;
const loader=@import("boot_diagnostic_loader.zig");
const parse=@import("chooser_parse.zig");
const guard=@import("gpt_firmware_guard.zig");
pub const activate_system_stores=true;
export var diagnostic_binding: [512]u8 = initialBinding();
fn initialBinding() [512]u8 {
    var value: [512]u8=@splat(0);
    @memcpy(value[0..27],"GoAwayBCA-EFI-DIAGNOSTIC-V1");
    return value;
}
var config: [512]u8=undefined;
var log: ?*File=null;
var log_failed=false;
var folder_name: [180:0]u16=undefined;

pub fn requireFlushedLog() !void {
    if(log_failed or log==null) return error.LogCouldNotBeFlushed;
}

pub fn diagnosticTrace(text: []const u8) void {
    const output=log orelse return;
    var done: usize=0;
    while (done<text.len) {
        const n=output.write(text[done..]) catch {log_failed=true;return;};
        if (n==0) {log_failed=true;return;}
        done+=n;
    }
    output.flush() catch {log_failed=true;};
}
fn record(comptime format: []const u8,args: anytype) void {
    var bytes: [4096]u8=undefined;
    const text=std.fmt.bufPrint(&bytes,format,args) catch {diagnosticTrace("Record exceeded log bound\r\n");return;};
    diagnosticTrace(text);
}
fn say(text: []const u8) void {
    var wide: [1024:0]u16=undefined;
    const n=@min(text.len,1023);
    for (text[0..n],0..) |c,i| wide[i]=c;
    wide[n]=0;
    if (u.system_table.con_out) |con| _=con.outputString(@ptrCast(&wide)) catch false;
}
fn key() !u16 {
    const input=u.system_table.con_in orelse return error.NoConsoleInput;
    const bs=u.system_table.boot_services orelse return error.NoBootServices;
    while (true) {
        const value=input.readKeyStroke() catch |err| switch (err) {
            error.NotReady=>{_=try bs.waitForEvent(&.{input.wait_for_key});continue;},
            else=>return err,
        };
        if (value.scan_code==0x17) return 27;
        return value.unicode_char;
    }
}
fn loadBinding() !void {
    const bytes: *volatile [512]u8=&diagnostic_binding;
    for (0..512) |i| config[i]=bytes[i];
    const version=std.mem.readInt(u32,config[32..36],.little);
    if (version!=1 and version!=2) return error.UnboundDiagnosticTemplate;
    var digest=std.crypto.hash.sha2.Sha256.init(.{});digest.update(config[0..168]);digest.update(config[200..]);
    var expected: [32]u8=undefined;digest.final(&expected);
    if (!std.mem.eql(u8,&expected,config[168..200])) return error.InvalidDiagnosticBinding;
    const sector=std.mem.readInt(u32,config[36..40],.little);
    if (sector!=512 and sector!=4096) return error.InvalidSectorSize;
}
fn shortFolder(bytes: []const u8) ![]const u8 {
    if(bytes.len!=16 or !std.mem.eql(u8,bytes[0..5],"Test-") or bytes[8]!=0) return error.InvalidDiagnosticFolder;
    for(bytes[5..8]) |c| if(c<'0' or c>'9') return error.InvalidDiagnosticFolder;
    if(std.mem.eql(u8,bytes[5..8],"000")) return error.InvalidDiagnosticFolder;
    for(bytes[8..]) |c| if(c!=0) return error.InvalidDiagnosticFolder;
    return bytes[0..8];
}
fn openLog(own: u.Handle) !void {
    const bs=u.system_table.boot_services orelse return error.NoBootServices;
    const dp=(try bs.handleProtocol(u.protocol.DevicePath,own)) orelse return error.NoDevicePath;
    if (!loader.matches(dp,config[72..88])) return error.WrongDiagnosticEsp;
    const fs=(try bs.handleProtocol(u.protocol.SimpleFileSystem,own)) orelse return error.NoEspFilesystem;
    const root=try fs.openVolume();defer root.close() catch {};
    const hex="0123456789abcdef";
    // Session is represented as the embedded byte sequence, consistently in Python and firmware.
    var session: [32]u8=undefined;
    for (config[40..56],0..) |c,i| {session[i*2]=hex[c>>4];session[i*2+1]=hex[c&15];}
    var path: [180]u8=undefined;
    const session_folder=if(std.mem.readInt(u32,config[32..36],.little)==2) try shortFolder(config[264..280]) else &session;
    const folder=try std.fmt.bufPrint(&path,"\\EFI\\WinDeploy\\Diagnostics\\{s}",.{session_folder});
    for (folder,0..) |c,i| folder_name[i]=c;folder_name[folder.len]=0;
    const directory=try root.open(@ptrCast(&folder_name),.read,.{});defer directory.close() catch {};
    for (0..1000) |number| {
        var narrow: [32]u8=undefined;var wide: [32:0]u16=undefined;
        const name=try std.fmt.bufPrint(&narrow,"boot-{d:0>3}.log",.{number});
        for(name,0..) |c,i| wide[i]=c;wide[name.len]=0;
        if (directory.open(@ptrCast(&wide),.read,.{})) |existing| {try existing.close();continue;} else |err| {
            if(err!=error.NotFound) return err;
        }
        log=try directory.open(@ptrCast(&wide),.read_write_create,.{});
        say("Saving diagnostic log: ");say(name);say("\r\n");
        return;
    }
    return error.DiagnosticLogLimit;
}
fn inventory(own: u.Handle) !void {
    const bs=u.system_table.boot_services orelse return error.NoBootServices;
    record("GoAwayBCA targeted boot diagnostic v1\r\nFirmware table revision={x}; firmware revision={x}\r\n",.{u.system_table.hdr.revision,u.system_table.firmware_revision});
    record("Expected logical sectors={d}; inherited Mac model={s}\r\n",.{std.mem.readInt(u32,config[36..40],.little),std.mem.sliceTo(config[200..264],0)});
    diagnosticTrace("Binding bytes (GPT little-endian):\r\n");
    inline for (.{.{"disk",@as(usize,56)},.{"ESP",@as(usize,72)},.{"Windows",@as(usize,88)}}) |item| {
        record("{s}=",.{item[0]});for (config[item[1]..][0..16]) |c| record("{x:0>2}",.{c});diagnosticTrace("\r\n");
    }
    const handles=(try bs.locateHandleBuffer(.{.by_protocol=&u.protocol.BlockIo.guid})) orelse return error.NoBlockIo;
    defer bs.freePool(@alignCast(@ptrCast(handles.ptr))) catch {};
    for(handles,0..) |handle,i| {
        const block=(try bs.handleProtocol(u.protocol.BlockIo,handle)) orelse continue;
        const fs=(try bs.handleProtocol(u.protocol.SimpleFileSystem,handle))!=null;
        record("Block {d}: logical_partition={} sector={d} last_lba={d} present={} read_only={} filesystem={}\r\n",.{i,block.media.logical_partition,block.media.block_size,block.media.last_block,block.media.media_present,block.media.read_only,fs});
    }
    guard.verifyPartition(own,config[56..72],config[72..88],&guard.esp_type) catch |err| {record("ESP GPT verification: {s}\r\n",.{@errorName(err)});return err;};
    const windows=try loader.partition(config[88..104]);
    try guard.verifyPartition(windows,config[56..72],config[88..104],&guard.basic_type);
    const block=(try bs.handleProtocol(u.protocol.BlockIo,windows)) orelse return error.NoWindowsBlockIo;
    if (block.media.block_size!=std.mem.readInt(u32,config[36..40],.little)) return error.SectorSizeChanged;
    diagnosticTrace("Selected disk and Windows partition verified.\r\n");
}
fn reportDirectory(own: u.Handle) !*File {
    const bs=u.system_table.boot_services orelse return error.NoBootServices;
    const fs=(try bs.handleProtocol(u.protocol.SimpleFileSystem,own)) orelse return error.NoEspFilesystem;
    const root=try fs.openVolume();defer root.close() catch {};
    return root.open(@ptrCast(&folder_name),.read,.{});
}
fn nextPciReport(dir: *File) ![32:0]u16 {
    for(0..100) |number| {
        var ascii: [32]u8=undefined;var wide: [32:0]u16=@splat(0);
        const name=try std.fmt.bufPrint(&ascii,"pci-{d:0>3}.txt",.{number});
        for(name,0..) |c,i| wide[i]=c;
        if(dir.open(@ptrCast(&wide),.read,.{})) |file| {try file.close();} else |err| {
            if(err==error.NotFound) return wide;return err;
        }
    }
    return error.NoUnusedReportName;
}
fn pciSummary(dir: *File,name: *const [32:0]u16) !void {
    const data=try loader.read(dir,@ptrCast(name),256*1024);
    var lines=std.mem.splitScalar(u8,data,'\n');var found=false;
    while(lines.next()) |line| {
        if(std.mem.startsWith(u8,line,"END handles=") or std.mem.startsWith(u8,line,"RESULT ")) {
            if(line.len>512) return error.InvalidReportSummary;
            record("PCI: {s}\n",.{line});found=true;
        }
    }
    if(!found) return error.MissingReportSummary;
}
fn collect(own: u.Handle,comptime name: []const u8,expected: *const [32]u8) !void {
    const length=std.mem.sliceTo(&folder_name,0).len;
    var wide: [256:0]u16=undefined;@memcpy(wide[0..length],folder_name[0..length]);wide[length]='\\';
    for(name,0..) |c,i| wide[length+1+i]=c;wide[length+1+name.len]=0;
    record("Collector {s}: LoadImage\r\n",.{name});
    const child=try loader.load(own,@ptrCast(&wide),expected);
    const bs=u.system_table.boot_services orelse return error.NoBootServices;
    defer _=bs.unloadImage(child) catch .aborted;
    const report_dir=if(std.mem.eql(u8,name,"PCI.efi")) try reportDirectory(own) else null;
    defer if(report_dir) |dir| dir.close() catch {};
    const report_name=if(report_dir) |dir| try nextPciReport(dir) else null;
    record("Collector {s}: StartImage\r\n",.{name});
    const result=try bs.startImage(child);
    if(report_dir) |dir| pciSummary(dir,&report_name.?) catch |err| record("PCI report summary unavailable: {s}; inspect the collector report.\r\n",.{@errorName(err)});
    try result.code.err();
    record("Collector {s}: completed; see its report for coverage and unavailable devices\r\n",.{name});
}
fn boot(own: u.Handle) !void {
    const bs=u.system_table.boot_services orelse return error.NoBootServices;
    const fs=(try bs.handleProtocol(u.protocol.SimpleFileSystem,own)) orelse return error.NoEspFilesystem;
    const root=try fs.openVolume();defer root.close() catch {};
    diagnosticTrace("Reading current Windows menu configuration\r\n");
    const raw=try u.pool_allocator.dupe(u8,try loader.read(root,utf16("\\EFI\\WinDeploy\\menu.bin"),parse.header_size+parse.max_entries*parse.record_size));
    defer u.pool_allocator.free(raw);
    const menu=try parse.parse(raw);
    var selected: ?usize=null;
    for(menu.entries[0..menu.count],0..) |item,i| if(std.mem.eql(u8,&item.windows_guid,config[88..104])) {
        if(selected!=null) return error.AmbiguousWindowsMenuEntry;selected=i;
    };
    const index=selected orelse return error.SelectedWindowsMissingFromMenu;
    record("Target entry index={d}; available={} ntfs={} hash_locked={}\r\n",.{index,menu.entries[index].available(),menu.entries[index].ntfs(),menu.entries[index].hashLocked()});
    if(log_failed) return error.LogCouldNotBeFlushed;
    try loader.launch(false,own,&menu.entries[index],raw,index);
    diagnosticTrace("Selected Windows loader returned success to diagnostics.\r\n");
}
fn run() !void {
    try loadBinding();
    const bs=u.system_table.boot_services orelse return error.NoBootServices;
    const image=(try bs.handleProtocol(u.protocol.LoadedImage,u.handle)) orelse return error.NoLoadedImage;
    const own=image.device_handle orelse return error.NoOwnDevice;
    try openLog(own);
    defer {if(log) |file| {file.flush() catch {};file.close() catch {};log=null;}}
    say("\r\nGoAwayBCA Windows EFI diagnostics\r\n1 - Collect diagnostics only\r\n2 - Collect diagnostics and try the selected Windows installation\r\nEsc - Return to OpenShell\r\n\r\nOption 2 uses normal per-Windows BCD selection, just like the boot menu.\r\nNo automatic patches, boot defaults or partition changes.\r\n");
    var choice: u16=0;while(choice!='1' and choice!='2' and choice!=27) choice=try key();
    if(choice==27) {diagnosticTrace("Cancelled before collection\r\n");return;}
    record("Selected diagnostic option={c}\r\n",.{@as(u8,@intCast(choice))});
    var identity_ok=true;
    inventory(own) catch |err| {record("Inventory/identity error: {s}\r\n",.{@errorName(err)});identity_ok=false;};
    collect(own,"PCI.efi",config[104..136]) catch |err| record("PCI collector error: {s}\r\n",.{@errorName(err)});
    collect(own,"ACPI.efi",config[136..168]) catch |err| record("ACPI collector error: {s}\r\n",.{@errorName(err)});
    if(choice=='2') {
        if(identity_ok) boot(own) catch |err| {
            record("Windows boot attempt failed: {s}\r\n",.{@errorName(err)});
            say("\r\nWindows boot returned: ");say(@errorName(err));say("\r\n");
        } else {diagnosticTrace("Boot not attempted because target identity/geometry could not be verified.\r\n");say("Target identity check failed; no Windows boot attempted.\r\n");}
    }
    diagnosticTrace("Diagnostic application finished.\r\n");
    say(if(log_failed) "Some log writes failed. Photograph the displayed error.\r\n" else "Logs saved beside this tool. Return to macOS and copy this diagnostic folder.\r\n");
    say("Press any key to return to OpenShell.\r\n");_=try key();
}
pub fn main() u.Status {
    defer loader.releaseNtfs();
    run() catch |err| {say("GoAwayBCA diagnostic could not start: ");say(@errorName(err));say("\r\nPress any key.\r\n");_=key() catch 0;return .aborted;};
    return .success;
}
