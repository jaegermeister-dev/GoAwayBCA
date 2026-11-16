// ConsoleControl is the legacy Intel/Apple protocol also implemented by
// OpenCore. SimpleTextOutput alone is invisible when the caller leaves it in
// graphics mode. We change only this application's console state and restore
// the caller's mode on return; no GOP resolution or OpenCore setting changes.
const std = @import("std");
const u = std.os.uefi;
const Text = u.protocol.SimpleTextOutput;

pub const Control = extern struct {
    get_mode: *const fn (*Control, *u32, ?*bool, ?*bool) callconv(u.cc) u.Status,
    set_mode: *const fn (*Control, u32) callconv(u.cc) u.Status,
    lock_stdin: *const fn (*Control, ?[*:0]const u16) callconv(u.cc) u.Status,
    pub const guid align(8) = u.Guid{
        .time_low = 0xf42f7782, .time_mid = 0x012e, .time_high_and_version = 0x4c12,
        .clock_seq_high_and_reserved = 0x99, .clock_seq_low = 0x56,
        .node = .{0x49,0xf9,0x43,0x04,0xf7,0x21},
    };
};

pub const Session = struct {
    text: ?*Text,
    control: ?*Control,
    old_screen: ?u32 = null,
    old_attribute: ?usize = null,
    old_cursor: bool = false,
    old_column: i32 = 0,
    old_row: i32 = 0,

    pub fn capture() Session {
        var control: ?*Control = null;
        if (u.system_table.boot_services) |bs| {
            if (u.system_table.console_out_handle) |h|
                control = bs.handleProtocol(Control,h) catch null;
            if (control == null) control = bs.locateProtocol(Control,null) catch null;
        }
        return captureFrom(u.system_table.con_out,control);
    }

    pub fn captureFrom(text: ?*Text, control: ?*Control) Session {
        var result = Session{.text=text,.control=control};
        if (control) |c| {
            var mode: u32 = 0;
            if (c.get_mode(c,&mode,null,null) == .success and mode < 2)
                result.old_screen = mode;
        }
        if (text) |t| {
            if (t.mode.attribute >= 0 and t.mode.attribute <= 0x7f)
                result.old_attribute = @intCast(t.mode.attribute);
            result.old_cursor = t.mode.cursor_visible;
            result.old_column = t.mode.cursor_column;
            result.old_row = t.mode.cursor_row;
        }
        return result;
    }

    pub fn show(self: *const Session) void {
        if (self.control) |c| _ = c.set_mode(c,0);
        if (self.text) |t| {
            // Do not switch GOP resolution or arbitrarily force 80x25.
            if (t.mode.mode >= t.mode.max_mode) t.setMode(0) catch {};
            t.setAttribute(.{.foreground=.white,.background=.black}) catch {};
            t.enableCursor(false) catch {};
        }
    }

    pub fn restore(self: *const Session) void {
        if (self.text) |t| {
            if (self.old_attribute) |a| _ = t._set_attribute(t,a);
            if (self.old_column >= 0 and self.old_row >= 0)
                t.setCursorPosition(@intCast(self.old_column),@intCast(self.old_row)) catch {};
            t.enableCursor(self.old_cursor) catch {};
        }
        if (self.control) |c| if (self.old_screen) |mode| { _ = c.set_mode(c,mode); };
    }
};

var mock_mode: u32 = 1;
var mock_sets: usize = 0;
fn get(c: *Control, mode: *u32, g: ?*bool, l: ?*bool) callconv(u.cc) u.Status {
    _=c; _=g; _=l; mode.*=mock_mode; return .success;
}
fn set(c: *Control, mode: u32) callconv(u.cc) u.Status {
    _=c; mock_sets+=1; mock_mode=mode; return .success;
}
fn lock(c: *Control, password: ?[*:0]const u16) callconv(u.cc) u.Status {
    _=c; _=password; return .unsupported;
}
test "graphics caller is restored after repeated redraw and child return" {
    mock_mode=1; mock_sets=0;
    var protocol = Control{.get_mode=get,.set_mode=set,.lock_stdin=lock};
    const session = Session.captureFrom(null,&protocol);
    session.show(); try std.testing.expectEqual(@as(u32,0),mock_mode);
    mock_mode=1; // a returning child switched back to graphics
    session.show(); try std.testing.expectEqual(@as(u32,0),mock_mode);
    session.restore(); try std.testing.expectEqual(@as(u32,1),mock_mode);
    try std.testing.expectEqual(@as(usize,3),mock_sets);
}
test "plain text firmware remains supported and preserves text mode" {
    mock_mode=0;
    var protocol = Control{.get_mode=get,.set_mode=set,.lock_stdin=lock};
    const session = Session.captureFrom(null,&protocol);
    session.show(); session.restore();
    try std.testing.expectEqual(@as(u32,0),mock_mode);
    const plain = Session.captureFrom(null,null);
    plain.show(); plain.restore();
}
