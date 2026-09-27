# fty_dev — points leagues in the fantasy tables

Working area for extending the `fty` schema beyond category leagues. All of it
targets **`fty_dev`** on Postgres; `fty` is read as a source and never written to.

ESPN only. Yahoo is out of scope, and ESPN's vocabulary (`H2H_CATEGORY`,
`H2H_POINTS`, …) is the standard other platforms will be conformed to later.

## Rebuilding the schema

Two steps, and the second is not optional.

`build_fty_dev.sql` defines the schema and is idempotent — it opens with
`DROP SCHEMA IF EXISTS fty_dev CASCADE`. It creates `matchup_box_score` and
`matchup_result` **empty** and does not seed them from `fty.matchup_box_score`,
because those rows are partial (see Verification). A rebuild alone therefore
leaves you with reference data and no matchup history.

```python
from sports_hub.db import Database
from sports_hub.context import Context
from sports_hub.fty import FtyComponent

db = Database(db_con="postgres")
raw = db.engine.raw_connection()
try:
    cur = raw.cursor()
    cur.execute(open("dev/build_fty_dev.sql").read())   # no vars: psycopg2 then
    raw.commit()                                        # leaves the FG% / FT%
finally:                                                # literals alone
    raw.close()

# then refill from ESPN — a few minutes, roughly 250 requests
for year, league_id in [(2023, 1966813226), (2024, 95537), (2024, 1966813226),
                        (2025, 24608), (2025, 95537), (2025, 1382487116),
                        (2025, 1966813226)]:
    ctx = Context(db)
    ctx.cur_season_year = year
    leagues = db.read(
        "SELECT DISTINCT cl.platform, cl.league_id, cp.credentials "
        "FROM fty_dev.customer_league cl "
        "JOIN fty_dev.customer_platform cp "
        "  ON cp.customer_id = cl.customer_id AND cp.platform = cl.platform "
        f"WHERE cl.league_id = {league_id}")
    FtyComponent(db, ctx, "nba", leagues, schema="fty_dev").backfill_matchups()
```

Passing parameters makes psycopg2 read the `%` in `'FG%'` as a placeholder, so
the script has to run with no `vars` argument. To test a change to the script
without disturbing `fty_dev`, run it with `fty_dev` replaced by a scratch schema
name and drop that afterwards.

The customer objects are replicated too, `customer_platform` included, so a
schema is self-contained and can stand in for `fty` wholesale — `FtyComponent`
reads credentials from whichever schema it was given. The trade-off is that the
credentials now exist in two schemas until `fty` is retired.

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

The check that matters is whether the stored values agree with ESPN's own
adjudication. Deriving the category record from the box score and comparing it
against `matchup_result` (fetched separately from ESPN):

| source of box score values | rows compared | disagreements with ESPN |
|---|---|---|
| `fty.matchup_box_score` | 1,342 | **262** |
| `fty_dev.matchup_box_score` (refetched) | 1,534 | **0** |

`fty`'s historical captures are unreliable — poorly formatted and in places just
wrong — so they are not worth reconciling or explaining. The build script does not
seed from them; `backfill_matchups()` refetches from ESPN instead, and that output
agrees with ESPN on every row.

`fty_dev.fty_matchup_box_score_vw` therefore no longer matches
`fty.matchup_box_score`, by design.

### Zero attempts are 0%, not undefined

`fg_pct` was initially `fgm / NULLIF(fga, 0)`, which makes a 0-attempt matchup
NULL and drops the category from any comparison. ESPN instead reports
`FG% = 0.0` with `result: LOSS`. Confirmed on 2023-24 matchup 20, where one
competitor posted an all-zero line (a forfeit): ESPN scored it 1-8, the NULL
version derived 1-6. Both ratio expressions now coalesce to 0, which is what
took the disagreement count to zero.

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

## Coverage

