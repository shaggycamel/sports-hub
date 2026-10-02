-- fty reference-data repair. Every value below was read from ESPN, not inferred.
-- Run before the points-league migration; verify counts at the end before COMMIT.
BEGIN;

-- A. Remove the shooting-component padding rows.
--    ESPN's scoringItems never contained FGM/FGA/FTM/FTA for these category
--    leagues (live: 9 items where the DB holds 13). They were added so that
--    get_matchup_box_score, which drives its stat list off league_categories,
--    would still fetch the inputs to FG%/FT%. ESPN returns those in
--    scoreByStat regardless — verified: 13 keys against 9 scoringItems — so
--    the padding is unnecessary as well as untrue.
--    Scoped to points IS NULL so the 2026-27 points league, where these four
--    ARE genuine scoring items, is left alone.
DELETE FROM fty.league_categories
WHERE platform = 'ESPN'
  AND points IS NULL
  AND category IN ('FGM','FGA','FTM','FTA');        -- expect 24 rows

-- B. Restore the nine real categories for 2024-25 Let's Get Tropical.
--    That league-season held only the four padding rows; its actual categories
--    had been deleted. Values from ESPN for league 1966813226, year 2024.
INSERT INTO fty.league_categories (season, platform, league_id, category, points)
SELECT '2024-25', 'ESPN', 1966813226, c, NULL::double precision
FROM unnest(ARRAY['PTS','REB','AST','STL','BLK','TO','3PM','FG%','FT%']) AS c;

-- C. Restore its missing fty.league row (league_categories referenced a
--    season that fty.league had no row for at all).
INSERT INTO fty.league (season, platform, league_id, league_name, scoring_type, team_count, is_public)
VALUES ('2024-25', 'ESPN', 1966813226, 'Let''s Get Tropical', 'H2H_MOST_CATEGORIES', 8, false);

-- D. Correct scoring_type where the stored value disagrees with ESPN.
--    Both leagues are H2H_MOST_CATEGORIES — a third format, not a category
--    league: the matchup is won by taking the most categories, so standings
--    count matchups, not category wins.
UPDATE fty.league SET scoring_type = 'H2H_MOST_CATEGORIES'
WHERE platform = 'ESPN'
  AND (season, league_id) IN (('2023-24', 1966813226), ('2025-26', 24608));

-- Verify before committing: every ESPN league-season should now equal ESPN.
--   2023-24 1966813226  9    2024-25 95537       9    2024-25 1966813226  9
--   2025-26 24608      11    2025-26 95537       9    2025-26 1382487116  8
--   2025-26 1966813226  9    2026-27 95537       9    2026-27 1440301458 11
--   2026-27 1966813226  9    2024-25 Yahoo 121793 9
SELECT season, platform, league_id, count(*) AS n
FROM fty.league_categories GROUP BY 1,2,3 ORDER BY platform, league_id, season;

COMMIT;
