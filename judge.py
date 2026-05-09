import asyncio
from typing import AsyncGenerator
from github_client import get_repo_data
from gemini_client import analyze_with_gemini, WEIGHTS

PARAMETERS = [
    ("innovation",         "Innovation & Creativity",       25, "#7F77DD"),
    ("technical_execution","Technical Execution",            30, "#1D9E75"),
    ("completeness",       "Project Completeness",           25, "#D85A30"),
    ("ps_alignment",       "Problem Statement Alignment",    15, "#185FA5"),
    ("code_quality",       "Code Quality & Best Practices",   5, "#854F0B"),
]


async def judge_project(submission: dict, api_key: str) -> AsyncGenerator[dict, None]:
    """
    Async generator that yields SSE-compatible event dicts.
    api_key is passed as a separate argument — never stored in submission dict.
    Stages: start -> github_fetch -> repo_info -> gemini_start -> scores -> complete
    """

    # Stage 1: Start
    yield _evt("status", message="Initialising evaluation pipeline...", progress=5)
    await asyncio.sleep(0.3)

    # Stage 2: Fetch GitHub repo
    yield _evt("status", message="Connecting to GitHub and fetching repository...", progress=15)

    try:
        repo_data = await get_repo_data(submission["github_url"])
    except Exception as e:
        yield _evt("error", message=f"GitHub fetch failed: {str(e)}")
        return

    readme_len   = len(repo_data.get("readme") or "")
    code_count   = len(repo_data.get("code_files") or [])
    file_count   = len(repo_data.get("file_structure") or [])
    has_readme   = readme_len > 60
    has_deps     = bool(repo_data.get("dependencies"))
    commit_count = len(repo_data.get("commit_messages") or [])
    language     = repo_data.get("repo_metadata", {}).get("language") or "Unknown"

    if has_readme:
        mode_label = "README-assisted mode"
    elif submission.get("project_description", "").strip():
        mode_label = "Description-assisted mode"
    else:
        mode_label = "Code-only mode (no README)"

    yield _evt(
        "repo_info",
        message=f"Repository fetched - {mode_label}",
        progress=38,
        data={
            "has_readme":   has_readme,
            "readme_words": len((repo_data.get("readme") or "").split()),
            "code_files":   code_count,
            "total_files":  file_count,
            "language":     language,
            "has_deps":     has_deps,
            "commits":      commit_count,
            "mode":         mode_label,
        },
    )
    await asyncio.sleep(0.4)

    # Stage 3: Gemini analysis — api_key used here only, never stored
    yield _evt("status", message="Sending project to Gemini for deep analysis...", progress=50)

    try:
        result = await analyze_with_gemini(
            repo_data,
            submission["problem_statement"],
            api_key,                              # passed directly, not from submission
            submission.get("project_description", ""),
        )
    except Exception as e:
        yield _evt("error", message=f"Gemini analysis failed: {str(e)}")
        return
    finally:
        # Overwrite local reference immediately after use
        api_key = None  # noqa: F841

    yield _evt("status", message="Analysis complete - computing scores...", progress=62)
    await asyncio.sleep(0.4)

    # Stage 4: Stream individual scores
    scores   = result.get("scores", {})
    progress = 64

    for key, label, weight, color in PARAMETERS:
        score_obj = scores.get(key, {})
        raw_score = float(score_obj.get("score", 0))
        reasoning = score_obj.get("reasoning", "No reasoning provided.")
        weighted  = round(raw_score * (weight / 100), 3)

        yield _evt(
            "score",
            message=f"Scored: {label}",
            progress=progress,
            data={
                "key":       key,
                "label":     label,
                "weight":    weight,
                "color":     color,
                "score":     raw_score,
                "weighted":  weighted,
                "reasoning": reasoning,
            },
        )
        progress += 6
        await asyncio.sleep(1.0)   # deliberate pause for live reveal effect

    # Stage 5: Final result
    final = {
        "total_score":      result["total_score"],
        "inferred_purpose": result.get("inferred_purpose", ""),
        "confidence":       result.get("confidence", "medium"),
        "analysis_mode":    result.get("analysis_mode", "code_only"),
        "overall_feedback": result.get("overall_feedback", ""),
        "strengths":        result.get("strengths", []),
        "weaknesses":       result.get("weaknesses", []),
        "scores":           scores,
    }

    # Persist scores into the submission dict (no key here)
    submission["scores"]      = scores
    submission["total_score"] = result["total_score"]
    submission["feedback"]    = {
        "overall":          result.get("overall_feedback", ""),
        "strengths":        result.get("strengths", []),
        "weaknesses":       result.get("weaknesses", []),
        "inferred_purpose": result.get("inferred_purpose", ""),
        "confidence":       result.get("confidence", "medium"),
        "analysis_mode":    result.get("analysis_mode", "code_only"),
    }

    yield _evt("complete", message="Evaluation complete!", progress=100, data=final)


def _evt(event_type: str, message: str = "", progress: int = 0, data: dict = None) -> dict:
    payload = {"type": event_type, "message": message, "progress": progress}
    if data:
        payload["data"] = data
    return payload