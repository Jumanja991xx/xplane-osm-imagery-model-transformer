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
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from xpsb import __version__
from xpsb import blender as blender_mod
from xpsb.geo import GeoJsonPolygon
from xpsb.imagery import basemap, xyz
from xpsb.imagery.sources import ESRI_ACCOUNT, custom_from_settings, esri_source, map_source
from xpsb.imagery.wms import WmsError
from xpsb.jobs.manager import (
    DesignEstimate,
    JobConflict,
    JobManager,
    JobNotFound,
    SimilarEstimate,
)
from xpsb.jobs.models import Brief, GateId, JobState, JobView
from xpsb.paths import AppPaths
from xpsb.photos import MAPILLARY_ACCOUNT, SearchResult
from xpsb.scene import ItemPatch, NewItem, PhotoTexture
from xpsb.secrets import ANTHROPIC_ACCOUNT, SecretStore, normalize_api_key
from xpsb.settings import ImagerySettings, Settings, SettingsStore
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
    has_mapillary_token: bool = False
    has_esri_key: bool = False
    blender: str | None = None  # version of the Blender the app would use


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


class RenameBody(BaseModel):
    name: str = Field(max_length=200)


class DesignBody(BaseModel):
    request: str = Field(min_length=3, max_length=1500)
    # The maximum the user confirmed; the architect never spends more than this.
    max_usd: float = Field(gt=0, le=1000)


class DesignOutcome(BaseModel):
    job: JobView
    summary: str
    cost_usd: float


class TexturesBody(BaseModel):
    textures: list[PhotoTexture] = Field(max_length=12)


class PhotoUpload(BaseModel):
    data: str = Field(max_length=40_000_000)  # base64 (optionally as data: URL)
    # The user confirms they may use the photo for this purpose.
    rights_confirmed: bool = False


class TokenBody(BaseModel):
    # ArcGIS API keys can be several hundred characters long; the Windows
    # credential store takes up to 1280.
    token: str = Field(min_length=10, max_length=1200)

    @property
    def clean(self) -> str:
        """Without spaces and line breaks picked up when copying from a web page."""
        return "".join(self.token.split())


class SourceTestBody(BaseModel):
    imagery: ImagerySettings
    # where to fetch the test tile (a regional source covers only its region)
    lon: float = Field(default=11.5, ge=-180, le=180)
    lat: float = Field(default=48.1, ge=-85, le=85)


class SourceTest(BaseModel):
    ok: bool
    detail: str | None = None


class BlenderTestBody(BaseModel):
    path: str | None = None  # as entered in the settings (before saving)
    device: Literal["auto", "cpu"] = "auto"


class BlenderTest(BaseModel):
    ok: bool
    version: str | None = None
    device: str = ""
    start_s: float | None = None
    gpu_search_s: float | None = None
    bake_s: float | None = None
    total_s: float = 0.0
    gpu_search_hung: bool = False
    problem: str | None = None
    log: str | None = None  # where Blender's own messages are


