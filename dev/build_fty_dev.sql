-- !! HISTORICAL — DO NOT RUN AS-IS !!
-- fty_dev no longer exists: on 2026-10-04 it was promoted to `fty` and the old fty was
-- dropped. Running this would build a parallel fty_dev from the current fty, carrying the
-- schema and view names it had BEFORE that promotion; the three views it defines
-- (fty_categories_vw, matchup_category_vw, fty_matchup_box_score_vw) do not match the nine
-- views that now live in fty. The system of record for views is
-- ~/shaggy_camel_sports/nba/database/sql/views/, applied with that repo's scripts/apply_views.py.
-- Kept for the design history only.
--
-- fty_dev: working schema for the points-league redesign.
-- Source is fty, which is never written to. ESPN only; Yahoo is excluded.
-- Credentials (fty.customer_platform) are deliberately NOT copied — dev code
-- keeps reading them from fty so secrets live in exactly one place.

DROP SCHEMA IF EXISTS fty_dev CASCADE;
CREATE SCHEMA fty_dev;

-- ---------------------------------------------------------------- copy source
CREATE TABLE fty_dev.category_label        AS SELECT * FROM fty.category_label;
CREATE TABLE fty_dev.league                AS SELECT * FROM fty.league                WHERE platform = 'ESPN';
CREATE TABLE fty_dev.league_categories     AS SELECT * FROM fty.league_categories     WHERE platform = 'ESPN';
CREATE TABLE fty_dev.league_competitor     AS SELECT * FROM fty.league_competitor     WHERE platform = 'ESPN';
CREATE TABLE fty_dev.league_matchup        AS SELECT * FROM fty.league_matchup        WHERE platform = 'ESPN';
CREATE TABLE fty_dev.league_matchup_dates  AS SELECT * FROM fty.league_matchup_dates  WHERE platform = 'ESPN';
CREATE TABLE fty_dev.league_byes           AS SELECT * FROM fty.league_byes           WHERE platform = 'ESPN';
CREATE TABLE fty_dev.competitor_roster     AS SELECT * FROM fty.competitor_roster     WHERE platform = 'ESPN';
CREATE TABLE fty_dev.free_agents           AS SELECT * FROM fty.free_agents           WHERE platform = 'ESPN';
CREATE TABLE fty_dev.recent_activity       AS SELECT * FROM fty.recent_activity       WHERE platform = 'ESPN';

-- Customer objects. fty_dev is intended to replace fty, so these come across
-- too — including customer_platform, which holds credentials. That means the
-- secrets now exist in two schemas until fty is retired; keep that in mind.
-- customer has no platform column, so all of it copies; the two mapping tables
-- are ESPN-only like everything else here.
CREATE TABLE fty_dev.customer          AS SELECT * FROM fty.customer;
CREATE TABLE fty_dev.customer_league   AS SELECT * FROM fty.customer_league   WHERE platform = 'ESPN';
CREATE TABLE fty_dev.customer_platform AS SELECT * FROM fty.customer_platform WHERE platform = 'ESPN';

ALTER TABLE fty_dev.customer          ADD PRIMARY KEY (customer_id);
ALTER TABLE fty_dev.customer_league   ADD PRIMARY KEY (customer_id, season, platform, league_id);
ALTER TABLE fty_dev.customer_platform ADD PRIMARY KEY (customer_id, platform);

-- ------------------------------------------------- reference-data repair (ESPN)
-- A. Drop the shooting-component padding. ESPN's scoringItems never held these
--    for category leagues; they were added so the box-score fetch (which reads
--    its stat list from league_categories) would still pull FG%/FT% inputs.
--    ESPN returns them in scoreByStat anyway — 13 keys against 9 scoringItems.
--    points IS NULL keeps the 2026-27 points league, where these four ARE real
--    scoring items, untouched.
DELETE FROM fty_dev.league_categories
WHERE points IS NULL AND category IN ('FGM','FGA','FTM','FTA');

