# AIPACA 自动化策略说明

本文档说明 AIPACA 当前版本的自动化交易策略、执行流程、安全边界和后续演进方向。当前系统仍处于 paper-first 原型阶段，默认不会真实提交订单。

## 文档维护规则

后续每次更新自动化策略、运行流程、风控规则、订单提交条件、定时执行方式或日志审计方式时，都必须同步更新本文档。

尤其是修改 `src/strategy.py` 或其他策略相关代码时，必须同时更新本文档中对应的策略说明、信号字段、入场/出场规则、仓位状态或资产筛选逻辑；如果只是无行为变化的重构，也应确认本文档无需调整。

如果代码行为和本文档不一致，以代码为准，但应立即补正文档，避免自动化策略说明过期。

## 当前自动化目标

AIPACA 当前的自动化目标不是追求高频或复杂决策，而是建立一条可控、可审计、默认安全的 VWAP mean-reversion 交易链路：

1. 从本地环境变量加载配置。
2. 连接 Alpaca Paper Trading 和 Market Data API。
3. 每轮开始读取 account、positions、open orders 和 clock。
4. 只在美股常规交易时段扫描入场。
5. 读取指定股票的近期 1-minute K 线和 VWAP。
6. 对符合 universe 条件的股票生成 VWAP 双向偏离反转信号。
7. 将信号交给统一风控模块审批。
8. 在允许的情况下生成订单预览或提交 paper order。
9. 将策略判断、风控结果、订单结果、状态变化和平仓动作写入 JSONL 日志。

## 当前运行模型

`src.bot` 采用单轮运行模型。执行一次：

```powershell
python -m src.bot
```

系统会完成一轮完整流程，然后退出。常驻运行由 `src.monitor` 负责：

```powershell
python -m src.monitor
```

当前主流程如下：

```text
load_settings()
  -> build_client()
  -> get_account()
  -> get_positions()
  -> get_orders(status="open")
  -> get_clock()
  -> session checks
  -> build fixed or dynamic scan universe
  -> load_recent_bars()
  -> vwap_mean_reversion_exit_signal()
  -> vwap_mean_reversion_entry_signal()
  -> evaluate_vwap_mean_reversion_asset()
  -> evaluate_signal()
  -> submit_or_preview_order()
  -> journal.record(...)
```

## 策略信号

当前主策略位于 `src/strategy.py`，使用 VWAP 双向偏离反转逻辑：

- `vwap_deviation_pct = (current_price - vwap) / vwap * 100`
- 当 `vwap_deviation_pct >= +4.0` 时，生成做空入场信号。
- 做空第一笔：`side=sell`，`position_intent=sell_to_open`，`position_direction=short`，`order_type=market`。
- 当 `vwap_deviation_pct <= -4.0` 时，生成做多入场信号。
- 做多第一笔：`side=buy`，`position_intent=buy_to_open`，`position_direction=long`，`order_type=market`。
- 第一笔金额默认是账户 equity 的 8%。
- 如果数据缺少最新价格或 VWAP，生成 `hold`。

策略只负责生成信号和状态转换，不直接绕过风控下单。下单权限集中在 `risk.py` 和 `broker.py`。

## Universe 与资产筛选

当前支持两种扫描模式：

- 固定扫描池：`ALPACA_DYNAMIC_UNIVERSE=false` 时，只扫描 `ALPACA_SYMBOLS` 中列出的股票。
- 动态扫描池：`ALPACA_DYNAMIC_UNIVERSE=true` 时，从 Alpaca `/v2/assets` 读取 active `us_equity` assets，先保留 active、tradable、符合 ETB 设置的股票，再按 symbol 排序并限制到 `ALPACA_UNIVERSE_MAX_SYMBOLS` 个。默认最大数量是 `100`。

动态池拉取失败、筛选为空或 `ALPACA_UNIVERSE_MAX_SYMBOLS <= 0` 时，本轮不会回退到 `ALPACA_SYMBOLS` 开新仓，而是跳过新入场并记录 `dynamic_universe_error` 或 `dynamic_universe_empty`。无论固定池还是动态池，当前已有持仓、未成交订单和本地 `SYMBOL_STATES` 中的 symbol 都会合并进本轮扫描列表，用于退出监控和状态同步；动态池限制只约束新入场扫描规模。

