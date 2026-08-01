# study-aid

A web-based study tool that turns a PDF, Word doc, or pasted text into an AI-generated quiz, then tracks your familiarity with the material across attempts. Practice mode covers every question once; evaluation mode re-weights toward whatever you've gotten wrong before.

Built with FastAPI, Jinja2, HTMX, and the Anthropic API. Session state lives in an in-memory store — no database, no accounts.

## Features

- Upload a PDF/DOCX or paste text directly
- Claude generates a multiple-choice, true/false, and short-answer quiz from the material, tagged by concept
- Practice mode: every question, once, in order — instant grading for MC/T-F, Claude grades short answers
- Results page: overall score plus a per-concept breakdown
- Evaluation mode: re-attempt weighted toward concepts you missed, with previously-failed questions guaranteed to reappear
- Session-based, no login — state expires after 2 hours or is lost on server restart

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # includes pytest + httpx for testing
cp .env.example .env
```

Edit `.env` and set `ANTHROPIC_API_KEY` to a real key from the [Anthropic Console](https://console.anthropic.com). Without it, the app runs but quiz generation and grading will fail gracefully with a retry prompt instead of producing a quiz.

If you only need to run the app (not the test suite), `requirements.txt` alone is sufficient.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Your Anthropic API key. Quiz generation and short-answer grading call the Claude API with this. |
| `QUIZ_MODEL` | No | `claude-haiku-4-5` | Overrides the Claude model used for quiz generation and grading. |
| `DEBUG` | No | unset (off) | Set to `true` to enable `GET /debug/session/{session_id}`, which dumps a session's full state as JSON — useful for demos and debugging. Leave unset in anything resembling production; it has no auth. |

## Running locally

```sh
.venv/bin/uvicorn main:app --reload
```

Open `http://localhost:8000`. Upload a PDF/DOCX or paste text, wait for the quiz to generate, and take it.

## Running tests

```sh
.venv/bin/python -m pytest
```

Tests mock all Claude API calls (see `tests/fake_claude.py`), so no `ANTHROPIC_API_KEY` is required to run the suite. `tests/pdf_fixtures.py` builds minimal in-memory PDFs/DOCX files for the ingestion tests rather than checking in binary sample files.

## Project structure

```
study-aid/
├── main.py              # FastAPI app and all routes
├── session_store.py     # In-memory session store (create/get/update, TTL, cleanup)
├── parsers.py           # PDF/DOCX/text extraction
├── quiz_engine.py        # Claude calls: generation, grading, scoring, evaluation weighting
├── templates/            # Jinja2 templates (HTMX-driven, no client-side framework)
│   └── partials/          # HTMX swap targets (question card, score bar, error states)
├── static/                # Custom CSS/JS, if any
├── tests/                 # pytest suite — unit + integration, Claude calls mocked
├── design.md               # Original design doc
└── mvp-implement-plan.md    # Epic-by-epic implementation plan and status
```

## How it works, briefly

Everything hangs off a `session_id` cookie pointing into an in-memory dict (`session_store.py`). A session holds the source text, the generated quiz, every attempt (practice or evaluation) with its answers and scores, and cumulative per-question failure counts. There's no persistence layer — restarting the server drops all sessions, and each session expires 2 hours after creation regardless.

Quiz generation and grading run in a thread pool (`fastapi.concurrency.run_in_threadpool`) rather than directly in the async route handlers, so a slow Claude call for one session doesn't block the whole server for everyone else.
