from app.api import quote_total


def test_quote_total_for_vip():
    assert quote_total(100, "vip") == 100
