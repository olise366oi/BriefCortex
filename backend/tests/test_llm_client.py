"""Tests for provider selection, including the gateway route."""
from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services import llm_client


def _settings(**overrides) -> Settings:
    base = dict(
        GATEWAY_BASE_URL=None,
        GATEWAY_MODEL=None,
        DEEPSEEK_API_KEY=None,
        DEEPSEEK_MODEL=None,
        OPENAI_API_KEY=None,
        OPENAI_MODEL=None,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def patched_settings(monkeypatch):
    def apply(**overrides):
        settings = _settings(**overrides)
        monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
        return settings

    return apply


def test_gateway_takes_priority_over_direct_provider(patched_settings):
    patched_settings(
        GATEWAY_BASE_URL="http://localhost:8080/v1",
        GATEWAY_MODEL="deepseek/deepseek-flash",
        DEEPSEEK_API_KEY="direct-key",
    )

    client, model = llm_client.build_client()

    assert str(client.base_url).startswith("http://localhost:8080/v1")
    assert model == "deepseek/deepseek-flash"


def test_gateway_defaults_to_the_economical_model(patched_settings):
    patched_settings(GATEWAY_BASE_URL="http://localhost:8080/v1")

    _client, model = llm_client.build_client()

    assert model == "deepseek/deepseek-flash"


def test_gateway_does_not_send_the_upstream_key(patched_settings):
    patched_settings(
        GATEWAY_BASE_URL="http://localhost:8080/v1",
        DEEPSEEK_API_KEY="real-deepseek-key",
        OPENAI_API_KEY="real-openai-key",
    )

    client, _model = llm_client.build_client()

    # The gateway holds the provider credentials; the app must not ship them.
    assert client.api_key == "gateway-managed"


def test_deepseek_direct_is_the_fallback_path(patched_settings):
    patched_settings(DEEPSEEK_API_KEY="direct-key")

    client, model = llm_client.build_client()

    assert client.api_key == "direct-key"
    assert str(client.base_url).startswith("https://api.deepseek.com")
    assert model == "deepseek-flash"


def test_deepseek_model_can_be_overridden(patched_settings):
    patched_settings(DEEPSEEK_API_KEY="direct-key", DEEPSEEK_MODEL="deepseek-v4-pro")

    _client, model = llm_client.build_client()

    assert model == "deepseek-v4-pro"


def test_openai_direct_is_the_last_resort(patched_settings):
    patched_settings(OPENAI_API_KEY="openai-key")

    client, model = llm_client.build_client()

    assert client.api_key == "openai-key"
    assert model == "gpt-4o-mini"


def test_no_credentials_raises(patched_settings):
    patched_settings()

    with pytest.raises(llm_client.LLMUnavailableError):
        llm_client.build_client()


@pytest.fixture(autouse=True)
def clear_client_cache():
    """Each test must build from its own settings, not a previously cached one."""
    llm_client._CLIENT_CACHE.clear()
    yield
    llm_client._CLIENT_CACHE.clear()


def test_repeated_calls_reuse_one_client(patched_settings):
    # Callers build the client once per article; without reuse each page created
    # a new connection pool (measured: 100+ selections on one briefing).
    patched_settings(DEEPSEEK_API_KEY="direct-key")

    first, _model = llm_client.build_client()
    second, _model = llm_client.build_client()

    assert first is second
    assert len(llm_client._CLIENT_CACHE) == 1


def test_a_changed_key_builds_a_new_client(patched_settings):
    patched_settings(DEEPSEEK_API_KEY="key-one")
    first, _model = llm_client.build_client()

    patched_settings(DEEPSEEK_API_KEY="key-two")
    second, _model = llm_client.build_client()

    assert first is not second
    assert second.api_key == "key-two"


def test_switching_provider_builds_a_new_client(patched_settings):
    patched_settings(DEEPSEEK_API_KEY="direct-key")
    deepseek_client, _model = llm_client.build_client()

    patched_settings(OPENAI_API_KEY="openai-key")
    openai_client, model = llm_client.build_client()

    assert deepseek_client is not openai_client
    assert model == "gpt-4o-mini"


def test_llm_route_reports_the_selected_path():
    assert _settings(GATEWAY_BASE_URL="http://gw/v1").llm_route == "gateway"
    assert _settings(DEEPSEEK_API_KEY="k").llm_route == "deepseek"
    assert _settings(OPENAI_API_KEY="k").llm_route == "openai"
    assert _settings().llm_route == "none"


def test_gateway_counts_as_having_credentials():
    assert _settings(GATEWAY_BASE_URL="http://gw/v1").has_llm_credentials
    assert not _settings().has_llm_credentials
