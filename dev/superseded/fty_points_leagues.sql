-- fty: support points leagues alongside category leagues
-- Run as one transaction. Reversible up to step 5 (the wide table is kept).
BEGIN;

-- 1. Scoring definition becomes self-contained: resolve the nba_category at
--    ingest, and make "is this a scored category" a per-league fact instead of
--    the global CASE currently hardcoded in fty_categories_vw.
ALTER TABLE fty.league_categories
  ADD COLUMN IF NOT EXISTS nba_category text,
  ADD COLUMN IF NOT EXISTS is_scored    boolean;

UPDATE fty.league_categories lc
SET nba_category = cl.nba_category,
    is_scored    = CASE
                     WHEN lc.points IS NOT NULL THEN true            -- points league: weighted == scored
                     ELSE lc.category NOT IN ('FGM','FGA','FTM','FTA') -- cat league: % components aren't cats
                   END
FROM fty.category_label cl
WHERE cl.fty_category = lc.category;

-- 2. Category semantics that have to exist as data once comparison/aggregation
--    happens in SQL rather than in a hand-written column list.
ALTER TABLE fty.category_label
  ADD COLUMN IF NOT EXISTS higher_is_better boolean,
  ADD COLUMN IF NOT EXISTS agg_type         text;    -- 'sum' | 'ratio'

UPDATE fty.category_label
SET higher_is_better = nba_category NOT IN ('tov','pf','tov_rt'),
    agg_type         = CASE WHEN nba_category LIKE '%\_pct' THEN 'ratio' ELSE 'sum' END;

-- 3. The long box score: observations only. One row per (competitor, matchup,
--    category), identical in both formats. Weighting (points leagues) and
--    comparison (category leagues) are interpretations, not facts, so they are
--    derived downstream from league_categories.points and category_label —
--    never stored here.
CREATE TABLE IF NOT EXISTS fty.matchup_box_score_long (
  season         text    NOT NULL,
  platform       text    NOT NULL,
  league_id      bigint  NOT NULL,
  matchup        bigint  NOT NULL,
  competitor_id  bigint  NOT NULL,
  category       text    NOT NULL,            -- nba_category vocabulary
  value          double precision,            -- raw stat total
  PRIMARY KEY (season, platform, league_id, matchup, competitor_id, category)
);

-- Unpivot everything already collected. NULLs dropped rather than stored, so a
-- league only carries rows for categories it actually scores.
INSERT INTO fty.matchup_box_score_long
  (season, platform, league_id, matchup, competitor_id, category, value)
SELECT bs.season, bs.platform, bs.league_id, bs.matchup, bs.competitor_id,
       u.category, u.value
FROM fty.matchup_box_score bs
CROSS JOIN LATERAL (VALUES
  ('pts', bs.pts),     ('blk', bs.blk),       ('stl', bs.stl),
  ('ast', bs.ast),     ('reb', bs.reb),       ('tov', bs.tov),
  ('fgm', bs.fgm),     ('fga', bs.fga),       ('ftm', bs.ftm),
  ('fta', bs.fta),     ('fg3_m', bs.fg3_m),   ('fg_pct', bs.fg_pct),
  ('ft_pct', bs.ft_pct), ('dd2', bs.dd2),     ('td3', bs.td3)
) AS u(category, value)
WHERE u.value IS NOT NULL
ON CONFLICT DO NOTHING;

-- 4. The format-agnostic matchup outcome. `score` is the column every
--    standings/leaderboard query reads without knowing the format.
CREATE TABLE IF NOT EXISTS fty.matchup_result (
  season         text   NOT NULL,
  platform       text   NOT NULL,
  league_id      bigint NOT NULL,
  matchup        bigint NOT NULL,
  competitor_id  bigint NOT NULL,
  opponent_id    bigint,
  score          double precision,   -- cat: wins + ties/2   | points: fantasy point total
  opponent_score double precision,
  cat_won        bigint,             -- NULL for points leagues
  cat_lost       bigint,
  cat_tied       bigint,
  result         text,               -- W / L / T
  PRIMARY KEY (season, platform, league_id, matchup, competitor_id)
);

-- 5. Keep the existing wide shape alive for current consumers, now sourced
--    from the long table. Drop fty.matchup_box_score only once this is verified.
CREATE OR REPLACE VIEW fty.fty_matchup_box_score_vw AS
SELECT bs.season, bs.platform, bs.league_id, bs.competitor_id, bs.matchup,
       max(bs.value) FILTER (WHERE bs.category = 'pts')    AS pts,
       max(bs.value) FILTER (WHERE bs.category = 'blk')    AS blk,
       max(bs.value) FILTER (WHERE bs.category = 'stl')    AS stl,
       max(bs.value) FILTER (WHERE bs.category = 'ast')    AS ast,
       max(bs.value) FILTER (WHERE bs.category = 'reb')    AS reb,
       max(bs.value) FILTER (WHERE bs.category = 'tov')    AS tov,
       max(bs.value) FILTER (WHERE bs.category = 'fgm')    AS fgm,
       max(bs.value) FILTER (WHERE bs.category = 'fga')    AS fga,
       max(bs.value) FILTER (WHERE bs.category = 'ftm')    AS ftm,
       max(bs.value) FILTER (WHERE bs.category = 'fta')    AS fta,
       max(bs.value) FILTER (WHERE bs.category = 'fg3_m')  AS fg3_m,
       max(bs.value) FILTER (WHERE bs.category = 'fg_pct') AS fg_pct,
       max(bs.value) FILTER (WHERE bs.category = 'ft_pct') AS ft_pct,
       max(bs.value) FILTER (WHERE bs.category = 'dd2')    AS dd2,
       max(bs.value) FILTER (WHERE bs.category = 'td3')    AS td3,
       competitor.competitor_abbrev, competitor.competitor_name
FROM fty.matchup_box_score_long bs
LEFT JOIN fty.league_competitor competitor
  ON  bs.competitor_id = competitor.competitor_id
  AND bs.league_id     = competitor.league_id
  AND bs.platform      = competitor.platform
  AND bs.season        = competitor.season
GROUP BY bs.season, bs.platform, bs.league_id, bs.competitor_id, bs.matchup,
         competitor.competitor_abbrev, competitor.competitor_name;

-- 6. h2h_cat stops being a hardcoded CASE and reads the per-league flag.
CREATE OR REPLACE VIEW fty.fty_categories_vw AS
SELECT fty_cat.platform, fty_cat.season, fty_cat.league_id,
       label.nba_category, label.fty_category, label.fmt_category,
       label.display_order, label.higher_is_better, label.agg_type,
       fty_cat.points,
       COALESCE(fty_cat.is_scored, false) AS h2h_cat
FROM fty.category_label label
LEFT JOIN fty.league_categories fty_cat ON label.fty_category = fty_cat.category;

COMMIT;
