"""Test configuration loading."""

from app.config import get_settings, settings


def test_settings_loads():
    s = get_settings()
    assert s is not None


def test_ollama_defaults():
    assert settings.ollama.base_url.startswith("http")
    assert settings.ollama.default_model != ""
    assert 0.0 <= settings.ollama.temperature <= 1.0


def test_sqlite_url():
    url = settings.sqlite.db_url
    assert "sqlite" in url
    assert "aiosqlite" in url


def test_chroma_config():
    assert settings.chroma.top_k > 0
    assert settings.chroma.embedding_model != ""


def test_api_config():
    assert settings.api.port > 0
    assert settings.api.prefix.startswith("/")
