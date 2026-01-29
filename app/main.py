# import os
# import json
# import time
# import logging
# from typing import Optional, Dict, Any

# from fastapi import FastAPI, Request, HTTPException
# from telegram import Update
# from google.cloud import secretmanager

# from telegram.ext import (
#     Application, MessageHandler, CommandHandler, ContextTypes, filters
# )

# from langchain_core.tools import tool
# from langchain_openai import ChatOpenAI
# from langgraph.prebuilt import create_react_agent
# from langgraph.checkpoint.memory import MemorySaver

# # -----------------------------
# # DOMICILIARIOS (MANUAL POR AHORA)
# # -----------------------------
# # Aquí pegas los chat_id que te den por /id en Telegram
# DRIVERS = [
#     # {"driver_id": "d1", "name": "Camila G", "chat_id": 7153322754, "is_available": True},
#     {"driver_id": "d1", "name": "Camila V", "chat_id": 1076570639, "is_available": True},
# ]

# # dispatch_id -> info del despacho
# ACTIVE_DISPATCHES: Dict[str, Dict[str, Any]] = {}

# # driver_chat_id -> dispatch_id activo
# DRIVER_ACTIVE: Dict[int, str] = {}

# MENU = {
#     "Pizzeria Orientini - Marinilla": {
#         "currency": "COP",
#         "delivery_fee": 6000,
#         "items": {
#             "pizza personal": {"price": 18000},
#             "pizza mediana": {"price": 35000},
#             "pizza familiar": {"price": 52000},
#             "gaseosa 1.5l": {"price": 8000},
#         },
#         "options": {
#             "pizza personal": {
#                 "bordes": {"normal": 0, "queso": 4000},
#                 "adiciones": {"extra queso": 3000, "pepperoni": 4000},
#             },
#             "pizza mediana": {
#                 "bordes": {"normal": 0, "queso": 6000},
#                 "adiciones": {"extra queso": 4000, "pepperoni": 6000},
#             },
#         },
#     },
#     "Hamburguesas El Parque": {
#         "currency": "COP",
#         "delivery_fee": 5000,
#         "items": {
#             "hamburguesa sencilla": {"price": 16000},
#             "hamburguesa doble": {"price": 24000},
#             "papas": {"price": 7000},
#             "gaseosa lata": {"price": 4500},
#         },
#         "options": {
#             "hamburguesa sencilla": {"adiciones": {"queso": 2000, "tocineta": 3000}},
#             "hamburguesa doble": {"adiciones": {"queso": 2000, "tocineta": 3000}},
#         },
#     },
# }


# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger("tg-langgraph-agent")

# app = FastAPI(title="Telegram + LangGraph Agent")

# tg_app: Optional[Application] = None
# AGENT = None  # se inicializa en startup

# def is_driver_chat(chat_id: int) -> bool:
#     return any(d.get("chat_id") == chat_id for d in DRIVERS)

# def get_driver_by_chat(chat_id: int) -> Optional[dict]:
#     for d in DRIVERS:
#         if d.get("chat_id") == chat_id:
#             return d
#     return None

# def load_secret_as_env(secret_name: str, env_var: str, project_id: str = "coil-398415"):
#     client = secretmanager.SecretManagerServiceClient()
#     secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
#     response = client.access_secret_version(name=secret_path)
#     os.environ[env_var] = response.payload.data.decode("utf-8")


# @tool
# def healthcheck() -> str:
#     """Devuelve un estado simple del servicio."""
#     return "ok"


# @tool
# def summarize_text(text: str, max_bullets: int = 5) -> str:
#     """Resume un texto en viñetas (máx max_bullets)."""
#     lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:max_bullets]
#     if not lines:
#         return "No hay contenido para resumir."
#     bullets = "\n".join([f"- {ln[:200]}" for ln in lines])
#     return f"Resumen ({len(lines)} puntos):\n{bullets}"

