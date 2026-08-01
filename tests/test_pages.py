from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_index_renders_without_error():
    resp = client.get("/")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
