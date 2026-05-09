import httpx
import base64
import re
from typing import Optional

PRIORITY_ENTRY_POINTS = [
    "main.py", "app.py", "server.py", "run.py",
    "index.js", "app.js", "server.js",
    "index.ts", "app.ts",
    "main.go", "main.java", "Main.java",
    "App.jsx", "App.tsx", "index.jsx", "index.tsx",
]

PRIORITY_DIRS = [
    "src", "api", "routes", "controllers",
    "services", "models", "core", "lib", "app",
]

SKIP_DIRS = {
    "node_modules", ".git", "dist", "build",
    "__pycache__", ".next", "venv", "env",
    ".venv", "coverage", ".cache", "tmp",
}

SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".svg", ".pdf", ".zip", ".tar", ".gz",
    ".min.js", ".min.css", ".map", ".lock",
    ".woff", ".woff2", ".ttf", ".eot",
}

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".go", ".java", ".rs", ".php", ".rb",
    ".cpp", ".c", ".cs", ".swift", ".kt",
    ".vue", ".html", ".css", ".scss",
}


def parse_github_url(url: str):
    url = url.rstrip("/").strip()
    patterns = [
        r"github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?$",
        r"github\.com/([^/\s]+)/([^/\s]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            repo = match.group(2)
            repo = repo.replace(".git", "")
            return match.group(1), repo
    raise ValueError(f"Invalid GitHub URL: {url}")


async def get_repo_data(github_url: str) -> dict:
    owner, repo = parse_github_url(github_url)

    async with httpx.AsyncClient(timeout=45.0) as client:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "HackathonJudgeAgent/1.0",
        }
        base = f"https://api.github.com/repos/{owner}/{repo}"

        # Repo metadata
        meta_resp = await client.get(base, headers=headers)
        meta = meta_resp.json() if meta_resp.status_code == 200 else {}

        # File tree
        tree_resp = await client.get(
            f"{base}/git/trees/HEAD?recursive=1", headers=headers
        )
        tree = tree_resp.json() if tree_resp.status_code == 200 else {}
        all_files = [
            f["path"] for f in tree.get("tree", [])
            if f.get("type") == "blob"
        ]

        # README
        readme = await _get_readme(client, base, headers, all_files)

        # Commits
        commits_resp = await client.get(
            f"{base}/commits?per_page=25", headers=headers
        )
        raw_commits = commits_resp.json() if commits_resp.status_code == 200 else []
        commit_messages = []
        if isinstance(raw_commits, list):
            commit_messages = [
                c.get("commit", {}).get("message", "").split("\n")[0]
                for c in raw_commits[:20]
            ]

        # Dependencies
        deps = await _get_dependencies(client, base, headers, all_files)

        # Key source files
        code_files = await _get_key_files(client, base, headers, all_files)

    return {
        "owner": owner,
        "repo": repo,
        "repo_metadata": {
            "description": meta.get("description") or "",
            "language": meta.get("language") or "Unknown",
            "topics": meta.get("topics", []),
            "updated_at": meta.get("updated_at", ""),
        },
        "file_structure": all_files[:80],
        "readme": readme,
        "commit_messages": commit_messages,
        "dependencies": deps,
        "code_files": code_files,
    }


async def _get_readme(client, base, headers, file_paths) -> str:
    candidates = [p for p in file_paths if p.lower() in {
        "readme.md", "readme.txt", "readme.rst", "readme"
    }]
    for path in candidates:
        try:
            r = await client.get(f"{base}/contents/{path}", headers=headers)
            if r.status_code == 200:
                data = r.json()
                content = base64.b64decode(data.get("content", "")).decode(
                    "utf-8", errors="replace"
                )
                return content[:6000]
        except Exception:
            continue
    return ""


async def _get_dependencies(client, base, headers, file_paths) -> dict:
    dep_map = {
        "package.json": "nodejs",
        "requirements.txt": "python",
        "pyproject.toml": "python",
        "Pipfile": "python",
        "go.mod": "golang",
        "pom.xml": "java",
        "build.gradle": "java",
        "Cargo.toml": "rust",
        "composer.json": "php",
        "Gemfile": "ruby",
    }
    result = {}
    for dep_file in dep_map:
        if dep_file in file_paths:
            try:
                r = await client.get(f"{base}/contents/{dep_file}", headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    content = base64.b64decode(data.get("content", "")).decode(
                        "utf-8", errors="replace"
                    )
                    result[dep_file] = content[:2500]
            except Exception:
                continue
    return result


async def _get_key_files(client, base, headers, file_paths) -> list:
    selected = []

    def should_skip(path):
        parts = path.split("/")
        if any(p in SKIP_DIRS for p in parts):
            return True
        ext = "." + path.split(".")[-1] if "." in path else ""
        if ext in SKIP_EXTENSIONS:
            return True
        return False

    # 1. Entry-point files
    for ep in PRIORITY_ENTRY_POINTS:
        for path in file_paths:
            if path.endswith("/" + ep) or path == ep:
                if not should_skip(path) and path not in selected:
                    selected.append(path)
                    break
        if len(selected) >= 3:
            break

    # 2. Priority directory files
    for path in file_paths:
        if len(selected) >= 6:
            break
        if should_skip(path) or path in selected:
            continue
        top_dir = path.split("/")[0] if "/" in path else ""
        ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
        if top_dir in PRIORITY_DIRS and ext in CODE_EXTENSIONS:
            selected.append(path)

    # 3. Any remaining code files
    for path in file_paths:
        if len(selected) >= 9:
            break
        if should_skip(path) or path in selected:
            continue
        ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
        if ext in CODE_EXTENSIONS:
            selected.append(path)

    # Fetch content
    result = []
    for path in selected[:9]:
        try:
            r = await client.get(f"{base}/contents/{path}", headers=headers)
            if r.status_code == 200:
                data = r.json()
                content = base64.b64decode(data.get("content", "")).decode(
                    "utf-8", errors="replace"
                )
                result.append({"path": path, "content": content[:3000]})
        except Exception:
            continue

    return result
