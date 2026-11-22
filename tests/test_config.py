import pytest
from pydantic import SecretStr, ValidationError

from bot.config import DatabaseSettings, Settings, TwitchSettings


def test_secrets_are_redacted(settings) -> None:
    assert "test-client-secret" not in repr(settings)
    assert "test-password" not in repr(settings.database)


def test_database_dsn_is_built_from_parts(settings) -> None:
    assert settings.database.dsn == (
        "postgresql+asyncpg://bot:test-password@localhost:5432/test_twitch_bot"
    )


def test_database_dsn_url_override() -> None:
    override = "postgresql+asyncpg://u:p@db:5432/example"
    config = DatabaseSettings(url=override, password=SecretStr("ignored"))
    assert config.dsn == override


def twitch_settings(**overrides: object) -> TwitchSettings:
    values: dict[str, object] = {
        "client_id": "id",
        "client_secret": SecretStr("secret"),
        "bot_username": "bot",
        "bot_id": "42",
        "channel": ["channel"],
        "channel_id": ["7"],
    }
    values.update(overrides)
    return TwitchSettings(**values)  # type: ignore[arg-type]


def test_twitch_ids_must_be_numeric() -> None:
    with pytest.raises(ValidationError):
        twitch_settings(bot_id="not-a-number")

    with pytest.raises(ValidationError):
        twitch_settings(channel_id=["7", "not-a-number"], channel=["a", "b"])


def test_single_channel_strings_are_normalized() -> None:
    config = twitch_settings(channel="channel", channel_id="7")
    assert config.channel == ["channel"]
    assert config.channel_id == ["7"]


def test_multiple_channels_are_accepted() -> None:
    config = twitch_settings(channel=["a", "b"], channel_id=["7", "8"])
    assert config.channel_id == ["7", "8"]


def test_duplicate_channel_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="unique"):
        twitch_settings(channel=["a", "b"], channel_id=["7", "7"])


def test_channel_lists_must_match_in_length() -> None:
    with pytest.raises(ValidationError, match="length"):
        twitch_settings(channel=["a"], channel_id=["7", "8"])


def test_oauth_redirect_uri_must_be_http() -> None:
    with pytest.raises(ValidationError, match="http"):
        twitch_settings(oauth_redirect_uri="localhost:8080/callback")


def test_oauth_scopes_accept_single_string() -> None:
    config = twitch_settings(oauth_scopes="user:read:chat")
    assert config.oauth_scopes == ["user:read:chat"]


def test_banned_phrase_config(settings) -> None:
    assert settings.chat.banned_phrases == []
    assert (
        settings.chat.banned_phrase_replacement
        == "[System] Blocklisted phrase detected in reply... Aborting."
    )

    configured = Settings(
        twitch=settings.twitch,
        database=settings.database,
        chat={
            "banned_phrases": ["  Bad Word ", "bad word", ""],
            "banned_phrase_replacement": "[filtered]",
        },
    )
    assert configured.chat.banned_phrases == ["bad word"]
    assert configured.chat.banned_phrase_replacement == "[filtered]"


def test_chat_settings_defaults_and_bounds(settings) -> None:
    assert settings.chat.min_interval_seconds == 1.5
    assert settings.chat.queue_maxsize == 100
    with pytest.raises(ValidationError):
        Settings(
            twitch=settings.twitch,
            database=settings.database,
            chat={"min_interval_seconds": -1},
        )


def test_default_channel_scopes(settings) -> None:
    assert settings.twitch.channel_scopes == ["channel:bot"]
    assert twitch_settings(channel_scopes="channel:bot").channel_scopes == ["channel:bot"]


def test_defaults_include_all_foundations_modules(settings) -> None:
    assert settings.enabled_modules == [
        "chat_logger",
        "quotes",
        "ecount",
        "lines",
        "ai_chat",
        "lynyxia",
    ]
