"""
Schemas Pydantic para validación de entrada, salida y respuestas del Trading Signal System.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ──────────────────────────────────────────────
#  Datos de mercado — Vela / Candlestick
# ──────────────────────────────────────────────

class Candle(BaseModel):
    """Vela (candlestick) de un par de trading."""

    timestamp: int = Field(..., description="Unix timestamp en milliseconds")
    open: float = Field(..., gt=0, description="Precio de apertura")
    high: float = Field(..., gt=0, description="Precio máximo")
    low: float = Field(..., gt=0, description="Precio mínimo")
    close: float = Field(..., gt=0, description="Precio de cierre")
    volume: float = Field(..., ge=0, description="Volumen del período")


# ──────────────────────────────────────────────
#  Entrada — Payload del webhook (TradingView / otro)
# ──────────────────────────────────────────────

class Indicators(BaseModel):
    """Indicadores técnicos recibidos en la alerta."""

    rsi_14: float = Field(..., ge=0, le=100, description="RSI de 14 períodos (0-100)")
    macd: str = Field(..., description="Estado del MACD: bullish_crossover, bearish_crossover, neutral")
    ema_20: float = Field(..., gt=0, description="EMA de 20 períodos")
    ema_200: float = Field(..., gt=0, description="EMA de 200 períodos")
    volume_24h_change_pct: float = Field(..., description="Cambio porcentual del volumen en 24h")
    atr_14: float = Field(..., ge=0, description="Average True Range (14 periodos)")
    fib_0_50: float = Field(..., description="Nivel Fibonacci 0.50 del macro rango")
    fib_0_618: float = Field(..., description="Nivel Fibonacci 0.618 del macro rango")
    in_golden_pocket: bool = Field(..., description="True si el precio actual está interactuando con el Golden Pocket")

    @field_validator("macd")
    @classmethod
    def validate_macd(cls, v: str) -> str:
        allowed = {"bullish_crossover", "bearish_crossover", "neutral"}
        if v not in allowed:
            raise ValueError(f"MACD debe ser uno de {allowed}, recibido: '{v}'")
        return v


class TradingAlert(BaseModel):
    """Payload completo de una alerta de trading entrante."""

    ticker: str = Field(..., min_length=1, max_length=20, description="Par de trading, ej: BTCUSDT")
    timeframe: str = Field(..., description="Temporalidad: 1m, 5m, 15m, 1h, 4h, 1d")
    current_price: float = Field(..., gt=0, description="Precio actual del activo")
    indicators: Indicators
    market_context: str = Field(default="", max_length=500, description="Contexto adicional del mercado")
    source: str = Field(default="webhook", description="Origen: webhook o scanner")

    @field_validator("timeframe")
    @classmethod
    def validate_timeframe(cls, v: str) -> str:
        allowed = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}
        if v not in allowed:
            raise ValueError(f"Timeframe debe ser uno de {allowed}, recibido: '{v}'")
        return v


# ──────────────────────────────────────────────
#  Salida — Decisión de la IA (respuesta de OpenRouter)
# ──────────────────────────────────────────────

class TradeAction(str, Enum):
    """Acciones posibles de trading."""
    BUY = "BUY"
    SELL = "SELL"
    NO_ACTION = "NO_ACTION"


class AIDecision(BaseModel):
    """Decisión generada por la IA, validada contra el schema esperado."""

    action: TradeAction = Field(..., description="Acción recomendada")
    confidence_score: int = Field(..., ge=0, le=100, description="Nivel de confianza 0-100")
    recommended_stop_loss_pct: float = Field(..., gt=0, le=10, description="Stop loss recomendado (%)")
    recommended_take_profit_pct: float = Field(..., gt=0, le=50, description="Take profit recomendado (%)")
    rationale: str = Field(..., min_length=10, max_length=1000, description="Justificación del análisis")


# ──────────────────────────────────────────────
#  Respuestas del API
# ──────────────────────────────────────────────

class SignalDispatchedResponse(BaseModel):
    """Respuesta cuando la señal fue aprobada y enviada a Telegram."""
    status: Literal["signal_dispatched"] = "signal_dispatched"
    decision: AIDecision


class FilteredOutResponse(BaseModel):
    """Respuesta cuando la señal fue filtrada por las reglas de riesgo."""
    status: Literal["filtered_out"] = "filtered_out"
    reason: str
    decision: AIDecision


class HealthResponse(BaseModel):
    """Respuesta del endpoint de health check."""
    status: Literal["healthy"] = "healthy"
    service: str = "trading-signal-system"
    version: str = "2.2.0"


# ──────────────────────────────────────────────
#  Estado del Scanner
# ──────────────────────────────────────────────

class ScannerStatus(BaseModel):
    """Estado actual del scanner y WebSocket."""
    ws_connected: bool = Field(..., description="Si el WebSocket está conectado")
    active_pairs: list[str] = Field(..., description="Pares de trading activos")
    timeframes: list[str] = Field(..., description="Temporalidades monitoreadas")
    buffer_sizes: dict[str, int] = Field(
        default_factory=dict,
        description="Cantidad de velas en buffer por par_timeframe",
    )
    last_candle_times: dict[str, str] = Field(
        default_factory=dict,
        description="Timestamp de la última vela cerrada por par_timeframe",
    )
