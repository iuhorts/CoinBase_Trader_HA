import logging
import time

import requests

API_BASE = "https://api.telegram.org"


class Notifier:
    def __init__(self, settings):
        self.s = settings
        self.log = logging.getLogger("telegram")
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.enabled = bool(settings.telegram_enabled and self.token and self.chat_id)
        self._throttle = {}
        self._offset = 0

    def send(self, text):
        if not self.enabled:
            self.log.info("telegram (disabled): %s", text)
            return
        try:
            requests.post(
                f"{API_BASE}/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
                timeout=10,
            )
        except Exception as exc:
            self.log.warning("telegram send failed: %s", exc)

    def notify_throttled(self, key, text, seconds=300):
        last = self._throttle.get(key, 0)
        if time.time() - last < seconds:
            return
        self._throttle[key] = time.time()
        self.send(text)

    def poll_commands(self):
        if not self.enabled or not self.s.telegram_commands:
            return []
        commands = []
        try:
            response = requests.get(
                f"{API_BASE}/bot{self.token}/getUpdates",
                params={"offset": self._offset, "timeout": 0},
                timeout=10,
            )
            for update in response.json().get("result", []):
                self._offset = update["update_id"] + 1
                message = update.get("message") or update.get("edited_message") or {}
                chat_id = str((message.get("chat") or {}).get("id", ""))
                text = (message.get("text") or "").strip()
                if text and chat_id == str(self.chat_id):
                    commands.append(text)
        except Exception as exc:
            self.log.warning("telegram getUpdates failed: %s", exc)
        return commands
