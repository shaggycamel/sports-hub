import datetime as dt
import polars as pl
import espn_api.basketball as bb

from sports_hub.fty_handlers.base import FtyHandler


class EspnNbaHandler(FtyHandler):
    NAME = "ESPN"

    def connect(self, league_id, season_year, creds):

        con = bb.League(
            league_id=int(league_id),
            year=int(season_year) + 1,
            espn_s2=creds["espn_s2"],
            swid=creds["swid"],
        )
        con.season = f"{season_year}-{str(season_year + 1)[-2:]}"
        return con

    def get_free_agents(self, con) -> pl.DataFrame:
        dfs = []
        for free_agent in con.free_agents(size=1000):
            dfs.append(
                {
                    "season": con.season,
                    "platform": self.NAME,
                    "league_id": con.league_id,
                    "timestamp": dt.datetime.now(dt.timezone.utc),
                    "player_id": free_agent.playerId,
                    "player_name": free_agent.name,
                    "player_team": free_agent.proTeam.replace("PHL", "PHI").replace("PHO", "PHX"),
                    "player_injury_status": None
                    if len(free_agent.injuryStatus) == 0
                    else free_agent.injuryStatus,
                    "player_position": free_agent.position,
                }
            )
        return pl.DataFrame(dfs)

    def get_league_competitor(self, con) -> pl.DataFrame:
        dfs = []
        for competitor in con.teams:
            dfs.append(
                {
                    "season": con.season,
                    "platform": self.NAME,
                    "league_id": con.league_id,
                    "competitor_id": competitor.team_id,
                    "competitor_abbrev": competitor.team_abbrev,
                    "competitor_name": competitor.team_name,
                }
            )
        return pl.DataFrame(dfs)

    def get_league_matchup(self, con) -> pl.DataFrame:
        df_byes = self.db.read(
            f"SELECT * FROM fty.league_byes WHERE platform = 'ESPN' AND season = '{con.season}' AND league_id = {con.league_id}",
        )

        dfs = []
        for competitor in con.teams:
            if competitor.team_id in df_byes["competitor_id"].to_list():
                bye_periods = (
                    df_byes.filter(pl.col("competitor_id") == competitor.team_id)
                    .get_column("matchup_period")
                    .to_list()
                )
                for ix in bye_periods:
                    competitor.schedule.insert(ix - 1, None)

            for ix, opponent in enumerate(competitor.schedule):
                dfs.append(
                    {
                        "season": con.season,
                        "platform": self.NAME,
                        "league_id": con.league_id,
                        "matchup_period": ix + 1,
                        "competitor_id": competitor.team_id,
                        "opponent_id": opponent.home_team.team_id
                        if opponent and competitor.team_id == opponent.away_team.team_id
                        else (opponent.away_team.team_id if opponent else None),
                    }
                )
        return pl.DataFrame(dfs)

    def get_competitor_roster(self, con) -> pl.DataFrame:
        dfs = []
        for competitor in con.teams:
            for player in competitor.roster:
                dfs.append(
                    {
                        "season": con.season,
                        "platform": self.NAME,
                        "league_id": con.league_id,
                        "timestamp": dt.datetime.now(dt.timezone.utc),
                        "matchup_period": con.currentMatchupPeriod,
                        "competitor_id": competitor.team_id,
                        "player_fantasy_id": player.playerId,
                        "player_name": player.name,
                        "player_team": player.proTeam.replace("PHL", "PHI").replace("PHO", "PHX"),
                        "player_injury_status": player.injuryStatus,
                        "player_acquisition_type": player.acquisitionType,
                    }
                )
        return pl.DataFrame(dfs)

    def get_recent_activity(self, con) -> pl.DataFrame:
        dfs = []
        for activity in con.recent_activity(size=50):
            for action in activity.actions:
                dfs.append(
                    {
                        "season": con.season,
                        "platform": self.NAME,
                        "league_id": con.league_id,
                        "timestamp": dt.datetime.fromtimestamp(
                            activity.date / 1000, tz=dt.timezone.utc
                        ),
                        "competitor_id": action[0].team_id,
                        "action": action[1],
                        "player": action[2],
                    }
                )

        df_already_done = self.db.read(
            f"SELECT * FROM fty.recent_activity WHERE season = '{con.season}' AND platform = 'ESPN' AND league_id = {con.league_id}",
        )
        return pl.DataFrame(dfs).join(df_already_done, on=df_already_done.columns, how="anti")

    def get_matchup_box_score(self, con) -> pl.DataFrame:
        box_scores = con.box_scores(matchup_period=con.currentMatchupPeriod)
        league_cats = self.db.read(
            f"SELECT * FROM fty.league_categories WHERE platform = 'ESPN' AND season = '{con.season}' AND league_id = {con.league_id}",
        )
        stats = league_cats["category"].to_list()

        dfs = []
        for box_score in box_scores:
            for h_a in ["home", "away"]:
                competitor = getattr(box_score, h_a + "_team")
                competitor_stats = getattr(box_score, h_a + "_stats")

                if competitor != 0:
                    dfs.append(
                        {
                            **{
                                "season": con.season,
                                "platform": self.NAME,
                                "league_id": con.league_id,
                                "competitor_id": competitor.team_id,
                                "matchup": con.currentMatchupPeriod,
                            },
                            **dict(
                                zip(stats, [competitor_stats[stat]["value"] for stat in stats])
                            ),
                        }
                    )

        cat_labels = league_cats.join(
            self.db.read("SELECT * FROM fty.category_label"),
            how="left",
            left_on="category",
            right_on="fty_category",
        )

        return pl.DataFrame(dfs).rename(
            dict(zip(cat_labels["category"], cat_labels["nba_category"]))
        )
