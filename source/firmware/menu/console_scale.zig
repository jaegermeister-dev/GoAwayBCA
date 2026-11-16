// Enlarge the firmware-rendered glyphs using GOP BLT. This preserves its
// Unicode font and the existing text menu, rather than replacing it with a
// bundled Latin-only font. No GOP SetMode, NVRAM, disk or OpenCore changes.
// Rendering failure leaves/restores the original unscaled console image.
const std = @import("std");
const u = std.os.uefi;
const Gop = u.protocol.GraphicsOutput;
const Pixel = Gop.BltPixel;
const black = Pixel{ .red = 0, .green = 0, .blue = 0, .reserved = 0 };
const Saved = struct { gop: *Gop, mode: u32, width: usize, height: usize, pixels: []Pixel };
var saved: ?Saved = null;
fn trace(comptime format: []const u8, args: anytype) void {
    if (!@hasDecl(@import("root"), "scaling_trace")) return;
    var bytes: [256]u8 = undefined;
    const line = std.fmt.bufPrint(&bytes, format, args) catch return;
    for (line) |c| asm volatile ("outb %[byte], %[port]" : : [byte] "{al}" (c), [port] "{dx}" (@as(u16,0xe9)));
}
pub const Bounds = struct { x: usize, y: usize, width: usize, height: usize, glyph_height: usize };
pub const Layout = struct { scale: usize, x: usize, y: usize, width: usize, height: usize };

pub fn dimensions(width: usize, height: usize) bool {
    return width >= 320 and height >= 200 and width <= 7680 and height <= 4320 and width * height <= 16 * 1024 * 1024;
}

fn ink(p: Pixel) bool {
    return p.red != 0 or p.green != 0 or p.blue != 0;
}

pub fn bounds(pixels: []const Pixel, width: usize, height: usize) ?Bounds {
    if (!dimensions(width, height) or pixels.len != width * height) return null;
    var left = width;
    var right: usize = 0;
    var top = height;
    var bottom: usize = 0;
    var glyph_height: usize = 0;
    var first_line = true;
    for (0..height) |y| {
        var row_ink = false;
        for (0..width) |x| if (ink(pixels[y * width + x])) {
            left = @min(left, x);
            right = @max(right, x);
            top = @min(top, y);
            bottom = @max(bottom, y);
            row_ink = true;
        };
        if (first_line and row_ink) {
            glyph_height += 1;
        } else if (first_line and glyph_height > 0) {
            first_line = false;
        }
    }
    if (top == height or glyph_height < 4) return null;
    return .{ .x = left, .y = top, .width = right - left + 1, .height = bottom - top + 1, .glyph_height = glyph_height };
}

pub fn layout(width: usize, height: usize, content: Bounds) ?Layout {
    if (!dimensions(width, height) or content.width == 0 or content.height == 0 or content.glyph_height < 4 or
        content.x >= width or content.y >= height or content.width > width - content.x or content.height > height - content.y) return null;
    // Use active output dimensions, not a guessed Mac model: the boot display
    // may be an external screen. Cap scaling and reduce it until ALL text fits.
    const desired = @max(@as(usize, 18), @min(@as(usize, 48), height / 40));
    var scale = @min(@as(usize, 6), (desired + content.glyph_height - 1) / content.glyph_height);
    const margin: usize = @min(@as(usize, 48), @min(width, height) / 20);
    while (scale > 1 and (content.width * scale > width - 2 * margin or content.height * scale > height - 2 * margin)) scale -= 1;
    if (scale <= 1) return null;
    return .{ .scale = scale, .x = margin, .y = margin, .width = content.width * scale, .height = content.height * scale };
}

pub fn enlarge(source: []const Pixel, source_width: usize, content: Bounds, result: []Pixel, target: Layout) bool {
    if (source_width == 0 or source.len % source_width != 0 or target.scale < 2 or target.scale > 6 or
        content.width == 0 or content.height == 0 or content.x >= source_width or
        content.width > source_width - content.x or content.y >= source.len / source_width or
        content.height > source.len / source_width - content.y or target.width != content.width * target.scale or
        target.height != content.height * target.scale or result.len != target.width * target.height) return false;
    for (0..target.height) |y| for (0..target.width) |x| {
        result[y * target.width + x] = source[(content.y + y / target.scale) * source_width + content.x + x / target.scale];
    };
    return true;
}

fn consoleGop() ?*Gop {
    const bs = u.system_table.boot_services orelse return null;
    if (u.system_table.console_out_handle) |h| {
        if (bs.handleProtocol(Gop, h) catch null) |gop| return gop;
    }
    const handles = (bs.locateHandleBuffer(.{ .by_protocol = &Gop.guid }) catch return null) orelse return null;
    defer bs.freePool(@ptrCast(@alignCast(handles.ptr))) catch {};
    // Do not guess which of several output protocols belongs to the console.
    if (handles.len != 1) return null;
    return bs.handleProtocol(Gop, handles[0]) catch null;
}

pub fn present() void {
    const gop = consoleGop() orelse { trace("SCALE no GOP\n", .{}); return; };
    presentOn(gop);
}

