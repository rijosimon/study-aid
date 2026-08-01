# study-aid — MVP Implementation Plan

> Status key: `[ ]` Not started · `[~]` In progress · `[x]` Done

---

## Epic 1: Project Scaffold & Dev Environment

**Goal:** A running FastAPI server with Jinja2 templating, Tailwind CSS, and a landing page shell. Nothing functional yet — just the skeleton every other epic builds on.

**Status:** `[x]` Done

### Stories

- [x] **S1.1** Create `requirements.txt` with all dependencies (`fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `pypdf`, `python-docx`, `anthropic`, `python-dotenv`)
- [x] **S1.2** Create `main.py` — FastAPI app with a `GET /` route and `GET /health` route
- [x] **S1.3** Create `templates/base.html` — HTML shell that loads HTMX (CDN) and Tailwind CSS (CDN)
- [x] **S1.4** Create `templates/index.html` — Landing page (upload form placeholder + paste textarea placeholder)
- [x] **S1.5** Create `.env.example` documenting required env vars (`ANTHROPIC_API_KEY`)
- [x] **S1.6** Mount `static/` directory in FastAPI for any custom CSS/JS assets

### Testing

- [x] `GET /health` returns `{"status": "ok"}` with HTTP 200
- [x] `GET /` renders `index.html` without errors
- [x] Tailwind and HTMX load correctly in the browser (check network tab)

### Demo

Start server with `uvicorn main:app --reload`. Open browser to `http://localhost:8000`. Landing page renders with Tailwind styles visible and no console errors.

---

## Epic 2: Session Management

**Goal:** A session store module that creates, retrieves, and expires sessions. Every other epic depends on this — it should be solid and independently testable before anything else touches it.

**Status:** `[x]` Done

### Stories

- [x] **S2.1** Create `session_store.py` — in-memory dict keyed by UUID, with `create_session()`, `get_session()`, `update_session()`, `delete_session()` functions
- [x] **S2.2** Add TTL enforcement — sessions expire after 2 hours; expired sessions return `None` from `get_session()`
- [x] **S2.3** Add a FastAPI dependency `get_current_session(request)` that reads the `session_id` cookie and returns the session or `None`
- [x] **S2.4** Add a `set_session_cookie(response, session_id)` helper used when creating new sessions
- [x] **S2.5** Add a background cleanup task (runs every 15 min) to purge expired sessions from memory

### Testing

- [x] Unit: `create_session()` returns a valid UUID and stores data
- [x] Unit: `get_session()` returns `None` for unknown session IDs
- [x] Unit: `get_session()` returns `None` for sessions past their TTL
- [x] Unit: `update_session()` merges data without overwriting unrelated keys
- [x] Integration: Cookie is set on first request; subsequent requests read the same session

### Demo

Using `httpx` or the browser dev tools: create a session via a test endpoint, confirm cookie is set, confirm session data is retrievable, wait for TTL simulation (set TTL to 10s for demo), confirm session is gone.

---

## Epic 3: Document Ingestion

**Goal:** Users can upload a PDF, DOCX, or paste plain text. The extracted text is stored in the session. This is the entry point to the product.

**Status:** `[x]` Done

### Stories

- [x] **S3.1** Create `parsers.py` — `extract_pdf(file_bytes) -> str`, `extract_docx(file_bytes) -> str`, `extract_text(raw: str) -> str`
- [x] **S3.2** Implement `POST /ingest` — accept `multipart/form-data` (file upload) or form field (pasted text); detect type; call appropriate parser
- [x] **S3.3** Create session on ingest; store `source_text` in session; redirect to `/generating/{session_id}`
- [x] **S3.4** Update `templates/index.html` — working file upload input (accept `.pdf`, `.docx`) + textarea for paste, single submit button
- [x] **S3.5** Add error handling for: empty file, unsupported file type, PDF with no extractable text (images-only), text under 50 characters
- [x] **S3.6** Create `templates/error.html` — simple error page with message and "Try again" link back to `/`

### Testing

- [x] Unit: `extract_pdf()` returns non-empty string from a sample PDF
- [x] Unit: `extract_docx()` returns non-empty string from a sample DOCX
- [x] Unit: `extract_pdf()` raises a clear exception for an image-only PDF
- [x] Unit: Empty string input to `extract_text()` raises validation error
- [x] Integration: POST to `/ingest` with a PDF file → session created → redirects to `/generating/{session_id}`
- [x] Integration: POST to `/ingest` with unsupported file type → redirects to error page

### Demo

Open landing page. Upload a 2-page PDF. Confirm redirect to `/generating/{session_id}`. Inspect session (via debug endpoint) to confirm `source_text` is populated with readable content.

---

## Epic 4: AI Quiz Generation

**Goal:** Claude reads the ingested text and returns a structured quiz stored in the session. Includes a loading/polling UI while generation is in progress.

**Status:** `[x]` Done

### Stories

- [x] **S4.1** Create `quiz_engine.py` — `generate_quiz(source_text: str) -> list[dict]` that calls Claude with the generation prompt and parses the JSON response
- [x] **S4.2** Implement the Claude system prompt and user prompt (see `design.md` Claude Integration section)
- [x] **S4.3** Add JSON schema validation of Claude's output — ensure all required fields are present; retry once if malformed
- [x] **S4.4** Implement `GET /generating/{session_id}` — renders `generating.html` which HTMX-polls `POST /generate/{session_id}` every 2 seconds
- [x] **S4.5** Implement `POST /generate/{session_id}` — if quiz not yet generated, calls `generate_quiz()`, stores result in session, returns HTMX redirect to `/quiz/{session_id}`; if already generated, redirects immediately
- [x] **S4.6** Create `templates/generating.html` — spinner + "Building your quiz…" message with HTMX polling via `hx-trigger="every 2s"`
- [x] **S4.7** Store quiz in session as `quiz: list[dict]` and initialise `failure_counts: {}` and `attempts: []`

### Testing

- [x] Unit: `generate_quiz()` returns a list of dicts with required fields (`id`, `concept`, `type`, `question`, `correct_answer`)
- [x] Unit: `generate_quiz()` handles malformed JSON from Claude by retrying once
- [x] Unit: Multiple choice questions always have `options` list with 4 items
- [x] Unit: Short answer questions have no `options` field
- [x] Integration: POST `/ingest` → GET `/generating/{session_id}` → POST `/generate/{session_id}` → session contains quiz with ≥1 question
- [x] Integration: Calling `/generate/{session_id}` a second time does not re-call Claude (idempotent)

### Demo

Upload a document. Watch the spinner page. After generation (10–30s), observe the redirect. Inspect the session's `quiz` field (via debug endpoint) to show all generated questions with their concept tags, types, and answers.

---

## Epic 5: Practice Mode Quiz UI

**Goal:** Users can take a full practice quiz — all questions asked once in order. Multiple choice and true/false are auto-graded; short answers are graded by Claude. Progress is tracked per question.

**Status:** `[ ]` Not started

### Stories

- [ ] **S5.1** Implement `GET /quiz/{session_id}?mode=practice` — renders `quiz.html` with the first question; initialises a new attempt record in the session
- [ ] **S5.2** Create `templates/quiz.html` — question counter, progress bar, question card area (HTMX swap target), submit button
- [ ] **S5.3** Create `templates/partials/question.html` — renders a single question card appropriate to its type (radio buttons for MC/TF, textarea for short answer)
- [ ] **S5.4** Implement `POST /answer/{session_id}` — receives `question_id` + `user_answer`; grades MC/TF instantly; calls Claude for short answer; stores result in session attempt; returns next question partial or redirect to results
- [ ] **S5.5** Add `grade_short_answer(question, correct_answer, user_answer) -> dict` to `quiz_engine.py` using the grading prompt from `design.md`
- [ ] **S5.6** After each answer, show inline feedback (correct/incorrect + explanation) before advancing to the next question via HTMX swap
- [ ] **S5.7** After the last question, redirect to `GET /results/{session_id}`

### Testing

- [ ] Unit: MC grading returns `correct=True` for the right option, `correct=False` for wrong
- [ ] Unit: T/F grading is case-insensitive (`"True"`, `"true"`, `"TRUE"` all match)
- [ ] Unit: `grade_short_answer()` returns `{"passed": bool, "feedback": str}`
- [ ] Integration: POST `/answer/{session_id}` for all questions in a session → session attempt is fully populated with answers and scores
- [ ] Integration: After final answer, response redirects to `/results/{session_id}`
- [ ] Integration: Answering a question that was already answered in this attempt returns an error (no double-submission)

### Demo

Take a practice quiz end-to-end. Answer a mix of correct and incorrect answers including at least one short answer. Observe inline feedback after each answer. Reach the results redirect after the last question.

---

## Epic 6: Scoring & Results

**Goal:** A results page showing overall familiarity score and per-concept breakdown. Sets the foundation for evaluation mode weighting.

**Status:** `[ ]` Not started

### Stories

- [ ] **S6.1** Add `calculate_scores(attempt: dict, quiz: list) -> dict` to `quiz_engine.py` — returns `overall_score` (%) and `concept_scores` dict
- [ ] **S6.2** Call `calculate_scores()` when the last answer is submitted; store results in the attempt record and update `failure_counts` in the session
- [ ] **S6.3** Implement `GET /results/{session_id}` — renders `results.html` with scores from the latest attempt
- [ ] **S6.4** Create `templates/results.html` — overall score prominently displayed, per-concept score list, "Practice Again" and "Evaluation Mode" buttons (evaluation button only active if ≥1 question was failed)
- [ ] **S6.5** Create `templates/partials/score_bar.html` — horizontal bar showing concept name + % score, colour-coded (green ≥80%, yellow 50–79%, red <50%)
- [ ] **S6.6** Update `failure_counts` in session: for each wrong answer, increment `failure_counts[question_id]` by 1

### Testing

- [ ] Unit: `calculate_scores()` returns 100% overall when all answers are correct
- [ ] Unit: `calculate_scores()` returns 0% for a concept where all questions were wrong
- [ ] Unit: `failure_counts` increments correctly across two consecutive attempts
- [ ] Integration: GET `/results/{session_id}` after a completed attempt renders scores without error
- [ ] Integration: `failure_counts` in session correctly reflects wrong answers after attempt is scored

### Demo

Complete a practice quiz with a mix of right and wrong answers. View results page: overall score shown, per-concept bars rendered in appropriate colours, failure counts visible in session debug view.

---

## Epic 7: Evaluation Mode & Adaptive Weighting

**Goal:** Users can re-attempt the quiz in evaluation mode, where questions are selected based on past failures. The more a question was missed, the more likely it appears.

**Status:** `[ ]` Not started

### Stories

- [ ] **S7.1** Add `compute_weights(questions, failure_counts) -> list[float]` to `quiz_engine.py` — implements the weighting algorithm from `design.md`
- [ ] **S7.2** Add `select_evaluation_questions(quiz, failure_counts) -> list[dict]` — uses `compute_weights()` and weighted sampling to select N questions; N = `min(total, max(10, failed_count * 2))`
- [ ] **S7.3** Implement `POST /retry/{session_id}` — accepts `mode` param; for evaluation mode calls `select_evaluation_questions()`; initialises new attempt; redirects to `/quiz/{session_id}?mode=evaluation`
- [ ] **S7.4** Update `GET /quiz/{session_id}` to handle `mode=evaluation` — uses the pre-selected question list from the new attempt rather than the full quiz
- [ ] **S7.5** Update `templates/results.html` — "Evaluation Mode" button posts to `/retry/{session_id}?mode=evaluation`; "Practice Again" posts to `/retry/{session_id}?mode=practice`
- [ ] **S7.6** Show attempt number and mode label in quiz UI header ("Attempt 2 — Evaluation Mode")

### Testing

- [ ] Unit: `compute_weights()` returns weight 1 for never-failed questions, weight N+1 for N failures
- [ ] Unit: `select_evaluation_questions()` always includes all questions that were failed at least once (they have non-zero weight)
- [ ] Unit: N calculation: 10 failed questions → N=20; 3 failed → N=10 (min floor); 50 total, 30 failed → N=50 (max cap)
- [ ] Unit: `select_evaluation_questions()` is deterministic given the same seed (for testability)
- [ ] Integration: After a practice attempt with 5 wrong answers, POST `/retry/{session_id}?mode=evaluation` → new attempt question list heavily weighted toward those 5
- [ ] Integration: Running two evaluation attempts accumulates failure counts correctly

### Demo

Do a practice attempt, intentionally fail 3–4 questions on specific concepts. Go to evaluation mode. Show (via question list inspection) that the failed questions and their concepts appear multiple times in the evaluation set. Complete the evaluation attempt and view updated scores.

---

## Epic 8: Hardening, Error Handling & Integration Testing

**Goal:** The app handles edge cases gracefully, all flows have end-to-end test coverage, and the product is ready for a real user to try.

**Status:** `[ ]` Not started

### Stories

- [ ] **S8.1** Handle expired/missing session gracefully — any route that requires a session and finds none redirects to `/` with a flash message "Session expired. Please start over."
- [ ] **S8.2** Handle PDF with no extractable text — show error page with message "This PDF appears to contain only images. Please paste the text manually."
- [ ] **S8.3** Handle Claude API errors (rate limit, timeout, malformed response) — show retry option rather than a crash
- [ ] **S8.4** Handle very long documents (>50 pages / >100k tokens) — truncate to first 80k characters and show a warning banner: "Document was truncated to fit AI limits."
- [ ] **S8.5** Write end-to-end integration tests covering the full happy path: ingest PDF → generate quiz → practice mode → results → evaluation mode → results
- [ ] **S8.6** Write integration tests for all identified error paths (S8.1–S8.4)
- [ ] **S8.7** Add a `GET /debug/session/{session_id}` endpoint (dev-only, gated by `DEBUG=true` env var) that returns full session state as JSON — useful for demos and debugging
- [ ] **S8.8** Final README with setup instructions, env var documentation, and how to run locally

### Testing

- [ ] E2E: Full happy path from PDF upload to evaluation mode results (automated with `httpx` test client)
- [ ] E2E: Session expiry flow — expired session cookie → redirect to landing with message
- [ ] E2E: Image-only PDF → error page renders correctly
- [ ] E2E: Claude API timeout → retry page shown, no 500 error
- [ ] Load: 10 concurrent sessions all generating quizzes simultaneously (basic concurrency check)

### Demo

Full end-to-end walkthrough with a real study document (e.g., a 10-page article):
1. Upload PDF on landing page
2. Watch quiz generate
3. Take practice quiz — answer some wrong on purpose
4. View results with per-concept scores
5. Switch to evaluation mode
6. Observe heavier weighting on failed concepts
7. Complete evaluation, view improved scores
