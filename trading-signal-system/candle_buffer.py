import logging
from collections import deque
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

class CandleBuffer:
    """
    Un búfer circular para almacenar las últimas N velas por par de símbolo/marco temporal.
    """

    def __init__(self, max_size: int = 200) -> None:
        """
        Inicializa el búfer de velas.

        :param max_size: El tamaño máximo del búfer para cada clave (símbolo_marco temporal).
        """
        self.max_size = max_size
        self._buffers: Dict[str, deque] = {}

    def _get_key(self, symbol: str, timeframe: str) -> str:
        """
        Genera la clave de almacenamiento.
        """
        return f"{symbol}_{timeframe}"

    def add(self, symbol: str, timeframe: str, candle_dict: Dict[str, Any]) -> None:
        """
        Agrega una nueva vela al búfer.

        :param symbol: Símbolo (ej. 'ETHUSDT').
        :param timeframe: Marco temporal (ej. '1h').
        :param candle_dict: Diccionario con la información de la vela.
        """
        key = self._get_key(symbol, timeframe)
        if key not in self._buffers:
            self._buffers[key] = deque(maxlen=self.max_size)
            logger.info(f"Nuevo símbolo/marco temporal detectado: {symbol} - {timeframe}")
        
        self._buffers[key].append(candle_dict)

    def get_candles(self, symbol: str, timeframe: str) -> List[Dict[str, Any]]:
        """
        Obtiene todas las velas almacenadas para el símbolo y marco temporal dados.
        """
        key = self._get_key(symbol, timeframe)
        if key in self._buffers:
            return list(self._buffers[key])
        return []

    def get_closes(self, symbol: str, timeframe: str) -> List[float]:
        """
        Obtiene una lista de los precios de cierre para un símbolo y marco temporal.
        """
        candles = self.get_candles(symbol, timeframe)
        return [candle["close"] for candle in candles]

    def get_volumes(self, symbol: str, timeframe: str) -> List[float]:
        """
        Obtiene una lista de los volúmenes para un símbolo y marco temporal.
        """
        candles = self.get_candles(symbol, timeframe)
        return [candle["volume"] for candle in candles]

    def size(self, symbol: str, timeframe: str) -> int:
        """
        Devuelve el número de velas almacenadas actualmente.
        """
        key = self._get_key(symbol, timeframe)
        if key in self._buffers:
            return len(self._buffers[key])
        return 0

    def get_latest(self, symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        """
        Obtiene la vela más reciente, o None si el búfer está vacío.
        """
        key = self._get_key(symbol, timeframe)
        if key in self._buffers and self._buffers[key]:
            return self._buffers[key][-1]
        return None

    def get_all_keys(self) -> List[str]:
        """
        Obtiene una lista de todas las claves de búfer activas.
        """
        return list(self._buffers.keys())
