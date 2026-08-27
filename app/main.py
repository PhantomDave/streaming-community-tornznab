from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import Database
from app.downloads.manager import DownloadManager
from app.logging import setup_logging
from app.qbit.router import router as qbit_router
from app.sc.client import StreamingCommunityClient
from app.torznab.router import router as torznab_router

setup_logging(settings.log_level)


@asynccontextmanager
async def lifespan(application: FastAPI):
    db = Database(settings.db_path)
    sc_client = StreamingCommunityClient(settings, db)
    download_manager = DownloadManager(settings, db)
    application.state.db = db
    application.state.sc_client = sc_client
    application.state.download_manager = download_manager
    await download_manager.start()
    try:
        yield
    finally:
        await download_manager.stop()
        await sc_client.close()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.include_router(torznab_router)
app.include_router(qbit_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
