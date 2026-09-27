-- Views over util.player / util.player_source_id.
--
-- Nothing here stores activity. The old util.conformed_player_id.is_active was a
-- boolean with no writer anywhere in the repo and no util.update_schedule row,
-- and it had drifted accordingly: 124 players with a 2025-26 roster row were not
-- flagged active, 29 had no crosswalk row at all, and 38 were flagged active
-- while sitting on no roster.
--
-- Activity is derived instead, from tables existing scheduled jobs already
-- refresh daily, so it cannot go stale. It is also scoped to a season, because
-- "active" was always implicitly per-season and the single boolean had nowhere
-- to put that.

BEGIN;

-- Every (season, platform, source_id) any source has ever reported.
--
-- The nba side is a UNION of the box scores and the roster for a reason worth
-- keeping: team_roster is a SAMPLED snapshot — it records whoever the job saw
-- when it ran, so a 10-day contract signed and expired between runs never
-- appears — whereas player_box_score is a COMPLETE event log, since a player
-- cannot appear in a game without landing in one. For 2025-26 the box scores
-- hold 710 players and the roster 645, and 72 of the 710 have no roster row at
-- all. The roster still earns its place: early in a season the event log is
-- nearly empty and the roster is all there is.
--
-- team_roster.player_id is double precision (the table predates the current
-- writer) and is cast here; that wart is worth fixing at source separately.
--
-- The player_id guard drops non-NBA participants. NBA's feed assigns temporary
-- ids to opposition players in pre-season exhibitions against international
-- clubs — 280 of them across the history, all confined to Pre Season games, 34
-- of them in 2025-26 ("Oz Blayzer", "Netanel Artzi", "Shi Yuchen"). Dropping
-- Pre Season wholesale would be the tidier rule but costs too much: it also
-- loses 34 real NBA players who appeared in no other game type. The id
-- magnitude separates them cleanly instead — real ids top out at 6,664,001, the
-- temporary block starts at 196,294,083, and the range between the two is
-- entirely empty (nba_api's own player universe maxes at 1,643,141).
CREATE VIEW util.player_directory_vw AS
SELECT s.season, 'nba' AS platform, b.player_id AS source_id, b.player_name AS source_name
FROM nba.player_box_score b
JOIN (SELECT DISTINCT game_id, season FROM nba.league_game_schedule) s USING (game_id)
WHERE b.player_id IS NOT NULL
  AND b.player_id < 100000000
UNION
SELECT r.season, 'nba', r.player_id::bigint, r.player
FROM nba.team_roster r
WHERE r.player_id IS NOT NULL
UNION
SELECT pi.season, 'statyx', pi.player_id, pi.full_name
FROM statyx.player_info pi
WHERE pi.player_id IS NOT NULL
UNION
SELECT fa.season, lower(fa.platform), fa.player_id, fa.player_name
FROM fty.free_agents fa
WHERE fa.player_id IS NOT NULL
UNION
SELECT cr.season, lower(cr.platform), cr.player_fantasy_id, cr.player_name
FROM fty.competitor_roster cr
WHERE cr.player_fantasy_id IS NOT NULL;

-- One row per player per source per season, with the mapping attached.
--
-- The join to player_source_id is a LEFT join on purpose: a source id that
-- identity resolution has not reached yet still drives a fetch loop. A brand new
-- player is fetched on the first run that sees them, and acquires a player_key
-- whenever util.conform_player_ids next runs.
CREATE VIEW util.active_player_vw AS
SELECT DISTINCT ON (d.season, d.platform, d.source_id)
       d.season,
       d.platform,
       d.source_id,
       d.source_name,
       m.player_key,
       p.conformed_name
FROM util.player_directory_vw d
LEFT JOIN util.player_source_id m ON m.platform = d.platform AND m.source_id = d.source_id
LEFT JOIN util.player p ON p.player_key = m.player_key
ORDER BY d.season, d.platform, d.source_id, d.source_name;

-- The resolution backlog: source ids the directory has seen that no player owns.
-- Non-empty means util.conform_player_ids has work to do. This is what replaces
-- a util.update_schedule row — the daily jobs that write the source tables are
-- already the detector, so no new scheduled job is needed.
CREATE VIEW util.unmatched_player_source_vw AS
SELECT d.season, d.platform, d.source_id, d.source_name
FROM util.player_directory_vw d
LEFT JOIN util.player_source_id m ON m.platform = d.platform AND m.source_id = d.source_id
WHERE m.player_key IS NULL;

COMMIT;
