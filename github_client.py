import httpx
import base64
import re

PRIORITY_ENTRY_POINTS = [
    "main.py", "app.py", "server.py", "run.py",
    "index.js", "app.js", "server.js",
    "index.ts", "app.ts",
    "main.go", "main.java", "Main.java",
    "App.jsx", "App.tsx", "index.jsx", "index.tsx",
]

PRIORITY_DIRS = {
    "src", "api", "routes", "controllers",
    "services", "models", "core", "lib", "app",
}

SKIP_DIRS = {
    "node_modules", ".git", "dist", "build",
    "__pycache__", ".next", "venv", "env",
    ".venv", "coverage", ".cache", "tmp", "vendor",
}

SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".svg", ".pdf", ".zip", ".tar", ".gz",
    ".min.js", ".min.css", ".map", ".lock",
    ".woff", ".woff2", ".ttf", ".eot", ".bin",
}

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".go", ".java", ".rs", ".php", ".rb",
    ".cpp", ".c", ".cs", ".swift", ".kt",
    ".vue", ".html", ".css", ".scss", ".sh",
    ".yaml", ".yml", ".toml", ".env.example",
}


def parse_github_url(url: str):
    url = url.rstrip("/").strip()
    # Strip trailing .git
    url = re.sub(r"\.git$", "", url)
    match = re.search(r"github\.com/([^/\s]+)/([^/\s]+)", url)
    if match:
        return match.group(1), match.group(2)
    raise ValueError(
        f"Could not parse GitHub URL: {url}. "
        "Expected format: https://github.com/owner/repo"
    )


async def get_repo_data(github_url: str) -> dict:
    owner, repo = parse_github_url(github_url)
    base = f"https://api.github.com/repos/{owner}/{repo}"

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "HackathonJudgeAgent/1.0",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:

        # ── 1. Repo metadata ─────────────────────────────────────────────────
        meta_resp = await client.get(base, headers=headers)

        if meta_resp.status_code == 404:
            raise ValueError(
                f"Repository '{owner}/{repo}' not found. "
                "Check the URL and make sure the repo is public."
            )
        if meta_resp.status_code == 403:
            raise ValueError(
                "GitHub API rate limit reached (60 req/hr for unauthenticated). "
                "Wait a few minutes and try again."
            )
        if meta_resp.status_code != 200:
            raise ValueError(
                f"GitHub API error {meta_resp.status_code} fetching repo metadata."
            )

        meta = meta_resp.json()
        default_branch = meta.get("default_branch") or "main"
        is_empty = meta.get("size", 1) == 0

        if is_empty:
            raise ValueError(
                f"Repository '{owner}/{repo}' appears to be empty (size=0). "
                "There is no code to evaluate."
            )

        # ── 2. File tree — use real branch name, with fallbacks ──────────────
        all_files = await _get_file_tree(client, base, headers, default_branch)

        # If tree is still empty after all attempts, try listing root contents
        if not all_files:
            all_files = await _get_root_contents(client, base, headers, default_branch)

        # ── 3. README ────────────────────────────────────────────────────────
        readme = await _get_readme(client, base, headers, all_files)

        # ── 4. Commits ───────────────────────────────────────────────────────
        commits_resp = await client.get(
            f"{base}/commits?sha={default_branch}&per_page=25",
            headers=headers,
        )
        commit_messages = []
        if commits_resp.status_code == 200:
            raw = commits_resp.json()
            if isinstance(raw, list):
                commit_messages = [
                    c.get("commit", {}).get("message", "").split("\n")[0]
                    for c in raw[:20]
                    if c.get("commit", {}).get("message", "").strip()
                ]

        # ── 5. Dependencies ──────────────────────────────────────────────────
        deps = await _get_dependencies(client, base, headers, all_files)

        # ── 6. Key source files ──────────────────────────────────────────────
        code_files = await _get_key_files(client, base, headers, all_files)

    return {
        "owner": owner,
        "repo": repo,
        "default_branch": default_branch,
        "repo_metadata": {
            "description": meta.get("description") or "",
            "language":    meta.get("language") or "Unknown",
            "topics":      meta.get("topics", []),
            "updated_at":  meta.get("updated_at", ""),
            "stars":       meta.get("stargazers_count", 0),
        },
        "file_structure":  all_files[:120],
        "readme":          readme,
        "commit_messages": commit_messages,
        "dependencies":    deps,
        "code_files":      code_files,
    }


async def _get_file_tree(client, base, headers, default_branch) -> list:
    """
    Try fetching the recursive file tree using multiple ref strategies.
    Returns a flat list of file paths, or [] if all attempts fail.
    """
    # Try branches in order: actual default → main → master
    branches_to_try = list(dict.fromkeys([default_branch, "main", "master"]))

    for branch in branches_to_try:
        url = f"{base}/git/trees/{branch}?recursive=1"
        resp = await client.get(url, headers=headers)

        if resp.status_code != 200:
            continue

        data = resp.json()
        files = [
            f["path"] for f in data.get("tree", [])
            if f.get("type") == "blob"
        ]

        if files:
            # Handle truncated trees (GitHub limits to 100k items)
            if data.get("truncated"):
                # Fetch root non-recursively and walk manually (best effort)
                files = await _expand_truncated_tree(client, base, headers, branch)
            return files

    return []


