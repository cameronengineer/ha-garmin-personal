"""Actions (services) for the Garmin Connect Personal API integration."""

from __future__ import annotations

import io
from pathlib import Path
import re
from typing import Any
import zipfile

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .api import GarminApiError
from .const import (
    ATTR_ACTIVITY_ID,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_FOLDER,
    DOMAIN,
    DOWNLOAD_DIR,
    SERVICE_DOWNLOAD_ACTIVITY,
)
from .coordinator import GarminConfigEntry

DOWNLOAD_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_ACTIVITY_ID): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(ATTR_FOLDER): cv.string,
    }
)


def _entry(hass: HomeAssistant, entry_id: str | None) -> GarminConfigEntry:
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED and (entry_id is None or e.entry_id == entry_id)
    ]
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_loaded_account")
    if len(entries) > 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="choose_account")
    return entries[0]


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:60] or "activity"


def _save(archive: bytes, folder: Path, stem: str) -> list[str]:
    """Write the original file(s) out of Garmin's ZIP; return saved paths."""
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                suffix = Path(member.filename).suffix.lower() or ".fit"
                target = folder / f"{stem}{'' if not saved else f'_{len(saved)}'}{suffix}"
                target.write_bytes(zf.read(member))
                saved.append(str(target))
    except zipfile.BadZipFile:
        # Not a ZIP after all: keep it as-is so nothing is lost.
        target = folder / f"{stem}.bin"
        target.write_bytes(archive)
        saved.append(str(target))
    return saved


async def _download_activity(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    entry = _entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
    coordinator = entry.runtime_data
    activity_id = call.data.get(ATTR_ACTIVITY_ID)
    activity: dict[str, Any] | None = None
    if activity_id is None:
        activity = coordinator.data.activity if coordinator.data else None
        if not activity or not activity.get("activityId"):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_activities")
        activity_id = int(activity["activityId"])
    else:
        activity = next(
            (
                a
                for a in (coordinator.data.activities if coordinator.data else [])
                if a.get("activityId") == activity_id
            ),
            None,
        )

    folder = Path(hass.config.path(DOWNLOAD_DIR))
    if custom := call.data.get(ATTR_FOLDER):
        # Relative to the config directory unless absolute, and only somewhere
        # Home Assistant is allowed to write (allowlist_external_dirs, media).
        folder = Path(hass.config.path(custom))
        if not await hass.async_add_executor_job(hass.config.is_allowed_path, str(folder)):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="folder_not_allowed",
                translation_placeholders={"folder": str(folder)},
            )

    try:
        archive = await coordinator.api.async_download_activity(activity_id)
    except GarminApiError as err:
        raise HomeAssistantError(f"Couldn't download activity {activity_id}: {err}") from err

    name = (activity or {}).get("activityName") or "activity"
    stem = f"{activity_id}_{_safe_name(name)}"
    paths = await hass.async_add_executor_job(_save, archive, folder, stem)
    return {"activity_id": activity_id, "files": paths}


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_DOWNLOAD_ACTIVITY,
        _download_activity,
        schema=DOWNLOAD_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
