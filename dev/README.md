# fty_dev — points leagues in the fantasy tables

Working area for extending the `fty` schema beyond category leagues. All of it
targets **`fty_dev`** on Postgres; `fty` is read as a source and never written to.

ESPN only. Yahoo is out of scope, and ESPN's vocabulary (`H2H_CATEGORY`,
`H2H_POINTS`, …) is the standard other platforms will be conformed to later.

## Rebuilding the schema

`build_fty_dev.sql` is the only definition of `fty_dev` and is idempotent — it
opens with `DROP SCHEMA IF EXISTS fty_dev CASCADE`, so re-running it recreates
everything from `fty`.

```python
from sports_hub.db import Database
db = Database(db_con="postgres")
raw = db.engine.raw_connection()
try:
    cur = raw.cursor()
    cur.execute(open("dev/build_fty_dev.sql").read())   # no vars: psycopg2 then
    raw.commit()                                        # leaves the FG% / FT%
finally:                                                # literals alone
    raw.close()
```

Passing parameters makes psycopg2 read the `%` in `'FG%'` as a placeholder, so
the script has to run with no `vars` argument.

Credentials are deliberately **not** copied into `fty_dev`; code reads
`fty.customer_platform` so secrets live in one place.

## The design

Category and points leagues disagree about how a category is scored, not about
what gets recorded. So `fty_dev.matchup_box_score` is long —

```
season · platform · league_id · matchup · competitor_id · category · value
```

— and produces identical rows whichever format the league runs. Scoring is a
join against `league_categories` (weights) and `category_label` (direction,
ratio components), not a second pipeline.

Anything derivable was left out, on the rule that the table holds observations
and scoring definitions but nothing computed:

| not stored | derivation |
|---|---|
| `fantasy_points` | `value × league_categories.points`; the league carries no `pointsOverrides` |
| `result` (W/L/T) | a comparison — needs an opponent, so it belongs in a query |
| `fg_pct`, `ft_pct` | `fgm/fga`, `ftm/fta`; ESPN's own value matches to 5e-9 |
| `is_scored` | redundant once the padding rows were removed — row presence says it |
| `nba_category` | lookup through `platform_category` (see below) |

Dropping the ratio rows is what makes the rest work: **every stored value is
additive**, so any window is `SUM(value)` with no per-category special case.
Averaging matchup percentages instead was off by up to 32 basis points.

`H2H_MOST_CATEGORIES` is treated as a category league. It maps through
`fty_dev.scoring_format` rather than by rewriting `league.scoring_type`, so that
column stays faithful to ESPN and other platforms become rows, not branches.

## Conforming platform vocabularies

Two things a platform names in its own way, and one mapping table each. Adding a
platform means inserting rows; no schema change and no code branch.

| platform says | table | conforms to |
|---|---|---|
| `H2H_CATEGORY`, `H2H_POINTS`, … | `scoring_format` | `category` / `points` |
| `PTS`, `3PM`, `FG%`, … | `platform_category` | `nba_category` |

`category_label` was doing both jobs with one `fty_category` column — ESPN's
abbreviation *and* the join key for `league_categories.category` — which
silently assumed every platform spells a category the same way. It happens to
hold for Yahoo's nine, but only by luck. So:

- **`category_label`** is now the conformed, platform-neutral vocabulary:
  `nba_category` (PK), `fmt_category`, `display_order`, `higher_is_better`,
  `numerator`, `denominator`.
- **`platform_category`** is `(platform, platform_category) → nba_category`,
  unique both ways, with an FK onto the vocabulary.

`league_categories.category` stays the platform's own label, as fetched, and
conforms on read. `fty_categories_vw` joins through the map with an inner join,
so an unmapped label drops a row rather than silently propagating a
platform-native name — check `league_categories` row count against the view
after adding a platform.

ESPN's vocabulary is the standard, and it is complete: across every league with
box scores, `scoreByStat` returns nothing outside the 15 labels in the map.

Left for later: if a second **sport** is added, the vocabulary needs a sport
dimension (`nba_category` is NBA-specific by name and content) and
`platform_category` becomes `(sport, platform, platform_category)`. Not built.

## Reference-data repairs

The build applies four fixes, all sourced from ESPN rather than inferred:

- **24 padding rows dropped.** `FGM/FGA/FTM/FTA` were never in ESPN's
  `scoringItems` for these category leagues. They had been added so the box
  score fetch — which reads its stat list from `league_categories` — would still
  pull the FG%/FT% inputs. Unnecessary: `scoreByStat` returns 13 keys against 9
  scoring items. Scoped to `points IS NULL` so the points league, where those
  four are genuine scoring items, is untouched.
- **2024-25 Let's Get Tropical restored.** It held only the four padding rows;
  its nine real categories were gone and `fty.league` had no row for the season.
- **`scoring_type` corrected** on 2023-24 Let's Get Tropical and 2025-26 Paid In
  Full — both are `H2H_MOST_CATEGORIES`, stored as `H2H_CATEGORY`.
- **Keys added** that `fty` never had: PKs on `category_label`,
  `platform_category`, `league_categories`, `matchup_box_score` and
  `matchup_result`, plus the FK from `platform_category` onto the vocabulary.

All ten league-seasons now match ESPN's live `scoringItems` exactly.

## Verification

`fty_dev.fty_matchup_box_score_vw` rebuilds the old wide shape from the long
table. It reconciles against `fty.matchup_box_score` row for row — 1,352 of
1,352, no unmatched keys, `pts` and `fgm` exact. `fg_pct`/`ft_pct` differ by
5e-9, which is ESPN's float32 rounding; the derived value is the more precise.

## Still open

`fty_dev.matchup_result` is created and empty. It needs the handler, which has
not been touched yet:

- point it at `fty_dev`
- write every key in `home_stats`, not the ones listed in `league_categories` —
  that list no longer contains the components
- for points leagues, sum **raw** stats across the lineup.
  `H2HPointsBoxScore` exposes no `home_stats` at all, only `home_score`, and
  `BoxPlayer.points_breakdown` prefers `appliedStats`, which is already
  weighted — storing that in `value` would break the shared meaning of the table
- populate `matchup_result` from ESPN's reported outcome (`totalPoints` for
  points leagues, `cumulativeScore.wins/ties/losses` for category)

Also unresolved: `BoxPlayer` overwrites `points_breakdown` on every iteration of
a player's `stats` array, keeping only the last entry. Worth pinning down which
entry that is before trusting it.

## Files

- `build_fty_dev.sql` — the schema, idempotent
- `one-grain-two-formats.html` — source for the design diagram
- `superseded/` — earlier drafts, kept for history. **Do not run them:** both
  target `fty` rather than `fty_dev`, and predate the pct, `is_scored` and
  `nba_category` decisions above.
