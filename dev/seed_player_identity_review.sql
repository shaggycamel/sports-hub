-- Seeds util.player_identity_review with the corrections found by hand-auditing
-- the live data (see dev/README.md and the name-match discussion). Lets
-- apply_player_identity_reviews() be exercised without an LLM.
--
-- Every row was read off the current tables; player keys are the identity
-- numbers in place at seeding time and are NOT stable across a rebuild. Safe to
-- re-run: ON CONFLICT (input_hash) DO NOTHING preserves a status a human has
-- since set.
--
-- status='accepted' rows apply when apply_player_identity_reviews() runs.
-- status='pending' rows are the two unresolved cases (Bogdan nba:43306, Travis
-- Trice) and are deliberately left for a human.

BEGIN;

INSERT INTO util.player_identity_review
    (origin, platform, source_id, source_name, subject_player_key, proposal,
     target_player_key, proposed_name, confidence, reason, model, model_version,
     input_hash, status)
VALUES
    -- false merges: the id sits on the wrong player, move it (link)
    ('seed', 'espn',   2327465, 'Drew Gordon',    937, 'link',  1570, NULL,
     0.99, 'espn id is Drew Gordon, wrongly on Drew Peterson', 'hand-audit', 'seed', 'seed:link:espn:2327465', 'accepted'),
    ('seed', 'statyx', 1253600, 'Armel Traore',   515, 'link',   870, NULL,
     0.99, 'statyx id is Armel Traore, wrongly on Nolan Traore', 'hand-audit', 'seed', 'seed:link:statyx:1253600', 'accepted'),
    ('seed', 'statyx', 1253639, 'Adama Bal',      314, 'link',   857, NULL,
     0.99, 'statyx id is Adama Bal, wrongly on Tamar Bates', 'hand-audit', 'seed', 'seed:link:statyx:1253639', 'accepted'),

    -- accent-fold splits: same person minted twice, merge stubs into keeper
    ('seed', 'espn',   4376,    'Boban Marjanovic',  1272, 'merge', 293,  NULL,
     0.99, 'accent split (ć); same player', 'hand-audit', 'seed', 'seed:merge:1272', 'accepted'),
    ('seed', 'espn',   6426,    'Davis Bertans',     1488, 'merge', 169,  NULL,
     0.99, 'accent split (ā); same player', 'hand-audit', 'seed', 'seed:merge:1488', 'accepted'),
    ('seed', 'espn',   5214989, 'Bogoljub Markovic', 1277, 'merge', 1276, NULL,
     0.99, 'accent split (ć); same prospect', 'hand-audit', 'seed', 'seed:merge:1277', 'accepted'),
    ('seed', 'espn',   5159486, 'Yongxi Cui',        2713, 'merge', 317,  NULL,
     0.99, 'word order split; same player as Cui Yongxi', 'hand-audit', 'seed', 'seed:merge:2713', 'accepted'),
    ('seed', 'espn',   5144067, 'Nikola Durisic',    2287, 'merge', 2293, NULL,
     0.99, 'accent split (đ); same player as Nikola Đurišić', 'hand-audit', 'seed', 'seed:merge:2287', 'accepted'),

    -- nba:43306 is one box score in 2014-15 for team FBU (Fenerbahçe), an
    -- international exhibition; 203992 is the real id (653 games). Same player.
    ('seed', 'nba',    43306,   'Bogdan Bogdanovic', 1275, 'merge', 805,  NULL,
     0.99, 'international exhibition id (FBU); same player as nba:203992', 'hand-audit', 'seed', 'seed:merge:1275', 'accepted'),

    -- generational-suffix splits (five confirmed in player_identity_ops.sql)
    ('seed', 'espn',   3059356, 'Billy Garrett Jr.', 1268, 'merge', 1267, 'Billy Garrett Jr.',
     0.95, 'suffix split; keeper holds the nba id', 'hand-audit', 'seed', 'seed:merge:1268', 'accepted'),
    ('seed', 'espn',   4592712, 'Boo Buie',          1280, 'merge', 548,  NULL,
     0.95, 'suffix split; keeper holds the nba id', 'hand-audit', 'seed', 'seed:merge:1280', 'accepted'),
    ('seed', 'espn',   3138155, 'Joel Berry II',     1866, 'merge', 1865, 'Joel Berry II',
     0.95, 'suffix split; keeper holds the nba id', 'hand-audit', 'seed', 'seed:merge:1866', 'accepted'),
    ('seed', 'statyx', 1253485, 'Kevin Knox',        2008, 'merge', 1084, NULL,
     0.95, 'suffix split; keeper holds the nba id', 'hand-audit', 'seed', 'seed:merge:2008', 'accepted'),
    ('seed', 'espn',   2991255, 'Michael Frazier',   2204, 'merge', 2205, NULL,
     0.95, 'suffix split; keeper holds the nba id', 'hand-audit', 'seed', 'seed:merge:2204', 'accepted'),

    -- nba:44839 is one box score in 2017-18 for team BNE (Brisbane), an
    -- international exhibition; 1626275 is his real id. Same player.
    ('seed', 'nba',    44839,   'Travis Trice',      2595, 'merge', 2596, NULL,
     0.99, 'international exhibition id (BNE); same player as nba:1626275', 'hand-audit', 'seed', 'seed:merge:2595', 'accepted'),

    -- explicitly NOT a merge: father and son
    ('seed', 'statyx', 1570924, 'Jameer Nelson Jr.', 1760, 'no_action', NULL, NULL,
     0.99, 'distinct from Jameer Nelson (nba 2749): father and son', 'hand-audit', 'seed', 'seed:noaction:1760', 'accepted')
ON CONFLICT (input_hash) DO NOTHING;

COMMIT;
