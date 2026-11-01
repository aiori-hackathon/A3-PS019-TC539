"""FastAPI application for the FieldLedger web UI (docs/api-contract.md).

Only this module and ``__main__`` import FastAPI. The core package and the CLI never do.
"""

from __future__ import annotations

import atexit
import logging
import shutil
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..discovery import discover_sources
from ..errors import ConfigError, FieldLedgerError, PathAccessError
from ..limits import DEFAULT_LIMITS, IngestionLimits
from ..metadata import load_metadata
from ..safety import resolve_within
from . import contract
from .apierrors import ApiError
from .paths import validate_relative_path
from .security import BodyLimitMiddleware, GuardMiddleware
from .service import Engine, Service, config_payload
from .workspace import WorkspaceManager, rmtree_robust

logger = logging.getLogger("fieldledger.web")
CHUNK = 1024 * 1024
MULTIPART_OVERHEAD = 2 * 1024 * 1024


@dataclass
class AppConfig:
    root: Path = field(default_factory=Path.cwd)
    host: str = "127.0.0.1"
    port: int = 8765
    limits: contract.WorkspaceLimits = field(default_factory=contract.WorkspaceLimits)
    ingestion: IngestionLimits = DEFAULT_LIMITS
    static_dir: Path | None = None
    temp_base: Path | None = None
    sweep_interval_seconds: float = 30.0


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_declarations: bool = False
    formats: list[str] = list(contract.REPORT_FORMATS)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: str = "bundled_synthetic"


_FINDING_PARAMS = {"q", "class", "source", "metadata_status", "review_status", "ambiguous",
                   "sort", "order", "page", "page_size"}


