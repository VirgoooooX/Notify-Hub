from __future__ import annotations

from typing import Any

from app.api.dependencies import require_admin
from app.application.audit import add_audit
from app.application.plugin_service import (
    PluginNotFoundError,
    PluginRunConflictError,
    PluginService,
)
from app.infrastructure.database.models import Secret
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

router = APIRouter(prefix="/plugins", tags=["plugins"], dependencies=[Depends(require_admin)])


class PluginConfigUpdate(BaseModel):
    config: dict[str, Any]
    schedule: dict[str, Any] | None = None


class DataResponse(BaseModel):
    data: Any


class SecretInput(BaseModel):
    value: str = Field(min_length=1, max_length=4096)


def _service(request: Request) -> PluginService:
    service = getattr(request.app.state, "plugin_service", None)
    if not isinstance(service, PluginService):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "plugin runtime is not ready")
    return service


def _not_found(exc: PluginNotFoundError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"plugin {exc.args[0]!r} was not found")


async def _audit(request: Request, action: str, resource_id: str) -> None:
    async with request.app.state.session_factory() as session, session.begin():
        add_audit(
            session,
            request.app.state.clock,
            actor_type="admin",
            actor_id=request.state.admin_id,
            action=action,
            resource_type="plugin",
            resource_id=resource_id,
            request_id=getattr(request.state, "request_id", None),
        )


@router.get("")
async def list_plugins(request: Request) -> DataResponse:
    items = await _service(request).list_plugins()
    store = request.app.state.secret_store
    for item in items:
        try:
            names = _service(request).registry.get(item["id"]).manifest.permissions.secrets
        except Exception:
            names = []
        item["secrets"] = [
            {
                "name": name,
                "configured": store is not None
                and await store.configured("plugin", item["id"], name),
            }
            for name in names
        ]
    return DataResponse(data=items)


@router.get("/{plugin_id}")
async def get_plugin(plugin_id: str, request: Request) -> DataResponse:
    try:
        result = await _service(request).get_plugin(plugin_id)
    except PluginNotFoundError as exc:
        raise _not_found(exc) from exc
    return DataResponse(data=result)


@router.put("/{plugin_id}/config")
async def update_config(plugin_id: str, body: PluginConfigUpdate, request: Request) -> DataResponse:
    try:
        result = await _service(request).update_config(
            plugin_id, body.config, schedule=body.schedule
        )
    except PluginNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    await _audit(request, "plugin.config.update", plugin_id)
    return DataResponse(data={"config": result})


@router.post("/{plugin_id}/enable", status_code=status.HTTP_204_NO_CONTENT)
async def enable_plugin(plugin_id: str, request: Request) -> None:
    try:
        await _service(request).enable(plugin_id)
    except PluginNotFoundError as exc:
        raise _not_found(exc) from exc
    await _audit(request, "plugin.enable", plugin_id)


@router.post("/{plugin_id}/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_plugin(plugin_id: str, request: Request) -> None:
    try:
        await _service(request).disable(plugin_id)
    except PluginNotFoundError as exc:
        raise _not_found(exc) from exc
    await _audit(request, "plugin.disable", plugin_id)


@router.post("/{plugin_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_plugin(plugin_id: str, request: Request) -> DataResponse:
    try:
        run_id = await _service(request).queue_manual(
            plugin_id, getattr(request.state, "request_id", None)
        )
    except PluginNotFoundError as exc:
        raise _not_found(exc) from exc
    except PluginRunConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await _audit(request, "plugin.run.queue", plugin_id)
    return DataResponse(data={"run_id": run_id, "status": "queued"})


@router.post("/{plugin_id}/test", status_code=status.HTTP_202_ACCEPTED)
async def test_plugin(plugin_id: str, request: Request) -> DataResponse:
    return await run_plugin(plugin_id, request)


def _secret_names(request: Request, plugin_id: str) -> list[str]:
    try:
        return _service(request).registry.get(plugin_id).manifest.permissions.secrets
    except Exception as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plugin was not found") from exc


@router.get("/{plugin_id}/secrets")
async def list_secrets(plugin_id: str, request: Request) -> DataResponse:
    names = _secret_names(request, plugin_id)
    async with request.app.state.session_factory() as session:
        rows = (
            await session.scalars(
                select(Secret).where(
                    Secret.scope_type == "plugin",
                    Secret.scope_id == plugin_id,
                    Secret.name.in_(names),
                )
            )
        ).all()
    configured = {row.name: row.updated_at for row in rows}
    return DataResponse(
        data=[
            {"name": name, "configured": name in configured, "updated_at": configured.get(name)}
            for name in names
        ]
    )


@router.put("/{plugin_id}/secrets/{name}")
async def put_secret(
    plugin_id: str, name: str, body: SecretInput, request: Request
) -> DataResponse:
    if name not in _secret_names(request, plugin_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "secret is not permitted by manifest")
    store = request.app.state.secret_store
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "secret store is not configured")
    await store.put("plugin", plugin_id, name, body.value)
    await _audit(request, "plugin.secret.update", plugin_id)
    return DataResponse(data={"name": name, "configured": True})


@router.delete("/{plugin_id}/secrets/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(plugin_id: str, name: str, request: Request) -> None:
    if name not in _secret_names(request, plugin_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "secret was not found")
    store = request.app.state.secret_store
    if store is not None:
        await store.delete("plugin", plugin_id, name)
    await _audit(request, "plugin.secret.delete", plugin_id)


@router.get("/{plugin_id}/runs")
async def list_runs(
    plugin_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> DataResponse:
    return DataResponse(data=await _service(request).list_runs(plugin_id, limit))


@router.get("/{plugin_id}/state")
async def get_state(plugin_id: str, request: Request) -> DataResponse:
    return DataResponse(data=await _service(request).get_state(plugin_id))


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_run(run_id: str, request: Request) -> None:
    if not await _service(request).cancel(run_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "run is not cancellable")
    await _audit(request, "plugin.run.cancel", run_id)
