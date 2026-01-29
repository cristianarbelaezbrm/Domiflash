import json
import time
from typing import Any, Dict, Optional, Set

from app.domain.models import Dispatch
from app.repositories.driver_repo import DriverRepository
from app.repositories.dispatch_repo import DispatchRepository


class DispatchService:
    def __init__(self, drivers: DriverRepository, dispatches: DispatchRepository):
        self.drivers = drivers
        self.dispatches = dispatches

    # -------------------------
    # Normalizadores (igual a tu script)
    # -------------------------
    def normalize_order(self, order_json) -> Dict[str, Any]:
        try:
            if isinstance(order_json, dict):
                return order_json
            if isinstance(order_json, str):
                s = order_json.strip()
                if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
                    return json.loads(s)
                return {"raw": s}
            return {"raw_type": str(type(order_json))}
        except Exception as e:
            return {"raw_error": "No se pudo interpretar order_json", "detail": str(e)}

    def normalize_exclude(self, exclude_chat_ids) -> Set[int]:
        if exclude_chat_ids is None:
            return set()

        if isinstance(exclude_chat_ids, (list, tuple, set)):
            out = set()
            for x in exclude_chat_ids:
                try:
                    out.add(int(x))
                except Exception:
                    continue
            return out

        # si llega como string JSON tipo "[123,456]"
        try:
            parsed = json.loads(str(exclude_chat_ids))
            return {int(x) for x in parsed}
        except Exception:
            return set()

    # -------------------------
    # Asignación / registro
    # -------------------------
    def assign_driver(self, order: Dict[str, Any], exclude: Optional[Set[int]] = None) -> Dict[str, Any]:
        exclude = exclude or set()

        driver = self.drivers.pick_available(exclude_chat_ids=exclude)
        if not driver:
            return {"ok": False, "error": "No hay domiciliarios disponibles."}

        dispatch_id = f"disp_{int(time.time())}"

        return {
            "ok": True,
            "dispatch_id": dispatch_id,
            "driver_id": driver.driver_id,
            "driver_name": driver.name,
            "driver_chat_id": int(driver.chat_id),
        }

    def register_dispatch(
        self,
        dispatch_id: str,
        driver_chat_id: int,
        customer_chat_id: int,
        order: Dict[str, Any],
        reassigned_from: Optional[str] = None,
    ) -> Dispatch:
        disp = Dispatch(
            dispatch_id=dispatch_id,
            driver_chat_id=int(driver_chat_id),
            customer_chat_id=int(customer_chat_id),
            order=order,
            status="sent",
            ts=int(time.time()),
            reassigned_from=reassigned_from,
        )
        self.dispatches.save(disp)
        self.dispatches.set_active_for_driver(int(driver_chat_id), dispatch_id)
        return disp

    # -------------------------
    # Mensaje para driver (igual a tu script)
    # -------------------------
    def format_order_message(self, order: Dict[str, Any]) -> str:
        pricing = order.get("pricing", {})
        total = pricing.get("total")
        currency = pricing.get("currency", "COP")
        medio_pago = order.get("medio_pago", "")

        total_txt = f"{int(total):,} {currency}" if total is not None else "No especificado"

        items_txt = ""
        for it in order.get("items", []):
            opts = it.get("opciones") or {}
            extras = []
            if opts.get("bordes"):
                extras.append(f"Borde: {opts['bordes']}")
            if opts.get("adiciones"):
                extras.append("Adiciones: " + ", ".join(opts["adiciones"]))
            extras_txt = f" ({'; '.join(extras)})" if extras else ""
            items_txt += f"- {it.get('cantidad',1)} x {it.get('nombre','')}{extras_txt}\n"

        return (
            "📦 *Nuevo pedido*\n\n"
            f"🏪 Restaurante: {order.get('restaurante','')}\n"
            f"👤 Cliente: {order.get('cliente','')}\n"
            f"📍 Dirección: {order.get('direccion','')}\n"
            f"📞 Teléfono: {order.get('telefono','')}\n\n"
            f"🧾 *Pedido:*\n{items_txt}\n"
            f"💳 Medio de pago: *{medio_pago}*\n"
            f"💰 *Total a cobrar:* *{total_txt}*\n\n"
            "Responde: *ACEPTO*, *NO PUEDO* o *COMPLETADO*"
        )
