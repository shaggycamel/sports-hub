import polars as pl
import janitor.polars  # noqa: F401  (registers .clean_names() on pl.DataFrame)
from sports_hub.statyx_client import StatyxPipeline

from sports_hub.db import Database
from sports_hub.context import Context

PLATFORM = "cockroach"  # same CockroachDB instance, statyx.* schema keeps this data
# separate from ground-truth nba.* stats


class StatyxComponent:
    """Statyx API data (odds, hit-rates, advanced stats) — writes into the statyx.* schema."""

    def __init__(self, db: Database, ctx: Context, sport: str = "nba"):
        self.db = db
        self.ctx = ctx
        self.pipeline = StatyxPipeline(sport=sport, config_path=db.ini_path)

    def get_player_advanced_stats(self):
        """Advanced per-game stats via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'advanced_stats' ORDER BY column_order",
            PLATFORM,
        )["column_name"].to_list()

        # player_ids sourced from nba_api's active_players — a Statyx<->nba_id
        # crosswalk is expected to live in the database (in progress) rather
        # than this component deriving its own id list from Statyx directly.
        ls_pl = self.ctx.active_players["id"].to_list()

        print("\n--------------------- statyx.advanced_stats")
        df = self.pipeline.run("advanced_stats", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            print(f"  {len(self.pipeline.errors)} player(s) failed:", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
        )

        self.db.write(df, "advanced_stats", schema="statyx", platform=PLATFORM)
        print("statyx.advanced_stats has been updated\n\n")
