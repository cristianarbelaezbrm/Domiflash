import os
import json
import time
import logging
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from google.cloud import secretmanager

from telegram.ext import (
    Application, MessageHandler, CommandHandler, ContextTypes, filters
)

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

# -----------------------------
# DOMICILIARIOS (MANUAL POR AHORA)
# -----------------------------
# Aquí pegas los chat_id que te den por /id en Telegram
DRIVERS = [
    # {"driver_id": "d1", "name": "Camila G", "chat_id": 7153322754, "is_available": True},
    {"driver_id": "d1", "name": "Camila V", "chat_id": 1076570639, "is_available": True},
]

# dispatch_id -> info del despacho
ACTIVE_DISPATCHES: Dict[str, Dict[str, Any]] = {}

# driver_chat_id -> dispatch_id activo
DRIVER_ACTIVE: Dict[int, str] = {}


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tg-langgraph-agent")

app = FastAPI(title="Telegram + LangGraph Agent")

tg_app: Optional[Application] = None
AGENT = None  # se inicializa en startup

def is_driver_chat(chat_id: int) -> bool:
    return any(d.get("chat_id") == chat_id for d in DRIVERS)

def get_driver_by_chat(chat_id: int) -> Optional[dict]:
    for d in DRIVERS:
        if d.get("chat_id") == chat_id:
            return d
    return None

def load_secret_as_env(secret_name: str, env_var: str, project_id: str = "coil-398415"):
    client = secretmanager.SecretManagerServiceClient()
    secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(name=secret_path)
    os.environ[env_var] = response.payload.data.decode("utf-8")


@tool
def healthcheck() -> str:
    """Devuelve un estado simple del servicio."""
    return "ok"


