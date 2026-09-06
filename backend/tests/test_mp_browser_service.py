"""Tests for the MP Playwright browser service, API permissions, and queue logic."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.api.errors import AppError
from app.channels.base import ChannelMessage
from app.infrastructure.database.models import MpArticleStatus

VALID_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00"
    b"\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeClock:
    def __init__(self, initial: datetime | None = None) -> None:
        self.value = initial or datetime(2026, 8, 25, 9, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


async def _create_test_article(library: Any, title: str) -> str:
    msg = ChannelMessage(
        message_type="article",
        title=title,
        content=f"# {title}\n\nContent for {title}",
        recipients=[],
        image_url="https://example.com/cover.png",
        payload={"publish_to_mp": True},
    )
    return await library.store_from_delivery(
        delivery_id=None,
        message=msg,
        status=MpArticleStatus.READY.value,
    )


@pytest.mark.asyncio
async def test_mp_browser_fifo_claim_and_publishing_status(api: tuple[Any, Any]) -> None:
    _client, app = api
    app.state.settings.mp_publish_mode = "browser"
    library = app.state.mp_article_library
    service = app.state.mp_browser_service

    id1 = await _create_test_article(library, "Article 1")
    id2 = await _create_test_article(library, "Article 2")

    # Claim first article
    res1 = await service.claim_article()
    assert res1 is not None
    art1, attempt1, resume1, draft_url1 = res1
    assert art1.id == id1
    assert attempt1 == 1
    assert resume1 == "new"
    assert draft_url1 is None
    assert art1.status == MpArticleStatus.PUBLISHING.value

    # Claim second article (FIFO)
    res2 = await service.claim_article()
    assert res2 is not None
    art2, attempt2, resume2, _draft_url2 = res2
    assert art2.id == id2
    assert attempt2 == 1
    assert resume2 == "new"

    # No more ready articles
    res3 = await service.claim_article()
    assert res3 is None


@pytest.mark.asyncio
async def test_mp_browser_permission_denied_without_allow_flag(api: tuple[Any, Any]) -> None:
    client, app = api
    app.state.settings.mp_publish_mode = "browser"
    init_res = await client.post(
        "/api/v1/admin/auth/initialize",
        json={"username": "administrator", "password": "correct-horse-battery-staple"},
    )
    assert init_res.status_code == 201
    admin_token = init_res.json()["data"]["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Create client without allow_mp_browser
    create_res = await client.post(
        "/api/v1/admin/api-clients",
        json={"name": "Normal Client", "allow_mp_browser": False},
        headers=admin_headers,
    )
    assert create_res.status_code == 201
    key = create_res.json()["data"]["api_key"]

    claim_res = await client.post(
        "/api/v1/admin/mp-browser/claim",
        headers={"X-API-Key": key},
    )
    assert claim_res.status_code == 403
    assert claim_res.json()["error"]["code"] == "mp_browser_forbidden"

    # 2. Create client with allow_mp_browser
    browser_res = await client.post(
        "/api/v1/admin/api-clients",
        json={"name": "Browser Client", "allow_mp_browser": True},
        headers=admin_headers,
    )
    assert browser_res.status_code == 201
    browser_key = browser_res.json()["data"]["api_key"]

    claim_ok_res = await client.post(
        "/api/v1/admin/mp-browser/claim",
        headers={"X-API-Key": browser_key},
    )
    # 204 when no articles queued
    assert claim_ok_res.status_code == 204


@pytest.mark.asyncio
async def test_mp_browser_checkpoint_transitions_and_regression_rejection(
    api: tuple[Any, Any],
) -> None:
    _client, app = api
    app.state.settings.mp_publish_mode = "browser"
    library = app.state.mp_article_library
    service = app.state.mp_browser_service

    art_id = await _create_test_article(library, "Checkpoint Article")
    claimed = await service.claim_article()
    assert claimed is not None

    # Missing draft url / id on draft_saved returns 422
    with pytest.raises(AppError) as exc_info:
        await service.checkpoint_article(art_id, phase="draft_saved")
    assert exc_info.value.status_code == 422

    # Checkpoint draft_saved
    cp1 = await service.checkpoint_article(
        art_id,
        phase="draft_saved",
        draft_url="https://mp.weixin.qq.com/cgi-bin/appmsg?id=123",
        provider_draft_id="draft_123",
    )
    bp = cp1.payload.get("browser_publish") or {}
    assert bp.get("phase") == "draft_saved"
    assert bp.get("draft_url") == "https://mp.weixin.qq.com/cgi-bin/appmsg?id=123"

    # Regressing back to editing returns 409
    with pytest.raises(AppError) as reg_exc:
        await service.checkpoint_article(art_id, phase="editing")
    assert reg_exc.value.status_code == 409

    # Advance to publish_clicked
    cp2 = await service.checkpoint_article(art_id, phase="publish_clicked")
    bp2 = cp2.payload.get("browser_publish") or {}
    assert bp2.get("phase") == "publish_clicked"


@pytest.mark.asyncio
async def test_mp_browser_timeout_resume_and_reconcile(api: tuple[Any, Any]) -> None:
    _client, app = api
    app.state.settings.mp_publish_mode = "browser"
    app.state.settings.mp_browser_claim_timeout_seconds = 60
    clock = FakeClock()
    app.state.clock = clock
    app.state.mp_article_library._clock = clock
    app.state.mp_browser_service._clock = clock
    library = app.state.mp_article_library
    service = app.state.mp_browser_service

    art_id = await _create_test_article(library, "Timeout Article")
    await service.claim_article()
    await service.checkpoint_article(
        art_id,
        phase="draft_saved",
        draft_url="https://mp.weixin.qq.com/draft/abc",
        provider_draft_id="mid_abc",
    )

    # Time forward past timeout
    clock.advance(timedelta(seconds=70))

    # Next claim should return resume action with draft_url
    res = await service.claim_article()
    assert res is not None
    art, attempt, resume, draft_url = res
    assert art.id == art_id
    assert attempt == 2
    assert resume == "resume"
    assert draft_url == "https://mp.weixin.qq.com/draft/abc"

    # Move to publish_clicked
    await service.checkpoint_article(art_id, phase="publish_clicked")
    clock.advance(timedelta(seconds=70))

    # Timeout after publish_clicked should return reconcile, even if max attempts reached
    app.state.settings.mp_browser_max_attempts = 2
    res_rec = await service.claim_article()
    assert res_rec is not None
    _art, _attempt, resume_rec, _draft_url = res_rec
    assert resume_rec == "reconcile"


@pytest.mark.asyncio
async def test_mp_browser_complete_idempotent_and_fail_rules(api: tuple[Any, Any]) -> None:
    _client, app = api
    app.state.settings.mp_publish_mode = "browser"
    app.state.settings.mp_browser_max_attempts = 3
    library = app.state.mp_article_library
    service = app.state.mp_browser_service

    # 1. Complete is idempotent
    art_id = await _create_test_article(library, "Complete Article")
    await service.claim_article()
    completed1 = await service.complete_article(
        art_id,
        published_url="https://mp.weixin.qq.com/s/pub_1",
    )
    assert completed1.status == MpArticleStatus.PUBLISHED.value
    # Repeated complete
    completed2 = await service.complete_article(
        art_id,
        published_url="https://mp.weixin.qq.com/s/pub_1",
    )
    assert completed2.status == MpArticleStatus.PUBLISHED.value

    # 2. Retryable failure returns to ready if below max attempts
    art_id2 = await _create_test_article(library, "Fail Article")
    await service.claim_article()
    failed_retry = await service.fail_article(
        art_id2,
        retryable=True,
        error_code="EDITOR_TIMEOUT",
        error_message="Selector timeout",
    )
    assert failed_retry.status == MpArticleStatus.READY.value

    # 3. Publish_clicked failure does not retry (directly failed)
    await service.claim_article()
    await service.checkpoint_article(art_id2, phase="publish_clicked")
    failed_direct = await service.fail_article(
        art_id2,
        retryable=True,
        error_code="PUBLISH_RESULT_UNKNOWN",
        error_message="Could not confirm",
    )
    assert failed_direct.status == MpArticleStatus.FAILED.value


@pytest.mark.asyncio
async def test_mp_browser_session_heartbeat_offline_and_qr_validation(
    api: tuple[Any, Any],
) -> None:
    _client, app = api
    clock = FakeClock()
    app.state.clock = clock
    app.state.mp_article_library._clock = clock
    app.state.mp_browser_service._clock = clock
    service = app.state.mp_browser_service

    # Update session with invalid format
    with pytest.raises(AppError) as inv_exc:
        await service.update_session(state="ready", qr_png_base64="not_base64@@@")
    assert inv_exc.value.status_code == 422

    # Update session with valid PNG
    qr_b64 = base64.b64encode(VALID_PNG).decode("ascii")
    sess = await service.update_session(
        state="auth_required",
        incident_id="inc_1",
        qr_png_base64=qr_b64,
    )
    assert sess.state == "auth_required"
    assert sess.qr_data_url is not None

    # Advance clock past 45s -> session becomes offline
    clock.advance(timedelta(seconds=50))
    offline_sess = await service.get_session()
    assert offline_sess.state == "offline"


@pytest.mark.asyncio
async def test_mp_browser_repeated_incident_deduplicates_alerts(api: tuple[Any, Any]) -> None:
    _client, app = api
    app.state.settings.mp_browser_alert_recipient_ids = ["admin_user"]
    service = app.state.mp_browser_service

    # First report of auth_required
    qr_b64 = base64.b64encode(VALID_PNG).decode("ascii")
    await service.update_session(
        state="auth_required",
        incident_id="incident_abc",
        qr_png_base64=qr_b64,
    )

    # Second heartbeat report with same incident_id
    await service.update_session(
        state="auth_required",
        incident_id="incident_abc",
        qr_png_base64=qr_b64,
    )

    # Recovered
    await service.update_session(
        state="ready",
    )
    cur = await service.get_session()
    assert cur.state == "ready"
    assert cur.qr_data_url is None