Backfilled across every league-season that has played games. `matchup_result`
row counts are exactly `matchups × team_count` in all seven:

| season | league | matchups | box score rows | result rows |
|---|---|---|---|---|
| 2023-24 | Let's Get Tropical | 21 | 1,848 | 168 |
| 2024-25 | Tucked | 20 | 2,640 | 240 |
| 2024-25 | Let's Get Tropical | 24 | 2,112 | 192 |
| 2025-26 | Paid In Full | 21 | 3,276 | 252 |
| 2025-26 | Tucked | 19 | 2,508 | 228 |
| 2025-26 | $100 8 Cat | 22 | 2,640 | 264 |
| 2025-26 | Let's Get Tropical | 20 | 2,200 | 200 |

17,224 box score rows, 1,544 results over 147 league-matchups. Zero score
mismatches; the 10 rows with a null `result` are exactly the 10 byes.

2024-25 Let's Get Tropical had no box scores at all before this — the wide table
never held any — so its 24 periods came from ESPN fresh.

2026-27 is empty because the season has not started.

`backfill_matchups()` drives the period list from `con.matchup_ids`, capped at
`currentMatchupPeriod`. That map comes back **empty** for 2023-24, so
`currentMatchupPeriod` is the fallback — without it that season silently
backfilled nothing.

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

## Replacing fty

`fty_dev` is intended to replace `fty`, not sit beside it. What that needs:

**Done**

- customer objects replicated (`customer`, `customer_league`, `customer_platform`)
  with primary keys, and `FtyComponent._registered_leagues` now reads credentials
  from its own schema rather than a hardcoded `fty`
- `util.update_schedule` row added for the new method:
  `fty | matchup_result | fty.get_matchup_result`

**Outstanding**

- **Unpause that row.** It is inserted with `pause = true` on purpose. Nothing in
  this repo reads `util.update_schedule`, so an external runner drives it, and
  `fty.matchup_result` does not exist yet — an unpaused row would have that runner
  calling a method against a schema with no such table. Flip it when the schemas
  swap.

  Daily is the right cadence and needs no special handling. `get_matchup_result`
  defaults to `con.currentMatchupPeriod` and scopes its delete to that period, so
  each run refreshes the live period in place. `util.update_log` shows the existing
  job running daily at ~15:06 UTC, and under it periods 1-15 of 2025-26 came out
  exactly right — refetching closed periods on a schedule is not needed.
  `backfill_matchups()` stays a manual tool for seeding and repair.

- `league`, `league_categories`, `league_competitor` and `league_matchup_dates`
  need no `update_schedule` rows: they are derived once at the start of a season.
- **Five views were never ported**, and belong to the nba.shiny dashboard rather
  than here: `fty_base_vw`, `fty_free_agents_vw`, `fty_league_schedule_vw`,
  `fty_recent_activity_vw`, `fty_team_roster_schedule_vw`. None of them touch
  anything that changed — between them they read only `league`,
  `league_competitor`, `free_agents`, `league_matchup`, `league_matchup_dates`,
  `competitor_roster` and `util.conformed_player_id`, all identical in `fty_dev`.
  They port verbatim. See the contract note below for what the dashboard does
  need to know.
- **`util.table_column_order` needs nothing**, but worth knowing why: only
  `fty.competitor_roster` is registered, and `get_competitor_roster` is the sole
  method using `write_ordered`. Everything else calls `db.write`, which never
  consults the registration, and `conform()` passes an unregistered table through
  untouched. Both new writers build frames from an explicit `pl.DataFrame` schema,
  so their column order is already deterministic.

## Contract changes for the nba.shiny dashboard

The five dashboard views port across unchanged. Three things do affect the
dashboard, and are worth reading before the schemas swap.

**`fty_categories_vw` changed shape.** Six of eight columns are unchanged;
two are gone:

