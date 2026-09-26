from sports_hub import SportsHub

hub = SportsHub(db_con='postgres')

CURRENT = "2025-26"
BACKFILL = ["2022-23", "2023-24", "2024-25", CURRENT]

# --- history back to 2022-23 -------------------------------------------------
for season in BACKFILL:
    hub.statyx.get_schedule(season=season)
    hub.statyx.get_standings(season=season)
    hub.statyx.get_game_stats(season=season)

# --- advanced stats only go back to 2024-10-23 -------------------------------
for season in ["2024-25", CURRENT]:
    hub.statyx.get_advanced_stats(season=season)

# --- contracts: no season filter, the API only serves the upcoming year ------
hub.statyx.get_contracts()

# --- everything else is current-season only ----------------------------------
hub.statyx.get_player_info(season=CURRENT)
hub.statyx.get_team_roster(season=CURRENT)
hub.statyx.get_play_types(season=CURRENT)
hub.statyx.get_shot_zones(season=CURRENT)
hub.statyx.get_potential_assists(season=CURRENT)
hub.statyx.get_usage_shock(season=CURRENT)
hub.statyx.get_defense_vs_position(season=CURRENT)
hub.statyx.get_play_type_defense(season=CURRENT)
hub.statyx.get_shot_zone_defense(season=CURRENT)

# --- Statyx returns 500 on these six; re-enable once they fix it -------------
# hub.statyx.get_assist_profile(season=CURRENT)
# hub.statyx.get_drives(season=CURRENT)
# hub.statyx.get_matchup_history(season=CURRENT)
# hub.statyx.get_scoring_breakdown(season=CURRENT)
# hub.statyx.get_shooting_splits(season=CURRENT)
# hub.statyx.get_team_assist_defense(season=CURRENT)
# Contract only last season data
