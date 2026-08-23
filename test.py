from sports_hub import SportsHub
from sports_hub import utility

hub = SportsHub(db_con = 'postgres')

# hub.statyx.get_contracts()
hub.statyx.get_schedule()
hub.statyx.get_shot_zones()
hub.statyx.get_standings()

# Reliant on contracts
hub.statyx.get_game_stats() # can't find season
hub.statyx.get_play_types() # empty table
hub.statyx.get_advanced_stats() # empty table

df_stx = hub.db.read('select player_id, player_name from statyx.contracts')
df_db = hub.db.read('select * from util.nba_fty_name_match where is_active')

df = utility.name_match(df_db, df_stx, "conformed_name", "player_name")
view(df)
