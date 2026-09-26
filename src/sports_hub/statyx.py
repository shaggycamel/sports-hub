import logging

import polars as pl
import polars.selectors as cs
import janitor.polars  # noqa: F401  (registers .clean_names() on pl.DataFrame)
from sports_hub.statyx_client import StatyxPipeline, infer_dtypes

logger = logging.getLogger(__name__)


class StatyxComponent:
    """Statyx API data (advanced stats, shot zones, on/off splits) — writes into the statyx.* schema."""

    def __init__(self, db, ctx, sport: str = "nba"):
        self.db = db
        self.ctx = ctx
        self.pipeline = StatyxPipeline(sport=sport, config_path=db.ini_path)

    def get_schedule(self):
        """League schedule via the Statyx API."""
        logger.info("statyx.schedule")
        df = self.pipeline.run("schedule", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.schedule: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
            .with_columns(cs.by_dtype(pl.Datetime("us", "UTC")).dt.replace_time_zone(None) )
        )

        self.db.write_ordered(df, "schedule", schema="statyx")

    def get_contracts(self):
        """Player contracts via the Statyx API."""
        logger.info("statyx.contracts")
        df = self.pipeline.run("contracts", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.contracts: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "contracts", schema="statyx")

    def get_game_stats(self):
        """Player per-game stats via the Statyx API."""
        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.game_stats")
        df = self.pipeline.run("game_stats", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.game_stats: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "game_stats", schema="statyx")

    def get_advanced_stats(self):
        """Advanced per-game stats via the Statyx API."""
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

        if df.is_empty():
            logger.warning("statyx.advanced_stats: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "advanced_stats", schema="statyx")

    def get_standings(self):
        """League standings via the Statyx API."""
        logger.info("statyx.standings")
        df = self.pipeline.run("standings", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.standings: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns([
                pl.lit(self.ctx.cur_season).alias("season"),
                pl.lit(self.ctx.date_est).alias("date")
            ])
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "standings", schema="statyx")

    def get_play_types(self):
        """Player play types via the Statyx API."""
        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.play_types")
        df = self.pipeline.run("play_types", params={"season": self.ctx.cur_season}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.play_types: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "play_types", schema="statyx")

    def get_shot_zones(self):
        """Player shot-zones via the Statyx API."""
        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.shot_zones")
        df = self.pipeline.run("shot_zones", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.shot_zones: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "shot_zones", schema="statyx")

    def get_potential_assists(self):
        """Player potential assists via the Statyx API."""
        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.potential_assists")
        df = self.pipeline.run("potential_assists", params={"season": self.ctx.cur_season}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.potential_assists: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "potential_assists", schema="statyx")

    def get_shooting_splits(self):
        """Player shooting splits via the Statyx API."""
        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.shooting_splits")
        df = self.pipeline.run("shooting_splits", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.shooting_splits: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "shooting_splits", schema="statyx")

    def get_drives(self):
        """Player drives via the Statyx API."""
        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.drives")
        df = self.pipeline.run("drives", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.drives: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "drives", schema="statyx")

    def get_scoring_breakdown(self):
        """Player scoring breakdown via the Statyx API."""
        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.scoring_breakdown")
        df = self.pipeline.run("scoring_breakdown", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.scoring_breakdown: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "scoring_breakdown", schema="statyx")

    def get_assist_profile(self):
        """Player assist profile via the Statyx API."""
        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.assist_profile")
        df = self.pipeline.run("assist_profile", params={"season": self.ctx.cur_season_year}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.assist_profile: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "assist_profile", schema="statyx")

    def get_matchup_history(self):
        """Player matchup history via the Statyx API."""
        ls_pl = self.ctx.active_players["statyx_id"].drop_nulls().to_list()

        logger.info("statyx.matchup_history")
        df = self.pipeline.run("matchup_history", params={"season": self.ctx.cur_season}, keys=ls_pl)

        if self.pipeline.errors:
            logger.warning("%d player(s) failed: %s", len(self.pipeline.errors), self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.matchup_history: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "matchup_history", schema="statyx")

    def get_defense_vs_position(self):
        """Defense vs position via the Statyx API."""
        logger.info("statyx.defense_vs_position")
        df = self.pipeline.run("defense_vs_position", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.defense_vs_position: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "defense_vs_position", schema="statyx")

    def get_play_type_defense(self):
        """Play type defense via the Statyx API."""
        logger.info("statyx.play_type_defense")
        df = self.pipeline.run("play_type_defense", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.play_type_defense: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "play_type_defense", schema="statyx")

    def get_shot_zone_defense(self):
        """Shot zone defense via the Statyx API."""
        logger.info("statyx.shot_zone_defense")
        df = self.pipeline.run("shot_zone_defense", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.shot_zone_defense: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "shot_zone_defense", schema="statyx")

    def get_usage_shock(self):
        """Usage shock via the Statyx API."""
        # /usage-shock takes no season param — it grades a rolling window the
        # API picks itself (window_start/window_end come back in the response).
        logger.info("statyx.usage_shock")
        df = self.pipeline.run("usage_shock")

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.usage_shock: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "usage_shock", schema="statyx")

    def get_team_assist_defense(self):
        """Team assist defense via the Statyx API."""
        logger.info("statyx.team_assist_defense")
        df = self.pipeline.run("team_assist_defense", params={"season": self.ctx.cur_season_year})

        if self.pipeline.errors:
            logger.warning("failed: %s", self.pipeline.errors)

        if df.is_empty():
            logger.warning("statyx.team_assist_defense: API returned no rows — nothing written")
            return

        df = (
            df.clean_names(case_type="snake")
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .pipe(infer_dtypes)
        )

        self.db.write_ordered(df, "team_assist_defense", schema="statyx")