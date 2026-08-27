from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Form, Query, Response

from app.config import settings
from app.deps import get_db, get_download_manager
from app.db import Database
from app.downloads.manager import DownloadManager
from app.magnet import extract_infohash_from_magnet
from app.qbit.models import to_qbit_info, to_qbit_properties

router = APIRouter(prefix="/api/v2", tags=["qbit"])
logger = logging.getLogger(__name__)

_sessions: set[str] = set()


@router.post("/auth/login")
async def auth_login(
    username: str = Form(),
    password: str = Form(),
) -> Response:
    if username != settings.qbit_username or password != settings.qbit_password:
        logger.warning("qBit auth login failed for username=%s", username)
        return Response(content="Fails.", status_code=403)
    sid = secrets.token_hex(16)
    _sessions.add(sid)
    logger.info("qBit auth login succeeded for username=%s", username)
    response = Response(content="Ok.")
    response.set_cookie("SID", sid, httponly=True)
    return response


@router.get("/app/version")
async def app_version() -> Response:
    return Response(content="v4.6.0", media_type="text/plain")


@router.get("/app/webapiVersion")
async def app_webapi_version() -> Response:
    return Response(content="2.9.2", media_type="text/plain")


@router.get("/app/preferences")
async def app_preferences() -> dict:
    # Radarr/Sonarr refuse trackerless magnets (which is all we ever hand out,
    # since the infohash is synthetic) unless the client reports DHT enabled.
    return {"save_path": settings.download_path, "web_ui_port": settings.port, "dht": True}


@router.get("/torrents/info")
async def torrents_info(
    category: str | None = Query(default=None),
    db: Database = Depends(get_db),
) -> list[dict]:
    jobs = db.list_jobs(category=category)
    response: list[dict] = []
    for job in jobs:
        release = db.get_release(job.infohash)
        response.append(to_qbit_info(job, release))
    logger.debug("torrents/info category=%s returned %d job(s)", category, len(response))
    return response


@router.get("/torrents/properties")
async def torrents_properties(hash: str, db: Database = Depends(get_db)) -> dict:
    job = db.get_job_by_infohash(hash.lower())
    if not job:
        return {}
    return to_qbit_properties(job, db.get_release(job.infohash))


@router.get("/torrents/files")
async def torrents_files(hash: str, db: Database = Depends(get_db)) -> list[dict]:
    release = db.get_release(hash.lower())
    if not release:
        return []
    filename = f"{release.release_name}.mkv"
    return [{"index": 0, "name": filename, "size": release.size_estimate, "progress": 1.0, "priority": 1}]


@router.get("/torrents/categories")
async def torrents_categories(db: Database = Depends(get_db)) -> dict[str, dict]:
    categories = db.list_categories()
    return {name: {"name": name, "savePath": f"{settings.download_path}/{name}"} for name in categories}


@router.post("/torrents/createCategory")
async def torrents_create_category(
    category: str = Form(),
    db: Database = Depends(get_db),
) -> Response:
    db.ensure_category(category)
    return Response(status_code=200)


@router.post("/torrents/setCategory")
async def torrents_set_category(
    hashes: str = Form(),
    category: str = Form(),
    db: Database = Depends(get_db),
) -> Response:
    db.ensure_category(category)
    for hash_value in [value.strip().lower() for value in hashes.split("|") if value.strip()]:
        db.set_job_category(hash_value, category)
    return Response(status_code=200)


@router.post("/torrents/add")
async def torrents_add(
    urls: str = Form(),
    category: str = Form(default=""),
    manager: DownloadManager = Depends(get_download_manager),
) -> Response:
    infohash = extract_infohash_from_magnet(urls)
    if not infohash:
        logger.warning("torrents/add rejected: could not extract infohash from urls=%r", urls)
        return Response(content="Fails.", status_code=400)
    logger.info("torrents/add infohash=%s category=%s", infohash, category or "default")
    try:
        await manager.create_or_enqueue(infohash=infohash, category=category or "default")
    except ValueError:
        logger.error("torrents/add failed: unknown release for infohash=%s", infohash)
        return Response(content="Fails.", status_code=404)
    logger.info("torrents/add queued for infohash=%s", infohash)
    return Response(content="Ok.")


@router.post("/torrents/delete")
async def torrents_delete(
    hashes: str = Form(),
    deleteFiles: bool = Form(default=False),
    manager: DownloadManager = Depends(get_download_manager),
) -> Response:
    hash_list = [value.strip().lower() for value in hashes.split("|") if value.strip()]
    logger.info("torrents/delete hashes=%s deleteFiles=%s", hash_list, deleteFiles)
    await manager.delete_hashes(hash_list, delete_files=deleteFiles)
    return Response(status_code=200)


@router.post("/torrents/pause")
async def torrents_pause(
    hashes: str = Form(),
    manager: DownloadManager = Depends(get_download_manager),
) -> Response:
    hash_list = [value.strip().lower() for value in hashes.split("|") if value.strip()]
    logger.info("torrents/pause hashes=%s", hash_list)
    await manager.pause_hashes(hash_list)
    return Response(status_code=200)


@router.post("/torrents/resume")
async def torrents_resume(
    hashes: str = Form(),
    manager: DownloadManager = Depends(get_download_manager),
) -> Response:
    hash_list = [value.strip().lower() for value in hashes.split("|") if value.strip()]
    logger.info("torrents/resume hashes=%s", hash_list)
    await manager.resume_hashes(hash_list)
    return Response(status_code=200)


@router.post("/torrents/setShareLimits")
async def torrents_set_share_limits() -> Response:
    return Response(status_code=200)


@router.post("/torrents/topPrio")
async def torrents_top_prio() -> Response:
    return Response(status_code=200)


@router.get("/transfer/info")
async def transfer_info() -> dict:
    return {
        "dl_info_speed": 0,
        "up_info_speed": 0,
        "dl_info_data": 0,
        "up_info_data": 0,
    }
