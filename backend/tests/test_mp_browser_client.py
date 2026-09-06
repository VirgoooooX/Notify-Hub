"""Tests for MPBrowserApiClient."""

from __future__ import annotations

import httpx
import pytest
import respx
from app.mp_browser.api_client import MPBrowserApiClient
from app.mp_browser.config import MPBrowserSettings
from pydantic import SecretStr


@pytest.fixture
def settings() -> MPBrowserSettings:
    return MPBrowserSettings(
        api_base_url="http://test-server/api/v1/admin/mp-browser",
        api_key=SecretStr("nfy_test_secret_client_key_12345"),
    )


@pytest.mark.asyncio
@respx.mock
async def test_mp_browser_client_claim(settings: MPBrowserSettings) -> None:
    client = MPBrowserApiClient(settings)
    claim_route = respx.post("http://test-server/api/v1/admin/mp-browser/claim")

    # 204 No Content
    claim_route.return_value = httpx.Response(204)
    res_none = await client.claim()
    assert res_none is None

    # 200 With article
    claim_route.return_value = httpx.Response(
        200,
        json={
            "data": {
                "article": {"id": "mpa_1", "title": "Test Title"},
                "attempt": 1,
                "resume": "new",
                "draft_url": None,
            }
        },
    )
    res_art = await client.claim()
    assert res_art is not None
    assert res_art["article"]["id"] == "mpa_1"
    assert claim_route.calls.last.request.headers["X-API-Key"] == "nfy_test_secret_client_key_12345"

    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_mp_browser_client_checkpoint_and_complete(settings: MPBrowserSettings) -> None:
    client = MPBrowserApiClient(settings)
    cp_route = respx.post("http://test-server/api/v1/admin/mp-browser/articles/mpa_1/checkpoint")
    cp_route.return_value = httpx.Response(
        200, json={"data": {"id": "mpa_1", "status": "publishing"}}
    )

    comp_route = respx.post("http://test-server/api/v1/admin/mp-browser/articles/mpa_1/complete")
    comp_route.return_value = httpx.Response(
        200, json={"data": {"id": "mpa_1", "status": "published"}}
    )

    cp_res = await client.checkpoint("mpa_1", phase="draft_saved", draft_url="http://draft.url")
    assert cp_res["status"] == "publishing"

    comp_res = await client.complete("mpa_1", published_url="https://mp.weixin.qq.com/s/pub")
    assert comp_res["status"] == "published"
    assert (
        comp_route.calls.last.request.content
        == b'{"status":"published","provider_draft_id":null,"provider_publish_id":null,"published_url":"https://mp.weixin.qq.com/s/pub"}'
    )

    draft_comp_route = respx.post(
        "http://test-server/api/v1/admin/mp-browser/articles/mpa_2/complete"
    )
    draft_comp_route.return_value = httpx.Response(
        200, json={"data": {"id": "mpa_2", "status": "draft"}}
    )
    draft_res = await client.complete(
        "mpa_2", status="draft", published_url="https://mp.weixin.qq.com/cgi-bin/appmsg?id=1"
    )
    assert draft_res["status"] == "draft"

    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_mp_browser_client_fail_and_session(settings: MPBrowserSettings) -> None:
    client = MPBrowserApiClient(settings)
    fail_route = respx.post("http://test-server/api/v1/admin/mp-browser/articles/mpa_1/fail")
    fail_route.return_value = httpx.Response(
        200, json={"data": {"id": "mpa_1", "status": "failed"}}
    )

    sess_route = respx.post("http://test-server/api/v1/admin/mp-browser/session")
    sess_route.return_value = httpx.Response(200, json={"data": {"state": "ready"}})

    rel_route = respx.post("http://test-server/api/v1/admin/mp-browser/articles/mpa_1/release-auth")
    rel_route.return_value = httpx.Response(200, json={"data": {"id": "mpa_1", "status": "ready"}})

    fail_res = await client.fail(
        "mpa_1", retryable=True, error_code="EDITOR_TIMEOUT", error_message="msg"
    )
    assert fail_res["status"] == "failed"

    sess_res = await client.update_session(state="ready")
    assert sess_res["state"] == "ready"

    rel_res = await client.release_for_auth("mpa_1")
    assert rel_res["status"] == "ready"

    await client.close()
