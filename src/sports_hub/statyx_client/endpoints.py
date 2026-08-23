from sports_hub.statyx_client.transforms import flatten_hit_rates

API_ROOT = "https://api.statyx.io/v1"

BASE_URLS = {
    "nba":    f"{API_ROOT}/nba",
    "nfl":    f"{API_ROOT}/nfl",
    "mlb":    f"{API_ROOT}/mlb",
    "wnba":   f"{API_ROOT}/wnba",
    "football": f"{API_ROOT}/football", 
}

ENDPOINTS = {
    "nba": {
        "game_stats":     {"path": "/players/{key}/game-stats",    "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "shot_zones":     {"path": "/players/{key}/shot-zones",    "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "hit_rates":      {"path": "/players/{key}/hit-rates",     "keyed": True,  "key_column": "player_id", "paginated": False, "flatten": flatten_hit_rates},
        "advanced_stats": {"path": "/players/{key}/advanced-stats","keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "play_types":     {"path": "/players/{key}/play-types",    "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "schedule":       {"path": "/schedule",                    "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "standings":      {"path": "/standings",                   "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "contracts":      {"path": "/contracts",                   "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
    },
    "nfl": {
        "game_stats":     {"path": "/players/{key}/stats",           "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "season_stats":   {"path": "/players/{key}/season-stats",    "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "advanced_stats": {"path": "/players/{key}/advanced-stats",  "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "hit_rates":      {"path": "/players/{key}/hit-rates",       "keyed": True,  "key_column": "player_id", "paginated": False, "flatten": flatten_hit_rates},
        "coverage":       {"path": "/players/{key}/coverage",        "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "schedule":       {"path": "/schedule",                      "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "standings":      {"path": "/standings",                     "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "team_stats":     {"path": "/team-stats",                    "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "team_coverage":  {"path": "/team-coverage",                 "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "defense_vs_position": {"path": "/defense-vs-position",      "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "depth_charts":   {"path": "/depth-charts",                  "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "injuries":       {"path": "/injuries",                      "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
    },
    "mlb": {
        "game_stats":     {"path": "/players/{key}/game-stats",      "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "season_stats":   {"path": "/players/{key}/season-stats",    "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "hit_rates":      {"path": "/players/{key}/hit-rates",       "keyed": True,  "key_column": "player_id", "paginated": False, "flatten": flatten_hit_rates},
        "schedule":       {"path": "/schedule",                      "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "standings":      {"path": "/standings",                     "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "team_stats":     {"path": "/team-stats",                    "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "probable_lineups": {"path": "/probable-lineups",            "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "probable_pitchers": {"path": "/probable-pitchers",          "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "injuries":       {"path": "/injuries",                      "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "ballparks":      {"path": "/ballparks",                     "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "park_factors":   {"path": "/park-factors",                  "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
    },
    "wnba": {
        "game_stats":     {"path": "/players/{key}/game-stats",      "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "season_stats":   {"path": "/players/{key}/season-stats",    "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "advanced_stats": {"path": "/players/{key}/advanced-stats",  "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "shot_locations": {"path": "/players/{key}/shot-locations",  "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "hit_rates":      {"path": "/players/{key}/hit-rates",       "keyed": True,  "key_column": "player_id", "paginated": False, "flatten": flatten_hit_rates},
        "schedule":       {"path": "/schedule",                      "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "standings":      {"path": "/standings",                     "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "defense_vs_position": {"path": "/defense-vs-position",      "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "injuries":       {"path": "/injuries",                      "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
    },
    "football": {
        "game_stats":     {"path": "/players/{key}/game-stats",      "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "season_stats":   {"path": "/players/{key}/season-stats",    "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "shotmap":        {"path": "/players/{key}/shotmap",         "keyed": True,  "key_column": "player_id", "paginated": True,  "flatten": None},
        "hit_rates":      {"path": "/players/{key}/hit-rates",       "keyed": True,  "key_column": "player_id", "paginated": False, "flatten": flatten_hit_rates},
        "fixtures":       {"path": "/fixtures",                      "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "competitions":   {"path": "/competitions",                  "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
        "teams":          {"path": "/teams",                         "keyed": False, "key_column": None,        "paginated": True,  "flatten": None},
    },
}