# @tool
# def assign_driver(order_json, exclude_chat_ids=None) -> str:
#     """
#     Asigna un domiciliario disponible.
#     - order_json: dict | JSON string | texto
#     - exclude_chat_ids: lista opcional de chat_id a excluir (p.ej. el que rechazó)
#     Retorna JSON: ok, dispatch_id, driver_chat_id, driver_name...
#     """
#     try:
#         # Normaliza entrada
#         if isinstance(order_json, dict):
#             order = order_json
#         elif isinstance(order_json, str):
#             s = order_json.strip()
#             if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
#                 order = json.loads(s)
#             else:
#                 order = {"raw": s}
#         else:
#             order = {"raw_type": str(type(order_json))}
#     except Exception as e:
#         return json.dumps({"ok": False, "error": "No se pudo interpretar order_json", "detail": str(e)})

#     try:
#         # Normaliza exclude_chat_ids
#         if exclude_chat_ids is None:
#             exclude_set = set()
#         elif isinstance(exclude_chat_ids, (list, tuple, set)):
#             exclude_set = {int(x) for x in exclude_chat_ids if str(x).strip().isdigit() or isinstance(x, int)}
#         else:
#             # si llega como string JSON tipo "[123,456]"
#             try:
#                 parsed = json.loads(str(exclude_chat_ids))
#                 exclude_set = {int(x) for x in parsed}
#             except Exception:
#                 exclude_set = set()

#         available = [
#             d for d in DRIVERS
#             if d.get("is_available") is True and int(d.get("chat_id")) not in exclude_set
#         ]

#         if not available:
#             return json.dumps({"ok": False, "error": "No hay domiciliarios disponibles."})

#         driver = available[0]
#         driver["is_available"] = False
#         dispatch_id = f"disp_{int(time.time())}"

#         return json.dumps({
#             "ok": True,
#             "dispatch_id": dispatch_id,
#             "driver_id": driver.get("driver_id"),
#             "driver_name": driver.get("name"),
#             "driver_chat_id": int(driver.get("chat_id")),
#         })
#     except Exception as e:
#         return json.dumps({"ok": False, "error": "Error asignando domiciliario", "detail": str(e)})
    
# @tool
# def get_menu(restaurant: str) -> str:
#     """Devuelve el menú disponible para un restaurante (items, opciones, domicilio)."""
#     r = (restaurant or "").strip()
#     if r not in MENU:
#         # devuelve lista de restaurantes para guiar
#         return json.dumps({
#             "ok": False,
#             "error": "Restaurante no encontrado",
#             "available_restaurants": list(MENU.keys())
#         })

#     data = MENU[r]
#     return json.dumps({
#         "ok": True,
#         "restaurant": r,
#         "currency": data.get("currency", "COP"),
#         "delivery_fee": data.get("delivery_fee", 0),
#         "items": {k: v["price"] for k, v in data["items"].items()},
#         "options": data.get("options", {})
#     })


# @tool
# def price_order(order_json: str) -> str:
#     """
#     Calcula total de una orden con base en MENU.
#     Espera order_json con estructura:
#     {
#       "restaurante": "...",
#       "items":[
#         {"nombre":"pizza personal","cantidad":2,
#          "opciones":{"bordes":"queso","adiciones":["extra queso","pepperoni"]}}
#       ]
#     }
#     Retorna: ok, subtotal, domicilio, total, detalle_lineas, warnings
#     """
#     try:
#         order = json.loads(order_json) if isinstance(order_json, str) else order_json
#     except Exception as e:
#         return json.dumps({"ok": False, "error": "order_json inválido", "detail": str(e)})

#     restaurant = (order.get("restaurante") or "").strip()
#     if restaurant not in MENU:
#         return json.dumps({"ok": False, "error": "Restaurante no encontrado", "restaurant": restaurant})

#     cfg = MENU[restaurant]
#     items_cfg = cfg.get("items", {})
#     options_cfg = cfg.get("options", {})
#     delivery_fee = int(cfg.get("delivery_fee", 0))

#     warnings = []
#     detail = []
#     subtotal = 0

#     for it in order.get("items", []):
#         name = (it.get("nombre") or "").strip().lower()
#         qty = int(it.get("cantidad") or 1)
#         if qty < 1:
#             qty = 1

#         # buscar item por nombre (case-insensitive)
#         # (para performance, en prod pre-normalizas un dict)
#         found_key = None
#         for k in items_cfg.keys():
#             if k.lower() == name:
#                 found_key = k
#                 break

#         if not found_key:
#             warnings.append(f"Item no encontrado: {it.get('nombre')}")
#             continue

