from __future__ import annotations

import base64
import binascii
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import ATTR_FILENAME, MAX_ENCODED_SIZE, MAX_FILE_SIZE, WS_UPLOAD
from .manager import get_hub


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_UPLOAD,
        vol.Required(ATTR_FILENAME): str,
        vol.Required("data"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_upload(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    encoded = msg["data"]
    if len(encoded) > MAX_ENCODED_SIZE:
        connection.send_error(
            msg["id"],
            "file_too_large",
            f"The file is larger than {MAX_FILE_SIZE // 1024} KiB",
        )
        return
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        connection.send_error(msg["id"], "invalid_encoding", "The data is not valid base64")
        return
    try:
        entry = await get_hub(hass).async_upload(msg[ATTR_FILENAME], data)
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "upload_failed", str(err))
        return
    connection.send_result(
        msg["id"],
        {
            "filename": entry.filename,
            "size": entry.size,
            "duration_ms": entry.duration_ms,
        },
    )


def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, websocket_upload)
