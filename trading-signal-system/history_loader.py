import httpx
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

async def fetch_historical_candles(symbol: str, timeframe: str, limit: int = 200) -> List[Dict[str, Any]]:
    """
    Descarga el historial de velas desde la API REST pública de Binance.
    Ideal para llenar el buffer (warm-up) antes de iniciar los WebSockets.
    """
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": symbol.upper(),
        "interval": timeframe,
        "limit": limit
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, timeout=15.0)
        response.raise_for_status()
        data = response.json()
        
    candles = []
    for k in data:
        # Binance REST API devuelve un array (list) para cada vela:
        # [
        #   0: Open time, 1: Open, 2: High, 3: Low, 4: Close, 5: Volume,
        #   6: Close time, 7: Quote asset volume, 8: Number of trades,
        #   9: Taker buy base asset volume, 10: Taker buy quote asset volume, 11: Ignore
        # ]
        candle = {
            "timestamp": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        candles.append(candle)
        
    return candles
