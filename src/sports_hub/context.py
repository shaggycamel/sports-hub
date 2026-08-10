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

    def __init__(self):
        self.cur_season = nba_parameters.Season.current_season
        self.cur_season_year = int(nba_parameters.Season.current_season[0:4])
        self.prev_season = nba_parameters.Season.previous_season
        self.prev_season_year = int(nba_parameters.Season.previous_season[0:4])
        self.date_est = dt.datetime.now(zoneinfo.ZoneInfo("America/New_York")).date()
        self.timestamp_utc = dt.datetime.now(zoneinfo.ZoneInfo("UTC"))
        self.nba_teams = pl.DataFrame(teams.get_teams())
        self.active_players = pl.DataFrame(players.get_active_players())
