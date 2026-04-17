package com.örnek.controller;

import com.örnek.service.ÖdemeService;

// Siparişi işler — ödeme servisini çağırır.
// Byte-vs-char slicing bug'ı olsa: class adı, method adı ve
// "new ÖdemeService()" çağrısı mangle olurdu.
public class SiparişController {
    private final ÖdemeService ödeme;

    public SiparişController() {
        this.ödeme = new ÖdemeService();
    }

    // Gerçek iş metodu: import edilen servise cross-file çağrı atar.
    public String işle(int tutar) {
        String sonuç = ödeme.ödemeAl(tutar);
        return sonuç;
    }
}
