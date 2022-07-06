"""
Router dedicated to Dockerflow APIs
"""
from typing import Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.app import environment
from src.jbi.services import jbi_service_health_map

api_router = APIRouter(tags=["Monitor"])


def heartbeat(request: Request, settings: environment.Settings):
    """Return status of backing services, as required by Dockerflow."""
    data: Dict = {**jbi_service_health_map()}
    status_code = 200
    for _, health in data.items():
        if not health.get("up"):
            status_code = 503

    return JSONResponse(content=data, status_code=status_code)


@api_router.get("/__heartbeat__")
def get_heartbeat(
    request: Request,
    settings: environment.Settings = Depends(environment.get_settings),
):
    """Dockerflow API for heartbeat: GET"""
    return heartbeat(request, settings)


@api_router.head("/__heartbeat__")
def head_heartbeat(
    request: Request,
    settings: environment.Settings = Depends(environment.get_settings),
):
    """Dockerflow API for heartbeat: HEAD"""
    return heartbeat(request, settings)


def lbheartbeat(request: Request):
    """Return response when application is running, as required by Dockerflow."""
    return {"status": "OK"}


@api_router.get("/__lbheartbeat__")
def get_lbheartbeat(request: Request):
    """Dockerflow API for lbheartbeat: GET"""
    return lbheartbeat(request)


@api_router.head("/__lbheartbeat__")
def head_lbheartbeat(request: Request):
    """Dockerflow API for lbheartbeat: HEAD"""
    return lbheartbeat(request)


@api_router.get("/__version__")
def version():
    """Return version.json, as required by Dockerflow."""
    return environment.get_version()
