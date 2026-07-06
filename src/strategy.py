from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SignalSide = Literal["buy", "sell", "hold"]
PositionIntent = Literal["buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close"]


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: SignalSide
    reason: str
    notional: float = 0.0
    qty: float | None = None
    position_intent: PositionIntent | None = None


@dataclass(frozen=True)
class AssetEligibility:
    symbol: str
    eligible: bool
    reasons: list[str]


def _sma(values: list[float], window: int) -> float:
    if len(values) < window:
        raise ValueError("Not enough values for moving average.")
    return sum(values[-window:]) / window


def moving_average_signal(
    symbol: str,
    bars: list[dict],
    *,
    short_window: int = 5,
    long_window: int = 20,
    notional: float = 100.0,
) -> Signal:
    closes = [float(bar["c"]) for bar in bars if "c" in bar]
    if len(closes) < long_window:
        return Signal(symbol=symbol, side="hold", reason="not enough bars")

    short_ma = _sma(closes, short_window)
    long_ma = _sma(closes, long_window)

    if short_ma > long_ma:
        return Signal(
            symbol=symbol,
            side="buy",
            reason=f"short SMA {short_ma:.2f} is above long SMA {long_ma:.2f}",
            notional=notional,
        )

    if short_ma < long_ma:
        return Signal(
            symbol=symbol,
            side="sell",
            reason=f"short SMA {short_ma:.2f} is below long SMA {long_ma:.2f}",
            notional=notional,
        )

    return Signal(symbol=symbol, side="hold", reason="moving averages are equal")


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def evaluate_short_spike_asset(asset: dict) -> AssetEligibility:
    symbol = str(asset.get("symbol", "")).upper()
    checks = {
        "asset must be active": str(asset.get("status", "")).lower() == "active",
        "asset must be tradable": _as_bool(asset.get("tradable")),
        "asset must be shortable": _as_bool(asset.get("shortable")),
        "asset must be easy to borrow": _as_bool(asset.get("easy_to_borrow")),
        "asset must support fractional shares": _as_bool(asset.get("fractionable")),
    }
    reasons = [reason for reason, ok in checks.items() if not ok]
    return AssetEligibility(symbol=symbol, eligible=not reasons, reasons=reasons)


def _latest_price_and_vwap(bars: list[dict]) -> tuple[float, float] | None:
    if not bars:
        return None

    latest = bars[-1]
    price = latest.get("c", latest.get("close"))
    vwap = latest.get("vw", latest.get("vwap"))
    if price is None or vwap is None:
        return None

    price_value = float(price)
    vwap_value = float(vwap)
    if price_value <= 0 or vwap_value <= 0:
        return None
    return price_value, vwap_value


def _position_qty(position: dict | None) -> float:
    if not position:
        return 0.0
    return float(position.get("qty", 0) or 0)


def vwap_spike_short_signal(
    symbol: str,
    bars: list[dict],
    *,
    position: dict | None = None,
    entry_threshold_pct: float = 4.5,
    notional: float = 500.0,
) -> Signal:
    price_and_vwap = _latest_price_and_vwap(bars)
    if price_and_vwap is None:
        return Signal(symbol=symbol, side="hold", reason="latest 1-minute bar is missing price or VWAP")

    price, vwap = price_and_vwap
    distance_pct = ((price - vwap) / vwap) * 100
    qty = _position_qty(position)

    if qty < 0 and price <= vwap:
        return Signal(
            symbol=symbol,
            side="buy",
            reason=f"price {price:.2f} pierced VWAP {vwap:.2f}; cover short",
            qty=abs(qty),
            position_intent="buy_to_close",
        )

    if qty > 0:
        return Signal(symbol=symbol, side="hold", reason="existing long position; skip short entry")

    if qty < 0:
        return Signal(
            symbol=symbol,
            side="hold",
            reason=f"short position remains open; price is {distance_pct:.2f}% from VWAP",
        )

    if distance_pct > entry_threshold_pct:
        return Signal(
            symbol=symbol,
            side="sell",
            reason=f"price {price:.2f} is {distance_pct:.2f}% above VWAP {vwap:.2f}",
            notional=notional,
            position_intent="sell_to_open",
        )

    return Signal(
        symbol=symbol,
        side="hold",
        reason=f"price is {distance_pct:.2f}% from VWAP; entry threshold is >{entry_threshold_pct:.2f}%",
    )
