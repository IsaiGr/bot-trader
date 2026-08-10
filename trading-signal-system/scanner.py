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
    Implementa el filtro macro del Golden Pocket y cruces de MACD/RSI.
    """
    def __init__(self, candle_buffer: CandleBuffer, cooldown_seconds: int = 300):
        self.candle_buffer = candle_buffer
        self.cooldown_seconds = cooldown_seconds
        self._last_alert_time: Dict[str, float] = {}

    async def on_candle_closed(self, symbol: str, timeframe: str, candle: dict) -> Optional[TradingAlert]:
        """
        Procesa una vela recién cerrada, calcula los indicadores usando Pandas y 
        evalúa si el precio está en una situación interesante.
        """
        # Añadir la vela al búfer
        self.candle_buffer.add(symbol, timeframe, candle)
        
        # Comprobar tiempo de enfriamiento (cooldown)
        current_time = time.time()
        last_time = self._last_alert_time.get(symbol, 0)
        if current_time - last_time < self.cooldown_seconds:
            return None
            
        # Comprobar si hay suficientes velas (mínimo 200 para EMA 200 y macro Fib)
        if self.candle_buffer.size(symbol, timeframe) < 200:
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
        
        # 1. Trigger de MACD
        macd_state = "neutral"
        if prev['macd'] <= prev['macd_signal'] and latest['macd'] > latest['macd_signal']:
            macd_state = "bullish_crossover"
        elif prev['macd'] >= prev['macd_signal'] and latest['macd'] < latest['macd_signal']:
            macd_state = "bearish_crossover"
            
        # 2. Trigger de Fibonacci
        macro_high = df['high'].max()
        macro_low = df['low'].min()
        diff = macro_high - macro_low
        pocket_bottom = macro_low + (diff * 0.50)
        pocket_top = macro_low + (diff * 0.618)
        
        curr_high = latest['high']
        curr_low = latest['low']
        in_golden_pocket = (curr_low <= pocket_top) and (curr_high >= pocket_bottom)
        
        # --- FILTRO MATEMÁTICO INTELIGENTE ---
        trigger_reasons = []
        if macd_state != "neutral":
            trigger_reasons.append(f"Cruce MACD ({macd_state})")
        if latest['rsi_14'] < 30:
            trigger_reasons.append("RSI Sobrevendido (<30)")
        elif latest['rsi_14'] > 70:
            trigger_reasons.append("RSI Sobrecomprado (>70)")
        if in_golden_pocket:
            trigger_reasons.append("Precio en Golden Pocket (Fibonacci)")
            
        # Si el mercado está muerto (sin triggers), no hacemos nada
        if not trigger_reasons:
            logger.debug(f"📉 {symbol}: Mercado sin estructura interesante. Ignorando.")
            return None
            
        # Si hay acción, armamos los datos para la IA
        reasons_str = ", ".join(trigger_reasons)
        
        indicators = Indicators(
            rsi_14=float(latest['rsi_14']) if pd.notna(latest['rsi_14']) else 50.0,
            macd=macd_state,
            ema_20=float(latest['ema_20']),
            ema_200=float(latest['ema_200']),
            volume_24h_change_pct=float(latest['volume_change']) if pd.notna(latest['volume_change']) else 0.0,
            atr_14=float(latest['atr_14']) if pd.notna(latest['atr_14']) else 0.0,
            fib_0_50=float(pocket_bottom),
            fib_0_618=float(pocket_top),
            in_golden_pocket=bool(in_golden_pocket)
        )
        
        alert = TradingAlert(
            ticker=symbol,
            timeframe=timeframe,
            current_price=float(latest['close']),
            indicators=indicators,
            market_context=(
                f"El escáner matemático detectó las siguientes condiciones: {reasons_str}. "
                f"Analiza si esto representa una oportunidad real de ganancia (pequeña o grande) "
                f"y filtra cualquier señal falsa. Si es seguro, envía la señal."
            )
        )
        
        self._last_alert_time[symbol] = current_time
        logger.info(f"🎯 Condiciones cumplidas ({reasons_str}). Enviando a la IA...")
        
        return alert
