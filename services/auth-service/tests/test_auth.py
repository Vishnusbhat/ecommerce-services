import httpx

from conftest import BASE_URLS

AUTH = BASE_URLS["auth"]


def _register_and_login(email: str, password: str = "password123") -> dict:
    r = httpx.post(f"{AUTH}/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    r = httpx.post(f"{AUTH}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_register_login_refresh_and_rotation(unique_email):
    tokens = _register_and_login(unique_email)
    old_refresh = tokens["refreshToken"]

    r = httpx.post(f"{AUTH}/auth/refresh", json={"refreshToken": old_refresh})
    assert r.status_code == 200, r.text
    new_tokens = r.json()
    assert new_tokens["refreshToken"] != old_refresh

    # Reusing the pre-rotation refresh token must now fail.
    r = httpx.post(f"{AUTH}/auth/refresh", json={"refreshToken": old_refresh})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_TOKEN"


def test_logout_then_reuse_of_refresh_token_fails(unique_email):
    tokens = _register_and_login(unique_email)

    r = httpx.post(
        f"{AUTH}/auth/logout",
        headers={"Authorization": f"Bearer {tokens['accessToken']}"},
        json={"refreshToken": tokens["refreshToken"]},
    )
    assert r.status_code == 204

    r = httpx.post(f"{AUTH}/auth/refresh", json={"refreshToken": tokens["refreshToken"]})
    assert r.status_code == 401


def test_duplicate_registration_returns_409(unique_email):
    r = httpx.post(f"{AUTH}/auth/register", json={"email": unique_email, "password": "password123"})
    assert r.status_code == 201

    r = httpx.post(f"{AUTH}/auth/register", json={"email": unique_email, "password": "password123"})
    assert r.status_code == 409