| gone | replacement |
|---|---|
| `h2h_cat` | row presence — after the reference-data repair, `league_categories` holds exactly ESPN's scoring items, so a row existing *is* the flag |
| `fty_category` | renamed `platform_category`, since the label is now per-platform |

Added: `points`, `higher_is_better`, `numerator`, `denominator`, `is_ratio`,
`scoring_type`, `scoring_format`. A consumer filtering on `h2h_cat = true` should
just drop the filter.

**`fty_matchup_box_score_vw` keeps all 22 columns** and needs no query changes.
One behavioural difference: `fg_pct` and `ft_pct` are now derived from their
components and come back as `0` rather than `NULL` when there were no attempts,
matching what ESPN itself reports. Anything special-casing a null percentage can
stop.

**Yahoo is absent.** `fty` carries one Yahoo league-season; `fty_dev` carries
ESPN only. Any view that showed Yahoo leagues will show fewer rows. That was
deliberate, but it is a visible change to the dashboard rather than an internal
one.

Also gone: `league_schedule_RETIRED`.

There is one genuinely new view worth surfacing in the dashboard —
`matchup_category_vw`, one row per competitor per matchup per scored category,
carrying the value, the weight and the direction. It is the natural source for a
per-category matchup breakdown in either league format.

# Player identity (util.player / util.player_source_id)

Replaces the wide, name-keyed `util.conformed_player_id`. **Built and verified
against local Postgres; the old table is left in place untouched** so the five
un-ported dashboard views keep working until they are refabricated.

## Why

`conformed_player_id` held one column pair per platform (`{source}_id`,
`{source}_name`) with no primary key, no unique constraint and no index. A new
source could only arrive by matching a name, and a missed match was appended as a
new player: 1158 rows held 1128 distinct names, and **all 30 duplicates were one
player split in two** — a nba/espn/yahoo row plus a statyx-only row with a null
`nba_id` (Cameron Payne, Markelle Fultz, Seth Curry, Dalano Banton, …). Nothing
about the shape could detect it, because there was no key to conflict on.

`is_active` was worse: a stored boolean **no code in this repo ever wrote**, with
no `util.update_schedule` row. It had drifted to 124 players with a 2025-26
roster row not flagged, 29 with no crosswalk row at all, and 38 flagged active
sitting on no roster.

## Shape

```
util.player            player_key PK, conformed_name, needs_review
util.player_source_id  platform, source_id, source_name, player_key
                       PK (platform, source_id)
```

Keying the mapping on `(platform, source_id)` makes the split inexpressible, and
makes the build idempotent by virtue of the key rather than the matcher being
right. `conformed_name` is deliberately **not** unique — genuine namesakes exist,
and the matcher declines to guess rather than the constraint forbidding them.

Platform vocabulary is lowercase in `util.*` (`nba`, `espn`, `statyx`, `yahoo`);
`fty.*` stores `ESPN`/`Yahoo`, so reads of those tables `lower()` it.

## Activity is derived, not stored

Neither table has an `is_active` column. `util.active_player_vw` derives it per
season from tables the daily jobs already refresh, so it cannot go stale:

| platform | source |
|---|---|
| nba | `nba.player_box_score` ∪ `nba.team_roster` |
| statyx | `statyx.player_info` |
| espn | `fty.free_agents` ∪ `fty.competitor_roster`, current season only |

The nba union matters. `team_roster` is a **sampled snapshot** — it records who
the job saw when it ran, so a 10-day contract signed and expired between runs
never appears. `player_box_score` is a **complete event log**: nobody plays
without landing in one. For 2025-26 the box scores hold 710 players against the
roster's 645, and 72 of the 710 have no roster row at all. The roster still earns
its place, because early in a season the event log is nearly empty.

`ctx.active_ids(platform, season=None)` reads the view, cached per
(platform, season). It replaces `ctx.active_players`, and 12 call sites lost
their `.drop_nulls()` compensation with it.

Two things the view is deliberately **not**:

