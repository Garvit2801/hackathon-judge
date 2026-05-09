# HackJudge — AI-Powered Hackathon Judging Agent

A production-ready, self-hosted hackathon project judge that evaluates GitHub repositories
using Gemini AI. Organizers submit projects via a web form and receive live, streaming scores
across 5 parameters in real time.

---

## Features

- **Live streaming scores** — watch each parameter score reveal one by one via SSE
- **Code-first analysis** — works even with empty READMEs by reading source code directly
- **5-parameter rubric** — Innovation (25%), Technical Execution (30%), Completeness (25%), PS Alignment (15%), Code Quality (5%)
- **Smart fallback** — commits, file names, and dependencies used when README is absent
- **Live leaderboard** — auto-refreshing rankings with expandable per-team details
- **Persistent storage** — submissions saved to JSON; survives server restarts
- **No database required** — pure file-based storage, zero infrastructure dependencies

---

## Quick Start (Local)

### 1. Clone / unzip the project

```bash
unzip hackathon-judge.zip
cd hackathon-judge
```

### 2. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Open in browser

```
http://localhost:8000
```

---

## Deployment

### Option A — Docker (recommended)

```bash
docker build -t hackjudge .
docker run -d -p 8000:8000 -v $(pwd)/data:/app/data --name hackjudge hackjudge
```

The `-v` flag mounts the `data/` directory so submissions persist across container restarts.

### Option B — Railway / Render / Fly.io

1. Push this folder to a GitHub repo
2. Connect to Railway / Render
3. Set start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Deploy — these platforms auto-detect Python and install requirements

### Option C — VPS (Ubuntu)

```bash
# Install Python 3.11+
sudo apt update && sudo apt install python3.11 python3.11-venv -y

# Setup
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run with process manager
pip install gunicorn
gunicorn main:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

---

## Project Structure

```
hackathon-judge/
├── main.py              ← FastAPI app + API routes + static file serving
├── github_client.py     ← GitHub REST API integration (repo fetch, file selection)
├── gemini_client.py     ← Gemini API prompt builder + response parser
├── judge.py             ← Judging pipeline (async generator, SSE events)
├── models.py            ← Pydantic request/response models
├── requirements.txt
├── Dockerfile
├── data/
│   └── submissions.json ← Auto-created; all submissions and scores stored here
└── static/
    ├── index.html       ← Submission form
    ├── judging.html     ← Live scoring page (SSE streaming)
    ├── leaderboard.html ← Rankings with expandable team details
    └── style.css        ← Shared design system
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST   | `/api/submit` | Submit a project for judging |
| GET    | `/api/judge/{id}` | SSE stream — live judging events |
| GET    | `/api/submissions` | All scored submissions (leaderboard) |
| GET    | `/api/submission/{id}` | Single submission detail |
| GET    | `/api/health` | Health check |

---

## Judging Parameters

| Parameter | Weight | What it measures |
|-----------|--------|-----------------|
| Innovation & Creativity | 25% | Novelty, problem-solving originality |
| Technical Execution | 30% | Code quality, architecture, stack choices |
| Project Completeness | 25% | Runability, feature implementation, entry points |
| PS Alignment | 15% | Solution vs organizer's problem statement |
| Code Quality | 5% | Tests, linting, secrets handling, license |

Each parameter scored 1–10. Final score = weighted sum.

---

## Notes for Organizers

- **Private GitHub repos** are not supported without adding a GitHub Personal Access Token.
  To add one, edit `github_client.py` and include `Authorization: token YOUR_TOKEN` in headers.

- **Empty READMEs** are handled automatically. The agent falls back to code analysis via
  function names, file structure, commit messages, and library imports.

- **Gemini API keys** are used for evaluation only and never stored permanently.
  The key field is excluded from all API responses.

- **Re-judging** is prevented — submissions already marked `scored` replay their
  cached results without consuming another API call.

- **Data reset**: to clear all submissions, delete `data/submissions.json` and restart.

---

## Gemini API Key

Each team submits their own Gemini API key (or the organizer can modify the form
to use a single shared key by removing the `gemini_api_key` form field and
hardcoding a key in `gemini_client.py`).

Get a free key at: https://aistudio.google.com/app/apikey
