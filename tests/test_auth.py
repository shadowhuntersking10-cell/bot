"""AUTH tests: registration, login, logout, validation, telegram verification."""
from __future__ import annotations

import hashlib
import hmac
import json
import time


def test_register_creates_user(client, user_factory):
    user = user_factory.register(client)
    assert user["username"]
    assert user["role"] == "CUSTOMER"
    res = client.get("/api/auth/me")
    assert res.status_code == 200
    assert res.json()["user"]["id"] == user["id"]


def test_register_duplicate_rejected(client, user_factory):
    user_factory.register(client, username="dupeuser1", email="dupe1@example.com")
    res = client.post(
        "/api/auth/register",
        json={"username": "dupeuser1", "email": "other1@example.com", "password": "Sup3rSecret!"},
    )
    assert res.status_code == 409
    res = client.post(
        "/api/auth/register",
        json={"username": "otheruser1", "email": "dupe1@example.com", "password": "Sup3rSecret!"},
    )
    assert res.status_code == 409


def test_register_invalid_username(client):
    res = client.post(
        "/api/auth/register",
        json={"username": "a b!", "email": "x@example.com", "password": "Sup3rSecret!"},
    )
    assert res.status_code == 400


def test_register_weak_password(client):
    res = client.post(
        "/api/auth/register",
        json={"username": "weakpassuser", "email": "weak@example.com", "password": "short"},
    )
    assert res.status_code == 400
    res = client.post(
        "/api/auth/register",
        json={"username": "weakpassuser2", "email": "weak2@example.com", "password": "12345678"},
    )
    assert res.status_code == 400


def test_login_invalid_credentials(client, user_factory):
    user_factory.register(client, username="logintest1", email="login1@example.com")
    client.post("/api/auth/logout")
    res = client.post(
        "/api/auth/login", json={"username": "logintest1", "password": "wrong-password"}
    )
    assert res.status_code == 401


def test_login_logout_cycle(client, user_factory):
    user_factory.register(client, username="logintest2", email="login2@example.com")
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["user"] is None
    res = client.post(
        "/api/auth/login", json={"username": "logintest2", "password": "Sup3rSecret!"}
    )
    assert res.status_code == 200
    assert client.get("/api/auth/me").json()["user"]["username"] == "logintest2"


def test_protected_route_requires_auth(client):
    client.cookies.clear()
    res = client.get("/api/cart")
    assert res.status_code == 401
    res = client.get("/api/orders")
    assert res.status_code == 401


def test_telegram_init_data_verification():
    from app.security import verify_telegram_init_data

    token = "123456789:TESTTOKENabcdefghijklmnopqrstuvwx"
    user = {"id": 555, "first_name": "Test", "language_code": "ru"}
    pairs = {
        "auth_date": str(int(time.time())),
        "query_id": "AAE",
        "user": json.dumps(user),
    }
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hashlib.sha256(token.encode()).digest()
    sig = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    init_data = "&".join(f"{k}={v}" for k, v in pairs.items()) + f"&hash={sig}"

    parsed = verify_telegram_init_data(init_data, token)
    assert parsed is not None
    assert parsed["id"] == 555

    # tampered data must fail
    bad = init_data.replace("555", "556")
    assert verify_telegram_init_data(bad, token) is None
    # wrong token must fail
    assert verify_telegram_init_data(init_data, token + "x") is None
    # stale auth_date must fail
    pairs["auth_date"] = str(int(time.time()) - 48 * 3600)
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    sig = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    stale = "&".join(f"{k}={v}" for k, v in pairs.items()) + f"&hash={sig}"
    assert verify_telegram_init_data(stale, token) is None


def test_password_reset_flow(client, user_factory):
    user_factory.register(client, username="resetuser1", email="reset1@example.com")
    client.post("/api/auth/logout")
    # forgot always returns ok (no enumeration)
    res = client.post("/api/auth/password/forgot", json={"email": "reset1@example.com"})
    assert res.status_code == 200
    # grab the token from DB (email delivery is an ops integration)
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import User

    with session_scope() as session:
        user = session.execute(
            select(User).where(User.email == "reset1@example.com")
        ).scalar_one()
        token = user.password_reset_token
    assert token
    res = client.post(
        "/api/auth/password/reset", json={"token": token, "password": "NewSup3rPass!"}
    )
    assert res.status_code == 200
    res = client.post(
        "/api/auth/login", json={"username": "resetuser1", "password": "NewSup3rPass!"}
    )
    assert res.status_code == 200
    # token is single-use
    res = client.post(
        "/api/auth/password/reset", json={"token": token, "password": "Another123Pass!"}
    )
    assert res.status_code == 400
