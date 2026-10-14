import os
import tempfile

# Must run before `app` is imported: config reads the environment at import time.
_TMP = tempfile.mkdtemp(prefix="csb-test-")
os.environ["WORKSPACES_DIR"] = _TMP
os.environ["USE_MOCK_AI"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def workspace(client):
    """A workspace populated with the mock IT Helpdesk agent."""
    ws = client.post("/api/workspaces", json={"name": "Helpdesk test"}).json()
    prop = client.post(f"/api/workspaces/{ws['id']}/ai/generate",
                       json={"mode": "agent", "prompt": "IT helpdesk with HR child agent and ticket flow"}).json()
    client.post(f"/api/proposals/{prop['id']}/accept", json={})
    return ws
