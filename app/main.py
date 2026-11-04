from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import Database
from app.deps import get_health_monitor
from app.downloads.manager import DownloadManager
from app.health import HealthMonitor
from app.logging import setup_logging
from app.provider import build_provider_registry
from app.qbit.router import router as qbit_router
from app.torznab.router import router as torznab_router

setup_logging(settings.log_level)


@asynccontextmanager
async def lifespan(application: FastAPI):
    db = Database(settings.db_path)
    provider_registry = build_provider_registry(settings, db)
    download_manager = DownloadManager(settings, db)
    health_monitor = HealthMonitor(provider_registry, settings.health_check_interval)
    application.state.db = db
    application.state.provider_registry = provider_registry
    application.state.download_manager = download_manager
    application.state.health_monitor = health_monitor
    await download_manager.start()
    await health_monitor.start()
    try:
        yield
    finally:
        await health_monitor.stop()
        await download_manager.stop()
        await provider_registry.close()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.include_router(torznab_router)
app.include_router(qbit_router)


@app.get("/health")
async def health(monitor: HealthMonitor = Depends(get_health_monitor)) -> JSONResponse:
    # 503 when any provider is down so an external uptime checker (Uptime
    # Kuma, a container healthcheck, ...) can alert on it.
    failing = monitor.failing()
    return JSONResponse(
        status_code=503 if failing else 200,
        content={
            "status": "degraded" if failing else "ok",
            "providers": {source: asdict(health) for source, health in monitor.status().items()},
        },
    )