- It is not "currently on an NBA roster". `team_roster` cannot answer that:
  spells only close on a **trade**, never when a player is cut or leaves the
  league, so 2024-25 finished with 583 of 686 rows still open months later.
  `exit_date IS NULL` means "latest team assignment". Answering the real question
  needs a change to `get_team_roster` — close any open spell whose player is
  absent from the fresh snapshot — and nothing needs it yet.
- It is not restricted to resolved players. The join to `player_source_id` is a
  LEFT join, so an unmapped source id still drives a fetch loop and a mid-season
  arrival is fetched on the first run that sees them.

## Two source-data problems this surfaced

**Bogus nba player ids.** NBA's feed assigns ids to opposition players in
pre-season exhibitions against international clubs. 280 of them sit in a block
starting at 196,294,083 while real ids top out at 6,664,001, and the directory
view guards on that gap.

**The guard is a heuristic and leaks, deliberately.** About 600 further
pre-season-only participants carry ordinary-looking ids (1, 7020, 27001-27011,
42824 "Chris Goulding" of Melbourne United) and get through. Two tidier rules
were tested against the data; both are worse:

| rule | why it loses |
|---|---|
| `Pre Season` only → drop | loses 34 real players per season who appeared in no other game type and never hit a roster snapshot — Victor Oladipo, Delon Wright, Frank Kaminsky, Jalen McDaniels in 2025-26 |
| must exist in nba_api's static universe | loses 46 real players with real minutes; that bundled list lags, and Jahmai Mashack played 40 regular-season games and 661 minutes in 2025-26 without appearing in it |

So the residue stays and a handful of non-NBA names per season reach
`ctx.active_ids('nba')`, costing one failed API call each. That is the cheaper
error: excluding a real player loses his stats silently, while including a fake
one shows up in a log. Worth noting `get_player_season_stats` has no try/except
around its per-player call, unlike `get_player_box_score`.

**Names disagree across sources on accents, punctuation and whitespace.** nba's
"Egor Dëmin" is espn's "Egor Demin"; espn's "P.J. Hairston" is nba's "PJ
Hairston"; `nba.team_roster` carries "Norris  Cole" with a double space. Matching
raw strings recreated the split it was meant to end — a first pass produced 20
duplicated players. Hence `util.norm_name()` (casefold, strip accents, strip
non-alpha, IMMUTABLE so it can be indexed), which every match goes through.

## The matcher

`utility.conform_player_ids(db, season=None)` — two SQL statements, no staging
table and no upsert. The first mints a player for any unmatched normalised name
nobody owns; the second attaches every unmatched source id to the player holding
that name. Re-running is a no-op because the second statement's output is exactly
what empties the view the first reads.

