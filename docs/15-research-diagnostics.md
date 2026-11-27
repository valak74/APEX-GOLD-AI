# Research: edge diagnostics and the reserved holdout

Status: implemented and tested on synthetic data (2026-11-27). **Results on real data come from the owner's
first run** after the Dukascopy import.

## Why

Adding rules without measuring them is how strategies end up fitted to noise. This step measures, on real
history, **which elements actually predict gold's movement and whether the move is larger than the costs**,
before any strategy is built or tuned.

## Reserved final out-of-sample period (`research/holdout.py`)

- **Reservation.** As soon as at least 60 days of 5m history exist, the system reserves the most recent period:
  - the last 6 months;
  - or the last 25 % when the history is shorter than 2 years.

  The reservation is a single row in `research.holdout`. Its date never moves afterwards, even when more data
  arrives.
- **Who never sees it.** Diagnostics and ordinary backtests always stop before that date.
- **Final verdict.**
  - It is admin-only (`final_holdout: true` on `POST /backtests`).
  - It runs on the reserved period only.
  - Every consultation is counted and shown, because each look weakens what it can prove.

## Diagnostics (`research/diagnostics.py`, `POST /research/diagnostics`, `apex diagnose`)

The study runs on research data, from the start of history up to the holdout, for 5m, 15m and 1h. The 15m and
1h series are resampled from 5m, so all three come from the same source.

1. **Cost burden.** The round-trip cost (spread + 2 × slippage + commission) is shown as a % of the median ATR.
2. **Component studies.** These cover sweeps (of pools and of swings), BOS, CHoCH, new FVGs and new order blocks.
   - Each result is the signed forward move in ATR, minus the average drift, at 4 horizons.
   - It is computed overall and by session, regime, volatility tercile and higher-timeframe alignment.
   - Events are thinned so that forward windows never overlap.
   - Each result is tested with a two-sided t-test.
3. **Exit study.** The signals of both setups are replayed with 9 exit rules:
   - the liquidity target used today;
   - fixed targets at 1, 1.5, 2 and 3 R;
   - 2 R with the stop moved to entry after +1 R;
   - half the position closed at 1 R and the rest at 2 R;
   - a 1.5 ATR trailing stop after +1 R;
   - a time-only exit.

   Results are in R, net of costs, with the same conservative fill conventions as the backtester.

A result is **retained** only if all of the following hold:

- it survives Benjamini–Hochberg correction (FDR 10 %), applied separately to the component family and the
  exit family;
- it is larger than the costs;
- it has the same sign on both halves of the research period;
- it rests on at least 100 independent events.

### Checks performed

- **Pure random walks (6 seeds):** no retained finding.
  - Two weaknesses were found this way and fixed: a single mixed correction family, and too-small cells.
- **Planted edge:** a drift of +0.8 ATR over 12 bars after each swing sweep is detected.
- **Persistent trends in synthetic data:** continuation elements (BOS, FVG, OB) are retained. Reversal elements
  (sweeps) are flagged as moving the opposite way.

## Look-ahead bug found and fixed

Pools of equal highs/lows were rebuilt when they grew. The two-swing version disappeared from the full-history
result, so the presence of a later swing changed the sweeps detected before it.

- **Fix:** every version of a pool is kept, with the bar at which it became known. An older version is retired
  only once the newer one is known (`superseded_idx`).
- **Verification:** the fix was checked on 3 000 random series × 3 cut points with no difference. A fixed
  regression test covers it.

## Deflated Sharpe ratio (`backtest/metrics.py`)

This implements the deflated Sharpe ratio of Bailey & López de Prado (2014).

- The selection benchmark grows with the number of variants tried: 6 per run, plus all earlier completed
  backtests on the same setup and timeframe.
- The ratio is corrected for skewness, fat tails and sample length.
- The gate requires at least 0.95.

## Limits

- The volatility terciles use thresholds computed on the whole research sample. This is a descriptive label,
  stated in the report.
- The exit study does not use the risk engine and allows one trade per window. It ranks exits; it does not replace
  the full backtest.
- A retained result is a hypothesis. It must then pass the full backtest, and finally the verdict on the
  reserved period.

## Recommended workflow

1. Run `apex import-dukascopy --from 2022-01-01 --to 2026-11-01`. The holdout is fixed automatically.
2. Open **Recherche** and run the diagnostic on 5m, 15m and 1h.
3. Turn only retained results into rules, then backtest them, in **Backtests**, on research data.
4. Run a **single** final verdict on the reserved period.
5. Run the strategy in PAPER for several weeks.
