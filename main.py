import json
import uuid
import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from models import SubmissionRequest
from judge import judge_project

# ── App setup ────────────────────────────────────────────────────────────────
app = FastAPI(title="Hackathon Judge Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Storage ───────────────────────────────────────────────────────────────────
#
#   TWO separate stores, intentionally:
#
#   submissions  →  persisted to disk (submissions.json)
#                   NEVER contains gemini_api_key
#
#   _key_vault   →  memory-only dict  { submission_id: api_key }
#                   populated on /api/submit
#                   wiped immediately after judging completes or errors
#                   never logged, never serialised, never returned in any response
#
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
SUBMISSIONS_FILE = DATA_DIR / "submissions.json"

# Memory-only key vault — intentionally not persisted anywhere
_key_vault: dict[str, str] = {}


def load_submissions() -> dict:
    if SUBMISSIONS_FILE.exists():
        try:
            with open(SUBMISSIONS_FILE) as f:
                data = json.load(f)
            # Safety net: strip any key that may have been written by an older version
            for record in data.values():
                record.pop("gemini_api_key", None)
            return data
        except Exception:
            return {}
    return {}


def save_submissions(store: dict) -> None:
    # Double-safety: assert no key leaks to disk under any circumstance
    clean = {
        sid: {k: v for k, v in rec.items() if k != "gemini_api_key"}
        for sid, rec in store.items()
    }
    with open(SUBMISSIONS_FILE, "w") as f:
        json.dump(clean, f, indent=2, default=str)


def _wipe_key(sub_id: str) -> None:
    """Overwrite then delete the key from the vault. Called after judging."""
    if sub_id in _key_vault:
        # Overwrite with zeros before deleting — reduces window for memory inspection
        _key_vault[sub_id] = "0" * len(_key_vault[sub_id])
        del _key_vault[sub_id]


submissions: dict = load_submissions()


# ── API Routes ────────────────────────────────────────────────────────────────

@app.post("/api/submit")
async def submit_project(data: SubmissionRequest):
    """
    Accept a submission. The API key is split from all other data immediately:
      - Key  → memory-only _key_vault[sub_id]
      - Rest → submissions dict (persisted to disk, key-free)
    """
    sub_id = str(uuid.uuid4())[:8].upper()

    # ── Store key in vault only (never touches disk) ──────────────
    _key_vault[sub_id] = data.gemini_api_key

    # ── Store everything else (no key field at all) ───────────────
    record = {
        "id":                  sub_id,
        "status":              "pending",
        "created_at":          datetime.utcnow().isoformat() + "Z",
        "team_name":           data.team_name,
        "project_title":       data.project_title,
        "member_names":        data.member_names,
        "contact_email":       data.contact_email,
        "track":               data.track,
        "problem_statement":   data.problem_statement,
        "github_url":          data.github_url,
        "project_description": data.project_description or "",
        "scores":              None,
        "total_score":         None,
        "feedback":            None,
        # gemini_api_key intentionally absent
    }

    submissions[sub_id] = record
    save_submissions(submissions)

    return {"submission_id": sub_id, "status": "pending"}


@app.get("/api/judge/{submission_id}")
async def judge_stream(submission_id: str):
    """
    SSE endpoint — streams live judging events.
    Pulls the key from the in-memory vault, uses it once, then wipes it.
    """
    submission_id = submission_id.upper()

    if submission_id not in submissions:
        raise HTTPException(status_code=404, detail="Submission not found")

    submission = submissions[submission_id]

    # ── Already scored — replay cached result, no key needed ─────
    if submission["status"] == "scored":
        async def replay():
            payload = {
                "type":    "already_scored",
                "message": "This submission has already been evaluated.",
                "data": {
                    "total_score": submission["total_score"],
                    "scores":      submission["scores"],
                    "feedback":    submission["feedback"],
                },
            }
            yield f"data: {json.dumps(payload)}\n\n"
        return StreamingResponse(replay(), media_type="text/event-stream")

    # ── Key must be in vault to proceed ──────────────────────────
    if submission_id not in _key_vault:
        # Server was restarted after submit but before judging — key is gone
        raise HTTPException(
            status_code=410,
            detail=(
                "API key no longer available (server may have restarted). "
                "Please re-submit your project."
            ),
        )

    # Pull key into a local variable — wipe vault entry immediately after judging
    api_key = _key_vault[submission_id]

    async def event_generator():
        submissions[submission_id]["status"] = "judging"
        save_submissions(submissions)

        try:
            # Pass key directly — it never re-enters the submissions dict
            async for event in judge_project(submission, api_key):
                # Safety check: assert the key is never accidentally in event JSON
                assert data_is_clean(event, api_key), \
                    "SECURITY: API key detected in SSE event payload — aborted"
                yield f"data: {json.dumps(event)}\n\n"
                await asyncio.sleep(0.01)

                if event["type"] == "error":
                    submissions[submission_id]["status"] = "error"
                    save_submissions(submissions)
                    _wipe_key(submission_id)
                    return

            submissions[submission_id]["status"] = "scored"
            save_submissions(submissions)

        except Exception as e:
            submissions[submission_id]["status"] = "error"
            save_submissions(submissions)
            err = {"type": "error", "message": f"Internal error: {str(e)}"}
            yield f"data: {json.dumps(err)}\n\n"
        finally:
            # Always wipe — success, error, or exception
            _wipe_key(submission_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering":"no",
            "Connection":       "keep-alive",
        },
    )


def data_is_clean(event: dict, key: str) -> bool:
    """Return False if the API key appears anywhere in the serialised event."""
    if not key or len(key) < 8:
        return True
    return key not in json.dumps(event)


@app.get("/api/submissions")
async def get_all_submissions():
    """Leaderboard — returns only score-relevant fields, never any key."""
    results = []
    for sub in submissions.values():
        if sub.get("total_score") is not None:
            results.append({
                "id":            sub["id"],
                "team_name":     sub["team_name"],
                "project_title": sub["project_title"],
                "track":         sub["track"],
                "total_score":   sub["total_score"],
                "scores":        sub["scores"],
                "status":        sub["status"],
                "created_at":    sub["created_at"],
                "feedback":      sub.get("feedback", {}),
            })
    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results


@app.get("/api/submission/{submission_id}")
async def get_submission(submission_id: str):
    """Single submission detail — key is structurally absent, not just filtered."""
    submission_id = submission_id.upper()
    if submission_id not in submissions:
        raise HTTPException(status_code=404, detail="Submission not found")
    # submissions dict never contains gemini_api_key — safe to return as-is
    return submissions[submission_id]


@app.get("/api/health")
async def health():
    return {
        "status":      "ok",
        "submissions": len(submissions),
        "keys_in_vault": len(_key_vault),   # should be 0 when no judging is active
    }


# ── Serve frontend ────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory="static", html=True), name="static")