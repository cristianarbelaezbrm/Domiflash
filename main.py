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
    {"driver_id": "d1", "name": "Camila G", "chat_id": 7153322754, "is_available": True},
]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tg-langgraph-agent")

app = FastAPI(title="Telegram + LangGraph Agent")

tg_app: Optional[Application] = None
AGENT = None  # se inicializa en startup


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
def assign_driver(order_json: str) -> str:
    """
    Asigna automáticamente un domiciliario disponible a un pedido.
    Recibe order_json y retorna JSON con driver asignado o error.
    """
    try:
        order = json.loads(order_json)
    except Exception:
        return json.dumps({"ok": False, "error": "order_json inválido (no es JSON)."})

    available = [d for d in DRIVERS if d.get("is_available")]

    if not available:
        return json.dumps({"ok": False, "error": "No hay domiciliarios disponibles."})

    # Regla simple: toma el primero disponible
    driver = available[0]
    driver["is_available"] = False  # lo reservamos temporalmente

    dispatch_id = f"disp_{int(time.time())}"

    return json.dumps({
        "ok": True,
        "dispatch_id": dispatch_id,
        "driver_id": driver["driver_id"],
        "driver_name": driver["name"],
        "driver_chat_id": driver["chat_id"],
    })

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

@tool
def send_order_to_driver(driver_chat_id: int, order_json: str) -> str:
    """
    Envía el pedido (order_json) por Telegram al chat_id del domiciliario.
    Retorna JSON ok/error.
    """
    try:
        order = json.loads(order_json)
    except Exception:
        return json.dumps({"ok": False, "error": "order_json inválido (no es JSON)."})

    msg = _format_order_message(order)

    import asyncio

    async def _send():
        await tg_app.bot.send_message(
            chat_id=driver_chat_id,
            text=msg,
            parse_mode="Markdown"
        )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_send())
        else:
            loop.run_until_complete(_send())
    except Exception as e:
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

        FORMATO DEL PEDIDO (cuando tengas todo):
        {
        "cliente": "...",
        "direccion": "...",
        "telefono": "...",
        "medio_pago": "...",
        "observaciones": "..."
        }

        DESPACHO AUTOMÁTICO (OBLIGATORIO):
        Después de que el usuario confirme explícitamente, debes:
        1) Llamar assign_driver(order_json).
        2) Si ok=True, llamar send_order_to_driver(driver_chat_id, order_json).
        3) Responder al cliente confirmando: domiciliario asignado + dispatch_id.

        Si no hay domiciliarios disponibles, informa al cliente y pregunta si desea esperar o cancelar.
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
    inputs: Dict[str, Any] = {"messages": [("user", user_text)]}
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

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
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
