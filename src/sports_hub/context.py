import datetime as dt
import zoneinfo
import polars as pl
import nba_api.stats.library.parameters as nba_parameters
from nba_api.stats.static import teams, players


class Context:
    """
    Shared, cheap-to-compute reference data that NBA/fty/statyx components
    commonly need (current season, today's date, the active player list).
    Computed once and passed around rather than each component recomputing
    or reaching into a shared god-object.
    """

    def __init__(self, db_con):

        temp_season = "2026-27"
        temp_prev_season = f"{int(temp_season[:4])-1}-{temp_season[2:4]}"
        self.cur_season = temp_season
        self.cur_season_year = int(temp_season[0:4])
        self.prev_season = temp_prev_season
        self.prev_season_year = int(temp_prev_season[0:4])

        # self.cur_season = nba_parameters.Season.current_season
        # self.cur_season_year = int(nba_parameters.Season.current_season[0:4])
        # self.prev_season = nba_parameters.Season.previous_season
        # self.prev_season_year = int(nba_parameters.Season.previous_season[0:4])
        self.date_est = dt.datetime.now(zoneinfo.ZoneInfo("America/New_York")).date()
        self.timestamp_utc = dt.datetime.now(zoneinfo.ZoneInfo("UTC"))
        self.nba_teams = pl.DataFrame(teams.get_teams())
        # NEED TO WRITE util.method THAT UPDATES ACTIVE PLAYERS
        self.active_players = db_con.read('SELECT * FROM util.conformed_player_id WHERE is_active')
