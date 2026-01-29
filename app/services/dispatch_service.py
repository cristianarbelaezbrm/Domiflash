import time
from typing import Any, Dict, Optional, Set
from app.domain.models import Dispatch
from app.repositories.driver_repo import DriverRepository
from app.repositories.dispatch_repo import DispatchRepository

class DispatchService:
    def __init__(self, drivers: DriverRepository, dispatches: DispatchRepository):
        self.drivers = drivers
        self.dispatches = dispatches

    def assign_driver(self, order: Dict[str, Any], exclude: Set[int] | None = None) -> Dict[str, Any]:
        driver = self.drivers.pick_available(exclude_chat_ids=exclude)
        if not driver:
            return {"ok": False, "error": "No hay domiciliarios disponibles."}

        dispatch_id = f"disp_{int(time.time())}"
        return {
            "ok": True,
            "dispatch_id": dispatch_id,
            "driver_id": driver.driver_id,
            "driver_name": driver.name,
            "driver_chat_id": driver.chat_id,
        }

    def register_dispatch(self, dispatch_id: str, driver_chat_id: int, customer_chat_id: int, order: Dict[str, Any]) -> Dispatch:
        disp = Dispatch(
            dispatch_id=dispatch_id,
            driver_chat_id=driver_chat_id,
            customer_chat_id=customer_chat_id,
            order=order,
            status="sent",
            ts=int(time.time()),
        )
        self.dispatches.save(disp)
        self.dispatches.set_active_for_driver(driver_chat_id, dispatch_id)
        return disp
