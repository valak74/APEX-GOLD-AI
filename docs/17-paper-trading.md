# Phase 9: supervised PAPER trading

Status: implemented and tested with a simulated broker and a fixed clock (2026-11-27). The real run starts on
the owner's machine, connected to the IC Markets demo account.

## Principle

In PAPER mode the bot trades **on real prices with simulated money**. This is the last step of evidence before
any thought of real money: does the strategy behave live as it did in the backtest, after real spreads,
latency and outages?

## Autopilot (`api/autopilot.py`, "Pilote" tab)

- **When it can run:**
  - PAPER mode only; the loop refuses any other mode;
  - off by default;
  - started by a trader, and only if trading is enabled (admin + password + TOTP) and the kill switch is not
    engaged.
- **Which opportunities it takes:**
  - only those from the scanner, for the selected setups and timeframes, at or above a minimum quality;
  - only **fresh** ideas: the signal bar closed less than one bar ago;
  - each idea at most once (journal + deterministic order id).
- **Approval.** The risk engine evaluates each order **at the current executable price**. If price has run
  away, the reward/risk check refuses the order.
- **Exits, as in the backtester:**
  - stop and target placed with the order;
  - time exit after `max_hold_bars`;
  - flat before the Friday close (`weekly_close_buffer_minutes`, 60 min by default).

  Known difference: the backtester exits at the last bar before the close.
- **Journal (`trading.paper_orders`).** Every idea examined is recorded as skipped, refused, failed, open or
  closed, with the reasons. Each trade carries its slippage, latency (bar close → fill), risk, net P&L and R.

## Continuity across restarts

- **Kill switch:** always restored.
- **Trading switch and autopilot:** restored **only in PAPER**. A LIVE process always starts disabled.
- **Open simulated positions:** saved on every change and re-created at startup, with their order id so an
  order is never duplicated.
- **Balance:** rebuilt as initial balance + closed P&L − commissions already paid.

## Evidence (`api/paper_report.py`)

- **Statistics** come with confidence intervals and include slippage and average latency.
- **Comparison with the latest backtest** of the same setup and timeframe: the PAPER expectancy must fall inside
  the backtest's 95 % interval, assessed from 30 trades per group.
- **Live vs backtest**:
  - the backtest's signal generator is replayed on the bars **recorded live**, then matched with the live
    scanner's opportunities;
  - a "backtest only" signal means a feed/server outage or a detector difference;
  - a "live only" signal means a gap to explain.
- **Phase 9 gate (roadmap)**, all required:
  - at least 90 days and at least 100 trades;
  - results inside the backtest interval;
  - zero unexplained risk incidents (failed orders, stop-outs).

  Even when every criterion is met, going live remains a human decision, at minimal risk.

## Alerts (`api/notifier.py`)

- **Channel:** a Telegram bot, free. The token and chat id go in `.env` only.
- **Messages:**
  - opens and closes;
  - kill switch;
  - feed lost for more than 2 minutes, and restored;
  - daily summary after 17:00 New York.
- **Failures:** an alert failure never affects trading. Alerts also show in the dashboard.

## Starting a PAPER run (owner)

1. Connect cTrader (`docs/09-guide-connexion-ctrader.md`) and leave the server running (VPS recommended).
2. Optional: Telegram (`APEX_TELEGRAM_BOT_TOKEN`, `APEX_TELEGRAM_CHAT_ID`).
3. Dashboard: activate trading (admin, password and TOTP), then open **Pilote**, choose the setups, and click
   **Démarrer en PAPER**.
4. Follow progress in Pilote. Run the live/backtest comparison every week.
