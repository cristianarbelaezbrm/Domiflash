# app/llm/tools.py
import json
from typing import Any, Dict, Optional, Set

from langchain_core.tools import tool

from app.repositories.menu_repo import MenuRepository
from app.services.pricing_service import PricingService
from app.services.dispatch_service import DispatchService


def build_tools(
    menu_repo: MenuRepository,
    pricing_service: PricingService,
    dispatch_service: DispatchService,
):
    @tool
    def healthcheck() -> str:
        """Devuelve un estado simple del servicio."""
        return "ok"

    @tool
    def summarize_text(text: str, max_bullets: int = 5) -> str:
        """Resume un texto en viñetas (máx max_bullets)."""
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()][: max_bullets]
        if not lines:
            return "No hay contenido para resumir."
        bullets = "\n".join([f"- {ln[:200]}" for ln in lines])
        return f"Resumen ({len(lines)} puntos):\n{bullets}"

    @tool
    def get_menu(restaurant: str) -> str:
        """Devuelve el menú disponible para un restaurante (items, opciones, domicilio)."""
        return json.dumps(menu_repo.get_menu(restaurant))

    @tool
    def price_order(order_json: str) -> str:
        """
        Calcula total de una orden con base en MENU.
        Retorna: ok, subtotal, domicilio, total, detalle_lineas, warnings
        """
        return json.dumps(pricing_service.price(order_json))

    @tool
    def assign_driver(order_json, exclude_chat_ids=None) -> str:
        """
        Asigna un domiciliario disponible.
        Retorna JSON: ok, dispatch_id, driver_chat_id, driver_name...
        """
        order = dispatch_service.normalize_order(order_json)
        exclude = dispatch_service.normalize_exclude(exclude_chat_ids)
        return json.dumps(dispatch_service.assign_driver(order=order, exclude=exclude))

    @tool
    def send_order_to_driver(driver_chat_id: int, customer_chat_id: int, dispatch_id: str, order_json: str) -> str:
        """
        Registra el despacho y devuelve el mensaje que debe enviarse al domiciliario.
        (El envío real se hace en el handler async.)
        """
        order = dispatch_service.normalize_order(order_json)
        dispatch_service.register_dispatch(
            dispatch_id=dispatch_id,
            driver_chat_id=int(driver_chat_id),
            customer_chat_id=int(customer_chat_id),
            order=order,
        )
        msg = dispatch_service.format_order_message(order)
        return json.dumps({"ok": True, "driver_chat_id": int(driver_chat_id), "message": msg})

    return [healthcheck, summarize_text, assign_driver, send_order_to_driver, get_menu, price_order]
