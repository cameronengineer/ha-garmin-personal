"""Tests for the Garmin Personal API client."""

from __future__ import annotations

from datetime import date, timedelta
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.garmin_personal.api import (
    GarminApiError,
    GarminAuthError,
    GarminConnectionError,
    GarminForbiddenError,
    GarminPersonalApi,
    GarminRateLimitError,
    decode_jwt_claims,
)
from custom_components.garmin_personal.const import TOKEN_PATH

from .conftest import API_KEY, BASE, DAILY_SUMMARY, make_jwt, mock_garmin

DAY = date(2026, 11, 22)


def _api(hass: HomeAssistant) -> GarminPersonalApi:
    return GarminPersonalApi(async_get_clientsession(hass), f"  {API_KEY}\n", BASE)


async def test_token_exchange(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The key goes in an ApiKey header and a JWT comes back as text."""
    mock_garmin(aioclient_mock)
    api = _api(hass)
    token = await api.async_get_token()

    method, url, _data, headers = aioclient_mock.mock_calls[0]
    assert method == "POST"
    assert str(url) == f"{BASE}{TOKEN_PATH}"
    assert headers["Authorization"] == f"ApiKey {API_KEY}"  # whitespace stripped
    assert token.claims["user_profile_pk"] == 777
    assert token.key_expires_at is None  # a full 24 h token says nothing about the key

    # Cached: a second call makes no request.
    await api.async_get_token()
    assert aioclient_mock.call_count == 1


async def test_short_token_reveals_key_expiry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A token issued for much less than 24 h ends when the key does."""
    mock_garmin(aioclient_mock, token=make_jwt(lifetime_s=5 * 3600))
    token = await _api(hass).async_get_token()
    assert token.key_expires_at == token.expires_at


async def test_token_refreshed_near_expiry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A normal 24 h token inside its last hour is replaced."""
    issued = int(time.time()) - 23 * 3600 - 1800  # issued 23.5 h ago, 30 min left
    mock_garmin(aioclient_mock, token=make_jwt(lifetime_s=1800, iat=issued))
    api = _api(hass)
    await api.async_get_token()
    await api.async_get_token()
    assert aioclient_mock.call_count == 2


async def test_key_capped_token_is_reused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A token cut short by the key's expiry isn't re-fetched on every call."""
    mock_garmin(aioclient_mock, token=make_jwt(lifetime_s=1800))  # key ends in 30 min
    api = _api(hass)
    for _ in range(5):
        await api.async_get_token()
    assert aioclient_mock.call_count == 1


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_bad_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    """A rejected key is an auth error."""
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", status=status, text="nope")
    with pytest.raises(GarminAuthError):
        await _api(hass).async_get_token()


async def test_token_server_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Server trouble and rate limits are connection errors, not auth errors."""
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", status=503)
    with pytest.raises(GarminConnectionError):
        await _api(hass).async_get_token()
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", status=429)
    with pytest.raises(GarminRateLimitError):
        await _api(hass).async_get_token()


async def test_token_not_a_jwt(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A body that isn't a JWT is reported, not half-used."""
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", text="hello")
    with pytest.raises(GarminApiError):
        await _api(hass).async_get_token()


async def test_token_wrapped_in_json(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """If Garmin ever returns JSON, the token is still found."""
    jwt = make_jwt()
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", text=f'{{"access_token": "{jwt}"}}')
    token = await _api(hass).async_get_token()
    assert token.value == jwt


async def test_endpoints(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Each endpoint call uses a Bearer token and unwraps its response."""
    mock_garmin(aioclient_mock)
    api = _api(hass)

    summary = await api.async_get_daily_summary(DAY)
    assert summary == DAILY_SUMMARY["data"][0]
    _m, url, _d, headers = aioclient_mock.mock_calls[-1]
    assert headers["Authorization"].startswith("Bearer ")
    assert url.query == {"startDate": "2026-11-22", "endDate": "2026-11-22"}

    assert (await api.async_get_daily_sleep(DAY))["sleepTimeSeconds"] == 26_100
    assert aioclient_mock.mock_calls[-1][1].query == {"date": "2026-11-22"}
    assert (await api.async_get_hrv(DAY))["hrvSummary"]["lastNightAvg"] == 61
    assert (await api.async_get_weight_range(DAY - timedelta(days=30), DAY)) is not None
    assert aioclient_mock.mock_calls[-1][1].query == {"includeAll": "true"}
    assert (await api.async_get_latest_activity())["activityName"] == "Morning Run"
    assert (await api.async_get_profile_settings())["displayName"] == "test-runner"


async def test_empty_responses(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """204s and empty lists mean 'no data', not errors."""
    mock_garmin(aioclient_mock, summary={"data": []}, hrv_status=204)
    aioclient_mock.get(f"{BASE}/activitylist-service/activities/search/activities", json=[])
    api = _api(hass)
    assert await api.async_get_daily_summary(DAY) is None
    assert await api.async_get_hrv(DAY) is None


async def test_get_retries_once_on_401(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 on data gets one retry with a fresh token, then is *not* an auth error.

    The token endpoint accepted the key, so the key is fine; asking the user for
    a new one would loop forever.
    """
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", text=make_jwt())
    aioclient_mock.get(f"{BASE}/userprofile-service/userprofile/settings", status=401)
    with pytest.raises(GarminForbiddenError):
        await _api(hass).async_get_profile_settings()
    posts = [c for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert len(posts) == 2


async def test_get_403_is_forbidden_not_auth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 403 on one endpoint is a per-endpoint refusal, never an auth error."""
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", text=make_jwt())
    aioclient_mock.get(f"{BASE}/userprofile-service/userprofile/settings", status=403)
    with pytest.raises(GarminForbiddenError) as err:
        await _api(hass).async_get_profile_settings()
    assert not isinstance(err.value, GarminAuthError)


async def test_get_errors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Rate limits, server errors and bad JSON surface as connection errors."""
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", text=make_jwt())
    aioclient_mock.get(f"{BASE}/userprofile-service/userprofile/settings", status=429)
    with pytest.raises(GarminRateLimitError):
        await _api(hass).async_get_profile_settings()

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", text=make_jwt())
    aioclient_mock.get(f"{BASE}/userprofile-service/userprofile/settings", status=500, text="x")
    with pytest.raises(GarminConnectionError):
        await _api(hass).async_get_profile_settings()

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", text=make_jwt())
    aioclient_mock.get(f"{BASE}/userprofile-service/userprofile/settings", text="{not json")
    with pytest.raises(GarminConnectionError):
        await _api(hass).async_get_profile_settings()


def test_decode_jwt_claims() -> None:
    """Claims decode without a signature check; missing exp is rejected."""
    assert decode_jwt_claims(make_jwt(sub="abc"))["sub"] == "abc"
    with pytest.raises(GarminApiError):
        decode_jwt_claims("a.eyJmb28iOiAxfQ.b")  # {"foo": 1}
    with pytest.raises(GarminApiError):
        decode_jwt_claims("nodots")
