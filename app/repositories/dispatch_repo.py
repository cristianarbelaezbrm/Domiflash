from typing import Dict, Optional
from app.domain.models import Dispatch

class DispatchRepository:
    def __init__(self):
        self._dispatches: Dict[str, Dispatch] = {}
        self._driver_active: Dict[int, str] = {}

    def set_active_for_driver(self, driver_chat_id: int, dispatch_id: str) -> None:
        self._driver_active[driver_chat_id] = dispatch_id

    def clear_active_for_driver(self, driver_chat_id: int) -> None:
        self._driver_active.pop(driver_chat_id, None)

    def get_active_dispatch_for_driver(self, driver_chat_id: int) -> Optional[Dispatch]:
        disp_id = self._driver_active.get(driver_chat_id)
        if not disp_id:
            return None
        return self._dispatches.get(disp_id)

    def save(self, dispatch: Dispatch) -> None:
        self._dispatches[dispatch.dispatch_id] = dispatch

    def get(self, dispatch_id: str) -> Optional[Dispatch]:
        return self._dispatches.get(dispatch_id)
