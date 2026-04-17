package com.örnek.controller

import com.örnek.service.ÖdemeService
import com.örnek.service.Günlükçü

// Sipariş controller — ödeme servisine cross-file çağrı.
// Byte-vs-char slicing bug'ı olsa class adı mangle olurdu.
class SiparişController {
    private val ödeme = ÖdemeService()
    private val günlük = Günlükçü()

    fun işle(tutar: Int): String {
        val sonuç = ödeme.ödemeAl(tutar)
        günlük.info("işlem tamam")
        return sonuç
    }
}
