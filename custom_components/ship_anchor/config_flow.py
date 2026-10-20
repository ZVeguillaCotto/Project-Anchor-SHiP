"""
SHiP Anchor Home Assistant setup flow.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
This lets a Home Assistant user add SHiP Anchor from the Integrations screen.
No settings are needed to install the integration and preview automations.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
Creates one unique config entry for the integration domain.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from . import DOMAIN


class ShipAnchorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure SHiP Anchor from the Home Assistant UI."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        """Create the integration entry after the user confirms setup."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title="SHiP Anchor", data={})

        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
