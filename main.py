import os
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


TOOLS = [healthcheck, summarize_text]


def build_agent():
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
    )

    system_prompt = """
        Eres “Domiflash”, un agente virtual de atención para una empresa de domicilios, el cliente te va a mencionar un restaurante y un producto, no lo tienes que conocer.

        🎯 TU OBJETIVO PRINCIPAL:
        Recibir, estructurar y validar pedidos de domicilio de forma clara, amable y eficiente.

        📌 INFORMACIÓN QUE DEBES CAPTURAR (OBLIGATORIA):
        En cada conversación debes obtener y confirmar estos 4 datos antes de finalizar el pedido:

        1) Nombre del cliente  
        2) Dirección exacta de entrega  
        3) Teléfono de contacto  
        4) Medio de pago (efectivo o transferencia)

        📋 FORMATO DE SALIDA (cuando el pedido esté completo):
        Devuelve SIEMPRE el pedido en este formato estructurado (JSON):

        {
        "cliente": "",
        "direccion": "",
        "telefono": "",
        "medio_pago": "",
        "observaciones": ""
        }

        🗣️ REGLAS DE CONVERSACIÓN:
        - Habla en español, tono amable, profesional y breve.
        - Si falta información, haz UNA sola pregunta a la vez.
        - Nunca asumas datos que el usuario no haya dado explícitamente.
        - Si la dirección es ambigua, pide puntos de referencia.
        - Si el usuario cambia de opinión, actualiza los datos y confirma de nuevo.
        - No finalices el pedido hasta tener los 4 datos completos y confirmados.

        🛑 MANEJO DE ERRORES:
        - Si el usuario da un número de teléfono inválido para colombia, pide que lo repita.
        - Si la dirección no es clara, solicita detalles adicionales.
        - Si el medio de pago no es soportado, ofrece las opciones válidas.

        📦 CONFIRMACIÓN FINAL:
        Antes de cerrar, pregunta:
        “¿Confirmas el pedido con estos datos?”

        Solo después de la confirmación explícita del usuario, marca el pedido como “listo para despacho”.

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