-- B. Restore the nine real categories for 2024-25 Let's Get Tropical, which
--    held only the padding rows. Values read from ESPN, league 1966813226/2024.
INSERT INTO fty_dev.league_categories (season, platform, league_id, category, points)
SELECT '2024-25', 'ESPN', 1966813226, c, NULL::double precision
FROM unnest(ARRAY['PTS','REB','AST','STL','BLK','TO','3PM','FG%','FT%']) AS c;

-- C. Restore its missing fty.league row.
INSERT INTO fty_dev.league (season, platform, league_id, league_name, scoring_type, team_count, is_public)
VALUES ('2024-25', 'ESPN', 1966813226, 'Let''s Get Tropical', 'H2H_MOST_CATEGORIES', 8, false);

-- D. Correct scoring_type to what ESPN reports. Both are H2H_MOST_CATEGORIES:
--    a third format, where the matchup is won on most categories, so standings
--    count matchups rather than category wins.
UPDATE fty_dev.league SET scoring_type = 'H2H_MOST_CATEGORIES'
WHERE (season, league_id) IN (('2023-24', 1966813226), ('2025-26', 24608));

-- ------------------------------------------------------ category vocabulary
-- higher_is_better: the direction a category comparison runs. Points leagues
--   get this free from a negative weight; category leagues need it as data.
-- numerator/denominator: a ratio category is derived from its components and
--   is therefore never stored as a fact. Non-null here == "this is a ratio",
--   which replaces an agg_type flag.
ALTER TABLE fty_dev.category_label
  ADD COLUMN higher_is_better boolean,
  ADD COLUMN numerator        text,
  ADD COLUMN denominator      text;

UPDATE fty_dev.category_label SET higher_is_better = nba_category NOT IN ('tov','pf','tov_rt');
UPDATE fty_dev.category_label SET numerator = 'fgm',   denominator = 'fga'   WHERE nba_category = 'fg_pct';
UPDATE fty_dev.category_label SET numerator = 'ftm',   denominator = 'fta'   WHERE nba_category = 'ft_pct';
UPDATE fty_dev.category_label SET numerator = 'fg3_m', denominator = 'fg3_a' WHERE nba_category = 'fg3_pct';

-- Split the vocabulary from the platform aliases. category_label held one
-- fty_category column doing two jobs: ESPN's abbreviation AND the join key for
-- league_categories.category, which silently assumed every platform spells a
-- category the same way. platform_category makes the alias per-platform, so a
-- new platform is rows rather than a schema change — the same seam as
-- scoring_format, for the same reason.
CREATE TABLE fty_dev.platform_category (
  platform          text NOT NULL,
  platform_category text NOT NULL,       -- the platform's own abbreviation
  nba_category      text NOT NULL,
  PRIMARY KEY (platform, platform_category),
  UNIQUE (platform, nba_category)        -- one label per category, both ways
);

-- Seed ESPN from the labels category_label already carried.
INSERT INTO fty_dev.platform_category (platform, platform_category, nba_category)
SELECT 'ESPN', fty_category, nba_category
FROM fty_dev.category_label
WHERE fty_category IS NOT NULL;

-- category_label is now platform-neutral: the conformed vocabulary only.
ALTER TABLE fty_dev.category_label DROP COLUMN fty_category;
ALTER TABLE fty_dev.category_label ADD PRIMARY KEY (nba_category);
ALTER TABLE fty_dev.platform_category
  ADD FOREIGN KEY (nba_category) REFERENCES fty_dev.category_label (nba_category);
ALTER TABLE fty_dev.league_categories
  ADD PRIMARY KEY (season, platform, league_id, category);

-- No is_scored column: after the repair, league_categories holds exactly
-- ESPN's scoring items, so "is this category scored in this league" is simply
-- whether a row exists. The hardcoded CASE in fty_categories_vw existed only
-- to filter the padding, and goes away with it.

