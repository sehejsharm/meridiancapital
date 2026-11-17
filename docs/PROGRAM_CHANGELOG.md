# Program change log

One entry per version of the uploaded trading program (`ganesh_kavach_og_real.py`). The
program itself is not in this repository and neither are its credentials, the account's
figures or rupee results; those stay with the owner. This records **what changed, why, and
what evidence the change rests on**.

The strategy is the same in every version: a 90-minute Donchian breakout on NIFTY, entries
10:15–14:00, one trade a day, everything closed by 15:10. Versions change settings and
safety, not the strategy.

Backtests use real one-minute NIFTY option prices (May 2021 – June 2026) under two fill
models: *realistic* (a stop triggers on the low inside the minute, which reproduces the live
record) and *minute-close* (optimistic). A change goes in only if it helps under both.

| Version | Label | What changed | Why / evidence | Checks |
|---|---|---|---|---|
| v8 | — | Angel's own figures for each trade's money (charges, balance) | Desk and Angel disagreed on P&L | Self-test |
| v9 | — | Late take-profit: from 180 min after entry, sell at +40% | Better in both years of the two-year study | Self-test |
| v10 | — | Runner lock: from +90%, the stop rides 10% behind the best price | Only tightens a stop already locking in +70%; small gain on few trades | Self-test |
| v11 | Config #5 v7 | Trades only contracts 2–6 days from expiry (no 7–8 day contracts) | The 7–8 day trades lost money in 5 of 6 years | Self-test |
| v12 | Config #5 v8 | Buys two strikes in the money (was one); one lot per ₹50,000 of balance; at most 50% of the balance per trade | Two strikes in the money was the only entry change of 65 tested that passed every test, under both fill models and in the later years | Self-test 192/192 |
| v13 | Config #5 v9 | Reads a control file from the desk: halt new entries while running; "sell now, keep running" (exit reason MANUAL) | Halting or selling used to mean stopping the program | Self-test 201/201 |
| v14 | Config #5 v10 | No Angel credentials in the file (read from the server's settings); exit settings can be set from Controls in a file read once at start | Credentials in an uploaded, stored file; exit changes needed a new upload | Self-test 214/214; with no settings file every exit and sizing decision is identical to v13 (644 comparisons) |

## Studies that changed nothing

| Study | Finding |
|---|---|
| Exit search (576,232 combinations) | Nothing beat today's exits by more than luck explains (reality check P = 0.73); only 6 combinations beat it in all six years |
| Arming the trail at +25% | Better over two years, clearly worse over five. Watched on live trades (Analytics), not adopted |
| Entry filters (quiet market, 30-minute momentum, channel edge, later start, breakout confirmation) | Not proven; entries left alone |
| Tighter trail, all-day fixed target, stop at 35/40%, cutting losers early or by the clock, index-reversal exits | Worse |
| Selling one of two lots early (scale-out at +20% to +100%) | Worse at every level under both fill models; better in at most 3 of 6 years |

## Before a version trades real money

1. Self-test passes (`--selftest`).
2. Its decisions are compared with the version it replaces (backtest equivalence, or the stated difference).
3. Three full paper sessions (the desk counts them and warns).
4. The owner's go-ahead.
