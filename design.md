# study-aid — Design Document

## Overview

A web-based study tool that ingests learning material (PDF, Word doc, or pasted text), uses Claude to generate a thorough quiz set, and tracks familiarity across multiple attempts. Two quiz modes: **Practice** (all questions) and **Evaluation** (weighted toward previously failed concepts). MVP is session-based with no authentication or persistent storage.

---

## MVP Feature Set

### 1. Material Ingestion
- Upload PDF (parsed server-side with `pypdf`)
- Upload DOCX (parsed with `python-docx`)
- Paste plain text directly

### 2. AI Quiz Generation
- Claude analyzes the content and generates a comprehensive question set
- Questions are tagged with a **concept/topic** label (enables per-concept scoring)
- Number of questions: AI-determined based on content length and density
- Question types: **Multiple Choice**, **True/False**, **Short Answer**

### 3. Quiz Modes
- **Practice Mode**: Every question is presented once, in order — full coverage pass
- **Evaluation Mode**: Question selection is weighted — concepts with higher failure rates in prior attempts appear more frequently; questions never attempted appear at baseline weight

### 4. Scoring & Adaptive Weighting
- Each attempt produces a **familiarity score** (0–100%) per concept and overall
- Short answers graded by Claude (pass/fail + brief explanation shown to user)
- Per-concept failure counts accumulate across attempts within the session
- Evaluation mode uses failure counts to compute question weights (failed 3× → 3× more likely to be selected)

### 5. Session-Based State (no auth)
- Session keyed by a UUID stored in a browser cookie
- All state lives in server-side memory (Python dict keyed by session UUID)
- Data is lost on server restart or session expiry (TTL: 2 hours)

---

## Future v2 (documented, not implemented in MVP)

- User accounts (email + password or OAuth)
- Persistent quiz history and scores stored in PostgreSQL
- View past attempts and score trends over time
- Share quiz sets with other users

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Backend framework | **FastAPI** (Python) | Async, fast, clean API design |
| Templating / Frontend | **HTMX** + **Jinja2** | Minimal JS, server-rendered, dynamic without a SPA framework |
| AI | **Anthropic Claude** (`claude-sonnet-4-6`) | Quiz generation + short answer grading |
| PDF parsing | `pypdf` | Pure Python, no system dependencies |
| DOCX parsing | `python-docx` | Standard library for Word documents |
| Session store | In-memory Python dict | Simple, zero-config for MVP |
| Styling | **Tailwind CSS** (CDN) | Fast to use, no build step needed |
| Deployment | Any ASGI host (Railway, Render, Fly.io) | FastAPI works out of the box |

---

## Project Structure

```
study-aid/
├── main.py                  # FastAPI app, routes
├── quiz_engine.py           # Quiz generation, grading, weighting logic
├── session_store.py         # In-memory session management
├── parsers.py               # PDF, DOCX, plain text extraction
├── templates/
│   ├── base.html            # Layout shell (HTMX + Tailwind loaded here)
│   ├── index.html           # Landing page: upload / paste
│   ├── generating.html      # Loading state (HTMX poll)
│   ├── quiz.html            # Quiz taking UI (practice or evaluation)
│   ├── results.html         # Score + per-concept breakdown
│   └── partials/
│       ├── question.html    # Single question card (HTMX swap target)
│       └── score_bar.html   # Concept familiarity bar
├── static/
│   └── (minimal custom CSS if needed)
├── requirements.txt
└── .env                     # ANTHROPIC_API_KEY
```

---

## API Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Landing page |
| `POST` | `/ingest` | Accept file or text; extract content; redirect to generating |
| `GET` | `/generating/{session_id}` | Polling page; triggers quiz generation if not started |
| `POST` | `/generate/{session_id}` | HTMX endpoint: call Claude, store quiz in session, return redirect |
| `GET` | `/quiz/{session_id}` | Quiz UI; `?mode=practice` or `?mode=evaluation` |
| `POST` | `/answer/{session_id}` | Submit answer; get next question + grade via HTMX swap |
| `GET` | `/results/{session_id}` | Final score + concept breakdown |
| `POST` | `/retry/{session_id}` | Start new attempt (evaluation mode) |

---

## Claude Integration

### Quiz Generation

**System prompt:**
> You are a study quiz generator. Given source material, produce a comprehensive set of questions that thoroughly covers the content. Tag each question with the concept it tests. Return valid JSON only.

**Output schema:**
```json
{
  "questions": [
    {
      "id": "q1",
      "concept": "string (topic/concept label)",
      "type": "multiple_choice | true_false | short_answer",
      "question": "string",
      "options": ["A", "B", "C", "D"],
      "correct_answer": "string",
      "explanation": "string"
    }
  ]
}
```

`options` is omitted for `short_answer` questions.

### Short Answer Grading

**Prompt:**
> Question: {question}
> Correct answer: {correct_answer}
> User's answer: {user_answer}
>
> Grade the user's answer. Reply with JSON: `{"passed": true|false, "feedback": "one sentence"}`.

---

## Adaptive Weighting Algorithm (Evaluation Mode)

```python
def compute_weights(questions, failure_counts):
    # failure_counts: {question_id: int}
    weights = []
    for q in questions:
        failures = failure_counts.get(q["id"], 0)
        weight = 1 + failures  # never-failed = 1, failed once = 2, etc.
        weights.append(weight)
    # Normalize and sample proportionally until N questions selected
    return weights
```

**N for evaluation mode:** `min(total_questions, max(10, questions_with_failures * 2))`

---

## Session State Schema

```python
{
  "session_id": "uuid",
  "source_text": "str",
  "quiz": [question_objects],          # generated by Claude
  "attempts": [
    {
      "mode": "practice | evaluation",
      "answers": {
        "q1": {"user_answer": "...", "correct": True, "score": 1.0}
      },
      "overall_score": 0.87,
      "concept_scores": {"Photosynthesis": 0.75, "Cell Division": 1.0}
    }
  ],
  "failure_counts": {"q3": 2, "q7": 1},  # cumulative across all attempts
  "created_at": "datetime",
  "expires_at": "datetime"
}
```

---

## Verification Plan

1. **Upload flow**: Upload a 2-page PDF → confirm text extraction → confirm quiz JSON generated and stored in session
2. **Practice mode**: Take a full practice quiz → confirm all questions appear → confirm per-concept scores calculated correctly
3. **Short answer grading**: Submit a correct and an incorrect short answer → confirm Claude grades both appropriately with feedback
4. **Evaluation mode**: After a practice attempt with some wrong answers → start evaluation → confirm questions from failed concepts appear at higher frequency
5. **Session expiry**: Confirm expired sessions return to the landing page gracefully (no crash)
6. **Edge cases**: Empty document, document with only images (no extractable text), very long document (50+ pages)
