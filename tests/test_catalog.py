"""PRODUCTS / catalog / search tests."""
from __future__ import annotations


def test_games_catalog_structure(client):
    res = client.get("/api/games?page_size=50")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 40  # desired catalog structure is present
    slugs = {g["slug"] for g in data["items"]}
    for expected in ("pubg-mobile", "free-fire", "roblox", "steam", "telegram-stars"):
        assert expected in slugs


def test_games_not_sellable_without_supplier(client):
    """Without a real supplier mapping nothing may be purchasable."""
    res = client.get("/api/games/pubg-mobile?lang=en")
    assert res.status_code == 200
    game = res.json()["game"]
    for product in game["products"]:
        for variant in product["variants"]:
            if variant["available"]:
                # sellable variants ALWAYS carry a real server-side price
                assert variant["price"] is not None
        if product["available"]:
            assert any(v["available"] and v["price"] is not None for v in product["variants"])
    # untouched game without supplier data must not be sellable at all
    res = client.get("/api/games/clash-of-clans")
    game = res.json()["game"]
    assert game["available"] is False
    assert all(p["available"] is False for p in game["products"])
    assert all(
        not v["available"] and v["price"] is None
        for p in game["products"]
        for v in p["variants"]
    )


def test_game_detail_404(client):
    assert client.get("/api/games/does-not-exist").status_code == 404


def test_search(client):
    res = client.get("/api/search?q=pubg")
    assert res.status_code == 200
    data = res.json()
    assert any(g["slug"] == "pubg-mobile" for g in data["games"])


def test_language_variants(client):
    for lang, expected in (("uz", "Sovg'a kartalari"), ("en", "Gift Cards"), ("ru", "Подарочные карты")):
        res = client.get(f"/api/games/gift-cards?lang={lang}")
        assert res.json()["game"]["name"] == expected


def test_supplier_status_honest(client):
    res = client.get("/api/supplier-status")
    data = res.json()
    # Payerpin not configured in tests -> must NOT claim automated fulfillment
    assert data["automated_fulfillment"] is False
    assert data["supplier_configured"] is False


def test_public_config(client):
    data = client.get("/api/config").json()
    assert data["brand"] == "VYRON"
    assert data["languages"] == ["uz", "en", "ru"]
    assert data["payment_configured"] is True  # sandbox explicitly configured
    assert data["supplier_configured"] is False
