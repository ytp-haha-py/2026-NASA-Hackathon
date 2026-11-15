from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from analog_api.adapters.earth_aoi import EarthAoiProvider
from analog_api.adapters.jobs import JobRunner
from analog_api.adapters.planetary_aoi import PlanetaryAoiProvider
from analog_api.adapters.seed import ensure_seeded, make_engine
from analog_api.adapters.typed_db import init_typed_db
from analog_api.api.routes import router
from analog_api.api.routes_aoi import router as aoi_router
from analog_api.api.routes_coverage import router as coverage_router
from analog_api.api.routes_earth_map import router as earth_map_router
from analog_api.api.routes_features import router as features_router
from analog_api.api.routes_global import router as global_router
from analog_api.api.routes_planetary import router as planetary_router
from analog_api.api.routes_planetary_aoi import router as planetary_aoi_router
from analog_api.api.routes_planetary_aoi_engine import router as planetary_aoi_engine_router
from analog_api.api.routes_typed import router as typed_router
from analog_api.config import Settings, resolve_repo_path
from analog_api.domain.aoi import AoiProcessor, AoiResult, ValidatedAoi
from analog_api.domain.errors import AppError

logger = logging.getLogger("analog_api")


class MultiBodyAoiProvider:
    """Dispatch an AOI job to the Earth or planetary scientific provider."""

    def __init__(self, earth: AoiProcessor, planetary: AoiProcessor) -> None:
        self.earth = earth
        self.planetary = planetary

    def analyze(self, request: ValidatedAoi) -> AoiResult:
        if request.body == "Earth":
            return self.earth.analyze(request)
        return self.planetary.analyze(request)


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, object]] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or []}},
    )


def create_app(
    settings: Settings | None = None,
    aoi_processor=None,
    planetary_processor=None,
) -> FastAPI:
    settings = settings or Settings()
    if settings.sqlite_path is not None:
        settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    ensure_seeded(engine, settings)
    init_typed_db(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    earth_processor = aoi_processor if aoi_processor is not None else EarthAoiProvider()
    if planetary_processor is None:
        planetary_processor = PlanetaryAoiProvider(
            root=resolve_repo_path(settings.planetary_coarse_dir),
            allow_network=settings.planetary_allow_network,
        )
    processor = MultiBodyAoiProvider(earth_processor, planetary_processor)
    job_runner = JobRunner(
        session_factory=session_factory,
        processor=processor,
        max_workers=settings.aoi_workers,
        inline=settings.aoi_inline,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        job_runner.recover()
        try:
            yield
        finally:
            job_runner.shutdown()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Search indexed Earth terrain windows for similarity to a user-supplied target "
            "vector, and analyze bounded new Earth AOIs on demand. Results are subset-based, "
            "terrain-shape-only comparisons with land-cover context."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.settings = settings
    app.state.job_runner = job_runner
    app.state.planetary_aoi_provider = planetary_processor

    app.include_router(router)
    app.include_router(aoi_router)
    app.include_router(earth_map_router)
    app.include_router(global_router)
    app.include_router(typed_router)
    app.include_router(coverage_router)
    app.include_router(features_router)
    app.include_router(planetary_router)
    app.include_router(planetary_aoi_router)
    app.include_router(planetary_aoi_engine_router)

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "location": list(error.get("loc", [])),
                "message": error.get("msg", "invalid value"),
                "type": error.get("type", "value_error"),
            }
            for error in exc.errors()
        ]
        message = (
            details[0]["message"]
            if len(details) == 1
            else "Request validation failed. See details for failed fields."
        )
        return error_response(
            422,
            "validation_error",
            str(message),
            jsonable_encoder(details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API error", exc_info=exc)
        return error_response(
            500, "internal_error", "Internal server error. See server logs for details."
        )

    return app


__all__ = ["create_app", "error_response"]
