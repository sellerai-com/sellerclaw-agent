"""Local routes the ``sellerclaw-ui`` plugin calls about cron (``Bearer {hooks_token}``, like media)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from sellerclaw_agent.cloud.cron_runs_backfill import MAX_TARGETED_JOB_IDS, get_cron_runs_backfill
from sellerclaw_agent.server.media_upload import require_hooks_token

router = APIRouter(
    prefix="/internal/openclaw/cron",
    dependencies=[Depends(require_hooks_token)],
)


class BackfillRunsRequest(BaseModel):
    #: Only these jobs (right after one of them finished); omitted means every mirrored job.
    job_ids: list[str] | None = Field(default=None, max_length=MAX_TARGETED_JOB_IDS)


@router.post("/backfill-runs", status_code=202)
async def backfill_runs(body: BackfillRunsRequest | None = Body(default=None)) -> JSONResponse:  # noqa: B008
    """Backfill recent run history into the cloud, in the background (see ``cron_runs_backfill``)."""
    get_cron_runs_backfill().request(body.job_ids if body is not None else None)
    return JSONResponse({"accepted": True}, status_code=202)
