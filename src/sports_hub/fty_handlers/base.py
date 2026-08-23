import polars as pl


class FtyHandler:
    """
    Interface for one (sport, platform) fantasy integration. Each handler
    owns everything specific to its combination — how to authenticate, what
    fields/categories that sport's API exposes, and how season is derived
    from its own connection object (not from a shared Context — see
    connect()). FtyComponent only knows how to look a handler up by
    (sport, platform) and call these methods; it doesn't know any of these
    per-combination details itself.
    """

    def __init__(self, db):
        self.db = db

    def connect(self, league_id: int, season_year: int, creds: dict):
        """
        Build and return a live API connection/session for this league.
        Implementations should stamp a `.season` attribute (and anything
        else downstream methods need, e.g. `.game_id` for Yahoo) onto the
        returned object here, once, rather than re-deriving it per call.
        """
        raise NotImplementedError

    def get_free_agents(self, con) -> pl.DataFrame:
        raise NotImplementedError

    def get_league_competitor(self, con) -> pl.DataFrame:
        raise NotImplementedError

    def get_league_matchup(self, con) -> pl.DataFrame:
        raise NotImplementedError

    def get_competitor_roster(self, con) -> pl.DataFrame:
        raise NotImplementedError

    def get_recent_activity(self, con) -> pl.DataFrame:
        raise NotImplementedError

    def get_matchup_box_score(self, con) -> pl.DataFrame:
        raise NotImplementedError
