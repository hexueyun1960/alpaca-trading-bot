from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SignalSide = Literal["buy", "sell", "hold"]
PositionIntent = Literal["buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close"]
OrderType = Literal["market", "limit"]
PositionDirection = Literal["long", "short"]
TradeStateName = Literal[
    "NO_POSITION",
    "ENTRY_SUBMITTED",
    "FIRST_FILLED",
    "SECOND_ORDER_PENDING",
    "POSITION_ACTIVE",
    "EXITING",
    "CLOSED_FOR_DAY",
]

try:
    NY_TZ = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    NY_TZ = None


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: SignalSide
    reason: str
    notional: float = 0.0
    qty: float | None = None
    position_intent: PositionIntent | None = None
    order_type: OrderType = "market"
    limit_price: float | None = None
    client_order_id: str | None = None
    position_direction: PositionDirection | None = None
    price: float | None = None
    vwap: float | None = None
    vwap_deviation_pct: float | None = None
    allow_position_add: bool = False


@dataclass(frozen=True)
class AssetEligibility:
    symbol: str
    eligible: bool
    reasons: list[str]
    spread_pct: float | None = None


@dataclass
class SymbolTradeState:
    symbol: str
    state: TradeStateName = "NO_POSITION"
    trade_date: str | None = None
    position_direction: PositionDirection | None = None
    first_fill_price: float | None = None
    first_filled_qty: float = 0.0
    first_filled_notional: float = 0.0
    first_entry_time: str | None = None
    second_order_id: str | None = None
    second_limit_price: float | None = None
    second_filled_qty: float = 0.0
    second_filled_notional: float = 0.0
    entries_submitted: int = 0
    entries_filled: int = 0
    closed_for_day: bool = False
    last_reason: str | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def locked(self) -> bool:
        return self.state in {
            "ENTRY_SUBMITTED",
            "FIRST_FILLED",
            "SECOND_ORDER_PENDING",
            "POSITION_ACTIVE",
            "EXITING",
            "CLOSED_FOR_DAY",
        } or self.closed_for_day


@dataclass(frozen=True)
class SessionRules:
    is_regular_session: bool
    allow_new_entries: bool
    force_flatten: bool
    final_position_check: bool


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


