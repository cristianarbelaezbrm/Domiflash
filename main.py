import os
import logging
from typing import Optional, Dict, Any
import dotenv

from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from google.cloud import secretmanager

# Telegram bot framework
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# LangChain / LangGraph
from langchain_core.tools import tool

try:
    # LangChain OpenAI (recomendado hoy)
    from langchain_openai import ChatOpenAI
except Exception as e:
    raise RuntimeError(
        "Instala langchain-openai. Ej: pip install langchain-openai"
    ) from e

try:
    from langgraph.prebuilt import create_react_agent
    from langgraph.checkpoint.memory import MemorySaver
except Exception as e:
    raise RuntimeError(
        "Instala langgraph. Ej: pip install langgraph"
    ) from e


def load_secret_as_env(secret_name, env_var):
    client = secretmanager.SecretManagerServiceClient()
    project_id = "coil-398415"

    secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(name=secret_path)

    secret_value = response.payload.data.decode("utf-8")
    os.environ[env_var] = secret_value

# -----------------------------
# Config
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tg-langgraph-agent")

load_secret_as_env("telegram_bot_mvp", "TELEGRAM_BOT_TOKEN")
load_secret_as_env("openai_key", "OPENAI_API_KEY")
load_secret_as_env("url_domiflash", "TELEGRAM_WEBHOOK_URL")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL")  # ej: https://xxxx.run.app
WEBHOOK_PATH = "/telegram"

if not TOKEN:
    raise RuntimeError("Falta TELEGRAM_BOT_TOKEN")
if not WEBHOOK_URL:
    raise RuntimeError("Falta TELEGRAM_WEBHOOK_URL")


# -----------------------------
# Herramientas del agente (ejemplos)
# -----------------------------
@tool
def healthcheck() -> str:
    """Devuelve un estado simple del servicio."""
    return "ok"


@tool
def summarize_text(text: str, max_bullets: int = 5) -> str:
    """Resumen rápido en viñetas de un texto largo (útil para tickets/transcripciones)."""
    # Nota: esto es un ejemplo sencillo (no LLM). Puedes reemplazar por lógica/LLM.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = lines[: max_bullets]
    if not lines:
        return "No hay contenido para resumir."
    bullets = "\n".join([f"- {ln[:200]}" for ln in lines])
    return f"Resumen ({len(lines)} puntos):\n{bullets}"


TOOLS = [healthcheck, summarize_text]


# -----------------------------
# Construcción del agente LangGraph (ReAct)
# -----------------------------
def build_agent():
    """
    Agente ReAct: decide cuándo usar tools y cuándo responder.
    Con MemorySaver se mantiene historial por thread_id (chat_id).
    """
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),  # cambia si quieres
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
    )

    system_prompt = os.getenv(
        "AGENT_SYSTEM_PROMPT",
        (
            "Eres un agente virtual útil, claro y orientado a acciones.\n"
            "Responde en español.\n"
            "Si la pregunta es ambigua, haz 1 pregunta corta para aclarar.\n"
            "Si puedes resolver sin herramientas, responde directo.\n"
            "Si necesitas una herramienta, úsala.\n"
        ),
    )

    # Checkpointer en memoria (en Cloud Run se reinicia si escala o redeploy).
    # Para producción “seria”, reemplázalo por Redis / Postgres checkpointing.
    checkpointer = MemorySaver()

    agent = create_react_agent(
    model=llm,
    tools=TOOLS,
    state_modifier=system_prompt,
    checkpointer=checkpointer,
    )
    return agent


AGENT = build_agent()


# -----------------------------
# FastAPI + Telegram App
# -----------------------------
app = FastAPI(title="Telegram + LangGraph Agent")
tg_app: Optional[Application] = None


# Util: invoca el agente con memoria por chat
async def run_agent(user_text: str, chat_id: int) -> str:
    """
    thread_id = chat_id => memoria por conversación.
    """
    config = {"configurable": {"thread_id": str(chat_id)}}

    # Entrada estándar del prebuilt agent: lista de mensajes
    # (LangChain message format)
    inputs: Dict[str, Any] = {"messages": [("user", user_text)]}

    result = await AGENT.ainvoke(inputs, config=config)

    # El resultado suele traer messages; tomamos el último del assistant
    messages = result.get("messages", [])
    # messages es una lista de BaseMessage; convertimos a texto con .content
    for m in reversed(messages):
        if getattr(m, "type", None) == "ai":
            return (m.content or "").strip()

    # fallback
    return "No pude generar una respuesta. Intenta de nuevo."


# --
