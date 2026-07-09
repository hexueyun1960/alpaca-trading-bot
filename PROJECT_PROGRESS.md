# Alpaca 项目进展

本文档用于记录 Alpaca 的开发进度、关键决策、当前状态和下一步计划。

## 2026-07-08

### VWAP 双向偏离反转策略

- 已将主策略推进为 VWAP mean-reversion trade cycle：
  - 价格高于 VWAP `+4%` 触发做空第一笔市价单。
  - 价格低于 VWAP `-4%` 触发做多第一笔市价单。
  - 第一笔默认使用账户 equity 的 `8%`。
  - 第一笔成交后提交第二笔逆势 `4%` 限价单，金额接近第一笔实际成交金额。
- 已加入每个 symbol 的进程内状态机：`NO_POSITION`、`ENTRY_SUBMITTED`、`FIRST_FILLED`、`SECOND_ORDER_PENDING`、`POSITION_ACTIVE`、`EXITING`、`CLOSED_FOR_DAY`。
- 已实现单 symbol 锁定规则：每天最多一个 trade cycle，每个 cycle 最多两笔入场。
- 已加入常规交易时段控制：15:30 后不开新仓，15:55 强制平仓，15:58 二次检查。
- 已保留 dry-run 完整 cycle 模拟，包括第一笔成交、第二笔限价、第二笔触价成交、VWAP 回穿平仓、最大亏损平仓和收盘强平。
- 已按 Alpaca API 规范修正 universe filter：Asset 对象只使用 Alpaca 官方字段，30 日均量改由 Alpaca daily bars 成交量计算。
- 已加入可选动态选股池：`ALPACA_DYNAMIC_UNIVERSE=true` 时从 Alpaca active `us_equity` assets 构建扫描列表，并通过 `ALPACA_UNIVERSE_MAX_SYMBOLS` 控制每轮最多扫描数量。
- 已更新 `AUTOMATION_STRATEGY.md`，后续策略代码变更必须同步策略文档。

## 2026-07-07

### PAPER ORDER 下单实现

- 已加入 `src.paper_order` 一次性 paper order 命令：
  - 默认读取 `.env`，沿用现有安全开关。
  - 默认 dry-run 只生成订单预览，不提交。
  - 真实 paper 下单必须同时满足：

```text
ALPACA_DRY_RUN=false
ALPACA_ENABLE_TRADING=true
```

  - 即使上述开关已开启，命令仍要求传入 `--confirm`。
  - 命令拒绝非 paper endpoint，避免误用实盘 URL。
  - 命令会读取账户、持仓、时钟和资产信息，并通过 `risk.py` 风控后才提交。
  - 真实 paper 提交时，如果市场关闭则默认拒绝；如明确希望让 Alpaca 排队 day order，可传入 `--allow-queued`。

示例：

```bash
python -m src.paper_order --symbol SPY --side buy --notional 25
python -m src.paper_order --symbol SPY --side buy --notional 25 --confirm
```

### 订单状态追踪

- `src.alpaca_client.py` 已加入 `get_order(order_id)`。
- `src.broker.py` 已加入提交后状态轮询：
  - dry-run 返回稳定的订单预览结构。
  - 真实提交返回提交响应和 `latest_status`。
  - `src.bot` 在策略触发真实 paper order 后也会查询订单状态。
- 订单提交失败会写入 `order_error` / `paper_order_error` 日志。

### 验证结果

- 已补充 broker 和 paper_order 单元测试，覆盖 dry-run 预览、状态轮询、paper endpoint 保护、真实提交确认参数和手动订单参数校验。

## 2026-07-02

### 当前状态

- 已创建第一版 Python 项目骨架。
- 已加入默认安全的自动交易循环。
- 已加入 Alpaca REST 客户端封装，当前只使用 Python 标准库。
- 已加入一个简单的均线交叉策略。
- 已补强行情读取，默认向前读取 120 天市场数据。
- 已加入持久化 JSONL 交易日志。
- 已加入本地配置检查命令：`python -m src.doctor`。
- 已加入基础风控：
  - 股票代码白名单
  - 单笔订单最大金额限制
  - 最低现金保留要求
  - 没有现有多头持仓时禁止卖出
  - 显式 paper trading 启用开关
  - 默认 dry-run，不真实提交订单
- 已为策略、风控、配置检查和交易日志添加基础单元测试。

### 验证结果

- `python -m unittest discover -s tests` 已通过，共 12 个测试。
- `python -m compileall src tests` 已通过。
- `python -m src.doctor` 已通过，确认 Alpaca paper API 凭证、paper URL、交易标的、K 线数量、行情回看天数、日志路径和 live-order guard 均处于可运行状态。
- `python -m src.bot` 已完成一次 Alpaca paper dry-run 连通性测试。
- 本次 dry-run 成功读取 `SPY` 的 60 根 K 线。
- 策略生成 `buy` 信号，但由于 `ALPACA_DRY_RUN=true` 且 `ALPACA_ENABLE_TRADING=false`，风控阻止真实提交订单，仅写入订单预览。

### 当前阻塞点 / 安全闸门

- Alpaca paper API 凭证已经配置并通过检查。
- 当前仍处于安全 dry-run 模式，不会提交订单。
- 如果要进入 paper order 提交测试，必须显式修改 `.env`：

```text
ALPACA_DRY_RUN=false
ALPACA_ENABLE_TRADING=true
```

在加入更完整的回测、订单状态追踪和停止开关之前，不建议打开 paper order 提交。

### 凭证接入检查

- 已运行 `python -m src.doctor`。
- 当前结果：`ALPACA_API_KEY_ID` 和 `ALPACA_API_SECRET_KEY` 已配置。
- 安全配置正常：paper URL、交易标的、K 线数量、行情回看天数、日志路径和 live-order guard 均通过检查。
- 注意：不要把 API key 填在 `.env.example`，应填写在 `.env`；`.env` 已被 `.gitignore` 忽略，适合存放本地密钥。

### 当前架构

```text
src/config.py        环境变量配置和安全默认值
src/alpaca_client.py 最小 Alpaca REST 客户端
src/data.py          行情数据读取，默认带历史回看范围
src/strategy.py      策略信号生成
src/risk.py          下单前风控检查
src/journal.py       JSONL 决策和订单日志
src/doctor.py        本地配置检查器
src/broker.py        下单封装
src/bot.py           单轮交易运行入口
tests/               基础单元测试
```

### 运行模式

Alpaca 目前一次只运行一轮交易循环。系统默认处于 dry-run 模式，不会提交订单。只有下面两个配置同时满足时，才允许提交 paper order：

```text
ALPACA_DRY_RUN=false
ALPACA_ENABLE_TRADING=true
```

### 下一阶段里程碑

1. 为当前均线策略加入回测，先验证历史表现。
2. 加入订单状态追踪和失败重试记录。
3. 加入一键停止开关和每日最大亏损限制。
4. 再进行小金额 paper order 提交测试。
5. 加入定时器，让系统能在美股交易时间内周期运行。
6. 加入持仓、订单、风控状态的 dashboard 或报告视图。
7. 在 paper trading 稳定运行一段时间之后，再设计实盘交易审批流程。

### 注意事项

- 在 paper trading 观察数周之前，保持实盘交易关闭。
- 后续所有策略都必须经过 `risk.py`，不要让策略代码绕过风控直接下单。
- AI 生成的交易信号只能作为辅助输入，不能直接拥有下单权限，除非后续已经建立足够强的风控和人工审批机制。
