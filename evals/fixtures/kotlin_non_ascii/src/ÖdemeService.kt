package com.örnek.service

// Ödeme servisi — Türkçe karakterlerle dolu.
// Em-dash (—), smart quotes (“ok”), bullet (•).
class ÖdemeService {
    fun ödemeAl(tutar: Int): String {
        return "ok"
    }
}

class Günlükçü {
    fun info(mesaj: String): String {
        return "[info] $mesaj"
    }
}
