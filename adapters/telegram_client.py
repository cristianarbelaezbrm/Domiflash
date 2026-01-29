from typing import Optional

class TelegramClient:
    def __init__(self):
        self._bot = None

    def set_bot(self, bot):
        self._bot = bot

    @property
    def ready(self) -> bool:
        return self._bot is not None

    async def send_text(self, chat_id: int, text: str, parse_mode: Optional[str] = None):
        if not self._bot:
            raise RuntimeError("Telegram bot no inicializado")
        await self._bot.send_message(chat_id=int(chat_id), text=text, parse_mode=parse_mode)
