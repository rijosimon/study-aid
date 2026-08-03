import pytest
from fastapi.testclient import TestClient

import db
from main import app


@pytest.fixture(autouse=True)
def clear_store():
    db.reset_db()
    yield
    db.reset_db()


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


# --- S3.1: upload status indicator ---


def test_index_page_has_upload_status_indicator_markup(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert 'id="upload-status"' in resp.text
    assert "Uploading and processing" in resp.text
    # Hidden by default — only shown by the submit-event JS.
    assert 'id="upload-status" class="hidden' in resp.text


def test_index_page_form_submit_shows_indicator_without_intercepting_submission(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert 'id="upload-form"' in resp.text
    # The listener must not actually call preventDefault() — the real
    # submission (and all existing success/error handling) must still work
    # unmodified. (A code comment mentions the phrase, hence the parens here.)
    assert "preventDefault()" not in resp.text
    assert 'getElementById("upload-form")' in resp.text
    assert "addEventListener(\"submit\"" in resp.text


def test_index_page_submit_button_gets_disabled_on_submit(client):
    resp = client.get("/")

    assert 'id="upload-submit-button"' in resp.text
    assert "upload-submit-button\").disabled = true" in resp.text


# --- S3.2: generating-page copy names Claude and sets a time expectation ---


def test_generating_page_names_claude_and_sets_time_expectation(client):
    quiz = db.create_quiz("some source text")

    resp = client.get(f"/generating/{quiz['session_id']}")

    assert resp.status_code == 200
    assert "Talking to Claude" in resp.text
    assert "Claude is reading through your material" in resp.text
    assert "10" in resp.text and "30 seconds" in resp.text
