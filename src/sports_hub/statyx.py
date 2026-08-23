import polars as pl
import polars.selectors as cs
import janitor.polars  # noqa: F401  (registers .clean_names() on pl.DataFrame)
from sports_hub.statyx_client import StatyxPipeline, infer_dtypes


class StatyxComponent:
    """Statyx API data (odds, hit-rates, advanced stats) — writes into the statyx.* schema."""

    def __init__(self, db, ctx, sport: str = "nba"):
        self.db = db
        self.ctx = ctx
        self.pipeline = StatyxPipeline(sport=sport, config_path=db.ini_path)

    def get_schedule(self):
        """League schedule via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.schedule' ORDER BY column_order",
        )["column_name"].to_list()

        print("\n--------------------- statyx.schedule")
        df = self.pipeline.run("schedule", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            print("  failed:", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
            .with_columns(cs.by_dtype(pl.Datetime("us", "UTC")).dt.replace_time_zone(None) )
        )

        self.db.write(df, "schedule", schema="statyx")
        print("statyx.schedule has been updated\n\n")

    def get_contracts(self):
        """Player contracts via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.contracts' ORDER BY column_order",
        )["column_name"].to_list()

        print("\n--------------------- statyx.contracts")
        df = self.pipeline.run("contracts", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            print("  failed:", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "contracts", schema="statyx")
        print("statyx.contracts has been updated\n\n")
        return df

    def get_game_stats(self):
        """Player per-game stats via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.game_stats' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        print("\n--------------------- statyx.game_stats")
        df = self.pipeline.run("game_stats", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            print(f"  {len(self.pipeline.errors)} player(s) failed:", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "game_stats", schema="statyx")
        print("statyx.game_stats has been updated\n\n")

    def get_advanced_stats(self):
        """Advanced per-game stats via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.advanced_stats' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()
        since_dt = str(
            self.db.read('SELECT MAX(game_date) FROM statyx.advanced_stats')
            .with_columns((pl.col('max') + pl.duration(days=1)))
            [0,0]
        )

        print("\n--------------------- statyx.advanced_stats")
        df = self.pipeline.run("advanced_stats", params={"since": since_dt}, keys=ls_pl)

        if self.pipeline.errors:
            print(f"  {len(self.pipeline.errors)} player(s) failed:", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "advanced_stats", schema="statyx")
        print("statyx.advanced_stats has been updated\n\n")

    def get_standings(self):
        """League standings via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.standings' ORDER BY column_order",
        )["column_name"].to_list()

        print("\n--------------------- statyx.standings")
        df = self.pipeline.run("standings", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            print("  failed:", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns([
                pl.lit(self.ctx.cur_season).alias("season"),
                pl.lit(self.ctx.date_est).alias("date")
            ])
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "standings", schema="statyx")
        print("statyx.standings has been updated\n\n")

    def get_play_types(self):
        """Player play types via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.play_types' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        print("\n--------------------- statyx.play_types")
        df = self.pipeline.run("play_types", params={"season": self.ctx.cur_season}, keys=ls_pl)

        if self.pipeline.errors:
            print(f"  {len(self.pipeline.errors)} player(s) failed:", self.pipeline.errors)

        df = (
            df.clean_names()
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "play_types", schema="statyx")
        print("statyx.play_types has been updated\n\n")

    def get_shot_zones(self):
        """Player shot-zones via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.shot_zones' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        print("\n--------------------- statyx.shot_zones")
        df = self.pipeline.run("shot_zones", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            print(f"  {len(self.pipeline.errors)} player(s) failed:", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "shot_zones", schema="statyx")
        print("statyx.shot_zones has been updated\n\n")

