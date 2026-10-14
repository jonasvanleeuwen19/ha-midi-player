from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import ESPHOME_DEVICE_NAME_KEY


def esphome_service_name(device_name: str, action: str) -> str:
    return f"{device_name.replace('-', '_')}_{action}"


def device_name_from_entry(data: Mapping[str, Any], title: str) -> str:
    return str(data.get(ESPHOME_DEVICE_NAME_KEY) or title)
