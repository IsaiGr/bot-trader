"""
Motor de reglas de riesgo post-IA (hard rules).
Estas reglas se aplican DESPUÉS de recibir la decisión de Gemini y NO pueden ser saltadas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config import Settings
from models import AIDecision, TradingAlert, TradeAction

logger = logging.getLogger(__name__)


@dataclass
class RiskVerdict:
    """Resultado de la evaluación de riesgo."""
    approved: bool
    reasons: list[str]

    @property
    def summary(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "Aprobado"


def evaluate_risk(
    decision: AIDecision,
    alert: TradingAlert,
    settings: Settings,
) -> RiskVerdict:
    """
    Aplica hard rules de gestión de riesgo sobre la decisión de Gemini.
    Retorna un RiskVerdict indicando si la señal fue aprobada o rechazada, con razones.
    """
    reasons: list[str] = []

    # ── Regla 0: NO_ACTION siempre se filtra ──
    if decision.action == TradeAction.NO_ACTION:
        reasons.append(f"Acción es NO_ACTION")
        return RiskVerdict(approved=False, reasons=reasons)
        
    # ── Regla 0.5: Imposición de Riesgo (Hardcoded SL/TP con ATR) ──
    # Quitamos el control a la IA y forzamos la matemática exacta.
    atr = alert.indicators.atr_14
    price = alert.current_price
    
    # Calcular límite dinámico de SL basado en ATR
    dynamic_max_sl = settings.MAX_STOP_LOSS_PCT  # Default
    if atr > 0 and price > 0:
        sl_distance = atr * 1.5 # 1.5x ATR
        sl_pct = (sl_distance / price) * 100
        tp_pct = sl_pct * 2.0 # Ratio R:R de 2.0 fijo
        
        dynamic_max_sl = min(max(settings.MAX_STOP_LOSS_PCT, sl_pct * 1.2), 10.0)
        
        logger.info(f"📐 Recalculando SL/TP: IA sugería SL={decision.recommended_stop_loss_pct:.2f}% TP={decision.recommended_take_profit_pct:.2f}% | Python impone SL={sl_pct:.2f}% TP={tp_pct:.2f}% (basado en ATR)")
        
        decision.recommended_stop_loss_pct = round(sl_pct, 2)
        decision.recommended_take_profit_pct = round(tp_pct, 2)
        decision.rationale += f"\n[Risk Engine Override]: SL y TP recalculados matemáticamente usando ATR (1.5x ATR). SL fijado en {sl_pct:.2f}%, TP en {tp_pct:.2f}%."

    # ── Regla 1: Confianza mínima ──
    if decision.confidence_score < settings.MIN_CONFIDENCE:
        reasons.append(
            f"Confianza {decision.confidence_score}% < mínimo {settings.MIN_CONFIDENCE}%"
        )

    # ── Regla 2: Stop loss máximo ──
    if decision.recommended_stop_loss_pct > dynamic_max_sl:
        reasons.append(
            f"Stop loss {decision.recommended_stop_loss_pct}% > máximo {dynamic_max_sl}%"
        )

    # ── Regla 3: Ratio riesgo/recompensa mínimo ──
    if decision.recommended_stop_loss_pct > 0:
        risk_reward = decision.recommended_take_profit_pct / decision.recommended_stop_loss_pct
        if risk_reward < settings.MIN_RISK_REWARD_RATIO:
            reasons.append(
                f"Ratio R:R {risk_reward:.2f} < mínimo {settings.MIN_RISK_REWARD_RATIO}"
            )
    else:
        reasons.append("Stop loss es 0 o negativo — inválido")

    # ── Regla 4: RSI contradice la dirección ──
    rsi = alert.indicators.rsi_14

    if decision.action == TradeAction.BUY and rsi > settings.RSI_OVERBOUGHT:
        reasons.append(
            f"BUY rechazado: RSI {rsi} > {settings.RSI_OVERBOUGHT} (sobrecompra)"
        )

    if decision.action == TradeAction.SELL and rsi < settings.RSI_OVERSOLD:
        reasons.append(
            f"SELL rechazado: RSI {rsi} < {settings.RSI_OVERSOLD} (sobreventa)"
        )

    # ── Regla 5: MACD contradice la dirección ──
    macd = alert.indicators.macd

    if decision.action == TradeAction.BUY and macd == "bearish_crossover":
        reasons.append("BUY rechazado: MACD indica bearish_crossover")

    if decision.action == TradeAction.SELL and macd == "bullish_crossover":
        reasons.append("SELL rechazado: MACD indica bullish_crossover")

    # ── Regla 6: Precio vs EMA 200 contradice la dirección ──
    # Excepción: Si el precio está en el Golden Pocket, permitir compras en retroceso
    price = alert.current_price
    ema_200 = alert.indicators.ema_200

    if decision.action == TradeAction.BUY and price < ema_200 * 0.99:
        if alert.indicators.in_golden_pocket:
            logger.info("🟡 Excepción Golden Pocket: Permitiendo BUY debajo de EMA 200")
        else:
            reasons.append(
                f"BUY rechazado: Precio {price} está >1% debajo de EMA 200 ({ema_200})"
            )

    if decision.action == TradeAction.SELL and price > ema_200 * 1.01:
        reasons.append(
            f"SELL rechazado: Precio {price} está >1% encima de EMA 200 ({ema_200})"
        )

    # ── Veredicto ──
    approved = len(reasons) == 0

    if approved:
        logger.info(
            "✅ Señal APROBADA: %s %s | Confianza: %d%% | SL: %.1f%% | TP: %.1f%%",
            decision.action.value, alert.ticker,
            decision.confidence_score,
            decision.recommended_stop_loss_pct,
            decision.recommended_take_profit_pct,
        )
    else:
        logger.warning(
            "🚫 Señal RECHAZADA: %s %s | Razones: %s",
            decision.action.value, alert.ticker, "; ".join(reasons),
        )

    return RiskVerdict(approved=approved, reasons=reasons)
