import asyncio
import json
import logging
import random
from typing import Callable, List, Dict, Any, Awaitable

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

class BinanceWSManager:
    """
    Gestor de WebSocket para conectarse a los flujos de klines de Binance.
    """

    def __init__(
        self,
        ws_url: str,
        symbols: List[str],
        timeframes: List[str],
        on_candle_closed: Callable[[str, str, Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """
        Inicializa el gestor de WebSocket.
        """
        self.ws_url = ws_url
        self.symbols = symbols
        self.timeframes = timeframes
        self.on_candle_closed = on_candle_closed
        self.is_running = False
        self._ws: websockets.WebSocketClientProtocol | None = None

    @property
    def is_connected(self) -> bool:
        """
        Verifica si la conexión WebSocket está activa.
        """
        return self._ws is not None and self._ws.state.name == "OPEN"

    async def start(self) -> None:
        """
        Inicia el bucle principal de conexión. Intenta reconectar en caso de fallo.
        """
        self.is_running = True
        backoff = 1.0

        while self.is_running:
            try:
                logger.info(f"🔌 Conectando a {self.ws_url}...")
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10
                ) as ws:
                    self._ws = ws
                    logger.info("🔌 ¡Conexión establecida con éxito!")
                    backoff = 1.0  # Restablece el backoff en conexión exitosa

                    # Preparar mensaje de suscripción
                    streams = []
                    for symbol in self.symbols:
                        for timeframe in self.timeframes:
                            streams.append(f"{symbol.lower()}@kline_{timeframe}")
                    
                    sub_msg = {
                        "method": "SUBSCRIBE",
                        "params": streams,
                        "id": 1
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info(f"🔌 Suscripción enviada para {len(streams)} flujos")

                    await self._process_messages(ws)
                    
            except ConnectionClosed as e:
                logger.warning(f"⚠️ Conexión cerrada: {e}")
            except Exception as e:
                logger.error(f"❌ Error inesperado: {e}")

            if not self.is_running:
                break

            # Reconexión con backoff exponencial y jitter
            jitter = random.uniform(0.5, 1.5)
            sleep_time = min(backoff * jitter, 60.0)
            logger.warning(f"⚠️ Reintentando conexión en {sleep_time:.2f} segundos...")
            await asyncio.sleep(sleep_time)
            backoff = min(backoff * 2, 60.0)

    def stop(self) -> None:
        """
        Detiene el bucle principal de WebSocket.
        """
        logger.info("🔌 Deteniendo gestor de WebSocket...")
        self.is_running = False

    async def _process_messages(self, ws: websockets.WebSocketClientProtocol) -> None:
        """
        Procesa los mensajes entrantes del WebSocket.
        """
        async for message in ws:
            if not self.is_running:
                break
            
            try:
                data = json.loads(message)
                
                # Ignorar mensajes de confirmación de suscripción u otros sin datos kline
                if "e" not in data or data["e"] != "kline":
                    continue
                
                k = data["k"]
                
                # Procesar solo las velas cerradas
                if k.get("x") is True:
                    symbol = data["s"]
                    timeframe = k["i"]
                    
                    candle = {
                        "timestamp": int(k["t"]),
                        "open": float(k["o"]),
                        "high": float(k["h"]),
                        "low": float(k["l"]),
                        "close": float(k["c"]),
                        "volume": float(k["v"]),
                    }
                    
                    logger.info(f"📊 Vela cerrada para {symbol} ({timeframe}): Cierre={candle['close']}")
                    await self.on_candle_closed(symbol, timeframe, candle)
                    
            except json.JSONDecodeError:
                logger.error("❌ Error al decodificar mensaje JSON")
            except KeyError as e:
                logger.error(f"❌ Falta la clave esperada en los datos: {e}")
            except Exception as e:
                logger.error(f"❌ Error al procesar mensaje: {e}")
