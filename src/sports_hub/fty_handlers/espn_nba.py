import json
import datetime as dt
import polars as pl
import espn_api.basketball as bb
from espn_api.basketball.constant import POSITION_MAP, STATS_MAP

from sports_hub.fty_handlers.base import FtyHandler

# ESPN reports the winning side of a matchup, not each competitor's result.
# UNDECIDED (an unplayed or in-progress period) is absent deliberately, so it
# resolves to None rather than to a decided result.
RESULT_MAP = {
    "HOME": {"home": "W", "away": "L"},
    "AWAY": {"home": "L", "away": "W"},
    "TIE":  {"home": "T", "away": "T"},
}


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

    def get_league(self, con) -> pl.DataFrame:
        # BaseSettings doesn't parse `isPublic` out of ESPN's raw response
        # (only scoring/schedule/trade settings get extracted), so it's
        # pulled directly from the raw league JSON instead of con.settings.
        raw = con.espn_request.get_league()
        return pl.DataFrame(
            [
                {
                    "season": con.season,
                    "platform": self.NAME,
                    "league_id": con.league_id,
                    "league_name": con.settings.name,
                    "scoring_type": con.settings.scoring_type,
                    "team_count": con.settings.team_count,
                    "is_public": raw["settings"]["isPublic"],
                }
            ]
        )

    def get_league_categories(self, con) -> pl.DataFrame:
        # `points` is only meaningful for points-format leagues (scoring_type
        # "H2H_POINTS") — for category leagues (H2H_CATEGORY, ROTOTOTAL) this
        # is forced to None explicitly below, rather than relying on ESPN's
        # scoringItems happening not to carry a points value for those formats.
        #
        # schema_overrides forces `points` to Float64 even when every value
        # in this league is None — otherwise an all-None column infers as
        # Null dtype, which pl.concat can't reconcile against a Float64
        # points column from a different (points-format) league.
        dfs = []
        for item in con.settings._raw_scoring_settings.get("scoringItems", []):
            stat_id = str(item["statId"])
            dfs.append(
                {
                    "season": con.season,
                    "platform": self.NAME,
                    "league_id": con.league_id,
                    "category": STATS_MAP.get(stat_id, f"unknown({stat_id})"),
                    "points": item.get("points") if con.settings.scoring_type == "H2H_POINTS" else None,
                }
            )
        return pl.DataFrame(dfs, schema_overrides={"points": pl.Float64})

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
            f"SELECT * FROM {self.schema}.league_byes WHERE platform = 'ESPN' AND season = '{con.season}' AND league_id = {con.league_id}",
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
            f"SELECT * FROM {self.schema}.recent_activity WHERE season = '{con.season}' AND platform = 'ESPN' AND league_id = {con.league_id}",
        )
        return pl.DataFrame(dfs).join(df_already_done, on=df_already_done.columns, how="anti")

    # Categories that get stored, keyed by ESPN's own label. Ratio categories
    # are excluded: FG%/FT% are derived from their components downstream, so a
    # stored row would duplicate them — and, unlike a count, could not be summed
    # across matchups. Anything ESPN reports with no mapping is dropped rather
    # than stored under a platform-native name.
    def _stored_categories(self) -> dict:
        rows = self.db.read(
            "SELECT pc.platform_category, pc.nba_category "
            f"FROM {self.schema}.platform_category pc "
            f"JOIN {self.schema}.category_label cl ON cl.nba_category = pc.nba_category "
            f"WHERE pc.platform = '{self.NAME}' AND cl.numerator IS NULL"
        )
        return dict(zip(rows["platform_category"], rows["nba_category"]))

    def _raw_matchup_rosters(self, con, matchup_period: int) -> dict:
        """
        {team_id: [per-player raw stat dict]} for a matchup period, read from
        ESPN's own response rather than through BoxPlayer.

        Needed only for points leagues. BoxPlayer cannot serve them:
        points_breakdown prefers ESPN's `appliedStats`, which are already
        multiplied by the league's weights, and BoxPlayer keeps no copy of the
        raw `stats` it passed over. Storing weighted values as `value` would
        break the one property this table relies on — that a row means the same
        thing whatever format the league runs.

        Mirrors the request League.box_scores makes, so the roster returned is
        the same one; only the parsing differs. Bench and IR slots are skipped
        since they do not score, and only statSourceId 0 (actual, not
        projected) is read.
        """
        scoring_period = (
            con.matchup_ids[matchup_period][-1] if matchup_period in con.matchup_ids else 1
        )
        data = con.espn_request.league_get(
            params={"view": ["mMatchupScore", "mScoreboard"], "scoringPeriodId": scoring_period},
            headers={
                "x-fantasy-filter": json.dumps(
                    {"schedule": {"filterMatchupPeriodIds": {"value": [matchup_period]}}}
                )
            },
        )

        rosters = {}
        for matchup in data.get("schedule", []):
            for side in ("home", "away"):
                if side not in matchup:
                    continue
                roster = matchup[side].get("rosterForMatchupPeriod", {})
                players = []
                for entry in roster.get("entries", []):
                    if POSITION_MAP.get(entry.get("lineupSlotId")) in ("BE", "IR"):
                        continue
                    player = (
                        entry["playerPoolEntry"]["player"]
                        if "playerPoolEntry" in entry
                        else entry["player"]
                    )
                    for stat in player.get("stats", []):
                        if stat.get("statSourceId") == 0:
                            players.append(stat.get("stats", {}))
                            break
                rosters[matchup[side]["teamId"]] = players
        return rosters

    @staticmethod
    def _totals_from_team_stats(team_stats: dict, stored: dict) -> dict:
        """Team totals a category league already reports, per category."""
        return {
            stored[label]: stat["value"]
            for label, stat in team_stats.items()
            if label in stored
        }

    @staticmethod
    def _totals_from_players(players: list, stored: dict) -> dict:
        """
        Team totals summed from the lineup, for a points league.

        UNVERIFIED against live data: the only registered points league is
        2026-27, whose season has not started, so box_scores currently returns
        empty lineups. The stat shape read here (statSourceId 0, raw `stats`
        keyed by ESPN stat id) is what a category league's players carry and is
        not format-specific, but confirm the totals against ESPN's own
        appliedStatTotal before trusting them.
        """
        totals = {}
        for player_stats in players:
            for stat_id, value in player_stats.items():
                label = STATS_MAP.get(str(stat_id))
                if label in stored and value is not None:
                    totals[stored[label]] = totals.get(stored[label], 0) + value
        return totals

    def get_matchup_box_score(self, con) -> pl.DataFrame:
        """
        One row per (competitor, category) for the current matchup period.

        Raw values only. Weighting and comparison are both derived downstream —
        from league_categories.points and category_label — so nothing is scored
        here and the rows are identical in shape whatever format the league runs.

        The two formats take different routes because ESPN hands them different
        shapes, not because the output differs: H2HCategoryBoxScore carries
        team-level cumulativeScore.scoreByStat, while H2HPointsBoxScore carries
        only a team total and a lineup, so its categories have to be summed from
        the players. Presence of the `_stats` attribute is the test, since that
        is the actual difference between the two classes.
        """
        box_scores = con.box_scores(matchup_period=con.currentMatchupPeriod)
        stored = self._stored_categories()
        rosters = None

        dfs = []
        for box_score in box_scores:
            for side in ("home", "away"):
                competitor = getattr(box_score, side + "_team")
                if competitor == 0:          # bye: no competitor this period
                    continue

                if hasattr(box_score, side + "_stats"):
                    totals = self._totals_from_team_stats(
                        getattr(box_score, side + "_stats"), stored
                    )
                else:
                    if rosters is None:      # one extra request per league, not per team
                        rosters = self._raw_matchup_rosters(con, con.currentMatchupPeriod)
                    totals = self._totals_from_players(
                        rosters.get(competitor.team_id, []), stored
                    )

                for category, value in totals.items():
                    dfs.append(
                        {
                            "season": con.season,
                            "platform": self.NAME,
                            "league_id": con.league_id,
                            "matchup": con.currentMatchupPeriod,
                            "competitor_id": competitor.team_id,
                            "category": category,
                            "value": value,
                        }
                    )

        return pl.DataFrame(
            dfs,
            schema={
                "season": pl.String,
                "platform": pl.String,
                "league_id": pl.Int64,
                "matchup": pl.Int64,
                "competitor_id": pl.Int64,
                "category": pl.String,
                "value": pl.Float64,
            },
        )

    def get_matchup_result(self, con) -> pl.DataFrame:
        """
        One row per competitor per matchup: the outcome as ESPN reports it,
        not recomputed here.

        `score` is deliberately format-agnostic so that standings read one
        column without branching — categories won for a category league (ESPN's
        own wins + ties/2), the fantasy point total for a points league. The
        won/lost/tied breakdown is category-only and stays null otherwise.

        Note ESPN reports totalPoints as 0.0 for category leagues, so it cannot
        stand in as a general score; each format's own field is read instead.
        """
        box_scores = con.box_scores(matchup_period=con.currentMatchupPeriod)

        dfs = []
        for box_score in box_scores:
            for side, other in (("home", "away"), ("away", "home")):
                competitor = getattr(box_score, side + "_team")
                if competitor == 0:
                    continue
                opponent = getattr(box_score, other + "_team")

                if hasattr(box_score, side + "_wins"):       # category league
                    won = getattr(box_score, side + "_wins")
                    lost = getattr(box_score, side + "_losses")
                    tied = getattr(box_score, side + "_ties")
                    score = won + tied / 2
                    opponent_score = (
                        None if opponent == 0
                        else getattr(box_score, other + "_wins")
                        + getattr(box_score, other + "_ties") / 2
                    )
                else:                                        # points league
                    won = lost = tied = None
                    score = getattr(box_score, side + "_score")
                    opponent_score = None if opponent == 0 else getattr(box_score, other + "_score")

                dfs.append(
                    {
                        "season": con.season,
                        "platform": self.NAME,
                        "league_id": con.league_id,
                        "matchup": con.currentMatchupPeriod,
                        "competitor_id": competitor.team_id,
                        "opponent_id": None if opponent == 0 else opponent.team_id,
                        "score": float(score),
                        "opponent_score": None if opponent_score is None else float(opponent_score),
                        "cat_won": won,
                        "cat_lost": lost,
                        "cat_tied": tied,
                        "result": RESULT_MAP.get(box_score.winner, {}).get(side),
                    }
                )

        return pl.DataFrame(
            dfs,
            schema={
                "season": pl.String,
                "platform": pl.String,
                "league_id": pl.Int64,
                "matchup": pl.Int64,
                "competitor_id": pl.Int64,
                "opponent_id": pl.Int64,
                "score": pl.Float64,
                "opponent_score": pl.Float64,
                "cat_won": pl.Int64,
                "cat_lost": pl.Int64,
                "cat_tied": pl.Int64,
                "result": pl.String,
            },
        )

    def get_league_byes(self, con) -> pl.DataFrame:
        real_rows = []
        for competitor in con.teams:
            for ix, opponent in enumerate(competitor.schedule):
                real_rows.append(
                    {"matchup_period": ix + 1, "competitor_id": competitor.team_id}
                )

        df_real = pl.DataFrame(real_rows).with_columns(pl.lit(True).alias("has_matchup"))

        df_periods = df_real.select("matchup_period").unique()
        df_competitors = pl.DataFrame([{"competitor_id": c.team_id} for c in con.teams])
        df_scaffold = df_periods.join(df_competitors, how="cross")

        df_byes = (
            df_scaffold.join(df_real, on=["matchup_period", "competitor_id"], how="left")
            .filter(pl.col("has_matchup").is_null())
            .with_columns(
                [
                    pl.lit(con.season).alias("season"),
                    pl.lit(self.NAME).alias("platform"),
                    pl.lit(con.league_id).alias("league_id"),
                ]
            )
            .select("season", "platform", "league_id", "matchup_period", "competitor_id")
            .sort("matchup_period", "competitor_id")
        )
        return df_byes