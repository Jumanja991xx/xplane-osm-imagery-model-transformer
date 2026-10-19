"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import secrets as pysecrets
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import (
    Body,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from xpsb import __version__
from xpsb.geo import GeoJsonPolygon
from xpsb.jobs.manager import JobConflict, JobManager, JobNotFound, SimilarEstimate
from xpsb.jobs.models import Brief, GateId, JobState, JobView
from xpsb.paths import AppPaths
from xpsb.scene import ItemPatch, NewItem
from xpsb.secrets import ANTHROPIC_ACCOUNT, SecretStore, normalize_api_key
from xpsb.settings import Settings, SettingsStore
from xpsb.xplane import dsftool
from xpsb.xplane_detect import XPlaneCheck, check_xplane_path, detect_xplane_installs

TAURI_ORIGINS = ["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"]
DEV_ORIGINS = ["http://localhost:1420", "http://127.0.0.1:1420"]


@dataclass
class AppContext:
    token: str
    paths: AppPaths
    settings: SettingsStore
    secrets: SecretStore
    jobs: JobManager
    resources_dir: Path | None


# ---- response models --------------------------------------------------------


class Health(BaseModel):
    status: str
    version: str


class DsfToolStatus(BaseModel):
    found: bool
    path: str | None = None
    version: str | None = None
    error: str | None = None


class SystemStatus(BaseModel):
    version: str
    platform: str
    data_dir: str
    dsftool: DsfToolStatus
    xplane: XPlaneCheck | None
    keyring_available: bool
    has_api_key: bool


class ApiKeyStatus(BaseModel):
    has_api_key: bool


class CreateJob(BaseModel):
    aoi: GeoJsonPolygon


class Approve(BaseModel):
    hash: str


class PathBody(BaseModel):
    path: str


class ReopenBody(BaseModel):
    to: Literal["analyzed", "clarified"]


class AdjustBody(BaseModel):
    attrs: dict[str, Any] = Field(default_factory=dict)


class SimilarBody(BaseModel):
    # The estimate the user confirmed; the search never spends more than this.
    max_usd: float = Field(gt=0, le=1000)


class ApiKeyBody(BaseModel):
    api_key: str


# ---- app --------------------------------------------------------------------


def create_app(ctx: AppContext, dev_cors: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ctx.jobs.bind_loop(asyncio.get_running_loop())
        ctx.jobs.load_existing()
        yield

    app = FastAPI(title="xpsb", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=TAURI_ORIGINS + (DEV_ORIGINS if dev_cors else []),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not pysecrets.compare_digest(value, ctx.token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"key": "error.unauthorized"})

    @app.exception_handler(JobConflict)
    async def _conflict(_: Request, exc: JobConflict):
        return JSONResponse(
            status_code=409, content={"detail": {"key": exc.key, "params": exc.params}}
        )

    @app.exception_handler(JobNotFound)
    async def _not_found(_: Request, exc: JobNotFound):
        return JSONResponse(status_code=404, content={"detail": {"key": "error.job_not_found"}})

    auth = [Depends(require_token)]

    # -- system / settings ----------------------------------------------------

    @app.get("/api/health", dependencies=auth)
    async def health() -> Health:
        return Health(status="ok", version=__version__)

    def dsftool_status(configured: str | None) -> DsfToolStatus:
        exe = dsftool.locate(configured, ctx.resources_dir)
        if exe is None:
            return DsfToolStatus(found=False)
        try:
            return DsfToolStatus(found=True, path=str(exe), version=dsftool.version(exe))
        except dsftool.DsfToolError as exc:
            return DsfToolStatus(found=False, path=str(exe), error=str(exc))

    @app.get("/api/system", dependencies=auth)
    async def system() -> SystemStatus:
        s = ctx.settings.load()
        return SystemStatus(
            version=__version__,
            platform=sys.platform,
            data_dir=str(ctx.paths.root),
            dsftool=await asyncio.to_thread(dsftool_status, s.dsftool_path),
            xplane=check_xplane_path(s.xplane_path) if s.xplane_path else None,
            keyring_available=ctx.secrets.available(),
            has_api_key=bool(ctx.secrets.get(ANTHROPIC_ACCOUNT)),
        )

    @app.get("/api/settings", dependencies=auth)
    async def get_settings() -> Settings:
        return ctx.settings.load()

    @app.put("/api/settings", dependencies=auth)
    async def put_settings(settings: Settings) -> Settings:
        return ctx.settings.save(settings)

    @app.post("/api/xplane/detect", dependencies=auth)
    async def xplane_detect() -> list[XPlaneCheck]:
        return await asyncio.to_thread(detect_xplane_installs)

    @app.post("/api/xplane/check", dependencies=auth)
    async def xplane_check(body: PathBody) -> XPlaneCheck:
        return check_xplane_path(body.path)

    @app.post("/api/dsftool/check", dependencies=auth)
    async def dsftool_check(body: PathBody) -> DsfToolStatus:
        return await asyncio.to_thread(dsftool_status, body.path or None)

    @app.put("/api/secrets/anthropic", dependencies=auth)
    async def set_api_key(body: ApiKeyBody) -> ApiKeyStatus:
        if not ctx.secrets.available():
            raise HTTPException(409, detail={"key": "error.keyring_unavailable"})
        try:
            key = normalize_api_key(body.api_key)
        except ValueError:
            raise HTTPException(422, detail={"key": "error.invalid_api_key"}) from None
        ctx.secrets.set(ANTHROPIC_ACCOUNT, key)
        return ApiKeyStatus(has_api_key=True)

    @app.delete("/api/secrets/anthropic", dependencies=auth)
    async def delete_api_key() -> ApiKeyStatus:
        ctx.secrets.delete(ANTHROPIC_ACCOUNT)
        return ApiKeyStatus(has_api_key=False)

    # -- jobs -------------------------------------------------------------------

    @app.post("/api/jobs", dependencies=auth, status_code=201)
    async def create_job(body: CreateJob) -> JobView:
        job = ctx.jobs.create(body.aoi)
        return ctx.jobs.view(job.id)

    @app.get("/api/jobs", dependencies=auth)
    async def list_jobs() -> list[dict]:
        return ctx.jobs.summaries()

    @app.post("/api/jobs/{job_id}/reopen", dependencies=auth)
    async def reopen(job_id: str, body: ReopenBody) -> JobView:
        ctx.jobs.reopen(job_id, JobState(body.to))
        return ctx.jobs.view(job_id)

    @app.get("/api/jobs/{job_id}", dependencies=auth)
    async def get_job(job_id: str) -> JobView:
        return await asyncio.to_thread(ctx.jobs.view, job_id)

    @app.put("/api/jobs/{job_id}/brief", dependencies=auth)
    async def put_brief(job_id: str, brief: Brief) -> JobView:
        ctx.jobs.set_brief(job_id, brief)
        return ctx.jobs.view(job_id)

    @app.post("/api/jobs/{job_id}/answers", dependencies=auth)
    async def post_answers(job_id: str, answers: Annotated[dict[str, str], Body()]) -> JobView:
        ctx.jobs.set_answers(job_id, answers)
        return ctx.jobs.view(job_id)

    @app.post("/api/jobs/{job_id}/items", dependencies=auth, status_code=201)
    async def add_item(job_id: str, body: NewItem) -> JobView:
        ctx.jobs.add_item(job_id, body)
        return ctx.jobs.view(job_id)

    @app.get("/api/jobs/{job_id}/similar/{item_id:path}", dependencies=auth)
    async def similar_estimate(job_id: str, item_id: str) -> SimilarEstimate:
        return ctx.jobs.similar_estimate(job_id, item_id)

    @app.post("/api/jobs/{job_id}/similar/{item_id:path}", dependencies=auth)
    async def find_similar(job_id: str, item_id: str, body: SimilarBody) -> JobView:
        await ctx.jobs.find_similar(job_id, item_id, body.max_usd)
        return ctx.jobs.view(job_id)

    @app.patch("/api/jobs/{job_id}/items/{item_id:path}", dependencies=auth)
    async def update_item(job_id: str, item_id: str, body: ItemPatch) -> JobView:
        ctx.jobs.update_item(job_id, item_id, body)
        return ctx.jobs.view(job_id)

    @app.delete("/api/jobs/{job_id}/items/{item_id:path}", dependencies=auth)
    async def delete_item(job_id: str, item_id: str) -> JobView:
        ctx.jobs.delete_item(job_id, item_id)
        return ctx.jobs.view(job_id)

    @app.get(
        "/api/jobs/{job_id}/imagery",
        dependencies=auth,
        response_class=FileResponse,
        responses={200: {"content": {"image/jpeg": {}}}},
    )
    async def job_imagery(job_id: str):
        path = ctx.jobs.imagery_file(job_id)
        if path is None:
            raise HTTPException(404, detail={"key": "error.no_imagery"})
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/jobs/{job_id}/preview", dependencies=auth)
    async def job_preview(job_id: str) -> dict:
        return ctx.jobs.preview(job_id)

    @app.get("/api/jobs/{job_id}/preview/{name}", dependencies=auth, response_class=FileResponse)
    async def job_preview_file(job_id: str, name: str):
        path = ctx.jobs.preview_file(job_id, name)
        if path is None:
            raise HTTPException(404, detail={"key": "error.no_preview"})
        media = "image/png" if path.suffix == ".png" else "application/json"
        return FileResponse(path, media_type=media)

    @app.patch("/api/jobs/{job_id}/objects/{item_id:path}", dependencies=auth)
    async def adjust_object(job_id: str, item_id: str, body: AdjustBody) -> JobView:
        await asyncio.to_thread(ctx.jobs.adjust_object, job_id, item_id, body.attrs)
        return ctx.jobs.view(job_id)

    @app.post("/api/jobs/{job_id}/gates/{gate}/approve", dependencies=auth)
    async def approve(job_id: str, gate: GateId, body: Approve) -> JobView:
        return ctx.jobs.approve(job_id, gate, body.hash)

    @app.post("/api/jobs/{job_id}/cancel", dependencies=auth)
    async def cancel(job_id: str) -> JobView:
        ctx.jobs.cancel(job_id)
        return ctx.jobs.view(job_id)

    @app.websocket("/ws/jobs/{job_id}")
    async def job_events(ws: WebSocket, job_id: str):
        # Browsers cannot set headers on WebSockets: the token comes as query parameter.
        if not pysecrets.compare_digest(ws.query_params.get("token", ""), ctx.token):
            await ws.close(code=4401)
            return
        try:
            event_log = ctx.jobs.events(job_id)
        except JobNotFound:
            await ws.close(code=4404)
            return
        await ws.accept()
        queue = event_log.subscribe()

        async def pump() -> None:
            for event in event_log.events:
                await ws.send_text(event.model_dump_json())
            while True:
                event = await queue.get()
                await ws.send_text(event.model_dump_json())

        async def watch_disconnect() -> None:
            # Without reading, a closed client is only noticed on the next send,
            # which may never come – and then the server could not shut down.
            while True:
                await ws.receive_text()

        tasks = {asyncio.create_task(pump()), asyncio.create_task(watch_disconnect())}
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            event_log.unsubscribe(queue)

    return app
