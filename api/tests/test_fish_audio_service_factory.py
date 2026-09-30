from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipecat.transcriptions.language import Language

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.options import (
    FISH_AUDIO_STT_LANGUAGES,
    FISH_AUDIO_TTS_LANGUAGES,
    FISH_AUDIO_TTS_MODELS,
)
from api.services.configuration.registry import (
    REGISTRY,
    FishAudioSTTConfiguration,
    FishAudioTTSConfiguration,
    ServiceProviders,
    ServiceType,
)
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.fish_stt import FISH_AUDIO_ASR_URL, FishAudioSTTService
from api.services.pipecat.service_factory import (
    create_stt_service,
    create_tts_service,
    stt_uses_external_turns,
)


def _audio_config() -> AudioConfig:
    return AudioConfig(
        transport_in_sample_rate=8000,
        transport_out_sample_rate=8000,
    )


def _fish_stt_config(
    language: str = "auto",
    base_url: str = FISH_AUDIO_ASR_URL,
) -> SimpleNamespace:
    return SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.FISH_AUDIO.value,
            api_key="test-key",
            model="fish-asr",
            language=language,
            base_url=base_url,
        )
    )


def test_fish_audio_stt_configuration_exposes_defaults_and_languages():
    config = FishAudioSTTConfiguration(api_key="test-key")
    language_schema = FishAudioSTTConfiguration.model_json_schema()["properties"][
        "language"
    ]

    assert config.provider == ServiceProviders.FISH_AUDIO
    assert config.model == "fish-asr"
    assert config.language == "auto"
    assert config.base_url == FISH_AUDIO_ASR_URL
    assert (
        REGISTRY[ServiceType.STT][ServiceProviders.FISH_AUDIO]
        is FishAudioSTTConfiguration
    )
    assert "auto" in FISH_AUDIO_STT_LANGUAGES
    assert "es" in FISH_AUDIO_STT_LANGUAGES
    assert language_schema["examples"] == list(FISH_AUDIO_STT_LANGUAGES)


def _fish_tts_config() -> SimpleNamespace:
    return SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.FISH_AUDIO.value,
            api_key="test-key",
            model="s1",
            voice="test-reference-id",
            language="en",
            speed=1.0,
        )
    )


def test_fish_audio_tts_configuration_exposes_defaults():
    config = FishAudioTTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.FISH_AUDIO
    assert config.model == "s1"
    assert config.language == "en"
    assert config.speed == 1.0
    assert (
        REGISTRY[ServiceType.TTS][ServiceProviders.FISH_AUDIO]
        is FishAudioTTSConfiguration
    )
    assert FISH_AUDIO_TTS_MODELS == ("s1", "s2-pro")
    assert "en" in FISH_AUDIO_TTS_LANGUAGES


def test_fish_audio_stt_uses_http_service_with_language_mapping():
    user_config = _fish_stt_config(language="es")

    assert not stt_uses_external_turns(user_config)

    with patch(
        "api.services.pipecat.service_factory.FishAudioSTTService"
    ) as stt_service:
        create_stt_service(user_config, _audio_config())

    stt_service.assert_called_once()
    kwargs = stt_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["base_url"] == FISH_AUDIO_ASR_URL
    assert kwargs["sample_rate"] == 8000
    assert kwargs["settings"].model == "fish-asr"
    assert kwargs["settings"].language == Language.ES


def test_fish_audio_stt_auto_language_passes_none():
    user_config = _fish_stt_config(language="auto")

    with patch(
        "api.services.pipecat.service_factory.FishAudioSTTService"
    ) as stt_service:
        create_stt_service(user_config, _audio_config())

    kwargs = stt_service.call_args.kwargs
    assert kwargs["settings"].language is None


def test_fish_audio_stt_service_defaults():
    service = FishAudioSTTService(api_key="test-key")

    assert service._settings.model == "fish-asr"
    assert service._settings.language == Language.EN
    assert service.can_generate_metrics() is True


def test_fish_audio_tts_uses_service_with_voice_mapping():
    user_config = _fish_tts_config()

    with patch(
        "api.services.pipecat.service_factory.FishAudioTTSService"
    ) as tts_service:
        create_tts_service(user_config, _audio_config())

    tts_service.assert_called_once()
    kwargs = tts_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["settings"].model == "s1"
    assert kwargs["settings"].voice == "test-reference-id"
    assert kwargs["settings"].language == Language.EN
    assert kwargs["settings"].prosody_speed == 1.0


def test_fish_audio_is_registered_for_key_validation():
    validator = UserConfigurationValidator()
    assert ServiceProviders.FISH_AUDIO.value in validator._validator_map


def test_fish_audio_key_validation_accepts_valid_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        assert validator._check_fish_audio_api_key("fish_audio", "valid-key") is True
    # Validates against the model-listing endpoint from the canonical
    # Fish Audio OpenAPI contract (GET /model, BearerAuth).
    called_url = mock_get.call_args.args[0]
    assert called_url == "https://api.fish.audio/model"
    assert (
        mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer valid-key"
    )


def test_fish_audio_key_validation_rejects_bad_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 401
        with pytest.raises(ValueError):
            validator._check_fish_audio_api_key("fish_audio", "bad-key")


def test_fish_audio_key_validation_allows_non_auth_errors():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 403
        assert validator._check_fish_audio_api_key("fish_audio", "scoped-key") is True
