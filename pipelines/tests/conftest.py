import pytest


@pytest.fixture(autouse=True)
def _isolate_operator_dotenv(monkeypatch):
    """Tests must never pick up a developer's real R2/.env credentials."""
    monkeypatch.setenv("ANALOG_SKIP_DOTENV", "1")