#         base_price = int(items_cfg[found_key]["price"])
#         line_extra = 0

#         chosen_opts = it.get("opciones") or {}

#         # bordes (string)
#         item_opts = options_cfg.get(found_key, {})
#         bordes_cfg = (item_opts.get("bordes") or {})
#         bordes_choice = chosen_opts.get("bordes")
#         if bordes_choice:
#             extra = bordes_cfg.get(str(bordes_choice).lower())
#             if extra is None:
#                 # intenta match por keys originales
#                 extra = bordes_cfg.get(str(bordes_choice))
#             if extra is None:
#                 warnings.append(f"Opción bordes inválida en {found_key}: {bordes_choice}")
#             else:
#                 line_extra += int(extra)

#         # adiciones (list)
#         add_cfg = (item_opts.get("adiciones") or {})
#         adds = chosen_opts.get("adiciones") or []
#         if isinstance(adds, str):
#             adds = [adds]

#         adds_ok = []
#         for a in adds:
#             a_str = str(a).strip().lower()
#             extra = None
#             for k in add_cfg.keys():
#                 if k.lower() == a_str:
#                     extra = add_cfg[k]
#                     adds_ok.append(k)
#                     break
#             if extra is None:
#                 warnings.append(f"Adición inválida en {found_key}: {a}")
#             else:
#                 line_extra += int(extra)

#         unit = base_price + line_extra
#         line_total = unit * qty
#         subtotal += line_total

#         detail.append({
#             "item": found_key,
#             "cantidad": qty,
#             "base": base_price,
#             "extras": line_extra,
#             "unitario": unit,
#             "total_linea": line_total,
#             "opciones": {"bordes": bordes_choice, "adiciones": adds_ok}
#         })

#     total = subtotal + delivery_fee

#     return json.dumps({
#         "ok": True,
#         "restaurant": restaurant,
#         "currency": cfg.get("currency", "COP"),
#         "subtotal": subtotal,
#         "delivery_fee": delivery_fee,
#         "total": total,
#         "detalle_lineas": detail,
#         "warnings": warnings
#     })


# def _format_order_message(order: dict) -> str:
#     pricing = order.get("pricing", {})
#     total = pricing.get("total")
#     currency = pricing.get("currency", "COP")
#     medio_pago = order.get("medio_pago", "")

#     total_txt = f"{total:,} {currency}" if total is not None else "No especificado"

#     items_txt = ""
#     for it in order.get("items", []):
#         opts = it.get("opciones") or {}
#         extras = []
#         if opts.get("bordes"):
#             extras.append(f"Borde: {opts['bordes']}")
#         if opts.get("adiciones"):
#             extras.append("Adiciones: " + ", ".join(opts["adiciones"]))
#         extras_txt = f" ({'; '.join(extras)})" if extras else ""
#         items_txt += f"- {it['cantidad']} x {it['nombre']}{extras_txt}\n"

#     return (
#         "📦 *Nuevo pedido*\n\n"
#         f"🏪 Restaurante: {order.get('restaurante','')}\n"
#         f"👤 Cliente: {order.get('cliente','')}\n"
#         f"📍 Dirección: {order.get('direccion','')}\n"
#         f"📞 Teléfono: {order.get('telefono','')}\n\n"
#         f"🧾 *Pedido:*\n{items_txt}\n"
#         f"💳 Medio de pago: *{medio_pago}*\n"
#         f"💰 *Total a cobrar:* *{total_txt}*\n\n"
#         "Responde: *ACEPTO*, *NO PUEDO* o *COMPLETADO*"
#     )


# @tool
# def send_order_to_driver(driver_chat_id: int, customer_chat_id: int, dispatch_id: str, order_json: str) -> str:
#     """
#     Registra el despacho y devuelve el mensaje que debe enviarse al domiciliario.
#     (El envío real se hace en el handler async para evitar errores de transporte en Cloud Run.)
#     """
#     try:
#         order = json.loads(order_json) if isinstance(order_json, str) else order_json
#     except Exception:
#         order = {"raw": str(order_json)}

