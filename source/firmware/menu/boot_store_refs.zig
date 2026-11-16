// Read-only, bounded reference validation for the observed per-OS BCD profile.
const std=@import("std");
const r=@import("boot_store_parse.zig");
const zero="{00000000-0000-0000-0000-000000000000}";
const resume_settings="{1afa9c49-16ab-4a5c-901b-212802da9460}";
const loader_settings="{6efb52bf-1766-41db-a6b3-0ee5eff72bd7}";
const firmware_manager="{a5a30fa2-3d06-4e9f-b5f4-a01df9d1fcba}";
const Refs=struct {
    count: usize,data: []const u8,
    fn name(self: Refs,index: usize,out: *[38]u8) *const [38]u8 {
        for (0..38) |j| out[j]=std.ascii.toLower(self.data[index*78+j*2]);
        return out;
    }
};

fn guid(name: []const u8) bool {
    if (name.len!=38 or name[0]!='{' or name[37]!='}') return false;
    for (name[1..37],1..) |c,i| {
        if (i==9 or i==14 or i==19 or i==24) {if (c!='-') return false;}
        else if (!std.ascii.isHex(c)) return false;
    }
    return true;
}
fn refs(value: r.Value,number: u32,allow_duplicate_firmware_order: bool) !Refs {
    const format=(number>>24)&15;
    if (format!=3 and format!=4) return error.InvalidReferenceType;
    var count: usize=1;
    if (format==3) {
        if (value.kind!=1 or value.data.len!=78) return error.InvalidReferenceType;
    } else {
        if (value.kind!=7 or value.data.len<80 or value.data.len>4994 or
            (value.data.len-2)%78!=0 or !std.mem.allEqual(u8,value.data[value.data.len-2..],0))
            return error.InvalidReferenceType;
        count=(value.data.len-2)/78;
    }
    const out: Refs=.{.count=count,.data=value.data};
    for (0..count) |i| {
        const item=value.data[i*78..][0..78];
        if (!std.mem.allEqual(u8,item[76..],0)) return error.InvalidReferenceTerminator;
        for (0..38) |j| {
            if (item[j*2+1]!=0) return error.InvalidReferenceGuid;
        }
        var current: [38]u8=undefined;_=out.name(i,&current);
        if (!guid(&current)) return error.InvalidReferenceGuid;
        for (0..i) |j| {
            var prior: [38]u8=undefined;
            if (!allow_duplicate_firmware_order and std.mem.eql(u8,&current,out.name(j,&prior))) return error.DuplicateReference;
        }
    }
    return out;
}
fn kind(h: *const r.Hive,obj: u32) !u32 {
    const value=try h.value(try h.child(obj,"Description"),"Type");
    if (value.kind!=4 or value.data.len!=4) return error.InvalidObjectType;
    return r.n(value.data,0);
}
fn optional(h: *const r.Hive,objects: u32,name: []const u8) !?u32 {
    return h.child(objects,name) catch |err| {
        if (err==error.MissingKey) return null;
        return err;
    };
}
fn inherited(h: *const r.Hive,objects: u32,owner: u32,trail: *[32]u32,depth: usize,done: *r.Children) anyerror!void {
    if (depth==32) return error.InheritanceDepth;
    for (trail[0..depth]) |prior| if (prior==owner) return error.InheritanceCycle;
    for (done.offsets[0..done.count]) |prior| if (prior==owner) return;
    trail[depth]=owner;
    const value=h.element(owner,"14000006") catch |err| {
        if (err==error.MissingKey) {
            if (done.count==done.offsets.len) return error.InheritanceLimit;
            done.offsets[done.count]=owner;done.count+=1;return;
        }
        return err;
    };
    const list=try refs(value,0x14000006,false);
    for (0..list.count) |i| {
        var name: [38]u8=undefined;
        if (try optional(h,objects,list.name(i,&name))) |target| try inherited(h,objects,target,trail,depth+1,done);
    }
    if (done.count==done.offsets.len) return error.InheritanceLimit;
    done.offsets[done.count]=owner;done.count+=1;
}

