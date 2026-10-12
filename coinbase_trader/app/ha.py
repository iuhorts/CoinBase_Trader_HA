import logging
import os

import requests


class HomeAssistant:
    def __init__(self, settings):
        self.s = settings
        self.log = logging.getLogger("ha")
        self.token = os.environ.get("SUPERVISOR_TOKEN")
        self.base = os.environ.get("SUPERVISOR_URL", "http://supervisor/core/api").rstrip("/")
        self.enabled = bool(settings.ha_sensors and self.token)
        self.prefix = "coinbase_trader"

    def entity_id(self, name):
        return f"sensor.{self.prefix}_{name}"

    def set_state(self, name, state, attributes=None):
        if not self.enabled:
            return
        entity = self.entity_id(name)
        payload = {"state": str(state)}
        if attributes:
            payload["attributes"] = attributes
        try:
            requests.post(
                f"{self.base}/states/{entity}",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10,
            )
        except Exception as exc:
            self.log.warning("HA set_state %s failed: %s", entity, exc)

    def get_state(self, name):
        if not self.enabled:
            return None
        try:
            response = requests.get(
                f"{self.base}/states/{self.entity_id(name)}",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10,
            )
            if response.status_code == 200:
                return response.json()
        except Exception as exc:
            self.log.warning("HA get_state %s failed: %s", name, exc)
        return None
