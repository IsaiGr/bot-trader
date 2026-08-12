"""
Notificador de Telegram con retry y backoff exponencial.
"""

from __future__ import annotations

import asyncio
import logging
from html import escape as html_escape

import httpx

from config import Settings
from models import AIDecision, TradingAlert

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def format_signal_message(alert: TradingAlert, decision: AIDecision) -> str:
    """Formatea la señal de trading como mensaje de Telegram con HTML."""

    action_emoji = "🟢" if decision.action.value == "BUY" else "🔴"

    return (
        f"🚨 <b>NUEVA SEÑAL DE TRADING</b> 🚨\n"
        f"{'━' * 28}\n\n"
        f"📌 <b>Par:</b> <code>{alert.ticker}</code>\n"
        f"🕐 <b>Timeframe:</b> <code>{alert.timeframe}</code>\n"
        f"💰 <b>Precio:</b> <code>{alert.current_price:,.2f}</code>\n\n"
        f"{action_emoji} <b>Acción:</b> <code>{decision.action.value}</code>\n"
        f"🎯 <b>Confianza:</b> <code>{decision.confidence_score}%</code>\n"
        f"🛑 <b>Stop Loss:</b> <code>{decision.recommended_stop_loss_pct:.1f}%</code>\n"
        f"📈 <b>Take Profit:</b> <code>{decision.recommended_take_profit_pct:.1f}%</code>\n\n"
        f"📊 <b>Indicadores:</b>\n"
        f"  • RSI(14): <code>{alert.indicators.rsi_14}</code>\n"
        f"  • MACD: <code>{alert.indicators.macd}</code>\n"
        f"  • EMA 20: <code>{alert.indicators.ema_20:,.2f}</code>\n"
        f"  • EMA 200: <code>{alert.indicators.ema_200:,.2f}</code>\n"
        f"  • Vol 24h: <code>{alert.indicators.volume_24h_change_pct:+.1f}%</code>\n\n"
        f"📝 <b>Análisis:</b>\n{html_escape(decision.rationale)}"
    )


async def send_telegram_message(
    message: str,
    settings: Settings,
) -> bool:
    """
    Envía un mensaje a Telegram con retry y backoff exponencial.
    Retorna True si se envió exitosamente, False si falló después de todos los intentos.
    Raises TelegramError si falla después de agotar reintentos.
    """
    url = f"{TELEGRAM_API_BASE}/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }

    last_error: Exception | None = None

    for attempt in range(1, settings.TELEGRAM_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as http_client:
                response = await http_client.post(url, json=payload)

                # Log response body on error for debugging
                if response.status_code != 200:
                    logger.warning(
                        "⚠️ Telegram HTTP %d (intento %d/%d): %s",
                        response.status_code, attempt, settings.TELEGRAM_MAX_RETRIES,
                        response.text[:300],
                    )
                    response.raise_for_status()

                result = response.json()
                if result.get("ok"):
                    logger.info("📨 Mensaje enviado a Telegram (intento %d)", attempt)
                    return True
                else:
                    logger.warning(
                        "⚠️ Telegram respondió ok=false: %s (intento %d)",
                        result.get("description", "sin descripción"),
                        attempt,
                    )
                    last_error = RuntimeError(result.get("description", "Telegram error"))

        except httpx.HTTPStatusError as e:
            last_error = e

        except httpx.RequestError as e:
            logger.warning(
                "⚠️ Error de red con Telegram (intento %d/%d): %s",
                attempt, settings.TELEGRAM_MAX_RETRIES, str(e),
            )
            last_error = e

        # Backoff exponencial antes del siguiente intento
        if attempt < settings.TELEGRAM_MAX_RETRIES:
            wait_time = settings.TELEGRAM_RETRY_BACKOFF * (2 ** (attempt - 1))
            logger.info("⏳ Reintentando en %.1f segundos...", wait_time)
            await asyncio.sleep(wait_time)

    # Agotados todos los intentos
    logger.error(
        "❌ Fallo al enviar mensaje a Telegram después de %d intentos. Último error: %s",
        settings.TELEGRAM_MAX_RETRIES,
        str(last_error),
    )
    raise TelegramError(
        f"No se pudo enviar el mensaje después de {settings.TELEGRAM_MAX_RETRIES} intentos"
    ) from last_error


class TelegramError(Exception):
    """Error al enviar mensaje a Telegram después de agotar reintentos."""
    pass
