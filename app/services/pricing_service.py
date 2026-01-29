import json
from typing import Any, Dict, List
from app.repositories.menu_repo import MenuRepository

class PricingService:
    def __init__(self, menu_repo: MenuRepository):
        self.menu_repo = menu_repo

    def price(self, order_json: str | Dict[str, Any]) -> Dict[str, Any]:
        try:
            order = json.loads(order_json) if isinstance(order_json, str) else order_json
        except Exception as e:
            return {"ok": False, "error": "order_json inválido", "detail": str(e)}

        restaurant = (order.get("restaurante") or "").strip()
        menu = self.menu_repo.raw()
        if restaurant not in menu:
            return {"ok": False, "error": "Restaurante no encontrado", "restaurant": restaurant}

        cfg = menu[restaurant]
        items_cfg = cfg.get("items", {})
        options_cfg = cfg.get("options", {})
        delivery_fee = int(cfg.get("delivery_fee", 0))

        warnings: List[str] = []
        detail: List[Dict[str, Any]] = []
        subtotal = 0

        for it in order.get("items", []):
            name = (it.get("nombre") or "").strip().lower()
            qty = int(it.get("cantidad") or 1)
            if qty < 1:
                qty = 1

            found_key = None
            for k in items_cfg.keys():
                if k.lower() == name:
                    found_key = k
                    break

            if not found_key:
                warnings.append(f"Item no encontrado: {it.get('nombre')}")
                continue

            base_price = int(items_cfg[found_key]["price"])
            line_extra = 0
            chosen_opts = it.get("opciones") or {}

            item_opts = options_cfg.get(found_key, {})
            bordes_cfg = (item_opts.get("bordes") or {})
            bordes_choice = chosen_opts.get("bordes")
            if bordes_choice:
                extra = bordes_cfg.get(str(bordes_choice).lower())
                if extra is None:
                    extra = bordes_cfg.get(str(bordes_choice))
                if extra is None:
                    warnings.append(f"Opción bordes inválida en {found_key}: {bordes_choice}")
                else:
                    line_extra += int(extra)

            add_cfg = (item_opts.get("adiciones") or {})
            adds = chosen_opts.get("adiciones") or []
            if isinstance(adds, str):
                adds = [adds]

            adds_ok = []
            for a in adds:
                a_str = str(a).strip().lower()
                extra = None
                for k in add_cfg.keys():
                    if k.lower() == a_str:
                        extra = add_cfg[k]
                        adds_ok.append(k)
                        break
                if extra is None:
                    warnings.append(f"Adición inválida en {found_key}: {a}")
                else:
                    line_extra += int(extra)

            unit = base_price + line_extra
            line_total = unit * qty
            subtotal += line_total

            detail.append({
                "item": found_key,
                "cantidad": qty,
                "base": base_price,
                "extras": line_extra,
                "unitario": unit,
                "total_linea": line_total,
                "opciones": {"bordes": bordes_choice, "adiciones": adds_ok},
            })

        total = subtotal + delivery_fee
        return {
            "ok": True,
            "restaurant": restaurant,
            "currency": cfg.get("currency", "COP"),
            "subtotal": subtotal,
            "delivery_fee": delivery_fee,
            "total": total,
            "detalle_lineas": detail,
            "warnings": warnings,
        }
