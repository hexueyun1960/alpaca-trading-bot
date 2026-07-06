# AIPACA 自动化策略说明

本文档专门说明 AIPACA 当前版本的自动化交易策略、执行流程、安全边界和后续演进方向。当前系统仍处于 paper-first 原型阶段，默认不会真实提交订单。

## 文档维护规则

后续每次更新自动化策略、运行流程、风控规则、订单提交条件、定时执行方式或日志审计方式时，都必须同步更新本文档。

如果代码行为和本文档不一致，以代码为准，但应立即补正文档，避免自动化策略说明过期。

## 当前自动化目标

AIPACA 当前的自动化目标不是追求高频或复杂决策，而是先建立一条可控、可审计、默认安全的交易运行链路：

1. 从本地环境变量加载配置。
2. 连接 Alpaca Paper Trading 和 Market Data API。
3. 读取指定股票的近期 K 线数据。
4. 通过简单均线策略生成交易信号。
5. 将信号交给统一风控模块审批。
6. 在允许的情况下生成订单预览或提交 paper order。
7. 将每一次策略判断、风控结果和订单结果写入 JSONL 日志。

## 当前运行模型

当前 `src.bot` 采用单轮运行模型。执行一次：

```powershell
python -m src.bot
```

系统会完成一轮完整流程，然后退出。它目前不是常驻服务，也没有内置定时器。

当前流程如下：

```text
load_settings()
  -> build_client()
  -> get_account()
  -> get_positions()
  -> load_recent_bars()
  -> moving_average_signal()
  -> evaluate_signal()
  -> journal.record("decision")
  -> submit_or_preview_order()
  -> journal.record("order_preview" or "order_result")
```

## 策略信号

当前策略位于 `src/strategy.py`，使用简单均线判断：

- 短期均线窗口：默认 5 根 K 线。
- 长期均线窗口：默认 20 根 K 线。
- 当短期均线高于长期均线时，生成 `buy` 信号。
- 当短期均线低于长期均线时，生成 `sell` 信号。
- 当数据不足或均线相等时，生成 `hold` 信号。

当前策略只负责生成信号，不直接下单。下单权限集中在风控和 broker 层，避免策略代码绕过安全检查。

## 行情读取

行情读取位于 `src/data.py`，当前默认配置为：

- 标的：`AIPACA_SYMBOLS`，默认 `SPY`。
- 时间周期：`AIPACA_TIMEFRAME`，默认 `1Day`。
- K 线数量：`AIPACA_BAR_LIMIT`，默认 `60`。
- 回看天数：`AIPACA_MARKET_DATA_LOOKBACK_DAYS`，默认 `120`。

系统会从 Alpaca Market Data API 读取近期行情，再交给策略模块计算信号。

## 风控审批

风控逻辑位于 `src/risk.py`。当前所有 `buy` / `sell` 信号都必须通过以下检查：

- 标的必须在股票白名单中。
- 订单金额必须大于 0。
- 单笔订单金额不能超过 `AIPACA_MAX_NOTIONAL_PER_ORDER`。
- 买入后现金不能低于 `AIPACA_MIN_CASH_RESERVE`。
- 卖出前必须已有对应多头持仓。
- 必须同时满足可提交订单条件。

其中可提交订单条件由 `Settings.can_submit_orders` 决定：

```text
AIPACA_ENABLE_TRADING=true
AIPACA_DRY_RUN=false
```

只有这两个条件同时满足，风控才可能批准真实 paper order 提交。

## 默认安全模式

系统默认处于 dry-run 模式：

```text
AIPACA_DRY_RUN=true
AIPACA_ENABLE_TRADING=false
```

在默认模式下：

- 系统会读取账户、持仓和行情。
- 系统会生成策略信号。
- 系统会记录风控判断。
- 如出现 `buy` 或 `sell` 信号，系统只写入订单预览。
- 系统不会向 Alpaca 提交真实 paper order。

这是当前建议长期保持的默认模式，直到回测、订单追踪、停止开关和更完整的风险限制完成。

## Paper Order 模式

如需进入 paper order 提交测试，必须显式修改 `.env`：

```text
AIPACA_DRY_RUN=false
AIPACA_ENABLE_TRADING=true
```

即使打开这两个配置，订单仍然必须经过 `risk.py` 审批。当前 broker 层只构造简单 market order：

- `side`: `buy` 或 `sell`
- `type`: `market`
- `time_in_force`: `day`
- `notional`: 策略传入的金额

当前阶段不建议连接实盘交易。项目应先在 paper trading 中稳定运行一段时间。

## 日志与审计

交易日志由 `src/journal.py` 写入，默认路径：

```text
logs/trade_journal.jsonl
```

当前会记录：

- `decision`: 每个标的的策略信号、风控结果、K 线数量和运行模式。
- `order_preview`: dry-run 模式下的订单预览。
- `order_result`: 真实 paper order 提交后的返回结果。

日志格式为 JSONL，方便后续做回放、统计、报表或 dashboard。

## 当前不会自动化的部分

当前版本刻意没有自动化以下能力：

- 没有定时器，不会按交易时间自动循环运行。
- 没有实盘交易入口。
- 没有 AI 信号直接下单能力。
- 没有自动扩大仓位。
- 没有订单状态轮询、失败重试或成交后复盘。
- 没有每日最大亏损限制。
- 没有一键停止开关。

这些限制是当前安全策略的一部分，避免系统在还没有充分验证前获得过多自主权限。

## 后续自动化演进顺序

建议后续按以下顺序推进：

1. 为当前均线策略加入回测，先验证历史表现。
2. 增加订单状态追踪、失败重试记录和成交结果审计。
3. 增加一键停止开关。
4. 增加每日最大亏损、最大持仓、最大交易次数等限制。
5. 在 paper trading 中进行小金额提交测试。
6. 加入定时器，让系统在美股交易时间内周期运行。
7. 增加持仓、订单、风控状态 dashboard 或日报。
8. 在 paper trading 稳定运行数周之后，再设计实盘交易审批流程。

## 核心原则

- 策略只生成信号，不拥有直接下单权。
- 所有订单都必须经过统一风控。
- 默认 dry-run，真实 paper order 必须显式开启。
- 所有关键决策都必须写入日志。
- AI 生成的信号只能作为辅助输入，不能绕过风控或人工审批。
- 实盘交易必须等到 paper trading 和审计流程稳定后再讨论。
