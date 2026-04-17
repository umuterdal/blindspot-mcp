# Sipariş controller — ödeme servisine cross-file çağrı atar.
# Import yolunda non-ASCII modül adı var: ``ödeme``.
from src.ödeme import ÖdemeService, Günlükçü


class SiparişController:
    """Sipariş yönetim kontrolcüsü."""

    def __init__(self):
        self.ödeme = ÖdemeService()
        self.günlük = Günlükçü()

    def işle(self, tutar: int) -> str:
        sonuç = self.ödeme.ödemeAl(tutar)
        self.günlük.info("işlem tamam")
        return sonuç
