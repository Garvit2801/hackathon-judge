import httpx
import json
import re

WEIGHTS = {
    "innovation": 0.25,
    "technical_execution": 0.30,
    "completeness": 0.25,
    "ps_alignment": 0.15,
    "code_quality": 0.05,
}

# Models tried in order — first one that succeeds is used.
# Update the top of this list when Google releases newer stable models.
GEMINI_MODELS = [
    "gemini-2.5-flash",       # Best price-performance, stable (2025+)
    "gemini-2.5-flash-lite",  # Lighter fallback, same family
    "gemini-2.0-flash",       # Previous stable generation fallback
]


def build_prompt(repo_data: dict, problem_statement: str, project_description: str = "") -> str:
    readme = (repo_data.get("readme") or "").strip()
    has_readme = len(readme) > 60

    code_sections = ""
    for cf in repo_data.get("code_files", []):
        code_sections += f"\n\n=== FILE: {cf['path']} ===\n{cf['content'][:2500]}"

    deps_section = ""
    for fname, content in (repo_data.get("dependencies") or {}).items():
        deps_section += f"\n{fname}:\n{content[:1500]}\n"

    commits = "\n".join(
        f"- {msg}" for msg in (repo_data.get("commit_messages") or [])[:12] if msg.strip()
    )

    file_tree = "\n".join((repo_data.get("file_structure") or [])[:60])
    meta = repo_data.get("repo_metadata", {})

    if has_readme:
        mode_instruction = (
            "A README is present. Use it as the primary intent signal, "
            "but verify claims against actual code."
        )
    elif project_description:
        mode_instruction = (
            "No README found. A one-line description was provided by the team: "
            f'"{project_description}". '
            "Use code, file structure, commits, and dependencies as primary evidence."
        )
    else:
        mode_instruction = (
            "No README or description found. "
            "Infer the project purpose entirely from: "
            "source code logic, function/class names, file/folder names, "
            "imported libraries, and commit messages."
        )

    prompt = f"""You are a senior software engineer acting as a hackathon judge.
Your task is to evaluate a GitHub project submission against a specific problem statement.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROBLEM STATEMENT (defined by organizer):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{problem_statement}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ANALYSIS MODE:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{mode_instruction}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REPOSITORY METADATA:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Description: {meta.get('description') or 'None provided'}
Primary Language: {meta.get('language') or 'Unknown'}
Topics: {', '.join(meta.get('topics', [])) or 'None'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
README:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{readme if has_readme else '[No README found]'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE STRUCTURE (first 60 paths):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{file_tree or '[Empty repository]'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DEPENDENCIES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{deps_section or '[No dependency files found]'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RECENT COMMIT MESSAGES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{commits or '[No commits found]'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SOURCE CODE FILES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{code_sections or '[No source code accessible]'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCORING RUBRIC — Score each from 1 to 10:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. INNOVATION & CREATIVITY [weight: 25%]
   10: Groundbreaking idea, novel approach, solves real problem uniquely
   7-8: Creative idea with solid differentiation from common solutions
   5-6: Decent idea, somewhat original but not particularly novel
   3-4: Generic or tutorial-level implementation
   1-2: Boilerplate or copied project with no original thinking

2. TECHNICAL EXECUTION [weight: 30%]
   10: Excellent architecture, clean code, sophisticated technical components
   7-8: Well-structured, appropriate tech stack, good engineering practices
   5-6: Functional code but with structural issues or poor practices
   3-4: Messy or incomplete code, wrong tool choices
   1-2: Broken, stub-only, or extremely minimal code

3. PROJECT COMPLETENESS [weight: 25%]
   10: Fully working MVP, all core features implemented, ready to demo
   7-8: Most features work, minor gaps, clear entry points exist
   5-6: Partially complete, some features missing or stubbed out
   3-4: Mostly scaffolding with little real implementation
   1-2: Near-empty, no runnable code

4. PROBLEM STATEMENT ALIGNMENT [weight: 15%]
   10: Solution directly and completely addresses all requirements
   7-8: Strong alignment, covers most requirements with clear connection
   5-6: Partial alignment, related to the problem but gaps exist
   3-4: Loosely connected, misses core requirements
   1-2: No meaningful connection to the problem statement

5. CODE QUALITY & BEST PRACTICES [weight: 5%]
   10: Tests present, linting configured, clean secrets handling, license
   7-8: Good practices followed, minor gaps
   5-6: Some good practices, some neglected
   3-4: Poor practices, potential issues
   1-2: No practices followed, possible hardcoded secrets

CRITICAL RULES:
- Base scores on CODE EVIDENCE, not README claims
- If README is missing, infer from: file names, function names, imports, commits
- Empty function bodies / 'pass' / 'TODO' = incomplete = lower completeness score
- A project with 1 commit at midnight = likely rushed = reflect in scores
- Never give 10/10 unless truly exceptional

Return ONLY valid JSON, no markdown, no explanation outside the JSON:

{{
  "inferred_purpose": "One paragraph describing what this project actually does based on code evidence",
  "confidence": "high|medium|low",
  "analysis_mode": "readme_assisted|code_only|description_assisted",
  "scores": {{
    "innovation": {{
      "score": <integer 1-10>,
      "reasoning": "<2-3 sentences citing specific evidence from the code/repo>"
    }},
    "technical_execution": {{
      "score": <integer 1-10>,
      "reasoning": "<2-3 sentences citing specific evidence>"
    }},
    "completeness": {{
      "score": <integer 1-10>,
      "reasoning": "<2-3 sentences citing specific evidence>"
    }},
    "ps_alignment": {{
      "score": <integer 1-10>,
      "reasoning": "<2-3 sentences citing specific evidence>"
    }},
    "code_quality": {{
      "score": <integer 1-10>,
      "reasoning": "<2-3 sentences citing specific evidence>"
    }}
  }},
  "overall_feedback": "<3-4 sentences of holistic evaluation>",
  "strengths": ["<specific strength 1>", "<specific strength 2>", "<specific strength 3>"],
  "weaknesses": ["<specific weakness 1>", "<specific weakness 2>"]
}}"""

    return prompt




