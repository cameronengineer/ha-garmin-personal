"""Tests for the download_activity action's destination folder."""

from __future__ import annotations

import io
from pathlib import Path
import zipfile

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.garmin_personal.const import DOMAIN

from .conftest import BASE, mock_garmin

DOWNLOAD_URL = f"{BASE}/download-service/files/activity/12345"


def _mock_download(aioclient_mock: AiohttpClientMocker) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("12345_ACTIVITY.fit", b".FIT-data")
    aioclient_mock.get(
        DOWNLOAD_URL,
        content=buf.getvalue(),
        headers={"Content-Type": "application/x-zip-compressed"},
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_download_to_allowed_folder(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    tmp_path: Path,
) -> None:
    """A folder Home Assistant is allowed to write to receives the file."""
    mock_garmin(aioclient_mock)
    _mock_download(aioclient_mock)
    await _setup(hass, config_entry)
    hass.config.allowlist_external_dirs.add(str(tmp_path.resolve()))
    folder = tmp_path / "garmin" / "fit"

    result = await hass.services.async_call(
        DOMAIN,
        "download_activity",
        {"folder": str(folder)},
        blocking=True,
        return_response=True,
    )
    (path,) = result["files"]
    assert Path(path) == folder / "12345_Morning_Run.fit"
    assert Path(path).read_bytes() == b".FIT-data"


async def test_download_refuses_folder_outside_allowlist(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    tmp_path: Path,
) -> None:
    """A folder outside allowlist_external_dirs is refused before calling Garmin."""
    mock_garmin(aioclient_mock)
    _mock_download(aioclient_mock)
    await _setup(hass, config_entry)
    folder = tmp_path / "elsewhere"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "download_activity",
            {"folder": str(folder)},
            blocking=True,
            return_response=True,
        )
    assert not folder.exists()
    assert not [c for c in aioclient_mock.mock_calls if "download-service" in str(c[1])]