入场前必须通过 `evaluate_vwap_mean_reversion_asset()`：

- `price > ALPACA_MIN_PRICE`，默认 `1`。
- `avg_daily_volume_30d > ALPACA_MIN_AVG_DAILY_VOLUME_30D`，默认 `3000000`；该值由 Alpaca `1Day` bars 的成交量计算，不从 Asset 对象读取。
- 如果 asset 提供 `easy_to_borrow` 且 `ALPACA_REQUIRE_ETB=true`，必须为 true。
- `spread_pct < ALPACA_MAX_SPREAD_PCT`，默认 `0.5`。
- 必须是 active、tradable asset。
- 做空入场必须 shortable；做多入场不要求 shortable。

按 Alpaca Assets API 规范，Asset 对象不提供 `avg_daily_volume_30d` 字段；当前实现通过 Alpaca `1Day` bars 的成交量自行计算 30 日均量。

Spread 计算：

```text
mid_price = (bid + ask) / 2
spread_pct = (ask - bid) / mid_price * 100
```

无有效 bid/ask 时跳过该股票，并记录拒绝原因。

## 交易时间

当前 `src.bot` 使用 Alpaca clock 和 New York time 控制交易窗口：

- 常规交易时段：09:30 到 16:00。
- `ALPACA_NO_NEW_ENTRIES_AFTER=15:30` 后不再开新仓，即收盘前半小时停止新入场。
- `ALPACA_FORCE_FLATTEN_TIME=15:55` 后触发强制取消订单和平仓。
- `ALPACA_FINAL_POSITION_CHECK_TIME=15:58` 后再次检查残留持仓和挂单。
- 非常规时段记录 `market_closed`，不扫描新入场。
- 不允许持仓过夜。

## 第二笔限价单

每个 trade cycle 最多两笔入场：

1. 第一笔市价单。
2. 第一笔完全成交后提交第二笔逆势 4% 限价单。

做空第一笔成交后：

```text
side = sell
order_type = limit
limit_price = first_fill_price * 1.04
notional ~= first_filled_notional
```

做多第一笔成交后：

```text
side = buy
order_type = limit
limit_price = first_fill_price * 0.96
notional ~= first_filled_notional
```

第二笔属于同一个 trade cycle，不算重复开仓。`ALPACA_MAX_ENTRIES_PER_CYCLE` 默认是 `2`，不允许第三笔加仓。

## 单股票锁定规则

每个 symbol 每天最多一个 trade cycle。只要该 symbol 满足任意条件，就不会继续扫描新的第一笔入场：

- 已提交第一笔入场订单。
- 有持仓。
- 有未成交订单。
- 第二笔限价单等待成交。
- 正在退出。
- 当天已经完成一个 trade cycle。

注意：第二笔限价单仍允许提交，因为它属于已存在的 trade cycle。

当前状态由进程内 `SYMBOL_STATES` 维护；每个 monitor 进程内会按交易日重置。本地状态和 Alpaca broker state 冲突时，以 Alpaca 持仓和挂单为准，并记录 `state_mismatch`。

## 平仓逻辑

平仓优先级：

1. 单只股票最大亏损。
2. 收盘前强制平仓。
3. VWAP 回穿平仓。

最大亏损：

- `ALPACA_MAX_LOSS_PER_SYMBOL_EQUITY_PCT=1.0`
- 当 symbol 浮亏达到或超过账户 equity 的 1%，取消该 symbol 未成交订单，平掉全部持仓，标记 `CLOSED_FOR_DAY`，记录 `max_loss_exit_signal`。

VWAP 回穿：

- 做空持仓：当 `current_price <= vwap`，买入平掉全部空仓。
- 做多持仓：当 `current_price >= vwap`，卖出平掉全部多仓。
- 平仓前取消该 symbol 的未成交订单。
- 平仓后标记 `CLOSED_FOR_DAY`，当天不再交易该 symbol。

收盘强平：

- 15:55 后取消订单并平仓。
- 15:58 后继续检查残留持仓和挂单。
- 强平日志记录 `force_close_signal`；如果同时达到最大亏损，优先记录 `max_loss_exit_signal`。

