from app.user_service import register_user


def test_register_user_saves_to_store():
    user = register_user("alice")
    assert user.name == "alice"
