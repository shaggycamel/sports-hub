import logging

import polars as pl
import polars.selectors as cs
import janitor.polars  # noqa: F401  (registers .clean_names() on pl.DataFrame)
from sports_hub.statyx_client import StatyxPipeline, infer_dtypes

logger = logging.getLogger(__name__)


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

        logger.info("statyx.schedule")
        df = self.pipeline.run("schedule", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
            .with_columns(cs.by_dtype(pl.Datetime("us", "UTC")).dt.replace_time_zone(None) )
        )

        self.db.write(df, "schedule", schema="statyx")
        logger.info("statyx.schedule has been updated (%d rows)", len(df))

    def get_contracts(self):
        """Player contracts via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.contracts' ORDER BY column_order",
        )["column_name"].to_list()

        logger.info("statyx.contracts")
        df = self.pipeline.run("contracts", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "contracts", schema="statyx")
        logger.info("statyx.contracts has been updated (%d rows)", len(df))

    def get_game_stats(self):
        """Player per-game stats via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.game_stats' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.game_stats")
        df = self.pipeline.run("game_stats", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "game_stats", schema="statyx")
        logger.info("statyx.game_stats has been updated (%d rows)", len(df))

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

        logger.info("statyx.advanced_stats")
        df = self.pipeline.run("advanced_stats", params={"since": since_dt}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "advanced_stats", schema="statyx")
        logger.info("statyx.advanced_stats has been updated (%d rows)", len(df))

    def get_standings(self):
        """League standings via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.standings' ORDER BY column_order",
        )["column_name"].to_list()

        logger.info("statyx.standings")
        df = self.pipeline.run("standings", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

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
        logger.info("statyx.standings has been updated (%d rows)", len(df))

    def get_play_types(self):
        """Player play types via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.play_types' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.play_types")
        df = self.pipeline.run("play_types", params={"season": self.ctx.cur_season}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        df = (
            df.clean_names()
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "play_types", schema="statyx")
        logger.info("statyx.play_types has been updated (%d rows)", len(df))

    def get_shot_zones(self):
        """Player shot-zones via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.shot_zones' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.shot_zones")
        df = self.pipeline.run("shot_zones", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "shot_zones", schema="statyx")
        logger.info("statyx.shot_zones has been updated (%d rows)", len(df))

    def get_hit_rates(self):
        """Player hit rates via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.hit_rates' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.hit_rates")
        df = self.pipeline.run("hit_rates", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "hit_rates", schema="statyx")
        logger.info("statyx.hit_rates has been updated (%d rows)", len(df))

    def get_potential_assists(self):
        """Player potential assists via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.potential_assists' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.potential_assists")
        df = self.pipeline.run("potential_assists", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "potential_assists", schema="statyx")
        logger.info("statyx.potential_assists has been updated (%d rows)", len(df))

    def get_shooting_splits(self):
        """Player shooting splits via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.shooting_splits' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.shooting_splits")
        df = self.pipeline.run("shooting_splits", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "shooting_splits", schema="statyx")
        logger.info("statyx.shooting_splits has been updated (%d rows)", len(df))

    def get_drives(self):
        """Player drives via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.drives' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.drives")
        df = self.pipeline.run("drives", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "drives", schema="statyx")
        logger.info("statyx.drives has been updated (%d rows)", len(df))

    def get_scoring_breakdown(self):
        """Player scoring breakdown via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.scoring_breakdown' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.scoring_breakdown")
        df = self.pipeline.run("scoring_breakdown", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "scoring_breakdown", schema="statyx")
        logger.info("statyx.scoring_breakdown has been updated (%d rows)", len(df))

    def get_assist_profile(self):
        """Player assist profile via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.assist_profile' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.assist_profile")
        df = self.pipeline.run("assist_profile", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "assist_profile", schema="statyx")
        logger.info("statyx.assist_profile has been updated (%d rows)", len(df))

    def get_odds(self):
        """Player odds via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.odds' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.odds")
        df = self.pipeline.run("odds", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "odds", schema="statyx")
        logger.info("statyx.odds has been updated (%d rows)", len(df))

    def get_matchup_history(self):
        """Player matchup history via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.matchup_history' ORDER BY column_order",
        )["column_name"].to_list()

        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.matchup_history")
        df = self.pipeline.run("matchup_history", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "matchup_history", schema="statyx")
        logger.info("statyx.matchup_history has been updated (%d rows)", len(df))

    def get_defense_vs_position(self):
        """Defense vs position via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.defense_vs_position' ORDER BY column_order",
        )["column_name"].to_list()

        logger.info("statyx.defense_vs_position")
        df = self.pipeline.run("defense_vs_position", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "defense_vs_position", schema="statyx")
        logger.info("statyx.defense_vs_position has been updated (%d rows)", len(df))

    def get_play_type_defense(self):
        """Play type defense via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.play_type_defense' ORDER BY column_order",
        )["column_name"].to_list()

        logger.info("statyx.play_type_defense")
        df = self.pipeline.run("play_type_defense", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "play_type_defense", schema="statyx")
        logger.info("statyx.play_type_defense has been updated (%d rows)", len(df))

    def get_shot_zone_defense(self):
        """Shot zone defense via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.shot_zone_defense' ORDER BY column_order",
        )["column_name"].to_list()

        logger.info("statyx.shot_zone_defense")
        df = self.pipeline.run("shot_zone_defense", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "shot_zone_defense", schema="statyx")
        logger.info("statyx.shot_zone_defense has been updated (%d rows)", len(df))

    def get_usage_shock(self):
        """Usage shock via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.usage_shock' ORDER BY column_order",
        )["column_name"].to_list()

        logger.info("statyx.usage_shock")
        df = self.pipeline.run("usage_shock", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "usage_shock", schema="statyx")
        logger.info("statyx.usage_shock has been updated (%d rows)", len(df))

    def get_team_assist_defense(self):
        """Team assist defense via the Statyx API."""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'statyx.team_assist_defense' ORDER BY column_order",
        )["column_name"].to_list()

        logger.info("statyx.team_assist_defense")
        df = self.pipeline.run("team_assist_defense", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        df = (
            df.clean_names()
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
            .pipe(infer_dtypes)
        )

        self.db.write(df, "team_assist_defense", schema="statyx")
        logger.info("statyx.team_assist_defense has been updated (%d rows)", len(df))