## 状态机

每只 symbol 维护以下状态：

- `NO_POSITION`: 无持仓、无挂单、当天未完成 cycle，可以扫描入场。
- `ENTRY_SUBMITTED`: 第一笔市价单已提交，等待成交确认。
- `FIRST_FILLED`: 第一笔已成交，准备提交第二笔限价单。
- `SECOND_ORDER_PENDING`: 第二笔限价单已提交，等待成交或退出条件。
- `POSITION_ACTIVE`: 当前有持仓，可能只有第一笔，也可能两笔都已成交。
- `EXITING`: 正在取消订单和平仓。
- `CLOSED_FOR_DAY`: 当天 cycle 已结束，当天不再交易该 symbol。

Dry-run 模式会模拟第一笔成交、第二笔限价单、第二笔触价成交、VWAP 回穿、最大亏损和收盘强平，以便验证完整 trade cycle。

## 风控审批

风控逻辑位于 `src/risk.py`。当前所有 `buy` / `sell` 信号都必须通过以下检查：

- 标的必须在本轮扫描 universe 中；固定模式下等于 `ALPACA_SYMBOLS`，动态模式下等于本轮 Alpaca assets 选出的 symbol 列表。
- 订单金额或数量必须大于 0。
- 如果 `ALPACA_MAX_NOTIONAL_PER_ORDER > 0`，单笔订单金额不能超过该上限；默认 `0` 表示不使用本地固定金额上限。
- 买入开仓后现金不能低于 `ALPACA_MIN_CASH_RESERVE`。
- 开仓金额不能超过 buying power。
- 不能在已有反向持仓时开新仓。
- 平仓必须有对应方向的现有持仓。
- 订单提交必须满足安全开关。

安全开关：

```text
ALPACA_DRY_RUN=true
ALPACA_ENABLE_TRADING=false
ALPACA_ALLOW_LIVE_TRADING=false
```

只有 `ALPACA_DRY_RUN=false` 且 `ALPACA_ENABLE_TRADING=true` 时，才可能提交 paper order。若使用 live endpoint，还必须显式设置 `ALPACA_ALLOW_LIVE_TRADING=true`。

## 日志与审计

交易日志由 `src/journal.py` 写入，默认路径：

```text
logs/trade_journal.jsonl
```

关键事件包括：

- `universe_rejected`
- `spread_rejected`
- `etb_rejected`
- `entry_signal`
- `entry_order_preview`
- `entry_order_submitted`
- `entry_order_filled`
- `second_order_preview`
- `second_order_submitted`
- `second_order_filled`
- `vwap_exit_signal`
- `max_loss_exit_signal`
- `force_close_signal`
- `order_cancel_submitted`
- `order_cancel_confirmed`
- `position_close_submitted`
- `position_close_filled`
- `state_mismatch`
- `closed_for_day`
- `market_closed`
- `dynamic_universe_selected`
- `dynamic_universe_empty`
- `dynamic_universe_error`

日志 payload 至少尽量包含：`symbol`、`event_type`、`price`、`vwap`、`vwap_deviation_pct`、`position_direction`、`qty`、`notional`、`account_equity`、`state`、`reason`。

## 当前限制

- 状态目前存放在进程内；monitor 进程重启后会依赖 Alpaca 持仓和挂单同步来避免重复下单，但还没有持久化 trade cycle 状态。
- 30 日均量由 Alpaca daily bars 计算；如果无法取得有效日线成交量，当前会按规则拒绝该股票。
- 当前没有回测模块。
- 当前没有 dashboard。
- 当前不建议连接实盘交易。

## 核心原则

- 策略只生成信号，不拥有直接下单权。
- 所有订单都必须经过统一风控。
- 默认 dry-run，真实 paper order 必须显式开启。
- 每个 symbol 每天最多一个 trade cycle。
- 每个 trade cycle 最多两笔入场。
- 最大亏损、收盘强平和不持仓过夜必须保留。
- 所有关键决策都必须写入日志。
- 实盘交易必须等到 paper trading 和审计流程稳定后再讨论。