def parse_findings_query(request: Request, class_ids: tuple[str, ...]) -> dict:
    qp = request.query_params
    unknown = set(qp.keys()) - _FINDING_PARAMS
    if unknown:
        raise ApiError("bad_request", "unknown query parameter")

    def one(name: str, default: str) -> str:
        values = qp.getlist(name)
        if len(values) > 1:
            raise ApiError("bad_request", f"{name} may be given once")
        return values[0] if values else default

    def many(name: str, allowed: tuple[str, ...] | None) -> list[str]:
        values = qp.getlist(name)
        if allowed is not None and any(v not in allowed for v in values):
            raise ApiError("bad_request", f"invalid value for {name}")
        return values

    sort, order = one("sort", "source"), one("order", "asc")
    if sort not in contract.FINDING_SORT_KEYS or order not in ("asc", "desc"):
        raise ApiError("bad_request", "invalid sort or order")
    try:
        page, size = int(one("page", "1")), int(one("page_size", "50"))
    except ValueError:
        raise ApiError("bad_request", "page and page_size must be integers") from None
    if page < 1 or not 1 <= size <= 200:
        raise ApiError("bad_request", "page must be at least 1 and page_size between 1 and 200")
    amb = one("ambiguous", "")
    if amb not in ("", "true", "false"):
        raise ApiError("bad_request", "ambiguous must be true or false")
    q = one("q", "")
    if len(q) > 256:
        raise ApiError("bad_request", "search text is too long")
    return {
        "q": q,
        "class": many("class", class_ids),
        "source": many("source", None),
        "metadata_status": many("metadata_status", contract.METADATA_STATUSES),
        "review_status": many("review_status", contract.REVIEW_STATUSES),
        "ambiguous": None if amb == "" else amb == "true",
        "sort": sort, "order": order, "page": page, "page_size": size,
    }


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or AppConfig()
    engine = Engine.load(config.root)  # raises ConfigError if config/ is missing or invalid
    manager = WorkspaceManager(config.limits, config.ingestion, temp_base=config.temp_base)
    service = Service(engine, manager, config.ingestion)
    static_dir = config.static_dir if config.static_dir is not None else Path(__file__).parent / "static"
    has_static = (static_dir / "index.html").is_file()
    stop = threading.Event()

    def sweeper() -> None:
        while not stop.wait(config.sweep_interval_seconds):
            try:
                manager.sweep()
            except Exception:
                logger.warning("sweep failed")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        manager.cleanup_stale()
        thread = threading.Thread(target=sweeper, name="fieldledger-sweeper", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            manager.clear()

    app = FastAPI(
        title="FieldLedger API", version=__version__, lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url="/api/openapi.json",
    )
    app.state.service, app.state.manager, app.state.engine = service, manager, engine
    atexit.register(manager.clear)
    limits = config.limits

    # ------------------------------------------------------------------ errors
    @app.exception_handler(ApiError)
    async def _api_error(_r: Request, exc: ApiError):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_r: Request, _exc: RequestValidationError):
        return JSONResponse(ApiError("bad_request", "invalid request").body(), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_r: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return JSONResponse(ApiError("not_found", "not found").body(), status_code=404)
        code = "bad_request" if exc.status_code < 500 else "internal_error"
        return JSONResponse({"error": {"code": code, "message": "request could not be processed"}},
                            status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_r: Request, exc: Exception):
        logger.warning("unhandled error type=%s", type(exc).__name__)  # never the message
        return JSONResponse(ApiError("internal_error", "an unexpected error occurred").body(),
                            status_code=500)

    # ------------------------------------------------------------------ system
    @app.get("/api/health")
    def health():
        return {"status": "ok", "api_version": contract.API_VERSION, "version": __version__}

    @app.get("/api/config")
    def get_config():
        return config_payload(engine, limits, config.ingestion)

    # ------------------------------------------------------------------ workspace
    @app.get("/api/workspace")
    def workspace_state():
        state = manager.state()
        if state.get("exists"):
            state["latest_scan_id"] = service.latest_scan_id()
        return state

    def _no_scan_running() -> None:
        if service.scan_active():
            raise ApiError("scan_in_progress", "wait for the running scan to finish or cancel it")

    async def _stream_to(upload, dest: Path, max_bytes: int, too_large: ApiError) -> int:
        size = 0
        try:
            with open(dest, "wb") as out:
                while True:
                    chunk = await upload.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise too_large
                    out.write(chunk)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        return size

    @app.post("/api/workspace/files")
    async def upload_files(request: Request):
        _no_scan_running()
        ws = manager.ensure()
        try:
            form = await request.form(max_files=limits.max_files_per_request,
                                      max_fields=limits.max_files_per_request * 2 + 10)
        except StarletteHTTPException:
            raise ApiError("too_many_files", "too many files in one request") from None
        try:
            uploads = form.getlist("files")
            names = form.getlist("relative_paths")
            if not uploads or len(uploads) != len(names) or not all(isinstance(n, str) for n in names):
                raise ApiError("bad_request", "send one relative_paths field per file")
            results = []
            for upload, name in zip(uploads, names):
                entry = {"name": name if len(name) <= 256 else name[:256], "accepted": False}
                tmp = None
                try:
                    key = validate_relative_path(name)
                    with ws.lock:
                        ws.check_can_add(key, limits)
                        cap = ws.per_file_limit(key, config.ingestion)
                        room = limits.max_workspace_bytes - ws.bytes_used()
                    if room <= 0:
                        raise ApiError("workspace_too_large", "the workspace size limit has been reached")
                    tmp = ws.new_incoming_path()
                    if room < cap:
                        too_large = ApiError("workspace_too_large", "the workspace size limit would be exceeded")
                    else:
                        too_large = ApiError("file_too_large", "the file exceeds its size limit")
                    size = await _stream_to(upload, tmp, min(cap, room), too_large)
                    with ws.lock:
                        if ws.bytes_used() > limits.max_workspace_bytes:
                            raise ApiError("workspace_too_large", "the workspace size limit would be exceeded")
                        ws.commit_file(key, tmp, size, limits)
                    tmp = None
                    entry["accepted"] = True
                except ApiError as exc:
                    entry.update(code=exc.code, message=exc.message)
                finally:
                    if tmp is not None:
                        tmp.unlink(missing_ok=True)
                results.append(entry)
            manager.touch()
            logger.info("upload processed files=%d accepted=%d", len(results),
                        sum(1 for r in results if r["accepted"]))
            return {"results": results, "revision": ws.revision}
        finally:
            await form.close()

    @app.delete("/api/workspace/files")
    def delete_file(source_key: str = ""):
        _no_scan_running()
        ws = manager.get()
        with ws.lock:
            if not ws.remove_file(source_key):
                raise ApiError("not_found", "unknown file")
            return {"revision": ws.revision}

    @app.post("/api/workspace/metadata")
    async def upload_metadata(request: Request):
        _no_scan_running()
        ws = manager.ensure()
        try:
            form = await request.form(max_files=1, max_fields=10)
        except StarletteHTTPException:
            raise ApiError("bad_request", "send exactly one metadata file") from None
        try:
            uploads = form.getlist("file") or form.getlist("files")
            if len(uploads) != 1 or not hasattr(uploads[0], "read"):
                raise ApiError("bad_request", "send exactly one metadata file")
            upload = uploads[0]
            if not (upload.filename or "").lower().endswith((".yaml", ".yml")):
                raise ApiError("unsupported_type", "metadata must be a YAML file")
            folder = ws.incoming_dir / ("m" + ws.new_incoming_path("").name)
            folder.mkdir()
            tmp = folder / "metadata.yaml"
            try:
                size = await _stream_to(
                    upload, tmp, limits.max_metadata_bytes,
                    ApiError("file_too_large", "the metadata file exceeds its size limit"))
                try:
                    ms = load_metadata(tmp)
                except ConfigError as exc:
                    raise ApiError("invalid_metadata", str(exc)) from None
                except (ValueError, TypeError):
                    raise ApiError("invalid_metadata", "the metadata file is invalid") from None
                with ws.lock:
                    ws.set_metadata(tmp, len(ms.entries), ms.metadata_origin, size)
                    return {"entries": len(ms.entries), "origin": ms.metadata_origin, "revision": ws.revision}
            finally:
                rmtree_robust(folder)
        finally:
            await form.close()

    @app.delete("/api/workspace/metadata")
    def delete_metadata():
        _no_scan_running()
        ws = manager.get()
        with ws.lock:
            if not ws.clear_metadata():
                raise ApiError("not_found", "no metadata file")
            return {"revision": ws.revision}

    @app.post("/api/workspace/bundled")
    def load_bundled():
        _no_scan_running()
        if not engine.benchmark_available():
            raise ApiError("benchmark_unavailable", "the bundled synthetic data was not found")
        ws = manager.ensure()
        with ws.lock:
            if ws.files or ws.metadata is not None:
                raise ApiError("bad_request", "clear the workspace before loading the bundled data")
            base = engine.benchmark_dir.resolve()
            try:
                for path in discover_sources(base):
                    key = validate_relative_path(path.relative_to(base).as_posix())
                    tmp = ws.new_incoming_path()
                    shutil.copyfile(path, tmp)
                    ws.commit_file(key, tmp, tmp.stat().st_size if tmp.exists() else path.stat().st_size, limits)
                meta_src = engine.bundled_metadata_path
                if meta_src.is_file():
                    tmp = ws.new_incoming_path(".yaml")
                    shutil.copyfile(meta_src, tmp)
                    ms = load_metadata(tmp)
                    ws.set_metadata(tmp, len(ms.entries), ms.metadata_origin, tmp.stat().st_size)
            except (ApiError, FieldLedgerError, OSError):
                for key in list(ws.files):
                    ws.remove_file(key)
                ws.clear_metadata()
                raise ApiError("internal_error", "the bundled data could not be loaded") from None
        return manager.state()

    @app.delete("/api/workspace")
    def clear_workspace():
        _no_scan_running()
        if not manager.clear():
            raise ApiError("no_workspace", "there is no active workspace")
        return {"cleared": True}

    # ------------------------------------------------------------------ scans
    @app.post("/api/scans", status_code=202)
    def start_scan(body: ScanRequest):
        return {"scan_id": service.start_scan(body.include_declarations, body.formats)}

    @app.get("/api/scans/{scan_id}")
    def scan_status(scan_id: str):
        return service.status(scan_id)

    @app.post("/api/scans/{scan_id}/cancel")
    def scan_cancel(scan_id: str):
        return service.cancel(scan_id)

    @app.get("/api/scans/{scan_id}/summary")
    def scan_summary(scan_id: str):
        return service.summary(scan_id)

    @app.get("/api/scans/{scan_id}/sources")
    def scan_sources(scan_id: str):
        return service.sources(scan_id)

    @app.get("/api/scans/{scan_id}/findings")
    def scan_findings(scan_id: str, request: Request):
        return service.findings(scan_id, parse_findings_query(request, engine.registry.ids()))

    @app.get("/api/scans/{scan_id}/findings/{finding_id}")
    def scan_finding(scan_id: str, finding_id: str):
        return service.finding_detail(scan_id, finding_id)

    @app.put("/api/scans/{scan_id}/findings/{finding_id}/review")
    def scan_review(scan_id: str, finding_id: str, body: ReviewRequest):
        return service.set_review(scan_id, finding_id, body.status)

    @app.get("/api/scans/{scan_id}/metadata")
    def scan_metadata(scan_id: str):
        return service.metadata(scan_id)

    @app.get("/api/scans/{scan_id}/export/{fmt}")
    def scan_export(scan_id: str, fmt: str):
        path, media, name = service.export(scan_id, fmt)
        return Response(
            content=path.read_bytes(), media_type=f"{media}; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/workspace/reviews/export")
    def reviews_export():
        return Response(
            content=service.reviews_export(), media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="fieldledger_review_annotations.json"'})

    @app.delete("/api/workspace/reviews")
    def reviews_reset():
        return {"removed": service.reset_reviews()}

    # ------------------------------------------------------------------ evaluation
    @app.post("/api/evaluations", status_code=202)
    def start_evaluation(body: EvaluationRequest | None = None):
        return {"evaluation_id": service.start_evaluation((body or EvaluationRequest()).dataset)}

    @app.get("/api/evaluations/{evaluation_id}")
    def get_evaluation(evaluation_id: str):
        return service.evaluation(evaluation_id)

    # ------------------------------------------------------------------ frontend
    if has_static:
        index = static_dir / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            if path == "api" or path.startswith("api/"):
                raise ApiError("not_found", "not found")
            if path:
                try:
                    target = resolve_within(static_dir, path)
                except (PathAccessError, ValueError, OSError):
                    raise ApiError("not_found", "not found") from None
                if target.is_file():
                    headers = {"Cache-Control": "public, max-age=31536000, immutable"} \
                        if path.startswith("assets/") else {"Cache-Control": "no-cache"}
                    return FileResponse(target, headers=headers)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        def no_frontend():
            return JSONResponse({"message": "FieldLedger API is running. The web UI has not been "
                                 "built; run 'npm run build' in frontend/ or use the Vite dev server."})

    # Middleware: added last so that it wraps everything (Guard is outermost).
    ws_cap = limits.max_workspace_bytes + MULTIPART_OVERHEAD
    app.add_middleware(BodyLimitMiddleware, limits={
        "/api/workspace/files": (min(ws_cap, limits.max_workspace_bytes + MULTIPART_OVERHEAD), "workspace_too_large"),
        "/api/workspace/metadata": (limits.max_metadata_bytes + MULTIPART_OVERHEAD, "file_too_large"),
    })
    app.add_middleware(GuardMiddleware, configured_host=config.host)
    return app
