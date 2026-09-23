import time
import zoneinfo
import datetime as dt
import logging
import requests
import bs4
import dateutil.parser
import sqlalchemy
import polars as pl
import polars.selectors as cs
import janitor.polars  # noqa: F401  (registers .clean_names() on pl.DataFrame)
import nbainjuries
import nba_api.stats.endpoints as nba_ep
import tabula

logger = logging.getLogger(__name__)


class NBAComponent:
    """Official NBA stats — writes into the nba.* schema."""

    def __init__(self, db, ctx):
        self.db = db
        self.ctx = ctx

    def get_player_season_stats(self):
        """Season stats (totals)"""
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'player_season_stats' ORDER BY column_order",
        )["column_name"].to_list()
        ls_pl = self.ctx.active_players["nba_id"].drop_nulls().to_list()

        logger.info("nba.player_season_stats")
        dfs = []
        for player in ls_pl:
            player_season = nba_ep.playercareerstats.PlayerCareerStats(player_id=str(player))
            player_season = pl.from_pandas(player_season.data_sets[0].get_data_frame())
            dfs.append(player_season)
            ix = ls_pl.index(player)
            if ix % 50 == 0:
                logger.debug("player: %d / %d", ix, len(ls_pl))
            time.sleep(1)
        logger.debug("player: %d / %d", ix, len(ls_pl))

        df = (
            pl.concat(dfs)
            .clean_names()
            .rename({"season_id": "season", "fg3m": "fg3_m", "fg3a": "fg3_a"})
            .with_columns(pl.lit(self.ctx.cur_season).alias("season"))
            .select(col_order)
        )

        self.db.write(df, "player_season_stats", schema="nba")
        logger.info("nba.player_season_stats has been updated")

    def get_player_info(self):
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'player_info' ORDER BY column_order",
        )["column_name"].to_list()
        ls_pl = self.ctx.active_players["nba_id"].drop_nulls().to_list()

        logger.info("nba.player_info")
        dfs = []
        for player in ls_pl:
            player_info = nba_ep.commonplayerinfo.CommonPlayerInfo(player_id=str(player))
            player_info = pl.from_pandas(player_info.data_sets[0].get_data_frame())
            dfs.append(player_info)
            ix = ls_pl.index(player)
            if ix % 50 == 0:
                logger.debug("player:", ix, "/", len(ls_pl))
            time.sleep(1)
        logger.debug("player:", ix, "/", len(ls_pl))

        df = (
            pl.concat(dfs)
            .clean_names()
            .with_columns(pl.col("height").str.split("-"))
            .with_columns(
                [
                    pl.lit(self.ctx.cur_season).alias("season"),
                    (pl.col("weight").cast(pl.Float64, strict=False) / 2.2046)
                    .round(3)
                    .alias("weight_kg"),
                    (
                        (
                            pl.col("height").list.get(0).cast(pl.Float64) * 12
                            + pl.col("height").list.get(1).cast(pl.Float64)
                        )
                        * 2.54
                    )
                    .round(2)
                    .alias("height_cm"),
                ]
            )
            .rename({"person_id": "player_id"})
            .select(col_order)
        )

        self.db.write(df, "player_info", schema="nba")
        logger.info("nba.player_info has been updated")
        

    def get_team_injuries(self, force_date=None):
        dt_est = self.ctx.date_est if force_date is None else force_date
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'injuries' ORDER BY column_order",
        )["column_name"].to_list()

        teams_true = self.db.read(
            "SELECT CONCAT(team_long, ' ', team_name) AS team, team_slug FROM nba.teams",
        )

        game_ids = self.db.read(
            f"SELECT game_date, game_id, matchup FROM nba.league_game_schedule WHERE game_date BETWEEN '{dt_est}' AND '{dt_est + dt.timedelta(days=2)}' AND matchup LIKE '%@%'",
        )

        player_ids = self.db.read(
            "SELECT nba_name, nba_id FROM util.conformed_player_id"
        ).with_columns(
            pl.col("nba_name").str.normalize("NFKD").str.replace_all("[^\\x00-\\x7F]", "")
        )

        dt_est = dt.datetime.combine(
            dt_est, dt.time(0, 0), tzinfo=zoneinfo.ZoneInfo("America/New_York")
        )
        times = [dt_est + dt.timedelta(minutes=(60 / 4) * i) for i in range(24 * 4)]
        times = [tm.replace(tzinfo=None) for tm in times]
        times.reverse()

        def get_valid_time():
            for tm in times:
                try:
                    nbainjuries._parser.validate_injrepurl(nbainjuries.injury.gen_url(tm))
                    return tm
                except (requests.exceptions.HTTPError, Exception):
                    continue

        df = (
            pl.from_pandas(nbainjuries.injury.get_reportdata(get_valid_time(), return_df=True))
            .clean_names()
            .filter(
                (pl.col("reason") != "NOT YET SUBMITTED") & (pl.col("player_name").is_not_null())
            )
            .with_columns(pl.col("player_name").str.split(", "))
            .with_columns(
                [
                    pl.col("game_date").str.to_date(format="%m/%d/%Y"),
                    pl.col("matchup").str.replace_all("@", " @ "),
                    (
                        pl.col("player_name").list.get(1) + " " + pl.col("player_name").list.get(0)
                    ).alias("player_name"),
                ]
            )
            .join(teams_true, how="left", on="team")
            .join(player_ids, how="left", left_on="player_name", right_on="nba_name")
            .join(game_ids, how="left", on=["game_date", "matchup"])
            .rename({"current_status": "status"})
            .select(col_order)
        )

        for row in df.iter_rows(named=True):
            game_date = row["game_date"]
            game_id = row["game_id"]
            player_name = row["player_name"].replace("'", "''")
            self.db.execute(
                f"DELETE FROM nba.injuries WHERE game_date = '{game_date}' AND game_id = {game_id} AND player_name = '{player_name}'",

            )

        self.db.write(df, "injuries", schema="nba")
        logger.info("nba.injuries has been updated")

    def get_player_box_score(self):
        bs_max_dt = (
            self.db.read(
                "select max(game_date) from nba.nba_player_box_score_vw where min is not null",

            )
            .item()
            .strftime("%Y-%m-%d")
        )
        game_ids = self.db.read(
            f"SELECT DISTINCT game_id FROM nba.league_game_schedule WHERE game_date > '{bs_max_dt}' AND game_date <= '{self.ctx.date_est}'",
        )
        cols_trad = self.db.read(
            "select * from util.table_column_order where table_name = 'player_box_score_traditional' order by column_order",
        )
        cols_adv = self.db.read(
            "select * from util.table_column_order where table_name = 'player_box_score_advanced' order by column_order",
        )

        logger.info("nba.player_box_score")
        dfs = []
        failed = []
        g_ids = game_ids.get_column("game_id").to_list()
        for game_id in g_ids:
            game_id = "00" + str(int(game_id))
            logger.debug("processing game_id %s", game_id)

            # Traditional stats
            try:
                bst = (
                    pl.from_pandas(
                        nba_ep.boxscoretraditionalv3.BoxScoreTraditionalV3(
                            game_id=game_id
                        ).get_data_frames()[0]
                    )
                    .rename(
                        dict(
                            zip(
                                cols_trad.drop_nulls()["origin_name"],
                                cols_trad.drop_nulls()["column_name"],
                            )
                        )
                    )
                    .with_columns(cs.string().replace("", None))
                    .with_columns(pl.col("game_id").cast(pl.Int64))
                    .with_columns(
                        pl.col("min").str.replace(r":.*", "").cast(pl.Int64, strict=False)
                    )
                    .with_columns(
                        (pl.col("firstName") + " " + pl.col("familyName")).alias("player_name")
                    )
                    .select(cols_trad["column_name"].to_list())
                )
            except Exception:
                logger.exception("game_id %s (traditional stats) failed", game_id)
                failed.append(game_id)
                break

            # Advanced stats
            try:
                bsa = (
                    pl.from_pandas(
                        nba_ep.boxscoreadvancedv3.BoxScoreAdvancedV3(
                            game_id=game_id
                        ).get_data_frames()[0]
                    )
                    .rename(
                        dict(
                            zip(
                                cols_adv.drop_nulls()["origin_name"],
                                cols_adv.drop_nulls()["column_name"],
                            )
                        )
                    )
                    .with_columns(cs.string().replace("", None))
                    .with_columns(pl.col("game_id").cast(pl.Int64))
                    .select(cols_adv["column_name"].to_list())
                )
            except Exception:
                logger.exception("game_id %s (advanced stats) failed", game_id)
                failed.append(game_id)
                break

            dfs.append(bst.join(bsa, on=["game_id", "player_id"], how="left"))

        if failed:
            logger.warning("player_box_score: %d game(s) failed: %s", len(failed), failed)

        if not dfs:
            logger.error("player_box_score: nothing fetched, skipping write")
            return

        df = pl.concat(dfs)
        self.db.write(df, "player_box_score", schema="nba")
        logger.info("nba.player_box_score have been updated")

    def get_team_box_score(self):
        bs_max_dt = (
            self.db.read(
                "select max(game_date) from nba.nba_team_box_score_vw where min is not null",

            )
            .item()
            .strftime("%Y-%m-%d")
        )
        game_ids = self.db.read(
            f"SELECT DISTINCT game_id FROM nba.league_game_schedule WHERE game_date > '{bs_max_dt}' AND game_date <= '{self.ctx.date_est}'",
        )
        cols_trad = self.db.read(
            "select * from util.table_column_order where table_name = 'team_box_score_traditional' order by column_order",
        )
        cols_adv = self.db.read(
            "select * from util.table_column_order where table_name = 'team_box_score_advanced' order by column_order",
        )

        logger.info("nba.team_box_score")
        dfs = []
        failed = []
        g_ids = game_ids.get_column("game_id").to_list()
        for game_id in g_ids:
            game_id = "00" + str(int(game_id))
            logger.debug("processing game_id %s", game_id)

            # Traditional stats
            try:
                bst = (
                    pl.from_pandas(
                        nba_ep.boxscoretraditionalv3.BoxScoreTraditionalV3(
                            game_id=game_id
                        ).get_data_frames()[1]
                    )
                    .rename(
                        dict(
                            zip(
                                cols_trad.drop_nulls()["origin_name"],
                                cols_trad.drop_nulls()["column_name"],
                            )
                        )
                    )
                    .with_columns(cs.string().replace("", None))
                    .with_columns(pl.col("game_id").cast(pl.Int64))
                    .with_columns(
                        pl.col("min").str.replace(r":.*", "").cast(pl.Int64, strict=False)
                    )
                    .group_by(["game_id", "team_id", "team_abbreviation"])
                    .agg(cs.numeric().sum())
                    .with_columns(
                        [
                            (pl.col("fgm") / pl.col("fga")).alias("fg_pct"),
                            (pl.col("fg3_m") / pl.col("fg3_a")).alias("fg3_pct"),
                            (pl.col("ftm") / pl.col("fta")).alias("ft_pct"),
                        ]
                    )
                )

                bst = (
                    bst.join_where(
                        bst.select("game_id", "team_id", "pts"),
                        pl.col("game_id") == pl.col("game_id_t"),
                        pl.col("team_id") != pl.col("team_id_t"),
                        suffix="_t",
                    )
                    .with_columns((pl.col("pts") - pl.col("pts_t")).alias("plus_minus"))
                    .select(cols_trad["column_name"].to_list())
                )

            except Exception:
                logger.exception("game_id %s (traditional stats) failed", game_id)
                failed.append(game_id)
                break

            # Advanced stats
            try:
                bsa = (
                    pl.from_pandas(
                        nba_ep.boxscoreadvancedv3.BoxScoreAdvancedV3(
                            game_id=game_id
                        ).get_data_frames()[1]
                    )
                    .rename(
                        dict(
                            zip(
                                cols_adv.drop_nulls()["origin_name"],
                                cols_adv.drop_nulls()["column_name"],
                            )
                        )
                    )
                    .with_columns(cs.string().replace("", None))
                    .with_columns(pl.col("game_id").cast(pl.Int64))
                    .select(cols_adv["column_name"].to_list())
                )
            except Exception:
                logger.exception("game_id %s (advanced stats) failed", game_id)
                failed.append(game_id)
                break

            dfs.append(bst.join(bsa, on=["game_id", "team_id"], how="left"))

        if failed:
            logger.warning("player_box_score: %d game(s) failed: %s", len(failed), failed)

        if not dfs:
            logger.error("player_box_score: nothing fetched, skipping write")
            return

        df = pl.concat(dfs)
        self.db.write(df, "team_box_score", schema="nba")
        logger.info("nba.team_box_score have been updated")

    def update_past_game_schedule(self, season="current"):
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'league_game_schedule' ORDER BY column_order",
        )["column_name"].to_list()

        if season == "current":
            season = self.ctx.cur_season_year
        else:
            season = self.ctx.prev_season_year

        logger.info("nba.historical_league_game_schedule")
        dfs = []
        for type_season in ["Regular Season", "Pre Season", "Playoffs", "All Star"]:
            hist_game_schedule = nba_ep.leaguegamelog.LeagueGameLog(
                season_type_all_star=type_season, season=season
            )
            dfs.append(
                (
                    pl.from_pandas(hist_game_schedule.get_data_frames()[0]).with_columns(
                        pl.lit(type_season).alias("season_type")
                    )
                )
            )
            time.sleep(1)

        df = (
            pl.concat([df for df in dfs if len(df) > 0])
            .clean_names()
            .with_columns(
                [
                    pl.col("game_id").cast(pl.Int64),
                    pl.col("game_date").str.to_date(),
                    pl.lit(f"{season}-{str(season + 1)[-2:]}").alias("season"),
                    pl.col("matchup").str.replace_all(r" @ | vs\.? ", "-").alias("opponent"),
                ]
            )
            .with_columns(pl.col("opponent").str.split("-"))
            .with_columns(
                pl.when(pl.col("team_abbreviation") == pl.col("opponent").list.get(0))
                .then(pl.col("opponent").list.get(1))
                .otherwise(pl.col("opponent").list.get(0))
                .alias("opponent")
            )
            .with_columns(
                [
                    (
                        pl.when(pl.col("wl") == "W")
                        .then(pl.col("team_abbreviation"))
                        .otherwise(pl.col("opponent"))
                        .alias("team_winner")
                    ),
                    (
                        pl.when(pl.col("wl") == "L")
                        .then(pl.col("team_abbreviation"))
                        .otherwise(pl.col("opponent"))
                        .alias("team_loser")
                    ),
                    (
                        pl.when(pl.col("matchup").str.contains("vs."))
                        .then(pl.lit(True))
                        .otherwise(pl.lit(False))
                        .alias("home")
                    ),
                ]
            )
            .rename({"team_abbreviation": "team"})
            .select(col_order)
        )

        self.db.execute(
            f"DELETE FROM nba.league_game_schedule WHERE season = '{self.ctx.cur_season}'",
        )

        self.db.write(df, "league_game_schedule", schema="nba")
        logger.info("nba.historical_game_schedule has been updated")

    def get_next_game_schedule(self):
        request = requests.get("https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json")
        key_dates = self.db.read("SELECT * FROM nba.key_dates")
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'league_game_schedule' ORDER BY column_order",
        )["column_name"].to_list()

        # Remove games already played this season - This assumes update_past_game_schedule is run first
        df_played_games = self.db.read(
            f"SELECT * FROM nba.league_game_schedule WHERE season = '{self.ctx.cur_season}' AND team_winner IS NOT NULL",
        )

        dfs = []
        for game_date in request.json()["leagueSchedule"]["gameDates"]:
            for game in game_date["games"]:
                dfs.append(
                    {
                        "game_id": int(game["gameId"]),
                        "game_date": dateutil.parser.parse(game_date["gameDate"]).date(),
                        "matchup": game["homeTeam"]["teamTricode"]
                        + " vs. "
                        + game["awayTeam"]["teamTricode"],
                    }
                )

        df = (
            pl.concat(
                [
                    pl.DataFrame(dfs),
                    (
                        pl.DataFrame(dfs)
                        .with_columns(pl.col("matchup").str.split_exact(" ", n=2))
                        .unnest(pl.col("matchup"))
                        .with_columns(
                            (pl.col("field_2") + " @ " + pl.col("field_0"))
                            .str.strip_chars()
                            .alias("matchup")
                        )
                        .drop(cs.starts_with("field"))
                    ),
                ]
            )
            .with_columns(pl.col("matchup").str.split_exact(" ", n=2).alias("temp"))
            .unnest(pl.col("temp"))
            .with_columns(
                [
                    pl.lit(self.ctx.cur_season).alias("season"),
                    pl.lit(None).alias("team_winner"),
                    pl.lit(None).alias("team_loser"),
                    pl.col("field_0").alias("team"),
                    pl.col("field_2").alias("opponent"),
                    pl.when(pl.col("field_1") == "vs.")
                    .then(pl.lit(True))
                    .otherwise(pl.lit(False))
                    .alias("home"),
                ]
            )
            .with_columns(
                [
                    pl.when(pl.col("matchup").str.strip_chars().is_in(["@", "vs."]))
                    .then(pl.lit("undetermined") if col == "matchup" else pl.lit(None))
                    .otherwise(pl.col(col))
                    .alias(col)
                    for col in ["team", "opponent", "matchup"]
                ]
            )
            .join_where(
                key_dates,
                pl.col("game_date") >= pl.col("begin_date"),
                pl.col("game_date") <= pl.col("end_date"),
            )
            .select(col_order)
            .join(
                df_played_games.select("game_id").with_columns(pl.col("game_id").cast(pl.Int64)),
                on="game_id",
                how="anti",
            )
            .filter(
                pl.col("team").is_in(self.ctx.nba_teams.get_column("abbreviation").to_list())
                | pl.col("opponent").is_in(self.ctx.nba_teams.get_column("abbreviation").to_list())
                | (pl.col("matchup") == "undetermined")
            )
        )

        self.db.write(df, "league_game_schedule", schema="nba")
        logger.info("nba.current_game_schedule has been updated")

    def get_team_roster(self, pre_season=False):
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'team_roster' ORDER BY column_order",
        )["column_name"].to_list()
        teams = self.ctx.nba_teams["id"].to_list()

        logger.info("nba.team_roster")
        dfs = []
        for team in teams:
            common_teamroster = nba_ep.commonteamroster.CommonTeamRoster(
                season=self.ctx.cur_season_year, team_id=team
            )
            dfs.append(pl.from_pandas(common_teamroster.get_data_frames()[0]))
            ix = teams.index(team)
            if ix % 5 == 0:
                logger.debug("team:", ix, "/", len(teams))
            time.sleep(1)
        logger.debug("team:", ix, "/", len(teams))

        df = (
            pl.concat(dfs)
            .clean_names()
            .with_columns(
                [
                    pl.col("num").str.replace("", None),
                    pl.lit(self.ctx.cur_season).alias("season"),
                    pl.lit(None).cast(pl.Float64).alias("salary"),
                    pl.lit(None if pre_season else self.ctx.date_est).cast(pl.Date).alias("entry_date"),
                    pl.lit(None).cast(pl.Date).alias("exit_date"),
                ]
            )
            .join(self.ctx.nba_teams, left_on="teamid", right_on="id", how="left")
            .rename({"teamid": "team_id", "abbreviation": "team_slug"})
            .select(col_order)
        )

        if pre_season:
            self.db.write(df, "team_roster", schema="nba")
            logger.info("nba.team_roster has been updated")
        else:
            df_existing = self.db.read(
                f"SELECT * FROM nba.team_roster WHERE season = '{self.ctx.cur_season}' AND exit_date IS NULL",

                schema_overrides={
                    "team_id": pl.Int64,
                    "player_id": pl.Int64,
                    "entry_date": pl.Date,
                    "exit_date": pl.Date,
                },
            ).with_columns(
                pl.when(pl.col("exit_date").is_null())
                .then(pl.lit(self.ctx.date_est))
                .otherwise(pl.col("exit_date"))
                .alias("exit_date")
            )

            df_traded = (
                pl.concat([df, df_existing])
                .join(
                    (
                        df.join(
                            df_existing, on=["season", "team_id", "player_id"], how="anti"
                        ).select("player_id")
                    ),
                    on="player_id",
                    how="inner",
                )
                .with_columns(pl.col("salary").backward_fill().forward_fill().over("player_id"))
            )

            if len(df_traded) > 0:
                del_ids = df_traded.unique("player_id")["player_id"].to_list()
                del_ids = ", ".join(map(str, del_ids))

                self.db.execute(
                    f"DELETE FROM nba.team_roster WHERE season = '{self.ctx.cur_season}' AND player_id IN ({del_ids}) AND exit_date IS NULL",
    
                )

                self.db.write(df_traded, "team_roster", schema="nba")
                logger.info("nba.team_roster traded players have been updated")
            else:
                logger.info("nba.team_roster: nothing to update")