-- ------------------------------------------------------------ the long facts
-- One row per (competitor, matchup, category). Ratio categories are absent by
-- construction, so every row is additive: any window is SUM(value), with no
-- per-category special case.
CREATE TABLE fty_dev.matchup_box_score (
  season         text    NOT NULL,
  platform       text    NOT NULL,
  league_id      bigint  NOT NULL,
  matchup        bigint  NOT NULL,
  competitor_id  bigint  NOT NULL,
  category       text    NOT NULL,
  value          double precision,
  PRIMARY KEY (season, platform, league_id, matchup, competitor_id, category)
);

-- Left EMPTY on purpose, and deliberately not seeded from fty.matchup_box_score:
-- those rows are partial mid-matchup snapshots. Deriving the category record from
-- them disagrees with ESPN's own cumulativeScore on 262 of 1,342 rows, where the
-- refetched values disagree on 0 of 1,534. Fill this with
-- FtyComponent.backfill_matchups(), which reads every period from ESPN.
-- Empty is better than wrong.

-- --------------------------------------------------------- the matchup outcome
-- Format-agnostic: `score` is categories won for H2H_CATEGORY, matchup win for
-- H2H_MOST_CATEGORIES, and the fantasy-point total for H2H_POINTS. Populated by
-- the handler from ESPN's own reported outcome, not derived here.
CREATE TABLE fty_dev.matchup_result (
  season         text   NOT NULL,
  platform       text   NOT NULL,
  league_id      bigint NOT NULL,
  matchup        bigint NOT NULL,
  competitor_id  bigint NOT NULL,
  opponent_id    bigint,
  score          double precision,
  opponent_score double precision,
  cat_won        bigint,
  cat_lost       bigint,
  cat_tied       bigint,
  result         text,
  PRIMARY KEY (season, platform, league_id, matchup, competitor_id)
);

-- ------------------------------------------------------- scoring format map
-- ESPN's vocabulary is the standard. H2H_MOST_CATEGORIES is treated as a
-- category league: it reads the same box score (espn_api routes both to
-- H2HCategoryBoxScore) and scores the same way; only the standings roll-up
-- differs, which is not modelled. Kept as a mapping rather than by rewriting
-- league.scoring_type, so that column stays faithful to what ESPN reports and
-- other platforms get rows here instead of code branches.
CREATE TABLE fty_dev.scoring_format (
  platform      text NOT NULL,
  scoring_type  text NOT NULL,
  scoring_format text NOT NULL,   -- 'category' | 'points'
  PRIMARY KEY (platform, scoring_type)
);
INSERT INTO fty_dev.scoring_format (platform, scoring_type, scoring_format) VALUES
  ('ESPN', 'H2H_CATEGORY',        'category'),
  ('ESPN', 'H2H_MOST_CATEGORIES', 'category'),
  ('ESPN', 'H2H_POINTS',          'points');

-- ------------------------------------------------------------------- views
-- Scored categories per league, with the direction and ratio metadata the
-- comparison needs. Row presence replaces the old hardcoded CASE.
CREATE VIEW fty_dev.fty_categories_vw AS
SELECT lc.season, lc.platform, lc.league_id,
       lc.category AS platform_category, cl.nba_category, cl.fmt_category, cl.display_order,
       lc.points, cl.higher_is_better, cl.numerator, cl.denominator,
       (cl.numerator IS NOT NULL) AS is_ratio,
       lg.scoring_type, sf.scoring_format
FROM fty_dev.league_categories lc
JOIN fty_dev.platform_category pc
  ON pc.platform = lc.platform AND pc.platform_category = lc.category
JOIN fty_dev.category_label cl ON cl.nba_category = pc.nba_category
LEFT JOIN fty_dev.league lg
  ON lg.season = lc.season AND lg.platform = lc.platform AND lg.league_id = lc.league_id
LEFT JOIN fty_dev.scoring_format sf
  ON sf.platform = lg.platform AND sf.scoring_type = lg.scoring_type;

