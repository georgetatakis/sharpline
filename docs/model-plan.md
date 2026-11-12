# Sharpline model settings

George's decisions for every model, settled 2026-11-10. These update the October model
plan (`Sharpline Model Plan.pdf`); where the two differ, this file and the spec win. The
spec's build steps (`nfl-prediction-platform-spec-v4.md`, Phase 3 on) say when each
setting gets built. "Fit later" means the value below is the starting point and the
backtest (step 19) or graded results replace it.

Priorities: player props and anytime TD. Build order: game model, then the shared usage
table, receiving props, QB passing yards, anytime TD, rushing props.

Dropped: temperature and rain, success rate, defense and special teams TDs, carries props.

## Shared rules (every model)

| Setting | Value |
|---|---|
| Training seasons | 2021 on, recent seasons weighted more (half-life 2 seasons) |
| Second window | Also train 2018 on; keep the one with the lower walk-forward log loss |
| Simulated games | 10,000 per prediction |
| De-vig | Every price before any edge: multiplicative first, Shin tested beside it |
| Shrink toward market | 70% market / 30% model to start. Fit later by regressing results on the market line and the model. Move toward the model only after closing line value is positive over 200+ bets |
| Minimum edges | Applied after shrinking |
| Stake | Quarter Kelly on the shrunken, de-vigged probability; at most 1% of bankroll per bet and 3% per game (same-game bets are correlated) |
| Logging | Every prediction logged with price and timestamp before the game |
| Data | Never later than the as-of week |
| Paper trading | Until about 100 logged picks per bet type |
| Prop prices | Logged from now on (step 17c), since their history isn't free |
| Usage | One shared usage table (target, carry, red zone, goal-line shares) that receiving, rushing, anytime TD and QB passing all read |

## Game model (spread, moneyline, total)

| Setting | Value | Built in |
|---|---|---|
| EPA per play, offense vs opposing defense | 60% of points per drive (backtest also tries 50/50) | step 17 (done) |
| Points per drive, offense vs opposing defense | 40% | step 17 (done) |
| Opponent adjustment | Ridge regression of offense and defense ratings, tested against raw averages | step 19 |
| Red zone TD rate | Weight 0; it swings too much year to year | step 17 (done) |
| Pace | Drives per game sets the number of drives | step 17 (done) |
| Success rate, explosive rate, PROE | Not in the game model (PROE goes to the prop models through expected pass rate) | |
| Pressure, blitz, coverage | Not used yet; test against nflverse FTN charting and PFR pressures once those files are downloaded | later overlap retest |
| Home field | 1.5 points; check against nflverse home margins since 2021 | 17 (done), checked in 19 |
| Rest | 0.25 points per extra day, at most 4 days, from nflverse home_rest / away_rest | 17b |
| Starting QB out | Hand-set points from the starter-to-backup gap: elite QB about 6 to 8, average starter about 3 to 4 | 17b |
| Wind | 15+ mph outdoors = -2 on the total until fit on total residuals from nflverse weather | 17 (done), fit in 19 |
| Early season | This season weighted n/(n+6) against last season (pulled halfway to average); hand-adjust the prior for new QBs | 17 (done) |
| Simulator | Each drive ends in a TD, field goal or nothing; whole-number scores, team TD counts | 17b |
| Spread of outcomes | Margin 13, total 13.5; calibrate the simulator to match | 17b, 19 |
| Minimum edge, spread | 1.5 points after shrinking; 3 and 7 are key numbers | 26 |
| Minimum edge, moneyline | 4% after shrinking and de-vig; nothing longer than +300 until 300 graded games | 26 |
| Minimum edge, total | 1.5 points after shrinking | 26 |

## Receiving props (yards, receptions)

| Setting | Value |
|---|---|
| Players | Top 20 by target share each week; WR, TE, RB |
| Team pass attempts | From the simulator: expected pass rate (nflfastR xpass, where PROE goes) plus game script |
| Target share | 50/50 blend of last 4 weeks and season, tuned walk-forward |
| Catch rate, yards per catch | Player's season pulled toward position average, about 40 to 60 targets of prior weight |
| Opponent | Yards per target allowed to the position, shrunk about 75% toward average |
| Slot vs outside | Not in v0 (screenshot only) |
| Injured teammate | No bump in v0; shares renormalized to sum to 1 |
| QB out | Lowers team yards per attempt and pass volume |
| Simulation | Targets (negative binomial), then catches, then yards; no mean-times-normal |
| Minimum edge | 5% after de-vig |

## QB passing yards

| Setting | Value |
|---|---|
| Attempts | Same team pass attempts as receiving |
| Yards per attempt | QB's season pulled toward league average, opponent pass defense shrunk about 75% |
| QB out or new QB | The game model's QB tiers |
| Minimum edge | 5% after de-vig |

## Rushing props (yards)

| Setting | Value |
|---|---|
| Carries | Team rush attempts from the simulator (game script: favorites run more late) x carry share (last 4 weeks + season) |
| Yards per carry | Bootstrapped from the player's real carries, pulled toward position average (keeps the breakaway tail) |
| Opponent | Yards per carry allowed, shrunk hard toward average; stuff rate and yards before contact only if they pass the overlap test |
| Running QBs | Not at first |
| Carries props | Dropped |
| Minimum edge | 5% after de-vig |

## Anytime TD

| Setting | Value |
|---|---|
| Team TDs | Straight from the simulated drives, no conversion from points |
| Player probability | P = 1 - exp(-λ); player λs sum to the team's rushing plus receiving TDs |
| Split weights | Red zone touches and goal-line carries high; red zone and end zone targets medium; snap share low |
| Red zone definitions | yardline_100 from nflverse play-by-play |
| Window | Full season with recency weights (4 to 6 games is too small a sample) |
| Opponent | TDs allowed by position, shrunk toward average |
| QB rushing TDs | In |
| Defense and special teams TDs | Out |
| Minimum edge | 5% after de-vig (these markets hold 10 to 15%); same-team TD bets count toward the 3% per-game cap |

## Files George needs to download for these

Claude Code never downloads data. These nflverse datasets aren't in
`data/extracted/nflverse/` yet:

- FTN charting (`nflreadr::load_ftn_charting()`) and PFR advanced stats
  (`nflreadr::load_pfr_advstats()`): for the pressure, blitz and coverage retest.
- Play-by-play already has `xpass`, `yardline_100`, and the passing columns needed for QB
  passing yards; schedules already have `home_rest` and `away_rest`.
