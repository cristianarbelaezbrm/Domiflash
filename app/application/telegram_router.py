# app/application/telegram_router.py
import json
import logging
import time
from typing import Any, Dict, Optional

from telegram import Update
from telegram.ext import ContextTypes

from app.adapters.telegram_client import TelegramClient
from app.repositories.driver_repo import DriverRepository
from app.repositories.dispatch_repo import DispatchRepository
from app.services.dispatch_service import DispatchService


logger = logging.getLogger("tg-langgraph-agent")


class TelegramRouter:
    """
    Router de mensajes:
    - Si escribe un domiciliario -> handle_driver_message (ACEPTO / NO PUEDO / COMPLETADO)
    - Si escribe un cliente -> run_agent + reply
    """

    def __init__(
        self,
        tg_client: TelegramClient,
        drivers: DriverRepository,
        dispatches: DispatchRepository,
        dispatch_service: DispatchService,
        agent: Any,  # LangGraph agent
    ):
        self.tg_client = tg_client
        self.drivers = drivers
        self.dispatches = dispatches
        self.dispatch_service = dispatch_service
        self.agent = agent

    # ---------------------------
    # Commands
    # ---------------------------
    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Envía un mensaje para iniciar la conversación.")

    async def id_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        await update.message.reply_text(f"Tu chat_id es: {chat_id}")

    # ---------------------------
    # Agent runner (cliente)
    # ---------------------------
    async def run_agent(self, user_text: str, chat_id: int) -> str:
        """
        Ejecuta el agente LangGraph y, si en la traza aparece una ToolMessage con payload
        {"ok": true, "driver_chat_id": ..., "message": "..."} entonces envía ese mensaje
        al domiciliario desde este contexto async (estable en Cloud Run).
        """
        if self.agent is None:
            return "El agente no está inicializado (revisa logs / secretos)."

        config = {"configurable": {"thread_id": str(chat_id)}}
        inputs: Dict[str, Any] = {
            "messages": [
                ("system", f"customer_chat_id={chat_id} (usa este valor exacto cuando llames herramientas)."),
                ("user", user_text),
            ]
        }

        try:
            result = await self.agent.ainvoke(inputs, config=config)
        except Exception:
            logger.exception("AGENT.ainvoke falló chat_id=%s", chat_id)
            return "Se presentó un error procesando el pedido."

        # 1) Si alguna tool devolvió payload con message para el domiciliario, enviarlo aquí
        try:
            for m in result.get("messages", []):
                if getattr(m, "type", None) == "tool":
                    content = getattr(m, "content", None)
                    if not isinstance(content, str) or not content:
                        continue
                    try:
                        payload = json.loads(content)
                    except Exception:
                        continue

                    if (
                        isinstance(payload, dict)
                        and payload.get("ok") is True
                        and payload.get("driver_chat_id") is not None
                        and payload.get("message")
                    ):
                        await self.tg_client.send_text(
                            chat_id=int(payload["driver_chat_id"]),
                            text=str(payload["message"]),
                            parse_mode="Markdown",
                        )
        except Exception:
            logger.exception("Fallo enviando mensaje al domiciliario desde run_agent chat_id=%s", chat_id)

        # 2) Respuesta final del agente al usuario
        try:
            messages = result.get("messages", [])
            for m in reversed(messages):
                if getattr(m, "type", None) == "ai":
                    return (m.content or "").strip()
        except Exception:
            logger.exception("Fallo extrayendo respuesta AI chat_id=%s", chat_id)

        return "No pude generar una respuesta. Intenta de nuevo."

    # ---------------------------
    # Reassign (cuando rechazan)
    # ---------------------------
    async def reassign_and_send(self, dispatch: Dict[str, Any], exclude_driver_chat_id: int) -> Dict[str, Any]:
        """
        Reasigna el pedido a otro driver disponible (excluyendo al que rechazó),
        actualiza repos y envía el mensaje al nuevo driver.
        """
        order = dispatch.get("order") or {}
        customer_chat_id = dispatch.get("customer_chat_id")

        # 1) asignar nuevo driver
        res = self.dispatch_service.assign_driver(order=order, exclude={int(exclude_driver_chat_id)})
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "No disponible")}

        new_driver_chat_id = int(res["driver_chat_id"])
        new_dispatch_id = str(res["dispatch_id"])
        new_driver_name = res.get("driver_name", "")

        # 2) registrar dispatch
        self.dispatch_service.register_dispatch(
            dispatch_id=new_dispatch_id,
            driver_chat_id=new_driver_chat_id,
            customer_chat_id=int(customer_chat_id) if customer_chat_id is not None else None,
            order=order,
            reassigned_from=dispatch.get("dispatch_id"),
        )

        # 3) enviar pedido al nuevo driver
        msg = self.dispatch_service.format_order_message(order)
        try:
            await self.tg_client.send_text(
                chat_id=new_driver_chat_id,
                text=msg,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.exception("Fallo enviando a nuevo driver chat_id=%s", new_driver_chat_id)

            # liberar driver para no dejarlo ocupado
            self.drivers.set_available(new_driver_chat_id, True)
            self.dispatches.clear_active_for_driver(new_driver_chat_id)

            # (Opcional) podrías marcar el dispatch como send_failed si lo persistes
            return {"ok": False, "error": "No pude enviar al nuevo domiciliario", "detail": str(e)}

        # 4) notificar cliente (opcional recomendado)
        try:
            if customer_chat_id is not None:
                await self.tg_client.send_text(
                    chat_id=int(customer_chat_id),
                    text=f"🔄 El domiciliario anterior no pudo. Ya asigné a {new_driver_name or 'otro domiciliario'} para tu pedido. (ID: {new_dispatch_id})",
                )
        except Exception:
            logger.exception("No pude notificar al cliente reasignación chat_id=%r", customer_chat_id)

        return {"ok": True, "dispatch_id": new_dispatch_id, "driver_chat_id": new_driver_chat_id}

    # ---------------------------
    # Domiciliario flow
    # ---------------------------
    async def handle_driver_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, driver_chat_id: int, text: str):
        driver = self.drivers.get_by_chat(driver_chat_id)
        t = (text or "").strip().lower()

        active = self.dispatches.get_active_dispatch_for_driver(int(driver_chat_id))
        if not active:
            await update.message.reply_text(
                "No tengo un pedido activo. Si te llega uno, responde ACEPTO, NO PUEDO o COMPLETADO."
            )
            return

        # Transformamos a dict “compatible” con tu lógica anterior
        dispatch = {
            "dispatch_id": active.dispatch_id,
            "driver_chat_id": active.driver_chat_id,
            "customer_chat_id": active.customer_chat_id,
            "order": active.order,
            "status": active.status,
        }
        customer_chat_id = dispatch.get("customer_chat_id")
        dispatch_id = dispatch.get("dispatch_id")

        # 1) ACEPTAR
        if t in ["acepto", "aceptar", "ok", "listo", "si", "sí"]:
            active.status = "accepted"
            active.accepted_ts = int(time.time())

            try:
                await context.bot.send_message(
                    chat_id=int(customer_chat_id),
                    text=f"✅ Tu pedido fue aceptado por {getattr(driver, 'name', 'el domiciliario')} y va en camino. (ID: {dispatch_id})",
                )
            except Exception:
                logger.exception("No pude notificar al cliente chat_id=%r", customer_chat_id)
                await update.message.reply_text("✅ Aceptado, pero no pude notificar al cliente (chat_id inválido).")
                return

            await update.message.reply_text("✅ Pedido aceptado. Cuando entregues, responde COMPLETADO.")
            return

        # 2) RECHAZAR
        if t in ["no puedo", "rechazo", "no", "cancelar"]:
            active.status = "rejected"
            active.rejected_ts = int(time.time())

            # liberar driver actual
            self.drivers.set_available(driver_chat_id, True)
            self.dispatches.clear_active_for_driver(driver_chat_id)

            # reasignar
            result = await self.reassign_and_send(dispatch, exclude_driver_chat_id=int(driver_chat_id))
            if result.get("ok"):
                await update.message.reply_text("Entendido. Reasigné el pedido a otro domiciliario.")
            else:
                # avisa al cliente si no hay nadie
                try:
                    await context.bot.send_message(
                        chat_id=int(customer_chat_id),
                        text=f"⚠️ El domiciliario no pudo tomar tu pedido (ID: {dispatch_id}). En este momento no tengo otro disponible. ¿Deseas esperar o cancelar?",
                    )
                except Exception:
                    logger.exception("No pude notificar al cliente sin disponibilidad chat_id=%r", customer_chat_id)

                await update.message.reply_text("Entendido. No hay otro domiciliario disponible por ahora.")
            return

        # 3) COMPLETAR
        if t in ["completado", "completo", "entregado", "finalizado", "terminado", "listo entregado"]:
            active.status = "completed"
            active.completed_ts = int(time.time())

            # liberar driver
            self.drivers.set_available(driver_chat_id, True)
            self.dispatches.clear_active_for_driver(driver_chat_id)

            # notificar al cliente
            try:
                await context.bot.send_message(
                    chat_id=int(customer_chat_id),
                    text=f"✅ Pedido entregado. ¡Gracias! (ID: {dispatch_id})",
                )
            except Exception:
                logger.exception("No pude notificar al cliente completado chat_id=%r", customer_chat_id)

            await update.message.reply_text("✅ Pedido marcado como COMPLETADO. Ya quedaste disponible.")
            return

        await update.message.reply_text("Responde únicamente con: ACEPTO, NO PUEDO o COMPLETADO.")

    # ---------------------------
    # Main router entrypoint
    # ---------------------------
    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = (update.message.text or "").strip()

        # 1) domiciliario
        if self.drivers.is_driver_chat(chat_id):
            try:
                await self.handle_driver_message(update, context, chat_id, text)
            except Exception:
                logger.exception("Error en handle_driver_message driver_chat_id=%s", chat_id)
                try:
                    await update.message.reply_text("Se presentó un error procesando tu respuesta. Reintenta.")
                except Exception:
                    pass
            return

        # 2) cliente
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            logger.exception("No pude enviar chat_action typing chat_id=%s", chat_id)

        answer = await self.run_agent(text, chat_id)

        try:
            await update.message.reply_text(answer)
        except Exception:
            logger.exception("Fallo reply_text al usuario chat_id=%s", chat_id)
