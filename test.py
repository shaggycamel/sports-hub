
from sports_hub import SportsHub

import polars as pl

hub = SportsHub(db_con = 'postgres')
leagues = (
    hub.db.read(f"select * from fty.customer_league where season = '{hub.ctx.cur_season}'")
    .group_by('league_id')
    .first(ignore_nulls=True)
    .filter(~pl.col('league_id').is_in([24608, 1966813226, 95537])) # Remove
)

hub.fty.connect_leagues(leagues=leagues)

# implement pdf method

# In fty implement:
# get_league - add column for pts/cat - table updated manually. 
    # Can potentially be replaced with customer_league? - don't, league table has lague_name, cat type etc...

# get_league_categories - add column for pts value

# Done manually:
# League matchup dates
# League byes

from espn_api.basketball.constant import STATS_MAP

x = hub.fty.leagues[('nba', 'ESPN', 1382487116)]

x.settings.scoring_type
x_raw = x.settings._raw_scoring_settings           # full raw scoringSettings dict from ESPN's API

for item in x_raw.get('scoringItems', []):
    stat_id = str(item['statId'])
    stat_name = STATS_MAP.get(stat_id, f'unknown({stat_id})')
    points = item.get('points') or item.get('pointsOverrides', {}).get('16')  # see note below
    print(stat_name, points)
