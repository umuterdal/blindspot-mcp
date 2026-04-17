// Ödeme servisi — Türkçe karakterlerle dolu.
// Em-dash (—), smart quotes (“ok”), bullet (•) comment stress:
// tree-sitter byte offsets str-slice'a uygulanırsa
// buradaki struct ve function adları kaydırılarak mangle olur.

const std = @import("std");

pub const OdemeService = struct {
    // • non-ASCII gövde içinde sembol tespiti
    pub fn pay(self: *OdemeService, amount: u32) []const u8 {
        _ = self;
        _ = amount;
        return "ok";
    }
};

pub const Logger = struct {
    pub fn info(self: *Logger, msg: []const u8) void {
        _ = self;
        _ = msg;
    }
};

// Sipariş controller — byte-vs-char slicing bug'ı olsa
// bu fonksiyon adı ve satır numarası mangle olurdu.
pub fn process_order(amount: u32) []const u8 {
    var svc: OdemeService = undefined;
    return svc.pay(amount);
}
