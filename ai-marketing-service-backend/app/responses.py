"""Shared response helpers."""

from typing import Any

from fastapi.responses import JSONResponse


def success_response(data: Any, message: str | None = None) -> dict[str, Any]:
    """Wrap data in { status: "success", data } envelope."""
    result: dict[str, Any] = {"status": "success", "data": data}
    if message:
        result["message"] = message
    return result


def accepted_response(job_id: str, message: str = "Job queued") -> JSONResponse:
    """Return 202 Accepted for async background jobs."""
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "message": message, "data": {"jobId": job_id}},
    )