#     ACTIVE_DISPATCHES[dispatch_id] = {
#         "dispatch_id": dispatch_id,
#         "driver_chat_id": int(driver_chat_id),
#         "customer_chat_id": int(customer_chat_id),
#         "order": order,
#         "status": "sent",
#         "ts": int(time.time()),
#     }
#     DRIVER_ACTIVE[int(driver_chat_id)] = dispatch_id

#     msg = _format_order_message(order)

#     return json.dumps({
#         "ok": True,
#         "driver_chat_id": int(driver_chat_id),
#         "message": msg
#     })


# TOOLS = [healthcheck, summarize_text, assign_driver, send_order_to_driver, get_menu, price_order]


# def build_agent():
#     llm = ChatOpenAI(
#         model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
#         temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
#     )

#     system_prompt = """
#         Eres “Domiflash”, agente virtual de atención para una empresa de domicilios.

#         OBJETIVO:
#         Ayuda al cliente a escoger su pedido, haz preguntas si es necesario. Debes tomar pedidos y coordinarlos con un domiciliario. Debes capturar y validar:

#         1) cliente (nombre)
#         2) direccion exacta
#         3) telefono
#         4) medio_pago

#         REGLAS:
#         - Habla en español, tono amable y operativo.
#         - Haz UNA sola pregunta a la vez si falta info.
#         - No inventes datos.
#         - Antes de despachar, muestra un resumen y pide confirmación: “¿Confirmas el pedido?”
#         - Customer_chat_id es el valor exacto mostrado en el system message dinámico: customer_chat_id=...

#         MENÚ Y PRECIOS (OBLIGATORIO):
#         - Para mostrar opciones y precios, primero llama get_menu(restaurante).
#         - Para calcular el valor final, llama price_order(order_json).

#         FORMATO DEL PEDIDO:
#         {
#         "restaurante": "...",
#         "cliente": "...",
#         "direccion": "...",
#         "telefono": "...",
#         "medio_pago": "...",
#         "observaciones": "...",
#         "items": [
#             {"nombre": "...", "cantidad": 1, "opciones": {"bordes": "...", "adiciones": ["..."]}}
#         ]
#         }

#         CIERRE ANTES DE CONFIRMAR (OBLIGATORIO):
#         - Cuando tengas los datos completos + items, llama price_order(order_json).
#         - Muestra: detalle por ítem, subtotal, domicilio, TOTAL.
#         - Pregunta: “¿Confirmas el pedido por <TOTAL>?”

#         DESPACHO AUTOMÁTICO (OBLIGATORIO):
#         Cuando el usuario confirme explícitamente el pedido, debes ejecutar EXACTAMENTE estos pasos:

#         Paso 1) Llama a assign_driver(order_json) usando el pedido en formato JSON.
#         - El resultado será un JSON con: ok, dispatch_id, driver_chat_id, driver_name, etc.

#         Paso 2) Si ok=true, llama a:
#         send_order_to_driver(driver_chat_id, customer_chat_id, dispatch_id, order_json)

#         IMPORTANTE:
#         - customer_chat_id es el valor EXACTO del system message dinámico: customer_chat_id=...
#         - No inventes driver_chat_id ni dispatch_id: usa los valores devueltos por assign_driver.
#         """

#     checkpointer = MemorySaver()

#     return create_react_agent(
#         model=llm,
#         tools=TOOLS,
#         state_modifier=system_prompt,   # <- correcto para langgraph 0.2.60
#         checkpointer=checkpointer,
#     )


# @app.on_event("startup")
# async def on_startup():
#     global tg_app, AGENT

#     # 1) Carga secretos aquí (no en import)
#     load_secret_as_env("telegram_bot_mvp", "TELEGRAM_BOT_TOKEN")
#     load_secret_as_env("openai_key", "OPENAI_API_KEY")
#     load_secret_as_env("url_domiflash", "TELEGRAM_WEBHOOK_URL")

#     token = os.getenv("TELEGRAM_BOT_TOKEN")
#     webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")

#     if not token or not webhook_url:
#         # deja el servicio arriba, pero marca error en logs
#         logger.error("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_WEBHOOK_URL")
#         return

#     # 2) Construye el agente
#     AGENT = build_agent()

#     # 3) Inicia Telegram webhook
#     tg_app = Application.builder().token(token).build()
#     tg_app.add_handler(CommandHandler("start", start_cmd))
#     tg_app.add_handler(CommandHandler("id", id_cmd))
#     tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

