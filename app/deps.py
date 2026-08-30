from __future__ import annotations

from fastapi import Request

from app.db import Database
from app.downloads.manager import DownloadManager
from app.provider import ProviderRegistry


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_provider_registry(request: Request) -> ProviderRegistry:
    return request.app.state.provider_registry


def get_download_manager(request: Request) -> DownloadManager:
    return request.app.state.download_manager
