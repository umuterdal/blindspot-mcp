// Sipariş controller — ödeme servisine cross-file çağrı atar.
// Byte-vs-char slicing bug'ı olsa: class adı, method adı ve
// "new ÖdemeService()" çağrısı mangle olurdu.
const { ÖdemeService, Günlükçü } = require('./services');

class SiparişController {
    constructor() {
        this.ödeme = new ÖdemeService();
        this.günlük = new Günlükçü();
    }

    işle(tutar) {
        const sonuç = this.ödeme.ödemeAl(tutar);
        this.günlük.info("işlem tamam");
        return sonuç;
    }
}

module.exports = { SiparişController };