#     await tg_app.initialize()
#     await tg_app.bot.set_webhook(url=f"{webhook_url.rstrip('/')}/telegram")
#     await tg_app.start()

#     logger.info("Startup OK. Webhook listo.")


# @app.on_event("shutdown")
# async def on_shutdown():
#     global tg_app
#     if tg_app is not None:
#         await tg_app.stop()
#         await tg_app.shutdown()
#         tg_app = None


# # async def run_agent(user_text: str, chat_id: int) -> str:
# #     if AGENT is None:
# #         return "El agente no está inicializado (revisa logs / secretos)."

# #     config = {"configurable": {"thread_id": str(chat_id)}}

# #     # 👇 Mensaje del sistema dinámico con el chat_id real del cliente
# #     inputs: Dict[str, Any] = {
# #         "messages": [
# #             ("system", f"customer_chat_id={chat_id} (usa este valor cuando llames herramientas)."),
# #             ("user", user_text),
# #         ]
# #     }

# #     result = await AGENT.ainvoke(inputs, config=config)

# #     messages = result.get("messages", [])
# #     for m in reversed(messages):
# #         if getattr(m, "type", None) == "ai":
# #             return (m.content or "").strip()
# #     return "No pude generar una respuesta. Intenta de nuevo."

# async def run_agent(user_text: str, chat_id: int) -> str:
#     """
#     Ejecuta el agente LangGraph y, si en la traza aparece una ToolMessage con payload
#     {"ok": true, "driver_chat_id": ..., "message": "..."} entonces envía ese mensaje
#     al domiciliario desde este contexto async (estable en Cloud Run).
#     """
#     if AGENT is None:
#         return "El agente no está inicializado (revisa logs / secretos)."

#     # thread_id = chat_id (memoria por conversación)
#     config = {"configurable": {"thread_id": str(chat_id)}}

#     # Pista explícita para el modelo (para que pase customer_chat_id correcto a la tool)
#     inputs: Dict[str, Any] = {
#         "messages": [
#             ("system", f"customer_chat_id={chat_id} (usa este valor exacto cuando llames herramientas)."),
#             ("user", user_text),
#         ]
#     }

#     try:
#         result = await AGENT.ainvoke(inputs, config=config)
#     except Exception:
#         logger.exception("AGENT.ainvoke falló chat_id=%s", chat_id)
#         return "Se presentó un error procesando el pedido."

#     # 1) Si alguna tool devolvió un payload con message para el domiciliario, lo enviamos aquí (async)
#     try:
#         for m in result.get("messages", []):
#             if getattr(m, "type", None) == "tool":
#                 content = getattr(m, "content", None)
#                 if not isinstance(content, str) or not content:
#                     continue

#                 # Intentar parsear JSON del tool output
#                 try:
#                     payload = json.loads(content)
#                 except Exception:
#                     continue

#                 if (
#                     isinstance(payload, dict)
#                     and payload.get("ok") is True
#                     and payload.get("driver_chat_id") is not None
#                     and payload.get("message")
#                 ):
#                     await tg_app.bot.send_message(
#                         chat_id=int(payload["driver_chat_id"]),
#                         text=str(payload["message"]),
#                         parse_mode="Markdown",
#                     )
#     except Exception:
#         logger.exception("Fallo enviando mensaje al domiciliario desde run_agent chat_id=%s", chat_id)

#     # 2) Retornar la respuesta final del agente al usuario
#     try:
#         messages = result.get("messages", [])
#         for m in reversed(messages):
#             if getattr(m, "type", None) == "ai":
#                 return (m.content or "").strip()
#     except Exception:
#         logger.exception("Fallo extrayendo respuesta AI chat_id=%s", chat_id)

#     return "No pude generar una respuesta. Intenta de nuevo."

# async def reassign_and_send(dispatch: dict, exclude_driver_chat_id: int) -> Dict[str, Any]:
#     """
#     Reasigna el pedido a otro driver disponible (excluyendo al que rechazó),
#     actualiza ACTIVE_DISPATCHES/DRIVER_ACTIVE y envía el mensaje al nuevo driver.
#     Retorna dict ok/error.
#     """
#     if tg_app is None:
#         return {"ok": False, "error": "Telegram app no inicializada"}