Matching is normalised-exact, **never fuzzy** — fuzzy auto-linking is what caused
the original 30 splits. A name matching more than one player is left unmatched
rather than guessed at. Genuine platform renames ("Bub Carrington" → "Carlton
Carrington") stay in the backlog for `name_match()` and a human.

## Cadence: no update_schedule row

`util.update_schedule` has no cadence column and the runner drives everything
daily (`util.update_log` shows `fty.free_agents` on 460 distinct days), so a row
would mean daily — far more than this needs. Only 24 players first appeared after
the 2025-26 pre-season seed.

Instead `util.unmatched_player_source_vw` is the backlog, and the daily jobs that
write the source tables are already the detector. Non-empty means run
`conform_player_ids`. Run it at season start; check the view otherwise.

## Verified state

Seeded from the 1158 old rows, then the full history resolved:

| | |
|---|---|
| `util.player` | 2737 (1609 `needs_review`) |
| `util.player_source_id` | 5070 |
| normalised name collisions | **0** |
| players owning no mapping | **0** |
| unmatched backlog | **0** |
| 2025-26 coverage | nba 683/683, statyx 703/703, espn 1098/1098 |

`conform_player_ids` run twice in a row resolves 2199 then 0. Spot-checked:
Cameron Payne holds all four platform ids on one key; Egor Dëmin holds **both**
of ESPN's duplicate ids (5175643, 5243213); the bogus `nba:196294141` for Norris
Cole is gone.

The 1610 `needs_review` rows are mostly legitimate — the box scores reach back to
2009-10, so most are historical players the old crosswalk never held. They are
flagged, not trusted.

## The backlog view is one row per source id, and that matters

`unmatched_player_source_vw` collapses with `DISTINCT ON (platform, source_id)`,
preferring the most recent season's spelling. Without it the two statements of
`conform_player_ids` read different grains: one source id can reach the directory
under several names — nba id 1641756 is "Mike Miles" in the box scores and "Mike
Miles Jr." on the roster — so the minting statement created a player per spelling
while the mapping statement only ever mapped the id once, leaving a player owning
nothing. Caught by asserting `players owning no mapping = 0`, which is worth
keeping as a check after any run.

## What name_match is and isn't for

`name_match` is **not** used by `conform_player_ids` and should not be. Run by
hand over the 181 players that hold no nba id, it returned a "close match" for
148 of them and most were nonsense — "Aday Mara" → "Cody Martin", "Jayden Nunn"
→ "Jalen Brunson", "Cameron Boozer" → "Carlos Boozer" (his father). Auto-linking
on that output is how the original 30 splits happened.

Most of those 181 are not splits at all: statyx's directory carries college and
draft-prospect players who have no NBA id yet (AJ Dybantsa, Cameron Boozer,
Braden Smith, Aday Mara).

It did earn its keep on one systematic class — pairs differing only by a
generational suffix, which `norm_name` does not reconcile:

| | |
|---|---|
| merge | Billy Garrett / Billy Garrett Jr. · Boo Buie / Boo Buie III · Joel Berry / Joel Berry II · Kevin Knox / Kevin Knox II · Michael Frazier / Michael Frazier II |
| **do not merge** | **Jameer Nelson** (nba 2749, 2009-2018) vs **Jameer Nelson Jr.** (statyx, 2025-26) — father and son |
| unclear | Travis Trice (nba 44839, 2017-18 pre-season) vs Travis Trice II (nba 1626275, 2015-16) — 44839 looks like a non-NBA id that slipped the guard |

Jameer Nelson is exactly why stripping suffixes inside `norm_name` would be
wrong, and why this stays a human decision applied as an `UPDATE`. The five
merges are not yet applied.

## Outstanding

- **`ctx.cur_season` is hardcoded** to `"2025-26"` at `context.py:21` with the
  `nba_parameters` lines commented out, while three ESPN leagues are already
  registered for 2026-27 (with no rows fetched yet). Everything above is
  season-scoped, so this gates it. If the hardcode is flipped before those
  leagues are fetched, the espn directory goes from 1098 ids to 0 — worth a guard
  that logs and skips a platform whose directory comes back empty.
- **`nba.team_roster.player_id` is `double precision`**, cast to bigint in the
  directory view. Worth fixing at source.
- **Dropping `util.conformed_player_id`** waits on the five dashboard views being
  refabricated against the new shape.
- `utility.deduplicate_tables` is still dead code: it takes a `db_con` with
  `.db_con` and `.cur_season`, attributes from the pre-split god-object.

## Files

- `build_fty_dev.sql` — the schema, idempotent
- `build_player_identity.sql` — `util.norm_name`, the two identity tables, and
  the seed from `util.conformed_player_id`. **Not idempotent** — it creates the
  tables; drop them first to re-run
- `build_player_identity_views.sql` — the directory, activity and backlog views
- `one-grain-two-formats.html` — source for the design diagram
- `superseded/` — earlier drafts, kept for history. **Do not run them:** both
  target `fty` rather than `fty_dev`, and predate the pct, `is_scored` and
  `nba_category` decisions above.