class KeyCheck(BaseModel):
    has_esri_key: bool
    ok: bool | None = None  # None: not checked (no key)
    detail: str | None = None


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
            has_mapillary_token=bool(ctx.secrets.get(MAPILLARY_ACCOUNT)),
            has_esri_key=bool(ctx.secrets.get(ESRI_ACCOUNT)),
            blender=getattr(await asyncio.to_thread(ctx.jobs.blender), "version", None),
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

    @app.put("/api/secrets/mapillary", dependencies=auth)
    async def set_mapillary_token(body: TokenBody) -> dict:
        if not ctx.secrets.available():
            raise HTTPException(409, detail={"key": "error.keyring_unavailable"})
        ctx.secrets.set(MAPILLARY_ACCOUNT, body.clean)
        return {"has_mapillary_token": True}

    async def check_esri() -> KeyCheck:
        key = ctx.secrets.get(ESRI_ACCOUNT)
        if not key:
            return KeyCheck(has_esri_key=False)
        ok, detail = await asyncio.to_thread(xyz.check_source, esri_source(key))
        return KeyCheck(has_esri_key=True, ok=ok, detail=detail)

    @app.put("/api/secrets/esri", dependencies=auth)
    async def set_esri_key(body: TokenBody) -> KeyCheck:
        """Stores the key and tries it right away with one tile."""
        if not ctx.secrets.available():
            raise HTTPException(409, detail={"key": "error.keyring_unavailable"})
        ctx.secrets.set(ESRI_ACCOUNT, body.clean)
        return await check_esri()

    @app.post("/api/imagery/test", dependencies=auth)
    async def test_source(body: SourceTestBody) -> SourceTest:
        """Tries the own XYZ source as entered (before saving) with one tile."""
        if body.imagery.custom_type != "xyz":
            return SourceTest(ok=False, detail="only XYZ sources can be tested here")
        source = custom_from_settings(body.imagery)
        if source is None:
            return SourceTest(ok=False, detail="incomplete URL ({z}, {x}, {y}; subdomains for {s})")
        ok, detail = await asyncio.to_thread(xyz.check_at, source, body.lon, body.lat)
        return SourceTest(ok=ok, detail=detail)

    @app.post("/api/blender/test", dependencies=auth)
    async def test_blender(body: BlenderTestBody) -> BlenderTest:
        """Bakes one test object with the Blender as entered and times it."""
        info = await asyncio.to_thread(blender_mod.find, body.path or None)
        if info is None:
            return BlenderTest(ok=False, problem="Blender not found")
        work = ctx.paths.cache / "blender-test"
        result = await asyncio.to_thread(
            blender_mod.self_test, info.path, work, body.device == "cpu"
        )
        return BlenderTest(version=info.version, log=str(work / "blender-test.log"), **vars(result))

    @app.post("/api/secrets/esri/check", dependencies=auth)
    async def check_esri_key() -> KeyCheck:
        return await check_esri()

    @app.delete("/api/secrets/esri", dependencies=auth)
    async def delete_esri_key() -> dict:
        ctx.secrets.delete(ESRI_ACCOUNT)
        return {"has_esri_key": False}

    @app.delete("/api/secrets/mapillary", dependencies=auth)
    async def delete_mapillary_token() -> dict:
        ctx.secrets.delete(MAPILLARY_ACCOUNT)
        return {"has_mapillary_token": False}

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

    @app.post("/api/jobs/{job_id}/package-name", dependencies=auth)
    async def rename_package(job_id: str, body: RenameBody) -> JobView:
        ctx.jobs.rename_package(job_id, body.name)
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
        "/api/basemap/{z}/{x}/{y}",
        dependencies=auth,
        response_class=Response,
        responses={200: {"content": {"image/png": {}, "image/jpeg": {}}}},
    )
    async def basemap_tile(z: int, x: int, y: int):
        """A tile of the user's own XYZ source for the map background (see basemap.py)."""
        source = map_source(ctx.settings.load().imagery)
        if source is None:
            raise HTTPException(404, detail={"key": "error.no_basemap"})
        try:
            data, ctype = await asyncio.to_thread(
                basemap.tile, source, ctx.paths.cache / "basemap", z, x, y
            )
        except WmsError as exc:
            raise HTTPException(
                502, detail={"key": "error.basemap", "detail": str(exc)[:200]}
            ) from None
        return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})

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

    @app.get("/api/jobs/{job_id}/design/{item_id:path}", dependencies=auth)
    async def design_estimate(job_id: str, item_id: str) -> DesignEstimate:
        return ctx.jobs.design_estimate(job_id, item_id)

    @app.post("/api/jobs/{job_id}/design/{item_id:path}", dependencies=auth)
    async def redesign(job_id: str, item_id: str, body: DesignBody) -> DesignOutcome:
        _, summary, cost = await ctx.jobs.redesign(job_id, item_id, body.request, body.max_usd)
        return DesignOutcome(job=ctx.jobs.view(job_id), summary=summary, cost_usd=round(cost, 4))

    @app.delete("/api/jobs/{job_id}/design/{item_id:path}", dependencies=auth)
    async def reset_design(job_id: str, item_id: str) -> JobView:
        await asyncio.to_thread(ctx.jobs.reset_design, job_id, item_id)
        return ctx.jobs.view(job_id)

    @app.post("/api/jobs/{job_id}/photos", dependencies=auth)
    async def add_photo(job_id: str, item: str, body: PhotoUpload) -> JobView:
        await asyncio.to_thread(ctx.jobs.add_photo, job_id, item, body.data, body.rights_confirmed)
        return ctx.jobs.view(job_id)

    @app.get(
        "/api/jobs/{job_id}/photos/{photo_id}",
        dependencies=auth,
        response_class=FileResponse,
        responses={200: {"content": {"image/jpeg": {}}}},
    )
    async def photo_file(job_id: str, photo_id: str):
        path = ctx.jobs.photo_file(job_id, photo_id)
        if path is None:
            raise HTTPException(404, detail={"key": "error.photo_not_found"})
        return FileResponse(path, media_type="image/jpeg")

    @app.delete("/api/jobs/{job_id}/photos/{photo_id}", dependencies=auth)
    async def delete_photo(job_id: str, photo_id: str) -> JobView:
        ctx.jobs.delete_photo(job_id, photo_id)
        return ctx.jobs.view(job_id)

    @app.post("/api/jobs/{job_id}/skip-blender", dependencies=auth)
    async def skip_blender(job_id: str) -> JobView:
        ctx.jobs.skip_blender(job_id)
        return ctx.jobs.view(job_id)

    @app.put("/api/jobs/{job_id}/textures", dependencies=auth)
    async def set_textures(job_id: str, item: str, body: TexturesBody) -> JobView:
        await asyncio.to_thread(ctx.jobs.set_textures, job_id, item, body.textures)
        return ctx.jobs.view(job_id)

    @app.post(
        "/api/jobs/{job_id}/texture-preview",
        dependencies=auth,
        responses={200: {"content": {"image/jpeg": {}}}},
    )
    async def texture_preview(job_id: str, item: str, body: PhotoTexture) -> Response:
        data = await asyncio.to_thread(ctx.jobs.texture_preview, job_id, item, body)
        return Response(content=data, media_type="image/jpeg")

    @app.get("/api/jobs/{job_id}/photo-search", dependencies=auth)
    async def search_photos(job_id: str, item: str) -> SearchResult:
        return await asyncio.to_thread(ctx.jobs.search_photos, job_id, item)

    @app.get(
        "/api/jobs/{job_id}/photo-search/{candidate_id}/thumb",
        dependencies=auth,
        responses={200: {"content": {"image/jpeg": {}}}},
    )
    async def candidate_thumb(job_id: str, candidate_id: str) -> Response:
        data = await asyncio.to_thread(ctx.jobs.candidate_thumb, job_id, candidate_id)
        return Response(content=data, media_type="image/jpeg")

    @app.post("/api/jobs/{job_id}/photo-search/{candidate_id}/import", dependencies=auth)
    async def import_photo(job_id: str, candidate_id: str) -> JobView:
        await asyncio.to_thread(ctx.jobs.import_photo, job_id, candidate_id)
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
