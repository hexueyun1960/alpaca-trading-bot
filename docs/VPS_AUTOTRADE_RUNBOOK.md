# VPS 自动交易运行手册

目标：服务器开机后自动运行 `src.monitor`，电脑关闭也不影响 Alpaca Paper Trading 自动扫描和下单。

## 1. 部署到 Ubuntu VPS

```bash
sudo mkdir -p /opt
cd /opt
sudo git clone <你的仓库地址> alpaca-bot
sudo chown -R "$USER":"$USER" /opt/alpaca-bot
cd /opt/alpaca-bot

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
```

`.env` 里 paper 自动下单至少要确认这些值：

```text
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DRY_RUN=false
ALPACA_ENABLE_TRADING=true
ALPACA_ALLOW_LIVE_TRADING=false
ALPACA_JOURNAL_PATH=/opt/alpaca-bot/logs/trade_journal.jsonl
ALPACA_MONITOR_INTERVAL_SECONDS=60
```

## 2. 启动前检查

```bash
cd /opt/alpaca-bot
source venv/bin/activate
python -m src.doctor
```

必须看到 `live_order_guard: paper order submission is enabled`。如果不是这个结果，服务即使运行也不会真实提交 paper order。

## 3. 安装常驻服务

```bash
cd /opt/alpaca-bot
sudo bash deploy/install_systemd_service.sh /opt/alpaca-bot alpaca-bot
```

这个命令会：

- 写入 `/etc/systemd/system/alpaca-bot.service`
- 设置开机自启
- 立即启动或重启服务
- 打印当前服务状态

## 4. 验证服务器真的在跑

```bash
sudo systemctl status alpaca-bot --no-pager --full
journalctl -u alpaca-bot -f
tail -f /opt/alpaca-bot/logs/trade_journal.jsonl
```

`trade_journal.jsonl` 应该持续出现这些心跳事件：

```text
monitor_started
monitor_cycle_started
monitor_cycle_finished
```

如果没有这些事件，说明 monitor 没有运行、服务没启动，或 `.env` 的 `ALPACA_JOURNAL_PATH` 指到了别处。

## 5. 一键检查

```bash
cd /opt/alpaca-bot
bash deploy/check_vps_status.sh /opt/alpaca-bot alpaca-bot
```

它会同时打印配置检查、systemd 状态、最近交易 journal、服务日志。

## 6. 常用操作

```bash
sudo systemctl restart alpaca-bot
sudo systemctl stop alpaca-bot
sudo systemctl start alpaca-bot
sudo systemctl disable alpaca-bot
```

更新代码后：

```bash
cd /opt/alpaca-bot
git pull
source venv/bin/activate
pip install -r requirements.txt
python -m src.doctor
sudo systemctl restart alpaca-bot
```

## 7. 判断为什么没下单

先看服务是否运行：

```bash
sudo systemctl status alpaca-bot --no-pager --full
```

再看 journal：

```bash
tail -n 100 /opt/alpaca-bot/logs/trade_journal.jsonl
```

常见情况：

- 有 `monitor_cycle_*`，但没有 `entry_order_submitted`：服务在跑，但策略、风控、时间窗口或 universe 过滤没有允许入场。
- 只有 `market_closed`：服务在非美股常规交易时段运行，正常跳过新入场。
- 没有 `monitor_cycle_*`：服务没有运行，或日志路径配置错了。
- 有 `order_error`：策略触发了下单，但 Alpaca API 拒绝或网络失败，需要看错误内容。

## 8. 安全边界

当前配置只应该用于 paper endpoint：

```text
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_ALLOW_LIVE_TRADING=false
```

不要在 paper trading 长时间稳定前切换 live endpoint。
