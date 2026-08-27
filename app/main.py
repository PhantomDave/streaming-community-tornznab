from fastapi import FastAPI

from app.config import settings
from app.logging import setup_logging

setup_logging(settings.log_level)

app = FastAPI(title=settings.app_name, version=settings.app_version)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
