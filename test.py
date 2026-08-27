
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

# In fty implement:
# get_league - add column for pts/cat
# get_league_categories - add column for pts value

# Done manually:
# League matchup dates
# League byes