pub fn validate(h: *const r.Hive,objects: u32,bootmgr: u32,loader: u32) !void {
    const all=try h.children(objects);
    for (all.offsets[0..all.count]) |owner| {
        var name_buffer: [256]u8=undefined;
        const owner_name=try h.nodeName(owner,&name_buffer);
        if (!guid(owner_name)) return error.InvalidObjectGuid;
        const owner_kind=try kind(h,owner);
        // Ignore outgoing references in firmware mirrors; validate references
        // from Windows objects so they cannot select a firmware application.
        if (owner_kind==0x10100001 or owner_kind==0x101fffff) continue;
        const elements=h.child(owner,"Elements") catch |err| {
            if (err==error.MissingKey) continue;
            return err;
        };
        const fields=try h.children(elements);
        for (fields.offsets[0..fields.count]) |field| {
            var buffer: [256]u8=undefined;
            const field_name=try h.nodeName(field,&buffer);
            if (field_name.len!=8) return error.InvalidElementNumber;
            const number=std.fmt.parseInt(u32,field_name,16) catch return error.InvalidElementNumber;
            const format=(number>>24)&15;
            if (format!=3 and format!=4) continue;
            // Only {fwbootmgr}'s reflected firmware order may repeat entries.
            // The per-install Windows display order remains exactly one OS;
            // all referenced objects are still checked below. No BCD rewrite.
            const firmware_order=number==0x24000001 and owner_kind==0x10100001 and
                std.ascii.eqlIgnoreCase(owner_name,firmware_manager);
            const list=try refs(try h.value(field,"Element"),number,firmware_order);
            for (0..list.count) |i| {
                var name: [38]u8=undefined;_=list.name(i,&name);
                const target=try optional(h,objects,&name);
                if (number==0x14000006) {
                    if (target) |t| {if (try kind(h,t)>>28!=2) return error.InvalidInheritanceTarget;}
                    else if (!std.mem.eql(u8,&name,resume_settings) and !std.mem.eql(u8,&name,loader_settings))
                        return error.MissingInheritanceTarget;
                } else if (number==0x14000008) {
                    if (owner_kind!=0x10200003 and owner_kind!=0x10200004) return error.InvalidRecoveryOwner;
                    if (std.mem.eql(u8,&name,zero) and list.count==1 and target==null) continue;
                    const t=target orelse return error.MissingRecoveryTarget;
                    if (t==loader or try kind(h,t)!=0x10200003) return error.InvalidRecoveryTarget;
                } else if (number==0x23000003) {
                    const t=target orelse return error.MissingApplicationTarget;
                    if (list.count!=1 or !((owner==bootmgr and t==loader) or
                        (owner_kind==0x10200003 and try kind(h,t)==0x10200004))) return error.InvalidDefaultOrResume;
                } else if (number==0x24000001) {
                    const t=target orelse return error.MissingDisplayTarget;
                    const target_kind=try kind(h,t);
                    if (!((owner==bootmgr and list.count==1 and t==loader) or
                        (owner_kind==0x10100001 and (target_kind==0x10100002 or target_kind==0x101fffff))))
                        return error.InvalidDisplayOrder;
                } else if (number==0x24000002) {
                    const t=target orelse return error.MissingBootSequenceTarget;
                    if (owner!=bootmgr or try kind(h,t)!=0x10200003) return error.InvalidBootSequence;
                } else return error.UnvalidatedReferenceElement;
            }
        }
    }
    var done: r.Children=.{};
    for (all.offsets[0..all.count]) |owner| {
        const owner_kind=try kind(h,owner);
        if (owner_kind==0x10100001 or owner_kind==0x101fffff) continue;
        var trail: [32]u32=undefined;try inherited(h,objects,owner,&trail,0,&done);
    }
}

test "strict reference list decoder" {
    var data: [158]u8=@splat(0);
    for (zero,0..) |c,i| data[i*2]=c;
    const one=try refs(.{.kind=7,.data=data[0..80]},0x14000008,false);
    try std.testing.expect(one.count==1);
    for (zero,0..) |c,i| data[78+i*2]=c;
    try std.testing.expectError(error.DuplicateReference,refs(.{.kind=7,.data=&data},0x14000006,false));
    try std.testing.expectError(error.DuplicateReference,refs(.{.kind=7,.data=&data},0x24000001,false));
    try std.testing.expectEqual(@as(usize,2),(try refs(.{.kind=7,.data=&data},0x24000001,true)).count);
    data[0]='x';
    try std.testing.expectError(error.InvalidReferenceGuid,refs(.{.kind=1,.data=data[0..78]},0x23000003,false));
}