@tool
def summarize_text(text: str, max_bullets: int = 5) -> str:
    """Resume un texto en viñetas (máx max_bullets)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:max_bullets]
    if not lines:
        return "No hay contenido para resumir."
    bullets = "\n".join([f"- {ln[:200]}" for ln in lines])
    return f"Resumen ({len(lines)} puntos):\n{bullets}"

@tool
def assign_driver(order_json) -> str:
    """
    Asigna un domiciliario disponible.
    Acepta order_json como: dict, JSON string, o texto. Retorna JSON ok/error.
    """
    try:
        # Normaliza entrada
        if isinstance(order_json, dict):
            order = order_json
        elif isinstance(order_json, str):
            s = order_json.strip()
            # intenta parsear como JSON si parece JSON
            if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
                order = json.loads(s)
            else:
                # no es JSON, pero no necesitamos el contenido para asignar
                order = {"raw": s}
        else:
            order = {"raw_type": str(type(order_json))}
    except Exception as e:
        return json.dumps({"ok": False, "error": "No se pudo interpretar order_json", "detail": str(e)})

    try:
        available = [d for d in DRIVERS if d.get("is_available") is True]
        if not available:
            return json.dumps({"ok": False, "error": "No hay domiciliarios disponibles."})

        driver = available[0]
        driver["is_available"] = False
        dispatch_id = f"disp_{int(time.time())}"

        return json.dumps({
            "ok": True,
            "dispatch_id": dispatch_id,
            "driver_id": driver.get("driver_id"),
            "driver_name": driver.get("name"),
            "driver_chat_id": driver.get("chat_id"),
        })
    except Exception as e:
        return json.dumps({"ok": False, "error": "Error asignando domiciliario", "detail": str(e)})



def _format_order_message(order: dict) -> str:
    return (
        "📦 *Nuevo pedido*\n"
        f"👤 Cliente: {order.get('cliente','')}\n"
        f"📍 Dirección: {order.get('direccion','')}\n"
        f"📞 Teléfono: {order.get('telefono','')}\n"
        f"💳 Pago: {order.get('medio_pago','')}\n"
        f"📝 Obs: {order.get('observaciones','')}\n\n"
        "Responde: *ACEPTO* o *NO PUEDO*"
    )

# @tool
# def send_order_to_driver(driver_chat_id: int, order_json: str) -> str:
#     """Envía el pedido por Telegram al chat_id del domiciliario."""
#     try:
#         order = json.loads(order_json) if isinstance(order_json, str) else order_json
#     except Exception:
#         order = {"raw": str(order_json)}

#     msg = _format_order_message(order)

#     import asyncio

#     async def _send():
#         await tg_app.bot.send_message(chat_id=driver_chat_id, text=msg, parse_mode="Markdown")

#     try:
#         try:
#             loop = asyncio.get_event_loop()
#             if loop.is_running():
#                 asyncio.create_task(_send())
#             else:
#                 loop.run_until_complete(_send())
#         except RuntimeError:
#             # típico en ThreadPoolExecutor: no hay event loop
#             asyncio.run(_send())
#     except Exception as e:
#         return json.dumps({"ok": False, "error": f"Fallo enviando a driver: {str(e)}"})

#     return json.dumps({"ok": True})

@tool
def send_order_to_driver(driver_chat_id: int, customer_chat_id: int, dispatch_id: str, order_json: str) -> str:
    """Envía el pedido por Telegram al domiciliario y registra el despacho activo."""
    try:
        order = json.loads(order_json) if isinstance(order_json, str) else order_json
    except Exception:
        order = {"raw": str(order_json)}

    # Guarda el despacho con el customer_chat_id REAL (no adivinado)
    ACTIVE_DISPATCHES[dispatch_id] = {
        "dispatch_id": dispatch_id,
        "driver_chat_id": int(driver_chat_id),
        "customer_chat_id": int(customer_chat_id),
        "order": order,
        "status": "sent",
        "ts": int(time.time()),
    }
    DRIVER_ACTIVE[int(driver_chat_id)] = dispatch_id

    msg = _format_order_message(order)

    import asyncio

    async def _send():
        await tg_app.bot.send_message(
            chat_id=int(driver_chat_id),
            text=msg,
            parse_mode="Markdown"
        )

    try:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_send())
            else:
                loop.run_until_complete(_send())
        except RuntimeError:
            asyncio.run(_send())
    except Exception as e:
        ACTIVE_DISPATCHES[dispatch_id]["status"] = "send_failed"
        return json.dumps({"ok": False, "error": f"Fallo enviando a driver: {str(e)}"})

    return json.dumps({"ok": True})


TOOLS = [healthcheck, summarize_text, assign_driver, send_order_to_driver]


def build_agent():
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
    )

    system_prompt = """
          Eres “Domiflash”, agente virtual de atención para una empresa de domicilios, el cliente te va a mencionar un restaurante y un producto, no lo tienes que conocer.

        OBJETIVO:
        Tomar pedidos y coordinarlos con un domiciliario. Debes capturar y validar:

        1) cliente (nombre)
        2) direccion exacta
        3) telefono
        4) medio_pago

        REGLAS:
        - Habla en español, tono amable y operativo.
        - Haz UNA sola pregunta a la vez si falta info.
        - No inventes datos.
        - Antes de despachar, muestra un resumen y pide confirmación: “¿Confirmas el pedido?”
        - Customer_chat_id es el valor exacto mostrado en el system message dinámico: customer_chat_id=...


        FORMATO DEL PEDIDO (cuando tengas todo):
        {
        "cliente": "...",
        "direccion": "...",
        "telefono": "...",
        "medio_pago": "...",
        "observaciones": "..."
        }

        DESPACHO AUTOMÁTICO (OBLIGATORIO):
        Cuando el usuario confirme explícitamente el pedido, debes ejecutar EXACTAMENTE estos pasos:

        Paso 1) Llama a assign_driver(order_json) usando el pedido en formato JSON.
        - El resultado será un JSON con: ok, dispatch_id, driver_chat_id, driver_name, etc.

        Paso 2) Si ok=true, llama a:
        send_order_to_driver(driver_chat_id, customer_chat_id, dispatch_id, order_json)

        IMPORTANTE:
        - customer_chat_id es el valor EXACTO del system message dinámico: customer_chat_id=...
        - No inventes driver_chat_id ni dispatch_id: usa los valores devueltos por assign_driver.
        """

    checkpointer = MemorySaver()

    return create_react_agent(
        model=llm,
        tools=TOOLS,
        state_modifier=system_prompt,   # <- correcto para langgraph 0.2.60
        checkpointer=checkpointer,
    )


@app.on_event("startup")
async def on_startup():
    global tg_app, AGENT

    # 1) Carga secretos aquí (no en import)
    load_secret_as_env("telegram_bot_mvp", "TELEGRAM_BOT_TOKEN")
    load_secret_as_env("openai_key", "OPENAI_API_KEY")
    load_secret_as_env("url_domiflash", "TELEGRAM_WEBHOOK_URL")

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")

    if not token or not webhook_url:
        # deja el servicio arriba, pero marca error en logs
        logger.error("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_WEBHOOK_URL")
        return

    # 2) Construye el agente
    AGENT = build_agent()

    # 3) Inicia Telegram webhook
    tg_app = Application.builder().token(token).build()
    tg_app.add_handler(CommandHandler("start", start_cmd))
    tg_app.add_handler(CommandHandler("id", id_cmd))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    await tg_app.initialize()
    await tg_app.bot.set_webhook(url=f"{webhook_url.rstrip('/')}/telegram")
    await tg_app.start()

    logger.info("Startup OK. Webhook listo.")


@app.on_event("shutdown")
async def on_shutdown():
    global tg_app
    if tg_app is not None:
        await tg_app.stop()
        await tg_app.shutdown()
        tg_app = None


async def run_agent(user_text: str, chat_id: int) -> str:
    if AGENT is None:
        return "El agente no está inicializado (revisa logs / secretos)."

    config = {"configurable": {"thread_id": str(chat_id)}}

    # 👇 Mensaje del sistema dinámico con el chat_id real del cliente
    inputs: Dict[str, Any] = {
        "messages": [
            ("system", f"customer_chat_id={chat_id} (usa este valor cuando llames herramientas)."),
            ("user", user_text),
        ]
    }

    result = await AGENT.ainvoke(inputs, config=config)

    messages = result.get("messages", [])
    for m in reversed(messages):
        if getattr(m, "type", None) == "ai":
            return (m.content or "").strip()
    return "No pude generar una respuesta. Intenta de nuevo."



async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Listo ✅ Escríbeme y te respondo.")

async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"Tu chat_id es: {chat_id}")

# async def handle_driver_message(update: Update, context: ContextTypes.DEFAULT_TYPE, driver_chat_id: int, text: str):
#     driver = get_driver_by_chat(driver_chat_id)
#     t = text.strip().lower()

#     dispatch_id = DRIVER_ACTIVE.get(driver_chat_id)
#     if not dispatch_id or dispatch_id not in ACTIVE_DISPATCHES:
#         await update.message.reply_text(
#             "No tengo un pedido activo asignado. Si te llega uno, responde ACEPTO o NO PUEDO."
#         )
#         return

#     dispatch = ACTIVE_DISPATCHES[dispatch_id]
#     customer_chat_id = dispatch["customer_chat_id"]

#     if t in ["acepto", "aceptar", "ok", "listo", "si"]:
#         dispatch["status"] = "accepted"

#         # Notificar al cliente
#         await context.bot.send_message(
#             chat_id=customer_chat_id,
#             text=f"✅ Tu pedido fue aceptado por {driver.get('name','el domiciliario')} y ya va en camino. (ID: {dispatch_id})"
#         )

#         await update.message.reply_text("✅ Perfecto. Quedaste asignado a este pedido.")

#         return

#     if t in ["no puedo", "nopuedo", "rechazo", "cancelar", "no"]:
#         dispatch["status"] = "rejected"

#         # Liberar domiciliario
#         if driver:
#             driver["is_available"] = True

#         await context.bot.send_message(
#             chat_id=customer_chat_id,
#             text=f"⚠️ El domiciliario no pudo tomar tu pedido (ID: {dispatch_id}). Estoy buscando otro disponible."
#         )

#         await update.message.reply_text("Entendido. Liberé el pedido.")

#         # aquí podrías reintentar asignación automática con otro driver
#         return

#     await update.message.reply_text("Responde únicamente con: ACEPTO o NO PUEDO.")


# async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     chat_id = update.effective_chat.id
#     text = (update.message.text or "").strip()

#     # 1) Si es domiciliario: manejar aceptación / rechazo
#     if is_driver_chat(chat_id):
#         await handle_driver_message(update, context, chat_id, text)
#         return

#     # 2) Si es cliente: flujo normal con el agente
#     await context.bot.send_chat_action(chat_id=chat_id, action="typing")
#     answer = await run_agent(text, chat_id)
#     await update.message.reply_text(answer)

async def handle_driver_message(update: Update, context: ContextTypes.DEFAULT_TYPE, driver_chat_id: int, text: str):
    driver = get_driver_by_chat(driver_chat_id)
    t = text.strip().lower()

    dispatch_id = DRIVER_ACTIVE.get(int(driver_chat_id))
    if not dispatch_id or dispatch_id not in ACTIVE_DISPATCHES:
        await update.message.reply_text("No tengo un pedido activo. Si te llega uno, responde ACEPTO o NO PUEDO.")
        return

    dispatch = ACTIVE_DISPATCHES[dispatch_id]
    customer_chat_id = dispatch.get("customer_chat_id")

    if t in ["acepto", "aceptar", "ok", "listo", "si", "sí"]:
        dispatch["status"] = "accepted"
        try:
            await context.bot.send_message(
                chat_id=int(customer_chat_id),
                text=f"✅ Tu pedido fue aceptado por {driver.get('name','el domiciliario')} y va en camino. (ID: {dispatch_id})"
            )
        except Exception as e:
            logger.exception("No pude notificar al cliente chat_id=%r", customer_chat_id)
            await update.message.reply_text("✅ Aceptado, pero no pude notificar al cliente (chat_id inválido).")
            return

        await update.message.reply_text("✅ Pedido aceptado. Gracias.")
        return

    if t in ["no puedo", "rechazo", "no", "cancelar"]:
        dispatch["status"] = "rejected"
        if driver:
            driver["is_available"] = True

        await context.bot.send_message(
            chat_id=int(customer_chat_id),
            text=f"⚠️ El domiciliario no pudo tomar tu pedido (ID: {dispatch_id}). Estoy buscando otro disponible."
        )

        await update.message.reply_text("Entendido. Pedido liberado.")
        return

    await update.message.reply_text("Responde únicamente con: ACEPTO o NO PUEDO.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    if is_driver_chat(chat_id):
        await handle_driver_message(update, context, chat_id, text)
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    answer = await run_agent(text, chat_id)
    await update.message.reply_text(answer)



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
    # Esto debe responder SIEMPRE, incluso si Telegram/LLM fallan
    return {
        "status": "ok",
        "agent_ready": AGENT is not None,
        "telegram_ready": tg_app is not None,
    }
