-- fty_dev.league_schedule_grid_vw — the complete competitor-by-period schedule.
--
-- league_matchup holds only periods where a competitor has an opponent, and
-- league_byes holds the complement. The two are disjoint by construction, so the
-- grid is a plain UNION ALL and its row count is exactly competitors x periods
-- for every league-season — an invariant worth asserting after any rerun.
--
-- Before this split, get_league_matchup took the period from the POSITION of an
-- entry in con.teams[].schedule and read league_byes in order to splice a None
-- into that list first. espn_api omits a bye from the list in some seasons and
-- inserts None in others, so the padding was load bearing and a wrong bye period
-- shifted every later matchup. Both writers now read ESPN's matchupPeriodId, so
-- neither depends on the other and their write order is free.
--
-- Named _grid_vw rather than replacing league_schedule_vw, which already exists
-- and belongs to the dashboard.

CREATE OR REPLACE VIEW fty_dev.league_schedule_grid_vw AS
SELECT season,
       platform,
       league_id,
       matchup_period,
       competitor_id,
       opponent_id,
       false AS is_bye
FROM fty_dev.league_matchup
UNION ALL
SELECT season,
       platform,
       league_id,
       matchup_period,
       competitor_id,
       NULL::bigint AS opponent_id,
       true AS is_bye
FROM fty_dev.league_byes;