#     order = dispatch.get("order") or {}
#     customer_chat_id = dispatch.get("customer_chat_id")

#     # 1) Asignar nuevo driver excluyendo al que rechazó
#     try:
#         res = assign_driver(order, exclude_chat_ids=[exclude_driver_chat_id])
#         payload = json.loads(res) if isinstance(res, str) else res
#     except Exception as e:
#         logger.exception("Error en assign_driver reassign")
#         return {"ok": False, "error": "Error reasignando", "detail": str(e)}

#     if not payload.get("ok"):
#         return {"ok": False, "error": payload.get("error", "No disponible")}

#     new_driver_chat_id = int(payload["driver_chat_id"])
#     new_dispatch_id = payload["dispatch_id"]
#     new_driver_name = payload.get("driver_name", "")

#     # 2) Registrar nuevo dispatch (puedes conservar historial si quieres)
#     ACTIVE_DISPATCHES[new_dispatch_id] = {
#         "dispatch_id": new_dispatch_id,
#         "driver_chat_id": new_driver_chat_id,
#         "customer_chat_id": int(customer_chat_id) if customer_chat_id is not None else None,
#         "order": order,
#         "status": "sent",
#         "ts": int(time.time()),
#         "reassigned_from": dispatch.get("dispatch_id"),
#     }
#     DRIVER_ACTIVE[new_driver_chat_id] = new_dispatch_id

#     # 3) Enviar pedido al nuevo driver
#     msg = _format_order_message(order)
#     try:
#         await tg_app.bot.send_message(
#             chat_id=new_driver_chat_id,
#             text=msg,
#             parse_mode="Markdown",
#         )
#     except Exception as e:
#         logger.exception("Fallo enviando a nuevo driver chat_id=%s", new_driver_chat_id)
#         # si falla, libéralo para no dejarlo ocupado
#         d = get_driver_by_chat(new_driver_chat_id)
#         if d:
#             d["is_available"] = True
#         DRIVER_ACTIVE.pop(new_driver_chat_id, None)
#         ACTIVE_DISPATCHES[new_dispatch_id]["status"] = "send_failed"
#         return {"ok": False, "error": "No pude enviar al nuevo domiciliario", "detail": str(e)}

#     # 4) Notificar al cliente (opcional pero recomendado)
#     try:
#         if customer_chat_id is not None:
#             await tg_app.bot.send_message(
#                 chat_id=int(customer_chat_id),
#                 text=f"🔄 El domiciliario anterior no pudo. Ya asigné a {new_driver_name or 'otro domiciliario'} para tu pedido. (ID: {new_dispatch_id})"
#             )
#     except Exception:
#         logger.exception("No pude notificar al cliente reasignación chat_id=%r", customer_chat_id)

#     return {"ok": True, "dispatch_id": new_dispatch_id, "driver_chat_id": new_driver_chat_id}



# async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     await update.message.reply_text("Envía un mensaje para iniciar la conversación.")

# async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     chat_id = update.effective_chat.id
#     await update.message.reply_text(f"Tu chat_id es: {chat_id}")

# async def handle_driver_message(update: Update, context: ContextTypes.DEFAULT_TYPE, driver_chat_id: int, text: str):
#     driver = get_driver_by_chat(driver_chat_id)
#     t = text.strip().lower()

#     dispatch_id = DRIVER_ACTIVE.get(int(driver_chat_id))
#     if not dispatch_id or dispatch_id not in ACTIVE_DISPATCHES:
#         await update.message.reply_text(
#             "No tengo un pedido activo. Si te llega uno, responde ACEPTO, NO PUEDO o COMPLETADO."
#         )
#         return

#     dispatch = ACTIVE_DISPATCHES[dispatch_id]
#     customer_chat_id = dispatch.get("customer_chat_id")

#     # ---------------------------
#     # 1) ACEPTAR
#     # ---------------------------
#     if t in ["acepto", "aceptar", "ok", "listo", "si", "sí"]:
#         dispatch["status"] = "accepted"
#         dispatch["accepted_ts"] = int(time.time())

