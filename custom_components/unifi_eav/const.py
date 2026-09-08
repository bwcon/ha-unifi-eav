"""Constants for the UniFi EAV integration."""

import logging

from homeassistant.const import Platform

DOMAIN = "unifi_eav"
LOGGER = logging.getLogger(__package__)

CONF_SITE = "site"
CONF_UNIFI_OS = "unifi_os"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SITE = "default"
DEFAULT_SCAN_INTERVAL = 15

PLATFORMS = [Platform.MEDIA_PLAYER]
