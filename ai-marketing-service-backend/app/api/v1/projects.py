"""Project CRUD routes."""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.v1.deps import CurrentUser, get_current_user
from app.db.projects import (
    create_project,
    get_project_for_team,
    list_projects,
    update_project,
)
from app.responses import success_response

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_PLATFORMS = {"instagram", "facebook", "tiktok", "linkedin", "twitter"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_404(project_id: str, team_id: str) -> dict[str, Any]:
    project = get_project_for_team(project_id, team_id)
    if not project:
        raise HTTPException(
            status_code=404,
            detail={"message": "Project not found.", "code": "NOT_FOUND"},
        )
    return project


def _public(project: dict[str, Any]) -> dict[str, Any]:
    """Strip DynamoDB internal keys before returning to client."""
    return {k: v for k, v in project.items() if k not in ("pk", "sk")}


# ── List / Create ─────────────────────────────────────────────────────────────

@router.get("")
def list_projects_route(
    status: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """List all projects for the current user's team."""
    items = list_projects(current_user.team_id, status_filter=status)
    return success_response([_public(p) for p in items])


class CreateProjectBody(BaseModel):
    name: str
    description: str | None = None
    platforms: list[str] = []
    website_url: str | None = None
    brand_voice: str | None = None


@router.post("")
def create_project_route(
    body: CreateProjectBody,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Create a new project under the authenticated user's team."""
    invalid = [p for p in body.platforms if p not in _VALID_PLATFORMS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail={"message": f"Invalid platform(s): {invalid}", "code": "INVALID_PLATFORM"},
        )
    project_id = str(uuid.uuid4())
    create_project(
        team_id=current_user.team_id,
        project_id=project_id,
        data={
            "name": body.name,
            "description": body.description,
            "platforms": body.platforms,
            "website_url": body.website_url,
            "brand_voice": body.brand_voice,
            "created_by": current_user.user_id,
        },
    )
    project = get_project_for_team(project_id, current_user.team_id)
    return success_response(_public(project), "Project created")


# ── Get / Update / Delete ─────────────────────────────────────────────────────

@router.get("/{project_id}")
def get_project_route(
    project_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    project = _get_or_404(project_id, current_user.team_id)
    return success_response(_public(project))


class UpdateProjectBody(BaseModel):
    name: str | None = None
    description: str | None = None
    platforms: list[str] | None = None
    website_url: str | None = None
    brand_voice: str | None = None


@router.patch("/{project_id}")
def update_project_route(
    project_id: str,
    body: UpdateProjectBody,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _get_or_404(project_id, current_user.team_id)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=422, detail={"message": "No fields to update.", "code": "NO_UPDATES"})
    if "platforms" in updates:
        invalid = [p for p in updates["platforms"] if p not in _VALID_PLATFORMS]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail={"message": f"Invalid platform(s): {invalid}", "code": "INVALID_PLATFORM"},
            )
    updated = update_project(project_id, current_user.team_id, updates)
    return success_response(_public(updated), "Project updated")


# ── Status transitions ────────────────────────────────────────────────────────

@router.post("/{project_id}/activate")
def activate_project(
    project_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Transition a draft/archived project to active."""
    project = _get_or_404(project_id, current_user.team_id)
    if project.get("status") == "active":
        return success_response(_public(project), "Already active")
    updated = update_project(project_id, current_user.team_id, {"status": "active"})
    return success_response(_public(updated), "Project activated")


@router.post("/{project_id}/archive")
def archive_project(
    project_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Transition a project to archived status."""
    project = _get_or_404(project_id, current_user.team_id)
    if project.get("status") == "archived":
        return success_response(_public(project), "Already archived")
    updated = update_project(project_id, current_user.team_id, {"status": "archived"})
    return success_response(_public(updated), "Project archived")
