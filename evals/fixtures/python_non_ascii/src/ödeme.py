# Ödeme servisi — Türkçe karakterlerle dolu.
# Em-dash (—), smart quotes (“ok”), bullet (•) comment stress:
# silent str-slice / byte-offset regression olursa bu dosyadaki
# sembol adları ve satır numaraları kaydırılarak mangle olur.


class ÖdemeService:
    """Ödeme işlemlerini yöneten servis sınıfı."""

    def ödemeAl(self, tutar: int) -> str:
        """Kredi kartı çekim işlemi — • non-ASCII comment."""
        return "ok"


class Günlükçü:
    """Basit günlük servisi."""

    def info(self, mesaj: str) -> str:
        return f"[info] {mesaj}"


def process_sipariş(tutar: int) -> str:
    """Sipariş işleme entry point'i."""
    return "done"