// Firmware may cache identical text between redraws. Return the underlying
// unscaled pixels before any console call so its cache and visible image agree.
pub fn restore() void {
    const prior = saved orelse return;
    saved = null;
    trace("SCALE restoring original\n", .{});
    defer u.pool_allocator.free(prior.pixels);
    const gop = prior.gop;
    if (gop.mode.mode == prior.mode and gop.mode.info.horizontal_resolution == prior.width and
        gop.mode.info.vertical_resolution == prior.height)
        gop.blt(prior.pixels.ptr, .blt_buffer_to_video, 0, 0, 0, 0, prior.width, prior.height, prior.width * @sizeOf(Pixel)) catch {};
}

fn presentOn(gop: *Gop) void {
    if (saved != null) { trace("SCALE still saved\n", .{}); return; }
    const width: usize = gop.mode.info.horizontal_resolution;
    const height: usize = gop.mode.info.vertical_resolution;
    if (!dimensions(width, height)) return;
    const mode = gop.mode.mode;
    const original = u.pool_allocator.alloc(Pixel, width * height) catch return;
    var retained = false;
    defer if (!retained) u.pool_allocator.free(original);
    gop.blt(original.ptr, .blt_video_to_blt_buffer, 0, 0, 0, 0, width, height, width * @sizeOf(Pixel)) catch return;
    const content = bounds(original, width, height) orelse { trace("SCALE no bounds\n", .{}); return; };
    trace("SCALE {d}x{d} content={any}\n", .{width,height,content});
    const target = layout(width, height, content) orelse { trace("SCALE no layout\n", .{}); return; };
    trace("SCALE layout={any}\n", .{target});
    const scaled = u.pool_allocator.alloc(Pixel, target.width * target.height) catch return;
    defer u.pool_allocator.free(scaled);
    if (!enlarge(original, width, content, scaled, target)) return;
    if (gop.mode.mode != mode or gop.mode.info.horizontal_resolution != width or gop.mode.info.vertical_resolution != height) return;
    var fill = [_]Pixel{black};
    gop.blt(&fill, .blt_video_fill, 0, 0, 0, 0, width, height, 0) catch return;
    gop.blt(scaled.ptr, .blt_buffer_to_video, 0, 0, target.x, target.y, target.width, target.height, target.width * @sizeOf(Pixel)) catch {
        gop.blt(original.ptr, .blt_buffer_to_video, 0, 0, 0, 0, width, height, width * @sizeOf(Pixel)) catch {};
        return;
    };
    saved = .{ .gop = gop, .mode = mode, .width = width, .height = height, .pixels = original };
    retained = true;
    trace("SCALE presented\n", .{});
}

test "high resolution gets larger readable text without clipping; low resolution fits" {
    const content = Bounds{ .x = 0, .y = 3, .width = 570, .height = 235, .glyph_height = 12 };
    const retina = layout(2880, 1800, content).?;
    try std.testing.expectEqual(@as(usize, 4), retina.scale);
    try std.testing.expect(retina.width + retina.x < 2880 and retina.height + retina.y < 1800);
    const hd = layout(1920, 1080, content).?;
    try std.testing.expectEqual(@as(usize, 3), hd.scale);
    try std.testing.expect(layout(800, 600, content) == null);
    var many = content;
    many.height = 900;
    const fit = layout(2880, 1800, many);
    try std.testing.expect(fit == null); // preserve all lines instead of clipping
    var already_large = content;
    already_large.glyph_height = 48;
    try std.testing.expect(layout(2880, 1800, already_large) == null);
}

test "bounds ignore reserved/alpha channel and use actual first glyph rows" {
    var pixels: [320 * 200]Pixel = @splat(black);
    for (pixels[0..]) |*px| px.reserved = 255;
    try std.testing.expect(bounds(&pixels, 320, 200) == null);
    for (3..15) |y| for (4..25) |x| {
        pixels[y * 320 + x].red = 255;
    };
    pixels[30 * 320 + 45].green = 255;
    const found = bounds(&pixels, 320, 200).?;
    try std.testing.expectEqual(@as(usize, 12), found.glyph_height);
    try std.testing.expectEqual(@as(usize, 42), found.width);
    try std.testing.expectEqual(@as(usize, 28), found.height);
    try std.testing.expect(bounds(pixels[0..10], 320, 200) == null);
}

test "nearest-neighbour scaling preserves glyph pixels exactly and rejects invalid buffers" {
    const white = Pixel{ .red = 255, .green = 255, .blue = 255, .reserved = 0 };
    const source = [_]Pixel{ white, black, black, white };
    const content = Bounds{ .x = 0, .y = 0, .width = 2, .height = 2, .glyph_height = 4 };
    const target = Layout{ .scale = 2, .x = 0, .y = 0, .width = 4, .height = 4 };
    var result: [16]Pixel = undefined;
    try std.testing.expect(enlarge(&source, 2, content, &result, target));
    try std.testing.expectEqual(white, result[0]);
    try std.testing.expectEqual(white, result[5]);
    try std.testing.expectEqual(black, result[6]);
    try std.testing.expectEqual(white, result[15]);
    try std.testing.expect(!enlarge(&source, 0, content, &result, target));
    try std.testing.expect(!enlarge(&source, 2, content, result[0..15], target));
}
