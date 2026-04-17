"""Two classes with an identically named `save` method to exercise owner-aware lookup."""


class User:
    def __init__(self, name: str) -> None:
        self.name = name

    def save(self) -> None:
        print(f"saving user {self.name}")


class Order:
    def __init__(self, order_id: int) -> None:
        self.order_id = order_id

    def save(self) -> None:
        print(f"saving order {self.order_id}")