-- Every scored category for a competitor-matchup, with its contribution under
-- whichever rule the league runs. Ratio categories are computed from their
-- components here rather than stored.
CREATE VIEW fty_dev.matchup_category_vw AS
SELECT c.season, c.platform, c.league_id, bs.matchup, bs.competitor_id,
       c.nba_category AS category, c.fmt_category, c.display_order,
       c.scoring_type, c.scoring_format, c.higher_is_better,
       CASE WHEN c.is_ratio
            -- ESPN reports 0 attempts as 0.0 with result LOSS, not as
            -- undefined, so a ratio with no denominator coalesces to 0 rather
            -- than NULL. A NULL would drop the category from the comparison and
            -- disagree with ESPN on any all-zero (forfeited) matchup.
            THEN COALESCE(num.value / NULLIF(den.value, 0), 0)
            ELSE bs.value
       END AS value,
       CASE WHEN c.points IS NOT NULL THEN bs.value * c.points END AS fantasy_points
FROM fty_dev.fty_categories_vw c
JOIN fty_dev.matchup_box_score bs
  ON  bs.season = c.season AND bs.platform = c.platform AND bs.league_id = c.league_id
  AND bs.category = COALESCE(c.numerator, c.nba_category)
LEFT JOIN fty_dev.matchup_box_score num
  ON  num.season = c.season AND num.platform = c.platform AND num.league_id = c.league_id
  AND num.matchup = bs.matchup AND num.competitor_id = bs.competitor_id
  AND num.category = c.numerator
LEFT JOIN fty_dev.matchup_box_score den
  ON  den.season = c.season AND den.platform = c.platform AND den.league_id = c.league_id
  AND den.matchup = bs.matchup AND den.competitor_id = bs.competitor_id
  AND den.category = c.denominator;

-- Compatibility: the wide shape current consumers expect, rebuilt from the long
-- table with the percentages derived.
CREATE VIEW fty_dev.fty_matchup_box_score_vw AS
SELECT bs.season, bs.platform, bs.league_id, bs.competitor_id, bs.matchup,
       max(bs.value) FILTER (WHERE bs.category = 'pts')   AS pts,
       max(bs.value) FILTER (WHERE bs.category = 'blk')   AS blk,
       max(bs.value) FILTER (WHERE bs.category = 'stl')   AS stl,
       max(bs.value) FILTER (WHERE bs.category = 'ast')   AS ast,
       max(bs.value) FILTER (WHERE bs.category = 'reb')   AS reb,
       max(bs.value) FILTER (WHERE bs.category = 'tov')   AS tov,
       max(bs.value) FILTER (WHERE bs.category = 'fgm')   AS fgm,
       max(bs.value) FILTER (WHERE bs.category = 'fga')   AS fga,
       max(bs.value) FILTER (WHERE bs.category = 'ftm')   AS ftm,
       max(bs.value) FILTER (WHERE bs.category = 'fta')   AS fta,
       max(bs.value) FILTER (WHERE bs.category = 'fg3_m') AS fg3_m,
       COALESCE(max(bs.value) FILTER (WHERE bs.category = 'fgm')
         / NULLIF(max(bs.value) FILTER (WHERE bs.category = 'fga'), 0), 0) AS fg_pct,
       COALESCE(max(bs.value) FILTER (WHERE bs.category = 'ftm')
         / NULLIF(max(bs.value) FILTER (WHERE bs.category = 'fta'), 0), 0) AS ft_pct,
       max(bs.value) FILTER (WHERE bs.category = 'dd2')   AS dd2,
       max(bs.value) FILTER (WHERE bs.category = 'td3')   AS td3,
       lc.competitor_abbrev, lc.competitor_name
FROM fty_dev.matchup_box_score bs
LEFT JOIN fty_dev.league_competitor lc
  ON  bs.competitor_id = lc.competitor_id AND bs.league_id = lc.league_id
  AND bs.platform = lc.platform AND bs.season = lc.season
GROUP BY bs.season, bs.platform, bs.league_id, bs.competitor_id, bs.matchup,
         lc.competitor_abbrev, lc.competitor_name;
