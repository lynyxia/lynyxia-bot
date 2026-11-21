from pydantic import SecretStr

from bot.config import DatabaseSettings


def test_secrets_are_redacted(settings) -> None:
    assert "test-client-secret" not in repr(settings)
    assert "test-password" not in repr(settings.database)
    assert settings.twitch.oauth_token.get_secret_value() == "oauth:test-token"


def test_database_dsn_is_built_from_parts(settings) -> None:
    assert settings.database.dsn == (
        "postgresql+asyncpg://bot:test-password@localhost:5432/test_twitch_bot"
    )


def test_database_dsn_url_override() -> None:
    override = "postgresql+asyncpg://u:p@db:5432/example"
    config = DatabaseSettings(url=override, password=SecretStr("ignored"))
    assert config.dsn == override


def test_defaults_include_all_foundations_modules(settings) -> None:
    assert settings.enabled_modules == ["chat_logger", "quotes", "ai_chat"]
