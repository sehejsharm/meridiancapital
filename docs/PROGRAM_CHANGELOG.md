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
| v15 | Config #5 v11 | v14 with the Angel credentials written back into the file (the server's settings still win when set), at the owner's request | The server had no credentials yet, so v14 could not start | Self-test 214/214; identical to v14 apart from the credentials and labels |
| v16 | Config #5 v12 | Writes the option chain beside itself every 15 s for the dashboard (chain, option flow, liquidity), on its own thread with its own rate limit on Angel's quote endpoint. Display only: places nothing, reads no trading state | The chain came only from the retired built-in engine | Self-test 228/228; all 96 functions and classes and every setting identical to v15; the trading loop gains only the hand-off lines |
| v17 | Config #5 v13 | Starting stop −30% of the premium paid (was −45%). Nothing else changes; sizing reads the stop, so the ₹9,000 risk cap allows more lots as the balance grows. The −45% drawdown halt is a different setting and is unchanged | Five-year study (May 2021 – May 2026, plus BANKNIFTY and SENSEX): about the same result per lot with a shallower worst run. Chosen by the owner over a lower-risk, lower-return alternative (0.1% breakout clearance) | Self-test 234 of 235 (the credentials check cannot pass on a blanked test copy; v16 the same); all 165 functions and classes identical to v16; the program's own stop, take-profit and sizing functions reproduce the backtest's 737 exits to the tick |
| v18 | Config #5 v14 | A wider trail early in the trade: once the stop is at entry (+20%), it starts 27.5% of the entry price behind the best price and narrows in a straight line to 20% at 180 minutes held, 20% after. It only ever tightens. Everything else is v17 | Profit-side study by five agents (about 2,300 settings): the only rule that passed the NIFTY walk-forward with losing trades and the worst run unchanged; neutral on BANKNIFTY and SENSEX, and carried by its biggest winners | Self-test 243 of 244 (credentials check on a blanked copy); changed: the stop function, a new trail-width function, and the four places that use the stop (each now passes the minutes held); the program's own functions reproduce the backtest's 737 exits to the tick except one late take-profit, where the program books +40% a few minutes later than the backtest's convention |
| v19 | Config #5 v15 | A failed-breakout exit for losers: on a closed NIFTY minute (not 15:09), sell when NIFTY has closed back past the far edge of the 90-minute channel the trade broke out of, or more than 1 × σ × √(minutes held) on the wrong side of the breakout level (σ = the option's entry implied volatility per trading minute). Exit reasons REVERSE_BRK and FAILED_BRK. The stop, break-even, trail, late take-profit and 15:10 are v18's | No-trail exit study (six agents, a literature scan, about 166,000 settings): no rule without a trail beat v18 chosen in advance; this loser exit added to v18 was better in 7 of 9 half-years, with and without ₹1 slippage, every σ multiple from 0.75 to 3 better than v18 (0.5 is not), positive on BANKNIFTY and SENSEX; smaller average loss and worst run. Evidence moderate (paired t 1.4). Built at the owner's request | Self-test 256 of 257 (credentials check on a blanked copy); changed: the exit check (one new step after the stop), the entry (records the channel, the signal minute and the entry IV), the loop (passes the last closed NIFTY minute); new: two helper functions; everything else identical to v18. The program's own functions reproduce the backtest's 737 exits with the same exit reason on every trade, to the tick except v18's one late take-profit |

## Studies that changed nothing

| Study | Finding |
|---|---|
| Exit search (576,232 combinations) | Nothing beat today's exits by more than luck explains (reality check P = 0.73); only 6 combinations beat it in all six years |
| Arming the trail at +25% | Better over two years, clearly worse over five. Watched on live trades (Analytics), not adopted |
| Entry filters (quiet market, 30-minute momentum, channel edge, later start, breakout confirmation) | Not proven; entries left alone |
| Tighter trail, all-day fixed target, stop at 35/40%, cutting losers early or by the clock, index-reversal exits | Worse |
| Selling one of two lots early (scale-out at +20% to +100%) | Worse at every level under both fill models; better in at most 3 of 6 years |
| Delta exits, five families (delta trail, delta targets, delta-sized index stops, theta/stagnation, delta- and volatility-scaled widths), about 2,000 variants | The two that passed 2024–26 lost on the held-back 2021–24 years |
| Other Greeks (gamma fade, IV spike, charm, elasticity) | None beat today's exits chosen in advance (walk-forward) or on BANKNIFTY and SENSEX |
| Exit packs from the live log (stop −25% with 90/120-minute time stops, +1% lock at +12%, 10% or stepped trail) and fixed take-profits | Helped the 35 live trades of Sep–Nov 2026; far worse over five years. Every time stop from 60 to 240 minutes is worse |
| Breakout confirmation (1, 2 or 5-minute hold; with the day's open; with the 20-day average) and put trend filters | Less in total on NIFTY; a 0.1% clearance is better per trade in all three markets but trades about one day in five (kept as an option) |

## Before a version trades real money

1. Self-test passes (`--selftest`).
2. Its decisions are compared with the version it replaces (backtest equivalence, or the stated difference).
3. Three full paper sessions (the desk counts them and warns).
4. The owner's go-ahead.
