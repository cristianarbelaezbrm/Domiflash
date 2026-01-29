from typing import Dict, Any
from app.domain.menu_data import MENU

class MenuRepository:
    def __init__(self, menu: Dict[str, Any] | None = None):
        self._menu = menu or MENU

    def get_menu(self, restaurant: str) -> Dict[str, Any]:
        r = (restaurant or "").strip()
        if r not in self._menu:
            return {
                "ok": False,
                "error": "Restaurante no encontrado",
                "available_restaurants": list(self._menu.keys()),
            }

        data = self._menu[r]
        return {
            "ok": True,
            "restaurant": r,
            "currency": data.get("currency", "COP"),
            "delivery_fee": data.get("delivery_fee", 0),
            "items": {k: v["price"] for k, v in data["items"].items()},
            "options": data.get("options", {}),
        }

    def raw(self) -> Dict[str, Any]:
        return self._menu
