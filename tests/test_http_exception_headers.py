"""Safe HTTPException responses preserve protocol headers without private details."""

from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException

from pulse.app import create_app


def make_app(tmp_path):
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>Pulse</html>")
    return create_app(
        tmp_path / "data/pulse.sqlite3", hosts=["testserver"], static_dir=static_dir
    )


def test_method_error_preserves_allow_and_uses_no_store(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        csrf = client.get("/api/session").json()["csrf_token"]
        response = client.post(
            "/healthz", json={}, headers={"X-Pulse-CSRF": csrf}
        )

    assert response.status_code == 405
    assert "GET" in response.headers["allow"]
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "Recurso no disponible."}


def test_authentication_challenge_is_preserved_without_reflecting_detail(tmp_path):
    app = make_app(tmp_path)

    async def private_error():
        raise HTTPException(
            status_code=401,
            detail="PRIVATE_SENTINEL",
            headers={"WWW-Authenticate": 'Bearer realm="Pulse"'},
        )

    app.add_api_route("/api/test/auth-error", private_error)
    shell_index = next(
        index for index, route in enumerate(app.router.routes)
        if getattr(route, "name", None) == "shell"
    )
    app.router.routes.insert(shell_index, app.router.routes.pop())
    with TestClient(app) as client:
        client.get("/api/session")
        response = client.get("/api/test/auth-error")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="Pulse"'
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE_SENTINEL" not in response.text
    assert response.json() == {"detail": "Recurso no disponible."}
