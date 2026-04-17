// Ödeme servisi — Türkçe karakterlerle dolu.
// Em-dash (—), smart quotes (“ok”), bullet (•) comment stress:
// tree-sitter byte offsets str-slice'a uygulanırsa buradaki
// identifier'lar ve method adları kaydırılarak mangle olur.

class ÖdemeService {
    ödemeAl(tutar) {
        // • Non-ASCII gövde içinde sembol tespiti
        return "ok";
    }
}

class Günlükçü {
    info(mesaj) {
        return "[info] " + mesaj;
    }
}

module.exports = { ÖdemeService, Günlükçü };
