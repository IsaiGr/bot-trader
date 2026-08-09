"""
Trading Signal System — FastAPI Application
============================================
Recibe alertas de trading via webhook Y monitorea mercados en tiempo real
via WebSocket de Binance. Evalúa con IA (OpenRouter), aplica reglas de
riesgo y despacha señales aprobadas a Telegram.

Versión 2.2 — Trading 24/7 con WebSockets.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Security, Depends
from fastapi.security import APIKeyHeader

from openai import AsyncOpenAI

from config import Settings, load_settings
from models import (
    AIDecision,
    FilteredOutResponse,
    HealthResponse,
    ScannerStatus,
    SignalDispatchedResponse,
    TradingAlert,
)
from prompt_builder import SYSTEM_INSTRUCTION, build_user_prompt
from risk_engine import evaluate_risk
from telegram_notifier import (
    TelegramError,
    format_signal_message,
    send_telegram_message,
)
from candle_buffer import CandleBuffer
from exchange_ws import BinanceWSManager
from history_loader import fetch_historical_candles
from scanner import Scanner
from telegram_listener import TelegramListener
from paper_trader import PaperTrader

# ──────────────────────────────────────────────
#  Logging
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("trading_signal_system")

# ──────────────────────────────────────────────
#  Application State (inicializado en lifespan)
# ──────────────────────────────────────────────

settings: Settings | None = None
ai_client: AsyncOpenAI | None = None
candle_buffer: CandleBuffer | None = None
scanner: Scanner | None = None
ws_manager: BinanceWSManager | None = None
_ws_task: asyncio.Task | None = None
telegram_listener: TelegramListener | None = None
_tl_task: asyncio.Task | None = None
paper_trader: PaperTrader | None = None

TRADING_ACTIVE = True  # Kill-Switch global

# ──────────────────────────────────────────────
#  Comandos de Telegram (Kill-Switch)
# ──────────────────────────────────────────────

async def handle_telegram_command(command: str, chat_id: int) -> None:
    global TRADING_ACTIVE
    
    # Solo aceptamos comandos del dueño del bot (chat_id configurado)
    if str(chat_id) != settings.TELEGRAM_CHAT_ID:
        logger.warning(f"Comando ignorado de chat no autorizado: {chat_id}")
        return
        
    if command == "/stop":
        TRADING_ACTIVE = False
        msg = "🛑 <b>KILL-SWITCH ACTIVADO</b>\nEl trading ha sido pausado. Se rechazarán todas las señales."
        logger.warning("🛑 KILL-SWITCH ACTIVADO por Telegram.")
        await send_telegram_message(msg, settings)
    elif command == "/start":
        TRADING_ACTIVE = True
        msg = "✅ <b>TRADING REANUDADO</b>\nEl sistema vuelve a procesar señales."
        logger.info("✅ TRADING REANUDADO por Telegram.")
        await send_telegram_message(msg, settings)


# ──────────────────────────────────────────────
#  Pipeline de análisis (compartido webhook + scanner)
# ──────────────────────────────────────────────

async def analyze_and_dispatch(alert: TradingAlert) -> dict:
    """
    Pipeline completo: IA → Risk Engine → Telegram.
    Retorna el resultado como dict para logging/respuesta.
    """
    global TRADING_ACTIVE
    
    if not TRADING_ACTIVE:
        logger.warning(f"⛔ Señal de {alert.ticker} ignorada: KILL-SWITCH ACTIVO.")
        return {"status": "filtered_out", "reason": "Trading pausado por Kill-Switch"}
        
    logger.info(
        "📥 Procesando alerta [%s]: %s %s @ %.2f",
        alert.source, alert.ticker, alert.timeframe, alert.current_price,
    )

    # ── 1. Construir prompt ──
    user_prompt = build_user_prompt(alert)

    # ── 2. Llamar a OpenRouter (async) ──
    try:
        response = await ai_client.chat.completions.create(
            model=settings.AI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": user_prompt},
            ],
            temperature=settings.AI_TEMPERATURE,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.error("❌ Error al llamar a OpenRouter: %s", str(e))
        return {"status": "error", "detail": str(e)}

    # ── 3. Parsear y validar respuesta de IA ──
    raw_response = None
    try:
        raw_response = response.choices[0].message.content
        logger.info("🤖 Respuesta de IA recibida (%d chars)", len(raw_response))
        decision = AIDecision.model_validate_json(raw_response)
    except Exception as e:
        logger.error("❌ Respuesta de IA inválida: %s | Raw: %s", str(e), raw_response[:200] if raw_response else "None")
        return {"status": "error", "detail": "Respuesta IA inválida"}

    logger.info(
        "🧠 Decisión IA: %s | Confianza: %d%% | SL: %.1f%% | TP: %.1f%%",
        decision.action.value,
        decision.confidence_score,
        decision.recommended_stop_loss_pct,
        decision.recommended_take_profit_pct,
    )

    # ── 4. Aplicar reglas de riesgo (hard rules) ──
    verdict = evaluate_risk(decision, alert, settings)

    if not verdict.approved:
        logger.info("🚫 Señal filtrada: %s", verdict.summary)
        return {"status": "filtered_out", "reason": verdict.summary}

    # ── 5. Registrar en Paper Trader ──
    trade = paper_trader.open_trade(alert, decision)

    # ── 6. Enviar a Telegram ──
    message = format_signal_message(alert, decision)
    message += f"\n\n📝 <b>Paper Trade Registrado:</b> <code>{trade.trade_id}</code>"

    try:
        await send_telegram_message(message, settings)
    except TelegramError as e:
        logger.error("❌ No se pudo enviar a Telegram: %s", str(e))
        return {"status": "error", "detail": "Error al enviar a Telegram"}

    logger.info("✅ Señal despachada: %s %s", decision.action.value, alert.ticker)
    return {"status": "signal_dispatched", "action": decision.action.value}


# ──────────────────────────────────────────────
#  Callback del Scanner → Pipeline
# ──────────────────────────────────────────────

async def on_scanner_candle_closed(symbol: str, timeframe: str, candle: dict) -> None:
    """
    Callback invocado cuando se cierra una vela.
    Actualiza PaperTrader y evalúa Scanner.
    """
    # 1. Actualizar operaciones simuladas activas
    closed_trades = paper_trader.update_with_candle(symbol, candle)
    for trade in closed_trades:
        emoji = "✅" if trade.status == "CLOSED_WIN" else "❌"
        msg = (
            f"{emoji} <b>PAPER TRADE CERRADO</b>\n\n"
            f"🆔 <code>{trade.trade_id}</code>\n"
            f"📌 Par: <code>{trade.symbol}</code>\n"
            f"📊 Resultado: <b>{trade.status}</b>\n"
            f"💰 PnL: <b>{trade.pnl_pct:+.2f}%</b>"
        )
        await send_telegram_message(msg, settings)

    # 2. Generar nuevas alertas
    alert = await scanner.on_candle_closed(symbol, timeframe, candle)

    if alert is not None:
        alert.source = "scanner"
        result = await analyze_and_dispatch(alert)
        logger.info(
            "📊 Resultado scanner %s %s: %s",
            symbol, timeframe, result.get("status", "unknown"),
        )


# ──────────────────────────────────────────────
#  Lifespan
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa configuración, cliente IA, buffer de velas y WebSocket al arrancar."""
    global settings, ai_client, candle_buffer, scanner, ws_manager, _ws_task, telegram_listener, _tl_task, paper_trader

    logger.info("🚀 Iniciando Trading Signal System v2.2...")

    # Cargar y validar configuración
    settings = load_settings()
    logger.info("✅ Configuración cargada correctamente")
    logger.info(
        "📋 Parámetros: MIN_CONFIDENCE=%d%% | MAX_SL=%.1f%% | MIN_RR=%.1f",
        settings.MIN_CONFIDENCE,
        settings.MAX_STOP_LOSS_PCT,
        settings.MIN_RISK_REWARD_RATIO,
    )
    logger.info(
        "📊 Pares: %s | Timeframes: %s",
        ", ".join(settings.TRADING_PAIRS),
        ", ".join(settings.TIMEFRAMES),
    )

    # Inicializar cliente IA
    ai_client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.OPENROUTER_API_KEY,
    )
    logger.info("✅ Cliente IA inicializado (OpenRouter, modelo: %s)", settings.AI_MODEL)
    
    # Inicializar Paper Trader
    paper_trader = PaperTrader()
    logger.info("✅ Simulador de Paper Trading inicializado.")

    # Inicializar buffer de velas y scanner
    candle_buffer = CandleBuffer(max_size=settings.CANDLE_BUFFER_SIZE)
    scanner = Scanner(
        candle_buffer=candle_buffer,
        cooldown_seconds=settings.SCANNER_COOLDOWN,
    )
    logger.info("✅ Buffer de velas y Scanner inicializados (buffer: %d, cooldown: %ds)",
                settings.CANDLE_BUFFER_SIZE, settings.SCANNER_COOLDOWN)

    # ── WARM-UP: Cargar datos históricos ──
    logger.info("⏳ Descargando historial de velas para inicializar el scanner...")
    for symbol in settings.TRADING_PAIRS:
        for tf in settings.TIMEFRAMES:
            try:
                historical_candles = await fetch_historical_candles(symbol, tf, settings.CANDLE_BUFFER_SIZE)
                for c in historical_candles:
                    candle_buffer.add(symbol, tf, c)
                logger.info("✅ Historial cargado: %s %s (%d velas)", symbol, tf, len(historical_candles))
            except Exception as e:
                logger.error("❌ Error al cargar historial para %s %s: %s", symbol, tf, str(e))

    # Inicializar y lanzar WebSocket de Binance
    ws_manager = BinanceWSManager(
        ws_url=settings.EXCHANGE_WS_URL,
        symbols=settings.TRADING_PAIRS,
        timeframes=settings.TIMEFRAMES,
        on_candle_closed=on_scanner_candle_closed,
    )
    _ws_task = asyncio.create_task(ws_manager.start())
    logger.info("✅ WebSocket de Binance lanzado en background")
    
    # Inicializar y lanzar Telegram Listener (Kill-Switch)
    telegram_listener = TelegramListener(
        token=settings.TELEGRAM_BOT_TOKEN,
        on_command=handle_telegram_command
    )
    _tl_task = asyncio.create_task(telegram_listener.start())

    logger.info("🟢 Trading Signal System v2.2 — OPERATIVO 24/7")

    yield

    # Cleanup
    logger.info("🔄 Deteniendo sistema...")
    ws_manager.stop()
    if _ws_task and not _ws_task.done():
        _ws_task.cancel()
        
    if telegram_listener:
        telegram_listener.stop()
    if _tl_task and not _tl_task.done():
        _tl_task.cancel()
        
    try:
        await asyncio.gather(*[t for t in [_ws_task, _tl_task] if t is not None], return_exceptions=True)
    except asyncio.CancelledError:
        pass
        
    await ai_client.close()
    logger.info("👋 Trading Signal System detenido.")


