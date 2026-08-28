from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adforge.auth import hash_password
from adforge.models import Campaign, Product, TruthReadiness
from adforge.web import WebContext, create_app

PASSWORD = "fixture-password-123"  # noqa: S105
SECRET = "fixture-secret-key-that-is-long-enough-123"  # noqa: S105


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    imports = tmp_path / "imports"
    imports.mkdir()
    app = create_app(
        runtime_root=tmp_path / "runtime",
        schema_root=Path("schemas"),
        secret_key=SECRET,
        password_hash=hash_password(PASSWORD, salt=b"0123456789abcdef"),
        import_root=imports,
        secure_cookie=False,
    )
    context: WebContext = app.state.context
    context.services.products.save(
        Product(
            id="product-1",
            name="Fixture Product",
            slug="fixture-product",
            truth_readiness=TruthReadiness.READY,
        )
    )
    return TestClient(app)


def login(client: TestClient) -> str:
    response = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/")
    assert page.status_code == 200
    match = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


def test_authentication_required_and_invalid_login_rejected(client: TestClient) -> None:
    assert client.get("/", follow_redirects=False).status_code == 303
    bad = client.post("/login", data={"password": "wrong-password"})
    assert bad.status_code == 200
    assert "Invalid credentials" in bad.text
    login(client)
    assert "Make the ad" in client.get("/").text


def test_required_routes_render_without_secret_leakage(client: TestClient) -> None:
    login(client)
    routes = (
        "/",
        "/products",
        "/products/product-1",
        "/campaigns/new",
        "/campaigns",
        "/outputs",
        "/settings",
    )
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, route
        assert SECRET not in response.text
        assert "fixture-password" not in response.text


def test_campaign_creation_path_validation_and_state_visibility(
    client: TestClient, tmp_path: Path
) -> None:
    csrf = login(client)
    rejected = client.post(
        "/campaigns",
        data={
            "csrf": csrf,
            "product_id": "product-1",
            "name": "Launch",
            "brief": "Use approved proof",
            "apk_path": "/etc/passwd",
        },
    )
    assert rejected.status_code == 422
    import_root = client.app.state.context.import_root
    apk = import_root / "fixture.apk"
    apk.write_bytes(b"fixture")
    created = client.post(
        "/campaigns",
        data={
            "csrf": csrf,
            "product_id": "product-1",
            "name": "Launch",
            "brief": "Use approved proof",
            "apk_path": str(apk),
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    detail = client.get(created.headers["location"])
    assert "CREATED" in detail.text
    assert "Use approved proof" in detail.text


def test_one_active_campaign_is_enforced_in_ux_and_action(client: TestClient) -> None:
    csrf = login(client)
    context: WebContext = client.app.state.context
    first = context.services.campaigns.save(
        Campaign(product_id="product-1", name="First", brief="First brief", active=True)
    )
    page = client.get("/campaigns/new")
    assert "holds the active production lease" in page.text
    assert "disabled" in page.text
    response = client.post(
        "/campaigns",
        data={
            "csrf": csrf,
            "product_id": "product-1",
            "name": "Second",
            "brief": "Second brief",
            "apk_path": "",
        },
    )
    assert response.status_code == 409
    assert context.services.campaigns.get(first.id) is not None


def test_csrf_is_required_for_state_changes(client: TestClient) -> None:
    login(client)
    response = client.post(
        "/campaigns",
        data={
            "csrf": "tampered",
            "product_id": "product-1",
            "name": "Campaign",
            "brief": "Brief",
            "apk_path": "",
        },
    )
    assert response.status_code == 403
