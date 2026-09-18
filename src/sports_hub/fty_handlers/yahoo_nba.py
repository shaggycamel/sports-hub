import time
import datetime as dt
import polars as pl
from yfpy.query import YahooFantasySportsQuery as yfpy

from sports_hub.fty_handlers.base import FtyHandler


class YahooNbaHandler(FtyHandler):
    NAME = "Yahoo"

    def connect(self, league_id, season_year, creds):
        creds = dict(creds)
        creds["league_id"] = league_id
        creds["token_time"] = float(creds["token_time"])

        con = yfpy(
            league_id=league_id,
            game_code="nba",
            yahoo_access_token_json=creds,
        )
        con.game_id = int(con.get_league_key(season_year)[0:3])
        con.season = f"{season_year}-{str(season_year + 1)[-2:]}"
        return con

    def get_league(self, con) -> pl.DataFrame:
        info = con.get_league_info()
        return pl.DataFrame(
            [
                {
                    "season": con.season,
                    "platform": self.NAME,
                    "league_id": con.league_id,
                    "league_name": info.name,
                    "scoring_type": info.scoring_type,
                    "team_count": int(info.num_teams),
                    # league_type is "private"/"public" on Yahoo's model —
                    # converted to bool here so this column's dtype matches
                    # ESPN's is_public (Bool) when both get pl.concat'd together.
                    "is_public": info.league_type == "public",
                }
            ]
        )

    def get_league_categories(self, con) -> pl.DataFrame:
        # UNVERIFIED against a live league — confirm the stat_modifiers.stats
        # shape (esp. `value` for points leagues) before relying on this.
        # scoring_type "head" == category league (stat_categories has the
        # ranked cats, no points); a points-format Yahoo league instead uses
        # stat_modifiers.stats, where each Stat's `value` is the point weight.
        settings = con.get_league_settings()
        is_points_league = settings.stat_modifiers.stats and any(
            stat.value not in (None, "") for stat in settings.stat_modifiers.stats
        )

        stats = settings.stat_modifiers.stats if is_points_league else settings.stat_categories.stats

        dfs = []
        for stat in stats:
            if not is_points_league and not stat.enabled:
                continue
            dfs.append(
                {
                    "season": con.season,
                    "platform": self.NAME,
                    "league_id": con.league_id,
                    "category": stat.abbr,
                    "points": float(stat.value) if is_points_league and stat.value not in (None, "") else None,
                }
            )
        return pl.DataFrame(dfs, schema_overrides={"points": pl.Float64})

    def get_free_agents(self, con) -> pl.DataFrame:
        dfs = []
        for player in con.get_league_players():
            p_ownership = con.get_player_ownership(player.player_key)
            if p_ownership.ownership.ownership_type in ["freeagents", "waivers"]:
                dfs.append(
                    {
                        "season": con.season,
                        "platform": self.NAME,
                        "league_id": con.league_id,
                        "player_id": p_ownership.player_id,
                        "player_name": p_ownership.name.full,
                        "player_team": p_ownership.editorial_team_abbr,
                        "player_injury_status": player.status,
                        "player_position": p_ownership.display_position,
                    }
                )
                time.sleep(1)

        n = len(dfs)
        return (
            pl.DataFrame(dfs)
            .with_columns(pl.col("player_injury_status").str.replace("", "ACTIVE"))
            .insert_column(
                4, column=pl.Series("timestamp", [dt.datetime.now(dt.timezone.utc)] * n)
            )
        )

    def get_league_competitor(self, con) -> pl.DataFrame:
        dfs = []
        for competitor in con.get_league_teams():
            dfs.append(
                {
                    "season": con.season,
                    "platform": self.NAME,
                    "league_id": con.league_id,
                    "competitor_id": competitor.team_id,
                    "competitor_abbrev": competitor.managers[0].nickname,
                    "competitor_name": competitor.name.decode(),
                }
            )
        return pl.DataFrame(dfs)

    def get_league_matchup(self, con) -> pl.DataFrame:
        # Matches the original dataHub.py: never implemented upstream either
        # (the Yahoo counterpart was commented out / "YET TO IMPLEMENT").
        raise NotImplementedError("Yahoo league_matchup is not implemented yet")

    def get_competitor_roster(self, con) -> pl.DataFrame:
        dfs = []
        for team_id in [team.team_id for team in con.get_league_teams()]:
            for player in con.get_team_roster_by_week(team_id).clean_data_dict()["players"]:
                if player["player"].selected_position.position is not None:
                    dfs.append(
                        {
                            "season": con.season,
                            "platform": self.NAME,
                            "league_id": con.league_id,
                            "matchup_period": con.get_league_info().current_week,
                            "competitor_id": team_id,
                            "player_fantasy_id": player["player"].player_id,
                            "player_name": player["player"].name.full,
                            "player_team": player["player"].editorial_team_abbr,
                            "player_injury_status": player["player"].status,
                            "player_acquisition_type": None,
                        }
                    )

        n = len(dfs)
        return (
            pl.DataFrame(dfs)
            .with_columns(pl.col("player_injury_status").str.replace("", "ACTIVE"))
            .insert_column(
                4, column=pl.Series("timestamp", [dt.datetime.now(dt.timezone.utc)] * n)
            )
        )

    def get_recent_activity(self, con) -> pl.DataFrame:
        dfs = []
        for activity in con.get_league_transactions():
            if activity.type != "commish":
                for player in activity.players:
                    el_id = [
                        el
                        for el in player.clean_data_dict()["transaction_data"].keys()
                        if el.endswith("_team_key")
                    ][0]

                    dfs.append(
                        {
                            "season": con.season,
                            "platform": self.NAME,
                            "league_id": con.league_id,
                            # NOTE: original used `dt.fromtimestamp(...)` where `dt` is the
                            # `datetime` module (not the class) — that would raise
                            # AttributeError as written. Fixed to dt.datetime.fromtimestamp.
                            "timestamp": dt.datetime.fromtimestamp(
                                activity.timestamp, tz=dt.timezone.utc
                            ),
                            # NOTE: original hardcoded '454.l.121793.t.' here (one specific
                            # league's game_id/league_id baked in as a literal). Generalized
                            # to use this connection's own game_id/league_id instead.
                            "competitor_id": int(
                                player.clean_data_dict()["transaction_data"][el_id].replace(
                                    f"{con.game_id}.l.{con.league_id}.t.", ""
                                )
                            ),
                            "action": player.clean_data_dict()["transaction_data"]["type"],
                            "player": player.clean_data_dict()["name"]["full"],
                        }
                    )

        df_already_done = self.db.read(
            f"SELECT * FROM fty.recent_activity WHERE season = '{con.season}' AND platform = 'Yahoo' AND league_id = {con.league_id}",
        )
        return pl.DataFrame(dfs).join(df_already_done, on=df_already_done.columns, how="anti")

    def get_matchup_box_score(self, con) -> pl.DataFrame:
        qry = f"""
            SELECT
                ls.season,
                ls.platform,
                ls.league_id,
                ls.week AS matchup,
                id.yahoo_id,
                gs.game_date,
                gs.game_id,
                bs.pts,
                bs.blk,
                bs.stl,
                bs.ast,
                bs.reb,
                bs.tov,
                bs.fgm,
                bs.fga,
                bs.ftm,
                bs.fta,
                bs.fg3_m
            FROM nba.player_box_score AS bs
            LEFT JOIN nba.league_game_Schedule AS gs ON bs.game_id = gs.game_id
            LEFT JOIN util.conformed_ids AS id ON bs.player_id = id.nba_id
            INNER JOIN (
                SELECT DISTINCT
                    season,
                    platform,
                    league_id,
                    week,
                    week_start,
                    week_end
                FROM fty.league_schedule
                WHERE platform = 'Yahoo'
                    AND season = '{con.season}'
                    AND league_id = {con.league_id}
                    AND '{dt.date.today()}' BETWEEN week_start AND week_end
            ) AS ls ON gs.game_date BETWEEN ls.week_start AND ls.week_end
        """
        box_scores = self.db.read(qry)

        dfs = []
        for competitor in con.get_league_teams():
            for date_r in pl.date_range(
                box_scores["game_date"].min(), box_scores["game_date"].max(), eager=True
            ):
                for player in con.get_team_roster_player_info_by_date(
                    competitor.team_id, date_r
                ):
                    if player.selected_position.position not in ["IL+", "BN"]:
                        dfs.append(
                            {
                                "competitor_id": competitor.team_id,
                                "game_date": date_r,
                                "yahoo_id": player.player_id,
                            }
                        )

        return (
            pl.DataFrame(dfs)
            .join(box_scores, on=["yahoo_id", "game_date"], how="inner")
            .group_by(["season", "platform", "league_id", "competitor_id", "matchup"])
            .agg(pl.all().exclude(["yahoo_id", "game_date", "game_id"]).sum())
            .with_columns(
                fg_pct=pl.col("fgm") / pl.col("fga"), ft_pct=pl.col("ftm") / pl.col("fta")
            )
        )