"""Consumer that calls User.save only."""

from app.models import User


def register_user(name: str) -> User:
    user = User(name)
    user.save()
    return user
