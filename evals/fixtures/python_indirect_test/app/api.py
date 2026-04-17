from app.orders import OrderService


def quote_total(subtotal: float, tier: str) -> float:
    service = OrderService()
    return service.total_for(subtotal, tier)