#         try:
#             await context.bot.send_message(
#                 chat_id=int(customer_chat_id),
#                 text=f"✅ Tu pedido fue aceptado por {driver.get('name','el domiciliario')} y va en camino. (ID: {dispatch_id})"
#             )
#         except Exception:
#             logger.exception("No pude notificar al cliente chat_id=%r", customer_chat_id)
#             await update.message.reply_text("✅ Aceptado, pero no pude notificar al cliente (chat_id inválido).")
#             return

#         await update.message.reply_text("✅ Pedido aceptado. Cuando entregues, responde COMPLETADO.")
#         return

#     # ---------------------------
#     # 2) RECHAZAR
#     # ---------------------------
#     if t in ["no puedo", "rechazo", "no", "cancelar"]:
#         # 1) marca el dispatch actual como rechazado
#         dispatch["status"] = "rejected"
#         dispatch["rejected_ts"] = int(time.time())

#         # 2) libera al driver actual
#         if driver:
#             driver["is_available"] = True

#         # 3) quita su asignación activa
#         DRIVER_ACTIVE.pop(int(driver_chat_id), None)

#         # 4) intenta reasignar automáticamente
#         result = await reassign_and_send(dispatch, exclude_driver_chat_id=int(driver_chat_id))

#         if result.get("ok"):
#             await update.message.reply_text("Entendido. Reasigné el pedido a otro domiciliario.")
#         else:
#             # si no hay nadie disponible (o falló), avisa al cliente
#             try:
#                 await context.bot.send_message(
#                     chat_id=int(customer_chat_id),
#                     text=f"⚠️ El domiciliario no pudo tomar tu pedido (ID: {dispatch_id}). En este momento no tengo otro disponible. ¿Deseas esperar o cancelar?"
#                 )
#             except Exception:
#                 logger.exception("No pude notificar al cliente sin disponibilidad chat_id=%r", customer_chat_id)

#             await update.message.reply_text("Entendido. No hay otro domiciliario disponible por ahora.")
#         return

#     # ---------------------------
#     # 3) COMPLETAR (NUEVO)
#     # ---------------------------
#     if t in ["completado", "completo", "entregado", "finalizado", "terminado", "listo entregado"]:
#         # (Opcional) si quieres exigir que antes esté accepted:
#         # if dispatch.get("status") != "accepted":
#         #     await update.message.reply_text("Primero debes ACEPTO antes de marcar COMPLETADO.")
#         #     return

#         dispatch["status"] = "completed"
#         dispatch["completed_ts"] = int(time.time())

#         # Liberar driver
#         if driver:
#             driver["is_available"] = True

#         # Quitar asignación activa
#         DRIVER_ACTIVE.pop(int(driver_chat_id), None)

#         # (Opcional) notificar al cliente
#         try:
#             await context.bot.send_message(
#                 chat_id=int(customer_chat_id),
#                 text=f"✅ Pedido entregado. ¡Gracias! (ID: {dispatch_id})"
#             )
#         except Exception:
#             logger.exception("No pude notificar al cliente completado chat_id=%r", customer_chat_id)

#         await update.message.reply_text("✅ Pedido marcado como COMPLETADO. Ya quedaste disponible.")
#         return

#     await update.message.reply_text("Responde únicamente con: ACEPTO, NO PUEDO o COMPLETADO.")


# async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     """
#     Router:
#     - Si escribe un domiciliario -> handle_driver_message (ACEPTO / NO PUEDO)
#     - Si escribe un cliente -> run_agent + reply
#     """
#     chat_id = update.effective_chat.id
#     text = (update.message.text or "").strip()

#     # 1) Domiciliario
#     if is_driver_chat(chat_id):
#         try:
#             await handle_driver_message(update, context, chat_id, text)
#         except Exception:
#             logger.exception("Error en handle_driver_message driver_chat_id=%s", chat_id)
#             try:
#                 await update.message.reply_text("Se presentó un error procesando tu respuesta. Reintenta.")
#             except Exception:
#                 pass
#         return

#     # 2) Cliente
#     try:
#         await context.bot.send_chat_action(chat_id=chat_id, action="typing")
#     except Exception:
#         # No es crítico
#         logger.exception("No pude enviar chat_action typing chat_id=%s", chat_id)

#     answer = await run_agent(text, chat_id)

