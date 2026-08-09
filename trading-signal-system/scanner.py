import logging
import time
from typing import Optional, Dict

import pandas as pd
import numpy as np

from candle_buffer import CandleBuffer
from models import TradingAlert, Indicators

logger = logging.getLogger(__name__)

class Scanner:
    """
    Escáner que procesa velas cerradas usando Pandas y decide si activar la tubería de análisis de IA.
    Implementa el filtro macro del Golden Pocket (0.50 - 0.618 de Fibonacci).
    """
    def __init__(self, candle_buffer: CandleBuffer, cooldown_seconds: int = 300):
        self.candle_buffer = candle_buffer
        self.cooldown_seconds = cooldown_seconds
        self._last_alert_time: Dict[str, float] = {}

    async def on_candle_closed(self, symbol: str, timeframe: str, candle: dict) -> Optional[TradingAlert]:
        """
        Procesa una vela recién cerrada, calcula los indicadores usando Pandas y 
        evalúa si el precio está interactuando con el Golden Pocket de Fibonacci.
        """
        logger.info(f"🔍 Escaneando vela cerrada para {symbol} en {timeframe}")
        
        # Añadir la vela al búfer
        self.candle_buffer.add(symbol, timeframe, candle)
        
        # Comprobar tiempo de enfriamiento (cooldown)
        current_time = time.time()
        last_time = self._last_alert_time.get(symbol, 0)
        if current_time - last_time < self.cooldown_seconds:
            logger.info(f"⏳ Saltando {symbol} debido al tiempo de enfriamiento.")
            return None
            
        # Comprobar si hay suficientes velas (mínimo 200 para EMA 200 y macro Fib)
        if self.candle_buffer.size(symbol, timeframe) < 200:
            logger.debug(f"Datos insuficientes para {symbol} en {timeframe}.")
            return None
            
        # Obtener los datos como DataFrame de Pandas
        candles = self.candle_buffer.get_candles(symbol, timeframe)
        df = pd.DataFrame(candles)
        
        # ── 1. Cálculos de Indicadores con Pandas ──
        
        # EMAs
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
        
        # Wilder's RSI (14)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss
        df['rsi_14'] = 100 - (100 / (1 + rs))
        
        # MACD (12, 26, 9)
        ema_12 = df['close'].ewm(span=12, adjust=False).mean()
        ema_26 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = ema_12 - ema_26
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        
        # ATR (14)
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df['atr_14'] = true_range.rolling(14).mean()
        
        # Volume Change 24h
        avg_volume_24 = df['volume'].shift(1).rolling(24).mean()
        df['volume_change'] = ((df['volume'] - avg_volume_24) / avg_volume_24) * 100
        
        # ── 2. Extracción y Lógica Condicional ──
        
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Estado del MACD
        macd_state = "neutral"
        if prev['macd'] <= prev['macd_signal'] and latest['macd'] > latest['macd_signal']:
            macd_state = "bullish_crossover"
        elif prev['macd'] >= prev['macd_signal'] and latest['macd'] < latest['macd_signal']:
            macd_state = "bearish_crossover"
            
        # ── 3. Filtro Fibonacci (Golden Pocket) ──
        # Basado en el rango macro (últimas 200 velas)
        macro_high = df['high'].max()
        macro_low = df['low'].min()
        diff = macro_high - macro_low
        
        # El Golden Pocket suele definirse entre el 0.50 y el 0.618 del retroceso
        pocket_bottom = macro_low + (diff * 0.50)
        pocket_top = macro_low + (diff * 0.618)
        
        # Verificamos si la vela actual (sus mechas o cuerpo) tocó esta zona
        curr_high = latest['high']
        curr_low = latest['low']
        
        in_golden_pocket = False
        if (curr_low <= pocket_top) and (curr_high >= pocket_bottom):
            in_golden_pocket = True
            
        # ¡FILTRO ESTRICTO! Si no está en el Golden Pocket, no hay alerta
        if not in_golden_pocket:
            logger.debug(f"📉 Precio de {symbol} fuera del Golden Pocket ({pocket_bottom:.2f} - {pocket_top:.2f}). Ignorando.")
            return None
            
        # ── 4. Construcción del Payload para la IA ──
        
        indicators = Indicators(
            rsi_14=float(latest['rsi_14']) if pd.notna(latest['rsi_14']) else 50.0,
            macd=macd_state,
            ema_20=float(latest['ema_20']),
            ema_200=float(latest['ema_200']),
            volume_24h_change_pct=float(latest['volume_change']) if pd.notna(latest['volume_change']) else 0.0,
            atr_14=float(latest['atr_14']) if pd.notna(latest['atr_14']) else 0.0,
            fib_0_50=float(pocket_bottom),
            fib_0_618=float(pocket_top),
            in_golden_pocket=in_golden_pocket
        )
        
        alert = TradingAlert(
            ticker=symbol,
            timeframe=timeframe,
            current_price=float(latest['close']),
            indicators=indicators,
            market_context=(
                f"El precio actual ha entrado en la zona macro de Golden Pocket de Fibonacci "
                f"(entre {pocket_bottom:.2f} y {pocket_top:.2f}). "
                f"La IA debe evaluar si esto representa un rebote o una ruptura."
            )
        )
        
        self._last_alert_time[symbol] = current_time
        logger.info(f"🎯 ¡Alerta generada! {symbol} ha entrado al Golden Pocket.")
        
        return alert
