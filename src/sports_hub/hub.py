import logging

import polars as pl
from sports_hub.db import Database
from sports_hub.context import Context
from sports_hub.nba import NBAComponent
from sports_hub.fty import FtyComponent
from sports_hub.statyx import StatyxComponent


class SportsHub:
    """
    Composes the shared connection layer (db), shared reference data (ctx),
    and the three data-domain components. Each component only knows how to
    fetch/shape its own domain's data — db is what unifies where it goes.

    Usage:
        hub = SportsHub()
        hub.nba.get_player_season_stats()
        hub.fty.sync_rosters()          # once fty.py is filled in
        hub.statyx.get_player_advanced_stats()
    """

    def __init__(self, ini_path: str | None = None, db_con: str | None = None, sport: str = "nba", leagues: pl.DataFrame | None = None):
        # Components log progress and row counts at INFO. Python drops INFO when
        # nothing has configured logging, which is why a plain script sees silence.
        # basicConfig is a no-op if handlers already exist, so a caller's own
        # setup wins; the second line only raises our own package's level, in case
        # that setup left the root at WARNING (IPython/Positron consoles do).
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
            datefmt="%H:%M:%S",
        )
        logging.getLogger("sports_hub").setLevel(logging.INFO)

        self.db = Database(ini_path, db_con)
        self.ctx = Context(self.db)
        self.sport = sport

        self.nba = NBAComponent(self.db, self.ctx)
        self.fty = FtyComponent(self.db, self.ctx, sport, leagues if leagues is not None else pl.DataFrame())
        self.statyx = StatyxComponent(self.db, self.ctx, sport=sport)