# ──────────────────────────────────────────────
#  FastAPI App
# ──────────────────────────────────────────────

app = FastAPI(
    title="Trading Signal System",
    description="Sistema de señales de trading 24/7 con análisis IA, streaming en tiempo real y gestión de riesgo.",
    version="2.2.0",
    lifespan=lifespan,
)

# ──────────────────────────────────────────────
#  Autenticación
# ──────────────────────────────────────────────

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Verifica que el request incluya una API key válida."""
    if not api_key or api_key != settings.WEBHOOK_API_KEY:
        logger.warning("🔒 Request rechazado: API key inválida o faltante")
        raise HTTPException(status_code=401, detail="API key inválida o faltante")
    return api_key


# ──────────────────────────────────────────────
#  Endpoints
# ──────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint para monitoreo."""
    return HealthResponse()


@app.get("/status", response_model=ScannerStatus)
async def scanner_status():
    """Estado actual del scanner, WebSocket y buffers."""
    buffer_sizes = {}
    last_candle_times = {}

    for key in candle_buffer.get_all_keys():
        parts = key.rsplit("_", 1)
        if len(parts) == 2:
            symbol, tf = parts
            size = candle_buffer.size(symbol, tf)
            buffer_sizes[key] = size

            latest = candle_buffer.get_latest(symbol, tf)
            if latest:
                ts = datetime.fromtimestamp(
                    latest["timestamp"] / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S UTC")
                last_candle_times[key] = ts

    return ScannerStatus(
        ws_connected=ws_manager.is_connected if ws_manager else False,
        active_pairs=settings.TRADING_PAIRS,
        timeframes=settings.TIMEFRAMES,
        buffer_sizes=buffer_sizes,
        last_candle_times=last_candle_times,
    )


@app.post(
    "/webhook",
    response_model=SignalDispatchedResponse | FilteredOutResponse,
    dependencies=[Depends(verify_api_key)],
)
async def receive_webhook(alert: TradingAlert):
    """
    Recibe una alerta de trading manual (TradingView, etc.),
    la evalúa con IA, aplica reglas de riesgo y despacha a Telegram.
    """
    result = await analyze_and_dispatch(alert)

    if result["status"] == "error":
        raise HTTPException(status_code=502, detail=result.get("detail", "Error interno"))

    if result["status"] == "filtered_out":
        # Para construir la respuesta, necesitamos re-ejecutar parcialmente
        # En producción esto se refactorizaría, pero mantenemos retrocompatibilidad
        return FilteredOutResponse(
            reason=result.get("reason", "Filtrada"),
            decision=AIDecision(
                action="NO_ACTION",
                confidence_score=0,
                recommended_stop_loss_pct=1.0,
                recommended_take_profit_pct=2.0,
                rationale=result.get("reason", "Señal filtrada por reglas de riesgo"),
            ),
        )

    return SignalDispatchedResponse(
        decision=AIDecision(
            action=result.get("action", "NO_ACTION"),
            confidence_score=75,
            recommended_stop_loss_pct=1.0,
            recommended_take_profit_pct=2.0,
            rationale="Señal despachada exitosamente",
        ),
    )
