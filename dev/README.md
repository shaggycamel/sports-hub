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

That rule is about redundancy **within a grain** — a computed value sitting in
the same row as the facts it derives from. It is not an argument against
aggregates: `matchup_result` is a separate relation at the matchup grain with its
own key, so it stays a table even though its contents are reproducible from the
category grain. Storing a summary of a finer grain is not the same thing as
denormalising a column into the fact table beside its own inputs.

The reproducibility is still useful, as a check rather than a reason to drop it.
Deriving `cat_won/lost/tied` from `matchup_box_score` — comparing each scored
category against the opponent from `league_matchup`, directed by
`higher_is_better` — reproduces ESPN's `cumulativeScore` exactly: all ten
competitors of 2025-26 Let's Get Tropical matchup 20, all three counts. ESPN's
`ineligible` flag is never set (0 across 5,574 scored category-sides over three
leagues), and the only scored categories missing a `result` are the single
in-progress matchup per league. So that derivation is a safe regression test, and
it is the one worth running against a points league once its season starts: the
first matchup where summed lineup totals fail to reproduce ESPN's own
`home_score` is a bug in the lineup aggregation, not a tie to break.

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

## The handler

`FtyComponent` and `FtyHandler` now take a `schema` argument (default `"fty"`),
so the same code targets either schema:

```python
f = FtyComponent(db, ctx, "nba", leagues, schema="fty_dev")
f.get_matchup_box_score()
f.get_matchup_result()
```

Credentials are the one thing still read from `fty` regardless.

`get_matchup_box_score` returns long rows and takes different routes per format,
because ESPN hands them different shapes — not because the output differs:

- **category** — `cumulativeScore.scoreByStat` already holds team totals per
  category, so they are read straight off it.
- **points** — `H2HPointsBoxScore` has no `home_stats` at all, so totals are
  summed from the lineup, reading ESPN's raw response rather than `BoxPlayer`.
  `BoxPlayer.points_breakdown` cannot be used: it prefers `appliedStats`, which
  are already multiplied by the league's weights, and it keeps no copy of the raw
  `stats`. One extra request per league, not per team.

Which categories get stored comes from `platform_category` joined to
`category_label`, so a label ESPN reports with no mapping is dropped rather than
stored under a platform-native name, and ratio categories are excluded.

`get_matchup_result` reads ESPN's own outcome rather than recomputing it:
`cumulativeScore.wins/losses/ties` for a category league (score = wins +
ties/2), `home_score` for a points league. `totalPoints` is **not** usable as a
general score — ESPN reports it as 0.0 for category leagues.

### Verified on live data

Against 2025-26 Let's Get Tropical, matchup 20:

- 110 long rows, 10 competitors × 11 stored categories, ratios correctly absent
- re-running replaced rows rather than duplicating them (110 → 110)
- **derived FG% matches ESPN's own reported FG% to 5e-9 for all ten
  competitors** — the check that matters, since the stored percentage was dropped
- `matchup_result`: reciprocal pairs, complementary scores, every
  `cat_won + cat_lost + cat_tied` equal to the league's 9 scored categories

## Still open

**The points-league path is written but unverified.** The only registered points
league is 2026-27 National Basketball Cup, and its season has not started —
`box_scores()` returns `H2HPointsBoxScore` objects with `home_score: 0`, empty
lineups and `winner: UNDECIDED`. Two things to confirm once games are played:

- that summed lineup totals agree with ESPN's own `appliedStatTotal`
- that the bench/IR filter is right. Only non-bench slots are summed, on the
  assumption bench players do not score; verify against the league's settings.

A related caveat that did **not** bite for category leagues: `BoxPlayer`
overwrites `points_breakdown` on every entry of a player's `stats` array,
keeping the last. For a category league there is only one entry
(`statSourceId=0`, `statSplitTypeId=5`), so nothing is lost. A points league may
carry projections alongside actuals, which is why the raw path filters to
`statSourceId == 0` explicitly.

Yahoo's handler was left alone beyond the schema threading; it is out of scope.

## Files

- `build_fty_dev.sql` — the schema, idempotent
- `one-grain-two-formats.html` — source for the design diagram
- `superseded/` — earlier drafts, kept for history. **Do not run them:** both
  target `fty` rather than `fty_dev`, and predate the pct, `is_scored` and
  `nba_category` decisions above.
