from typing import Optional, List, Set
from app.domain.models import Driver

class DriverRepository:
    def __init__(self, drivers: List[Driver]):
        self._drivers = drivers

    def is_driver_chat(self, chat_id: int) -> bool:
        return any(d.chat_id == chat_id for d in self._drivers)

    def get_by_chat(self, chat_id: int) -> Optional[Driver]:
        for d in self._drivers:
            if d.chat_id == chat_id:
                return d
        return None

    def pick_available(self, exclude_chat_ids: Set[int] | None = None) -> Optional[Driver]:
        exclude_chat_ids = exclude_chat_ids or set()
        for d in self._drivers:
            if d.is_available and d.chat_id not in exclude_chat_ids:
                d.is_available = False
                return d
        return None

    def set_available(self, chat_id: int, is_available: bool) -> None:
        d = self.get_by_chat(chat_id)
        if d:
            d.is_available = is_available