def _as_optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def calculate_spread_pct(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid_price = (bid + ask) / 2
    if mid_price <= 0:
        return None
    return ((ask - bid) / mid_price) * 100


def evaluate_vwap_mean_reversion_asset(
    asset: dict,
    *,
    price: float | None,
    bid: float | None,
    ask: float | None,
    avg_daily_volume_30d: float | None = None,
    min_price: float = 1.0,
    min_avg_daily_volume_30d: float = 3_000_000.0,
    require_etb: bool = True,
    max_spread_pct: float = 0.5,
    require_shortable: bool = True,
) -> AssetEligibility:
    symbol = str(asset.get("symbol", "")).upper()
    reasons = []

    if str(asset.get("status", "")).lower() != "active":
        reasons.append("asset must be active")
    if not _as_bool(asset.get("tradable")):
        reasons.append("asset must be tradable")
    if require_shortable and not _as_bool(asset.get("shortable")):
        reasons.append("asset must be shortable")
    if require_etb and "easy_to_borrow" in asset and not _as_bool(asset.get("easy_to_borrow")):
        reasons.append("asset must be easy to borrow")
    if price is None or price <= min_price:
        reasons.append(f"price must be > {min_price:g}")

    if avg_daily_volume_30d is None:
        reasons.append("avg_daily_volume_30d is required")
    elif avg_daily_volume_30d <= min_avg_daily_volume_30d:
        reasons.append(f"avg_daily_volume_30d must be > {min_avg_daily_volume_30d:g}")

    spread_pct = calculate_spread_pct(bid, ask)
    if spread_pct is None:
        reasons.append("valid bid/ask required")
    elif spread_pct >= max_spread_pct:
        reasons.append(f"spread_pct must be < {max_spread_pct:g}")

    return AssetEligibility(symbol=symbol, eligible=not reasons, reasons=reasons, spread_pct=spread_pct)


def evaluate_short_spike_asset(asset: dict) -> AssetEligibility:
    return evaluate_vwap_mean_reversion_asset(
        asset,
        price=2.0,
        bid=1.995,
        ask=2.0,
        avg_daily_volume_30d=5_000_000,
        require_shortable=True,
    )


def _session_bars_for_latest_day(bars: list[dict]) -> list[dict]:
    timestamps = [_bar_timestamp(bar) for bar in bars]
    latest_timestamp = next((timestamp for timestamp in reversed(timestamps) if timestamp is not None), None)
    if latest_timestamp is None:
        return bars

    session_start = time(9, 30)
    session_end = time(16, 0)
    selected = []
    for bar, timestamp in zip(bars, timestamps):
        if timestamp is None:
            continue
        if timestamp.date() != latest_timestamp.date():
            continue
        if not (session_start <= timestamp.time() < session_end):
            continue
        selected.append(bar)
    return selected


def latest_price_and_session_vwap(bars: list[dict]) -> tuple[float, float] | None:
    if not bars:
        return None

    session_bars = _session_bars_for_latest_day(bars)
    if not session_bars:
        return None

    latest = session_bars[-1]
    price_value = _bar_price(latest)
    if price_value is None or price_value <= 0:
        return None

    weighted_vwap = 0.0
    total_volume = 0.0
    for bar in session_bars:
        bar_vwap = _bar_vwap(bar)
        volume = _bar_volume(bar)
        if bar_vwap is None or volume is None or bar_vwap <= 0 or volume <= 0:
            continue
        weighted_vwap += bar_vwap * volume
        total_volume += volume

    if total_volume <= 0:
        return None
    return price_value, weighted_vwap / total_volume


def _position_qty(position: dict | None) -> float:
    if not position:
        return 0.0
    return float(position.get("qty", 0) or 0)


def position_direction(position: dict | None) -> PositionDirection | None:
    qty = _position_qty(position)
    if qty > 0:
        return "long"
    if qty < 0:
        return "short"
    return None


def _bar_timestamp(bar: dict) -> datetime | None:
    raw = bar.get("t", bar.get("timestamp"))
    if not raw:
        return None
    try:
        value = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(NY_TZ) if NY_TZ is not None else dt


def _bar_price(bar: dict) -> float | None:
    return _as_optional_float(bar.get("c", bar.get("close")))


def _bar_vwap(bar: dict) -> float | None:
    return _as_optional_float(bar.get("vw", bar.get("vwap")))


def _bar_volume(bar: dict) -> float | None:
    return _as_optional_float(bar.get("v", bar.get("volume")))


def calculate_vwap_deviation_pct(price: float, vwap: float) -> float:
    if vwap <= 0:
        raise ValueError("VWAP must be positive.")
    return ((price - vwap) / vwap) * 100


def _notional_from_equity(account_equity: float, equity_pct: float) -> float:
    return round(account_equity * (equity_pct / 100), 2)


def vwap_mean_reversion_entry_signal(
    symbol: str,
    bars: list[dict],
    *,
    account_equity: float,
    state: SymbolTradeState | None = None,
    entry_deviation_pct: float = 4.0,
    first_order_equity_pct: float = 8.0,
    fixed_entry_notional: float | None = None,
) -> Signal:
    current_state = state or SymbolTradeState(symbol=symbol)
    if current_state.locked:
        return Signal(symbol=symbol, side="hold", reason=f"symbol locked in {current_state.state}")

    price_and_vwap = latest_price_and_session_vwap(bars)
    if price_and_vwap is None:
        return Signal(symbol=symbol, side="hold", reason="latest session bar is missing price, VWAP, or volume")

    price, vwap = price_and_vwap
    deviation_pct = calculate_vwap_deviation_pct(price, vwap)
    notional = (
        round(fixed_entry_notional, 2)
        if fixed_entry_notional is not None and fixed_entry_notional > 0
        else _notional_from_equity(account_equity, first_order_equity_pct)
    )

    if deviation_pct >= entry_deviation_pct:
        return Signal(
            symbol=symbol,
            side="sell",
            reason=f"price {price:.2f} is {deviation_pct:.2f}% above session VWAP {vwap:.2f}",
            notional=notional,
            position_intent="sell_to_open",
            position_direction="short",
            price=price,
            vwap=vwap,
            vwap_deviation_pct=deviation_pct,
        )

    if deviation_pct <= -entry_deviation_pct:
        return Signal(
            symbol=symbol,
            side="buy",
            reason=f"price {price:.2f} is {deviation_pct:.2f}% below session VWAP {vwap:.2f}",
            notional=notional,
            position_intent="buy_to_open",
            position_direction="long",
            price=price,
            vwap=vwap,
            vwap_deviation_pct=deviation_pct,
        )

    return Signal(
        symbol=symbol,
        side="hold",
        reason=f"price is {deviation_pct:.2f}% from session VWAP; entry threshold is +/-{entry_deviation_pct:.2f}%",
        price=price,
        vwap=vwap,
        vwap_deviation_pct=deviation_pct,
    )


def vwap_mean_reversion_exit_signal(
    symbol: str,
    bars: list[dict],
    *,
    position: dict | None,
    account_equity: float,
    max_loss_equity_pct: float = 1.0,
) -> Signal:
    direction = position_direction(position)
    qty = abs(_position_qty(position))
    if direction is None or qty <= 0:
        return Signal(symbol=symbol, side="hold", reason="no position to exit")

    price_and_vwap = latest_price_and_session_vwap(bars)
    if price_and_vwap is None:
        return Signal(symbol=symbol, side="hold", reason="latest session bar is missing price, VWAP, or volume")

    price, vwap = price_and_vwap
    deviation_pct = calculate_vwap_deviation_pct(price, vwap)
    unrealized_pl = _as_optional_float((position or {}).get("unrealized_pl")) or 0.0
    max_loss = account_equity * (max_loss_equity_pct / 100)

    if unrealized_pl <= -max_loss:
        return Signal(
            symbol=symbol,
            side="buy" if direction == "short" else "sell",
            reason=f"max loss reached: unrealized_pl={unrealized_pl:.2f} max_loss={max_loss:.2f}",
            qty=qty,
            position_intent="buy_to_close" if direction == "short" else "sell_to_close",
            position_direction=direction,
            price=price,
            vwap=vwap,
            vwap_deviation_pct=deviation_pct,
        )

    if direction == "short" and price <= vwap:
        return Signal(
            symbol=symbol,
            side="buy",
            reason=f"price {price:.2f} crossed back through session VWAP {vwap:.2f}; cover short",
            qty=qty,
            position_intent="buy_to_close",
            position_direction="short",
            price=price,
            vwap=vwap,
            vwap_deviation_pct=deviation_pct,
        )

    if direction == "long" and price >= vwap:
        return Signal(
            symbol=symbol,
            side="sell",
            reason=f"price {price:.2f} crossed back through session VWAP {vwap:.2f}; close long",
            qty=qty,
            position_intent="sell_to_close",
            position_direction="long",
            price=price,
            vwap=vwap,
            vwap_deviation_pct=deviation_pct,
        )

    return Signal(
        symbol=symbol,
        side="hold",
        reason=f"{direction} position remains open; price is {deviation_pct:.2f}% from session VWAP",
        position_direction=direction,
        price=price,
        vwap=vwap,
        vwap_deviation_pct=deviation_pct,
    )


def second_entry_limit_signal(
    state: SymbolTradeState,
    *,
    second_order_distance_pct: float = 4.0,
    fixed_entry_notional: float | None = None,
    current_price: float | None = None,
    max_entries_per_cycle: int = 2,
) -> Signal:
    if state.position_direction not in {"long", "short"}:
        return Signal(symbol=state.symbol, side="hold", reason="first entry direction is missing")
    if state.first_fill_price is None or state.first_filled_notional <= 0:
        return Signal(symbol=state.symbol, side="hold", reason="first fill details are missing")
    if (
        state.entries_submitted >= max_entries_per_cycle
        or state.second_order_id
        or state.state == "SECOND_ORDER_PENDING"
    ):
        return Signal(symbol=state.symbol, side="hold", reason="second entry already submitted")

    distance = second_order_distance_pct / 100
    if state.position_direction == "short":
        trigger_price = round(state.first_fill_price * (1 + distance), 2)
        if current_price is None or current_price < trigger_price:
            return Signal(
                symbol=state.symbol,
                side="hold",
                reason=f"second short entry waits for price >= {trigger_price:.2f}",
                position_direction=state.position_direction,
            )
        side: SignalSide = "sell"
        intent: PositionIntent = "sell_to_open"
    else:
        trigger_price = round(state.first_fill_price * (1 - distance), 2)
        if current_price is None or current_price > trigger_price:
            return Signal(
                symbol=state.symbol,
                side="hold",
                reason=f"second long entry waits for price <= {trigger_price:.2f}",
                position_direction=state.position_direction,
            )
        side = "buy"
        intent = "buy_to_open"

    return Signal(
        symbol=state.symbol,
        side=side,
        reason=f"second {state.position_direction} entry at {second_order_distance_pct:.2f}% adverse move",
        notional=round(
            fixed_entry_notional
            if fixed_entry_notional is not None and fixed_entry_notional > 0
            else state.first_filled_notional,
            2,
        ),
        position_intent=intent,
        order_type="market",
        position_direction=state.position_direction,
        price=current_price,
        allow_position_add=True,
    )


def mark_first_fill(
    state: SymbolTradeState,
    *,
    fill_price: float,
    filled_qty: float,
    filled_notional: float,
    position_direction_value: PositionDirection,
    filled_at: datetime | str,
) -> SymbolTradeState:
    state.state = "FIRST_FILLED"
    state.position_direction = position_direction_value
    state.first_fill_price = fill_price
    state.first_filled_qty = filled_qty
    state.first_filled_notional = filled_notional
    state.first_entry_time = filled_at.isoformat() if isinstance(filled_at, datetime) else filled_at
    state.entries_filled = max(state.entries_filled, 1)
    return state


def vwap_spike_short_signal(
    symbol: str,
    bars: list[dict],
    *,
    position: dict | None = None,
    entry_threshold_pct: float = 4.5,
    notional: float = 500.0,
) -> Signal:
    price_and_vwap = latest_price_and_session_vwap(bars)
    if price_and_vwap is None:
        return Signal(symbol=symbol, side="hold", reason="latest session bar is missing price, VWAP, or volume")

    price, vwap = price_and_vwap
    distance_pct = ((price - vwap) / vwap) * 100
    qty = _position_qty(position)

    if qty < 0 and price <= vwap:
        return Signal(
            symbol=symbol,
            side="buy",
            reason=f"price {price:.2f} pierced session VWAP {vwap:.2f}; cover short",
            qty=abs(qty),
            position_intent="buy_to_close",
        )

    if qty > 0:
        return Signal(symbol=symbol, side="hold", reason="existing long position; skip short entry")

    if qty < 0:
        return Signal(
            symbol=symbol,
            side="hold",
            reason=f"short position remains open; price is {distance_pct:.2f}% from session VWAP",
        )

    if distance_pct > entry_threshold_pct:
        return Signal(
            symbol=symbol,
            side="sell",
            reason=f"price {price:.2f} is {distance_pct:.2f}% above session VWAP {vwap:.2f}",
            notional=notional,
            position_intent="sell_to_open",
        )

    return Signal(
        symbol=symbol,
        side="hold",
        reason=f"price is {distance_pct:.2f}% from session VWAP; entry threshold is >{entry_threshold_pct:.2f}%",
    )
