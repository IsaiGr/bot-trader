import csv
import logging
import os
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Dict, List, Optional

from models import TradingAlert, AIDecision, TradeAction

logger = logging.getLogger(__name__)

@dataclass
class PaperTrade:
    """Representa una operación de trading simulada (virtual)."""
    trade_id: str
    symbol: str
    action: str  # BUY or SELL
    entry_price: float
    stop_loss: float
    take_profit: float
    entry_time: datetime
    status: str = "OPEN" # OPEN, CLOSED_WIN, CLOSED_LOSS
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    pnl_pct: Optional[float] = None

class PaperTrader:
    """
    Gestor de Paper Trading. Mantiene las operaciones virtuales en memoria y 
    evalúa cada nueva vela del mercado para cerrar posiciones cuando tocan SL o TP.
    Guarda un historial en un archivo CSV local para análisis de rendimiento.
    """
    def __init__(self, history_file: str = "data/paper_trades_history.csv"):
        self.history_file = history_file
        self.active_trades: Dict[str, PaperTrade] = {}
        
        # Ensure directory exists for docker volume
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
        
        self._init_csv()

    def _init_csv(self):
        if not os.path.exists(self.history_file):
            logger.info(f"📄 Creando nuevo archivo de historial de paper trading: {self.history_file}")
            with open(self.history_file, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Trade_ID", "Date_Entry", "Date_Exit", "Symbol", "Action",
                    "Entry_Price", "Exit_Price", "Stop_Loss", "Take_Profit", 
                    "Status", "PnL_Pct"
                ])

    def _save_open_trade(self, trade: PaperTrade):
        """Registra un trade abierto en el archivo histórico para persistencia."""
        try:
            with open(self.history_file, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    trade.trade_id,
                    trade.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "",  # exit_time
                    trade.symbol,
                    trade.action,
                    f"{trade.entry_price:.4f}",
                    "",  # exit_price
                    f"{trade.stop_loss:.4f}",
                    f"{trade.take_profit:.4f}",
                    "OPEN",
                    "0.00"
                ])
        except Exception as e:
            logger.error(f"❌ Error al guardar trade abierto en CSV: {e}")

    def open_trade(self, alert: TradingAlert, decision: AIDecision) -> PaperTrade:
        """Abre una nueva posición virtual y la guarda en memoria."""
        trade_id = str(uuid.uuid4())[:8]
        entry_price = alert.current_price
        
        # Calcular niveles exactos de precio para el SL y TP
        if decision.action == TradeAction.BUY:
            sl_price = entry_price * (1 - (decision.recommended_stop_loss_pct / 100))
            tp_price = entry_price * (1 + (decision.recommended_take_profit_pct / 100))
        else: # SELL
            sl_price = entry_price * (1 + (decision.recommended_stop_loss_pct / 100))
            tp_price = entry_price * (1 - (decision.recommended_take_profit_pct / 100))

        trade = PaperTrade(
            trade_id=trade_id,
            symbol=alert.ticker,
            action=decision.action.value,
            entry_price=entry_price,
            stop_loss=sl_price,
            take_profit=tp_price,
            entry_time=datetime.now(timezone.utc)
        )
        self.active_trades[trade_id] = trade
        logger.info(f"📝 Paper Trade Abierto [{trade_id}]: {trade.action} {trade.symbol} @ {trade.entry_price:.2f} (SL: {trade.stop_loss:.2f}, TP: {trade.take_profit:.2f})")
        self._save_open_trade(trade)
        return trade

    def update_with_candle(self, symbol: str, candle: dict) -> List[PaperTrade]:
        """
        Evalúa si la vela entrante cruzó el SL o TP de algún trade abierto.
        Retorna los trades que se hayan cerrado en esta actualización.
        """
        closed_trades = []
        high = candle["high"]
        low = candle["low"]
        close_time = datetime.fromtimestamp(candle["timestamp"]/1000, tz=timezone.utc)
        
        # Iterar sobre una copia para poder borrar del original
        for trade_id, trade in list(self.active_trades.items()):
            if trade.symbol != symbol:
                continue
                
            closed = False
            
            if trade.action == "BUY":
                # Si el mínimo de la vela tocó el SL
                if low <= trade.stop_loss:
                    trade.status = "CLOSED_LOSS"
                    trade.exit_price = trade.stop_loss
                    trade.pnl_pct = -abs((trade.entry_price - trade.stop_loss) / trade.entry_price * 100)
                    closed = True
                # Si el máximo de la vela tocó el TP
                elif high >= trade.take_profit:
                    trade.status = "CLOSED_WIN"
                    trade.exit_price = trade.take_profit
                    trade.pnl_pct = abs((trade.take_profit - trade.entry_price) / trade.entry_price * 100)
                    closed = True
                    
            elif trade.action == "SELL":
                # Si el máximo de la vela tocó el SL
                if high >= trade.stop_loss:
                    trade.status = "CLOSED_LOSS"
                    trade.exit_price = trade.stop_loss
                    trade.pnl_pct = -abs((trade.stop_loss - trade.entry_price) / trade.entry_price * 100)
                    closed = True
                # Si el mínimo de la vela tocó el TP
                elif low <= trade.take_profit:
                    trade.status = "CLOSED_WIN"
                    trade.exit_price = trade.take_profit
                    trade.pnl_pct = abs((trade.entry_price - trade.take_profit) / trade.entry_price * 100)
                    closed = True
                    
            if closed:
                trade.exit_time = close_time
                self._save_to_csv(trade)
                closed_trades.append(trade)
                del self.active_trades[trade_id]
                logger.info(f"🏁 Paper Trade Cerrado [{trade_id}]: {trade.status} | PnL: {trade.pnl_pct:+.2f}%")
                
        return closed_trades

    def _save_to_csv(self, trade: PaperTrade):
        """Guarda un trade cerrado en el archivo histórico."""
        with open(self.history_file, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                trade.trade_id,
                trade.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                trade.exit_time.strftime("%Y-%m-%d %H:%M:%S") if trade.exit_time else "",
                trade.symbol,
                trade.action,
                f"{trade.entry_price:.4f}",
                f"{trade.exit_price:.4f}",
                f"{trade.stop_loss:.4f}",
                f"{trade.take_profit:.4f}",
                trade.status,
                f"{trade.pnl_pct:.2f}"
            ])
