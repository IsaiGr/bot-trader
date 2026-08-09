"""
Configuración centralizada del Trading Signal System.
Valida variables de entorno al inicio (fail-fast) y centraliza constantes configurables.
"""

import os
import logging
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    """Configuración inmutable del sistema, cargada desde variables de entorno."""

    # --- API Keys ---
    OPENROUTER_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    WEBHOOK_API_KEY: str = ""  # Clave para autenticar requests entrantes al webhook

    # --- Modelo IA ---
    AI_MODEL: str = "deepseek/deepseek-chat-v3-0324"
    AI_TEMPERATURE: float = 0.1

    # --- Exchange WebSocket ---
    EXCHANGE_WS_URL: str = "wss://stream.binance.com:9443/ws"
    TRADING_PAIRS: list[str] = field(default_factory=lambda: ["ETHUSDT"])
    TIMEFRAMES: list[str] = field(default_factory=lambda: ["1h", "4h"])
    CANDLE_BUFFER_SIZE: int = 200
    SCANNER_COOLDOWN: int = 300  # Segundos entre alertas del mismo par

    # --- Risk Management Thresholds ---
    MIN_CONFIDENCE: int = 75           # Confianza mínima para despachar señal
    MAX_STOP_LOSS_PCT: float = 3.0     # Stop loss máximo permitido (%)
    MIN_RISK_REWARD_RATIO: float = 1.5 # Ratio mínimo take_profit / stop_loss
    RSI_OVERBOUGHT: float = 70.0       # RSI por encima = sobrecompra
    RSI_OVERSOLD: float = 30.0         # RSI por debajo = sobreventa

    # --- Telegram ---
    TELEGRAM_MAX_RETRIES: int = 3
    TELEGRAM_RETRY_BACKOFF: float = 1.0  # Segundos base para backoff exponencial


def load_settings() -> Settings:
    """
    Carga la configuración desde variables de entorno.
    Falla rápidamente si falta alguna variable crítica.
    """
    required_vars = ["OPENROUTER_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "WEBHOOK_API_KEY"]
    missing = [var for var in required_vars if not os.getenv(var)]

    if missing:
        raise RuntimeError(
            f"Variables de entorno requeridas no encontradas: {', '.join(missing)}. "
            f"Copia .env.example a .env y configura los valores."
        )

    # Parsear listas separadas por comas
    trading_pairs = [
        p.strip().upper()
        for p in os.getenv("TRADING_PAIRS", "ETHUSDT").split(",")
        if p.strip()
    ]
    timeframes = [
        tf.strip()
        for tf in os.getenv("TIMEFRAMES", "1h,4h").split(",")
        if tf.strip()
    ]

    return Settings(
        OPENROUTER_API_KEY=os.environ["OPENROUTER_API_KEY"],
        TELEGRAM_BOT_TOKEN=os.environ["TELEGRAM_BOT_TOKEN"],
        TELEGRAM_CHAT_ID=os.environ["TELEGRAM_CHAT_ID"],
        WEBHOOK_API_KEY=os.environ["WEBHOOK_API_KEY"],
        AI_MODEL=os.getenv("AI_MODEL", "deepseek/deepseek-chat-v3-0324"),
        AI_TEMPERATURE=float(os.getenv("AI_TEMPERATURE", "0.1")),
        EXCHANGE_WS_URL=os.getenv("EXCHANGE_WS_URL", "wss://stream.binance.com:9443/ws"),
        TRADING_PAIRS=trading_pairs,
        TIMEFRAMES=timeframes,
        CANDLE_BUFFER_SIZE=int(os.getenv("CANDLE_BUFFER_SIZE", "200")),
        SCANNER_COOLDOWN=int(os.getenv("SCANNER_COOLDOWN", "300")),
        MIN_CONFIDENCE=int(os.getenv("MIN_CONFIDENCE", "75")),
        MAX_STOP_LOSS_PCT=float(os.getenv("MAX_STOP_LOSS_PCT", "3.0")),
        MIN_RISK_REWARD_RATIO=float(os.getenv("MIN_RISK_REWARD_RATIO", "1.5")),
        RSI_OVERBOUGHT=float(os.getenv("RSI_OVERBOUGHT", "70.0")),
        RSI_OVERSOLD=float(os.getenv("RSI_OVERSOLD", "30.0")),
        TELEGRAM_MAX_RETRIES=int(os.getenv("TELEGRAM_MAX_RETRIES", "3")),
        TELEGRAM_RETRY_BACKOFF=float(os.getenv("TELEGRAM_RETRY_BACKOFF", "1.0")),
    )
