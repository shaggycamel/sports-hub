-- util.player_identity_check — the DB-native integrity assertions for
-- util.player / util.player_source_id. Every check must report 0 failures.
--
--     SELECT * FROM util.player_identity_check WHERE failures > 0;   -- want no rows
--
-- The third check, colliding_names, folds names through NFKD and so cannot live
-- in portable SQL (no user-defined function on Cockroach). It is in Python:
-- UtilComponent.check_player_identity(), which returns all three checks as a
-- frame. Use that for a complete pass; this view stays for a quick SQL look.

BEGIN;

CREATE OR REPLACE VIEW util.player_identity_check AS
SELECT 'players_owning_nothing' AS check_name, count(*) AS failures
FROM util.player p
WHERE NOT EXISTS (
    SELECT 1 FROM util.player_source_id m WHERE m.player_key = p.player_key
)
UNION ALL
SELECT 'backlog', count(*)
FROM util.unmatched_player_source_vw;

COMMIT;