def _extract_json(text: str) -> str:
    """
    Pull the JSON object out of a Gemini response.
    Handles: raw JSON, ```json blocks, partial markdown fences.
    """
    text = text.strip()

    # Strip leading markdown fence if present (even without closing ```)
    if text.startswith("```"):
        # Remove opening fence line
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        # Remove closing fence if present
        text = re.sub(r"\s*```\s*$", "", text)
        text = text.strip()

    # Find the outermost { ... } block
    start = text.find("{")
    if start == -1:
        return text  # let caller handle parse error

    # Walk to find matching closing brace
    depth = 0
    end = -1
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
        if not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break

    if end != -1:
        return text[start : end + 1]

    # Closing brace not found — return from start to end (truncated JSON)
    return text[start:]


def _repair_truncated_json(text: str) -> str:
    """
    When Gemini hits MAX_TOKENS the JSON is cut mid-stream.
    Strategy:
      1. Extract whatever JSON object we have so far.
      2. Close any open string with a safe placeholder.
      3. Close any unclosed arrays and objects.
    This produces parseable (if incomplete) JSON so scoring can continue
    with whatever fields were completed before the cutoff.
    """
    raw = _extract_json(text)

    # Close any open string — find last unescaped quote situation
    # Simple heuristic: count unescaped quotes; odd count = open string
    in_string = False
    escape_next = False
    for ch in raw:
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string

    suffix = ""
    if in_string:
        suffix += " [truncated]\""   # close the open string

    # Count unclosed braces and brackets
    depth_brace = 0
    depth_bracket = 0
    in_str2 = False
    esc2 = False
    for ch in (raw + suffix):
        if esc2:
            esc2 = False
            continue
        if ch == "\\":
            esc2 = True
            continue
        if ch == '"':
            in_str2 = not in_str2
        if not in_str2:
            if ch == "{":
                depth_brace += 1
            elif ch == "}":
                depth_brace = max(0, depth_brace - 1)
            elif ch == "[":
                depth_bracket += 1
            elif ch == "]":
                depth_bracket = max(0, depth_bracket - 1)

    # Close open arrays first, then objects
    suffix += "]" * depth_bracket
    suffix += "}" * depth_brace

    return raw + suffix


async def analyze_with_gemini(
    repo_data: dict,
    problem_statement: str,
    api_key: str,
    project_description: str = "",
) -> dict:
    prompt = build_prompt(repo_data, problem_statement, project_description)

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.25,
            "maxOutputTokens": 8192,   # raised: complex projects need more tokens
            "topP": 0.8,
        },
    }

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
    last_error = None

    async with httpx.AsyncClient(timeout=120.0) as client:
        for model in GEMINI_MODELS:
            url = f"{BASE_URL}/{model}:generateContent?key={api_key}"
            response = await client.post(url, json=payload)

            if response.status_code == 404:
                last_error = f"Model '{model}' not available (404)"
                continue

            if response.status_code == 429:
                raise Exception(
                    "Rate limit hit. Your Gemini API key has exceeded its quota. "
                    "Wait a minute and try again, or use a different key."
                )

            if response.status_code == 400:
                err = response.json().get("error", {})
                raise Exception(
                    f"Invalid request ({model}): {err.get('message', 'Bad request')}"
                )

            if response.status_code != 200:
                err = response.json().get("error", {})
                last_error = (
                    f"Gemini API error {response.status_code} on '{model}': "
                    f"{err.get('message', 'Unknown error')}"
                )
                continue

            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                last_error = f"Model '{model}' returned no candidates"
                continue

            # Check finish reason — MAX_TOKENS means response was cut off
            finish_reason = (
                candidates[0].get("finishReason")
                or candidates[0].get("finish_reason", "")
            )
            text = candidates[0]["content"]["parts"][0]["text"]

            if finish_reason == "MAX_TOKENS":
                # Response was truncated — try to repair before giving up
                text = _repair_truncated_json(text)

            break  # success — stop trying models

        else:
            raise Exception(
                f"All Gemini models failed. Last error: {last_error}. "
                f"Check your API key is valid and has access to the Gemini API."
            )

        # ── Extract JSON from response ────────────────────────────────────────
        json_str = _extract_json(text)

        try:
            result = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise Exception(
                f"Failed to parse Gemini JSON response: {e}\n"
                f"Raw (first 600 chars): {text[:600]}"
            )

        # Validate and clamp scores
        scores = result.get("scores", {})
        for key in ["innovation", "technical_execution", "completeness", "ps_alignment", "code_quality"]:
            if key not in scores:
                scores[key] = {"score": 5, "reasoning": "Score not provided by model."}
            scores[key]["score"] = max(1, min(10, float(scores[key].get("score", 5))))

        # Calculate weighted total
        total = sum(
            scores[k]["score"] * WEIGHTS[k]
            for k in WEIGHTS
            if k in scores
        )
        result["total_score"] = round(total, 2)
        result["scores"] = scores

        return result