#     # Responder al cliente (protegido contra transport closed)
#     try:
#         await update.message.reply_text(answer)
#     except Exception:
#         logger.exception("Fallo reply_text al usuario chat_id=%s", chat_id)


# @app.post("/telegram")
# async def telegram_webhook(req: Request):
#     if tg_app is None:
#         raise HTTPException(status_code=503, detail="Bot no inicializado")

#     data = await req.json()
#     update = Update.de_json(data, tg_app.bot)
#     await tg_app.process_update(update)
#     return {"ok": True}


# @app.get("/health")
# async def health():
#     # Esto debe responder SIEMPRE, incluso si Telegram/LLM fallan
#     return {
#         "status": "ok",
#         "agent_ready": AGENT is not None,
#         "telegram_ready": tg_app is not None,
#     }



# app/main.py
import logging
import os
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

from app.config import settings
from app.adapters.secrets import load_secret_as_env
from app.adapters.telegram_client import TelegramClient

from app.domain.models import Driver
from app.repositories.driver_repo import DriverRepository
from app.repositories.dispatch_repo import DispatchRepository
from app.repositories.menu_repo import MenuRepository

from app.services.pricing_service import PricingService
from app.services.dispatch_service import DispatchService

from app.llm.tools import build_tools
from app.llm.agent_factory import build_agent

from app.application.telegram_router import TelegramRouter


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tg-langgraph-agent")

app = FastAPI(title="Telegram + LangGraph Agent (Clean-ish)")

tg_app: Optional[Application] = None
router: Optional[TelegramRouter] = None


# -----------------------------
# DRIVERS (manual por ahora)
# -----------------------------
DRIVERS = [
    Driver(driver_id="d1", name="Camila V", chat_id=1076570639, is_available=True),
]


@app.on_event("startup")
async def on_startup():
    global tg_app, router

    # 1) Secrets (no en import)
    load_secret_as_env("telegram_bot_mvp", "TELEGRAM_BOT_TOKEN", project_id=settings.project_id)
    load_secret_as_env("openai_key", "OPENAI_API_KEY", project_id=settings.project_id)
    load_secret_as_env("url_domiflash", "TELEGRAM_WEBHOOK_URL", project_id=settings.project_id)

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")

    if not token or not webhook_url:
        logger.error("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_WEBHOOK_URL")
        return

    # 2) Repos / services
    drivers_repo = DriverRepository(DRIVERS)
    dispatch_repo = DispatchRepository()
    menu_repo = MenuRepository()
    pricing_service = PricingService(menu_repo=menu_repo)
    dispatch_service = DispatchService(drivers=drivers_repo, dispatches=dispatch_repo)

    # 3) Tools + agent
    tools = build_tools(menu_repo=menu_repo, pricing_service=pricing_service, dispatch_service=dispatch_service)
    agent = build_agent(tools=tools, model=settings.llm_model, temperature=settings.llm_temperature)

    # 4) Telegram infra
    tg_client = TelegramClient()

    # 5) Router (application layer)
    router = TelegramRouter(
        tg_client=tg_client,
        drivers=drivers_repo,
        dispatches=dispatch_repo,
        dispatch_service=dispatch_service,
        agent=agent,
    )

    # 6) Inicializar Telegram app + handlers
    tg_app = Application.builder().token(token).build()

    tg_app.add_handler(CommandHandler("start", router.start_cmd))
    tg_app.add_handler(CommandHandler("id", router.id_cmd))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router.on_text))

    await tg_app.initialize()
    await tg_app.bot.set_webhook(url=f"{webhook_url.rstrip('/')}/telegram")
    await tg_app.start()

    # Conectar bot al client wrapper (para enviar desde run_agent)
    tg_client.set_bot(tg_app.bot)

    logger.info("Startup OK. Webhook listo.")


@app.on_event("shutdown")
async def on_shutdown():
    global tg_app
    if tg_app is not None:
        await tg_app.stop()
        await tg_app.shutdown()
        tg_app = None


@app.post("/telegram")
async def telegram_webhook(req: Request):
    if tg_app is None:
        raise HTTPException(status_code=503, detail="Bot no inicializado")

    data = await req.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "telegram_ready": tg_app is not None,
        "router_ready": router is not None,
    }