async def _expand_truncated_tree(client, base, headers, branch) -> list:
    """
    For truncated repos, fetch top-level dirs individually and combine.
    """
    all_files = []
    # First get root
    root_resp = await client.get(
        f"{base}/git/trees/{branch}", headers=headers
    )
    if root_resp.status_code != 200:
        return all_files

    root_items = root_resp.json().get("tree", [])
    all_files = [f["path"] for f in root_items if f.get("type") == "blob"]

    # Walk one level of subdirectories
    subdirs = [f for f in root_items if f.get("type") == "tree"][:15]
    for subdir in subdirs:
        sha = subdir.get("sha", "")
        if not sha:
            continue
        sub_resp = await client.get(
            f"{base}/git/trees/{sha}?recursive=1", headers=headers
        )
        if sub_resp.status_code == 200:
            sub_data = sub_resp.json()
            for f in sub_data.get("tree", []):
                if f.get("type") == "blob":
                    all_files.append(f"{subdir['path']}/{f['path']}")

    return all_files


async def _get_root_contents(client, base, headers, branch) -> list:
    """
    Fallback: use the /contents API to list root-level files.
    Less complete than the tree API but always works.
    """
    resp = await client.get(
        f"{base}/contents/?ref={branch}", headers=headers
    )
    if resp.status_code != 200:
        return []

    items = resp.json()
    if not isinstance(items, list):
        return []

    files = []
    for item in items:
        if item.get("type") == "file":
            files.append(item["path"])
        elif item.get("type") == "dir":
            # One level deeper
            sub_resp = await client.get(
                f"{base}/contents/{item['path']}?ref={branch}", headers=headers
            )
            if sub_resp.status_code == 200:
                sub_items = sub_resp.json()
                if isinstance(sub_items, list):
                    for s in sub_items:
                        if s.get("type") == "file":
                            files.append(s["path"])

    return files


async def _get_readme(client, base, headers, file_paths: list) -> str:
    # First try the dedicated GitHub README endpoint (always works)
    try:
        r = await client.get(f"{base}/readme", headers=headers)
        if r.status_code == 200:
            data = r.json()
            raw = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
            return raw[:8000]
    except Exception:
        pass

    # Fallback: find in file tree
    candidates = [
        p for p in file_paths
        if p.lower() in {"readme.md", "readme.txt", "readme.rst", "readme"}
    ]
    for path in candidates:
        try:
            r = await client.get(f"{base}/contents/{path}", headers=headers)
            if r.status_code == 200:
                data = r.json()
                raw = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
                return raw[:8000]
        except Exception:
            continue

    return ""


async def _get_dependencies(client, base, headers, file_paths: list) -> dict:
    dep_files = [
        "package.json", "requirements.txt", "pyproject.toml",
        "Pipfile", "go.mod", "pom.xml", "build.gradle",
        "Cargo.toml", "composer.json", "Gemfile",
    ]
    result = {}
    paths_lower = {p.lower(): p for p in file_paths}

    for dep_file in dep_files:
        actual_path = paths_lower.get(dep_file.lower())
        if not actual_path:
            continue
        try:
            r = await client.get(f"{base}/contents/{actual_path}", headers=headers)
            if r.status_code == 200:
                data = r.json()
                content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
                result[dep_file] = content[:3000]
        except Exception:
            continue

    return result


async def _get_key_files(client, base, headers, file_paths: list) -> list:
    selected = []

    def should_skip(path: str) -> bool:
        parts = path.split("/")
        if any(p in SKIP_DIRS for p in parts):
            return True
        lower = path.lower()
        for ext in SKIP_EXTENSIONS:
            if lower.endswith(ext):
                return True
        return False

    def get_ext(path: str) -> str:
        return "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""

    # Priority 1 — known entry-point filenames
    for ep in PRIORITY_ENTRY_POINTS:
        for path in file_paths:
            basename = path.split("/")[-1]
            if basename == ep and not should_skip(path) and path not in selected:
                selected.append(path)
                break
        if len(selected) >= 3:
            break

    # Priority 2 — files in key directories
    for path in file_paths:
        if len(selected) >= 6:
            break
        if should_skip(path) or path in selected:
            continue
        top_dir = path.split("/")[0] if "/" in path else ""
        if top_dir in PRIORITY_DIRS and get_ext(path) in CODE_EXTENSIONS:
            selected.append(path)

    # Priority 3 — any remaining code files
    for path in file_paths:
        if len(selected) >= 9:
            break
        if should_skip(path) or path in selected:
            continue
        if get_ext(path) in CODE_EXTENSIONS:
            selected.append(path)

    # Fetch file contents concurrently (up to 9 files)
    import asyncio

    async def fetch_one(path: str):
        try:
            r = await client.get(f"{base}/contents/{path}", headers=headers)
            if r.status_code == 200:
                data = r.json()
                content = base64.b64decode(data.get("content", "")).decode(
                    "utf-8", errors="replace"
                )
                return {"path": path, "content": content[:4000]}
        except Exception:
            pass
        return None

    tasks = [fetch_one(p) for p in selected[:9]]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]