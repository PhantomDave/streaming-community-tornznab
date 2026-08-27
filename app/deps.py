from __future__ import annotations

from fastapi import Request

from app.db import Database
from app.downloads.manager import DownloadManager
from app.sc.client import StreamingCommunityClient


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_sc_client(request: Request) -> StreamingCommunityClient:
    return request.app.state.sc_client


def get_download_manager(request: Request) -> DownloadManager:
    return request.app.state.download_manager
