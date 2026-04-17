"""Consumer that calls Order.save only."""

from app.models import Order


def place_order(order_id: int) -> Order:
    order = Order(order_id)
    order.save()
    return order
