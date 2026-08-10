import configparser
import polars as pl

from sports_hub.db import Database
from sports_hub.context import Context
from sports_hub.fty_handlers import HANDLERS

PLATFORM = "cockroach"  # fty.* tables live in the same CockroachDB instance


class FtyComponent:
    """
    Fantasy platform integration — writes into the fty.* schema.

    Connections are scoped per LEAGUE, not per customer: multiple customers
    can belong to the same league, and fetching that league's data once
    (rather than once per customer) avoids redundant API calls. Customer
    attribution is a separate join against fty.customer_league downstream,
    not something this component filters by.

    Each (sport, platform) combination has its own handler (see
    fty_handlers/) — ESPN and Yahoo are different APIs, and different
    sports within the same platform have different field/category shapes,
    so each combination owns its full behavior rather than branching
    inside shared methods. Season is derived per-connection by each
    handler (see FtyHandler.connect), not read from Context — Context only
    holds things genuinely generic across every sport/platform.

    NOTE: assumes fty.league has a `sport` column so leagues can be routed
    to the right handler. The original schema this was ported from was
    NBA-only and didn't have one — add it before running this for real.
    """

    def __init__(self, db: Database, ctx: Context):
        self.db = db
        self.ctx = ctx
        self.handlers = {key: cls(db) for key, cls in HANDLERS.items()}
        self.leagues = self._connect_leagues()

    def _season_year_for(self, sport: str) -> int:
        # Season semantics are sport-specific. Only NBA is wired up today —
        # add a branch here (not inside a handler) when a new sport's season
        # source is decided, so this stays the one place that knowledge lives.
        if sport == "nba":
            return self.ctx.cur_season_year
        raise NotImplementedError(f"No season source configured for sport '{sport}'")

    def _connect_leagues(self) -> dict:
        """
        Build one live API connection per active league (not per customer).
        Keyed by (sport, platform, league_id) so callers can dispatch to the
        right handler and know which league a result belongs to.
        """
        df_leagues = self.db.read(
            f"""
                SELECT lgl.sport, lgl.platform, lgl.league_id, lgl_end.league_end_date
                FROM fty.league AS lgl
                LEFT JOIN (
                    SELECT league_id, MAX(matchup_end) AS league_end_date
                    FROM fty.league_matchup_dates
                    WHERE season = '{self.ctx.cur_season}'
                    GROUP BY league_id
                ) AS lgl_end ON lgl.league_id = lgl_end.league_id
                WHERE lgl.season = '{self.ctx.cur_season}'
                    AND (lgl_end.league_end_date + 1) >= '{self.ctx.date_est}'
            """,
            PLATFORM,
        )

        parser = configparser.ConfigParser()
        parser.read(self.db.ini_path)

        leagues = {}
        for row in df_leagues.iter_rows(named=True):
            sport = row["sport"]
            platform = row["platform"]
            league_id = row["league_id"]
            key = (sport, platform)

            if key not in self.handlers:
                print(f"No handler registered for {key} (league {league_id}) — skipping")
                continue

            creds = dict(parser.items(platform.lower() + "_api"))
            season_year = self._season_year_for(sport)

            con = self.handlers[key].connect(league_id, season_year, creds)
            leagues[(sport, platform, league_id)] = con

        return leagues

    def _dispatch(self, method_name: str) -> list:
        dfs = []
        for (sport, platform, league_id), con in self.leagues.items():
            print(f"\n--------------------- {platform};{league_id} fty.{method_name}")
            handler = self.handlers[(sport, platform)]
            dfs.append(getattr(handler, method_name)(con))
        return dfs

    def get_free_agents(self):
        self.db.execute("TRUNCATE TABLE fty.free_agents", PLATFORM)
        df = pl.concat(self._dispatch("get_free_agents"))
        self.db.write(df, "free_agents", schema="fty", platform=PLATFORM)
        print("fty.free_agents has been updated\n\n")

    def get_league_competitor(self):
        self.db.execute(
            f"DELETE FROM fty.league_competitor WHERE season = '{self.ctx.cur_season}'", PLATFORM
        )
        df = pl.concat(self._dispatch("get_league_competitor"))
        self.db.write(df, "league_competitor", schema="fty", platform=PLATFORM)
        print("\nfty.league_competitor has been updated\n\n")

    def get_league_matchup(self):
        self.db.execute(
            f"DELETE FROM fty.league_matchup WHERE season = '{self.ctx.cur_season}'", PLATFORM
        )
        df = pl.concat(self._dispatch("get_league_matchup"))
        self.db.write(df, "league_matchup", schema="fty", platform=PLATFORM)
        print("fty.league_matchup has been updated\n\n")

    def get_competitor_roster(self):
        col_order = self.db.read(
            "SELECT column_name FROM util.table_column_order WHERE table_name = 'competitor_roster' ORDER BY column_order",
            PLATFORM,
        )["column_name"].to_list()

        df_mup = self.db.read(
            f"SELECT * FROM fty.league_matchup_dates WHERE '{self.ctx.date_est}' BETWEEN matchup_start AND matchup_end",
            PLATFORM,
        )

        self.db.execute(
            f"DELETE FROM fty.competitor_roster WHERE assigned_date = '{self.ctx.date_est}'",
            PLATFORM,
        )

        df = (
            pl.concat(self._dispatch("get_competitor_roster"))
            .with_columns(pl.lit(self.ctx.date_est).alias("assigned_date"))
            .join(df_mup, on=["platform", "league_id"], how="left")
            .select(col_order)
        )

        self.db.write(df, "competitor_roster", schema="fty", platform=PLATFORM)
        print("fty.competitor_roster has been updated\n\n")

    def get_recent_activity(self):
        df = pl.concat(self._dispatch("get_recent_activity"))
        self.db.write(df, "recent_activity", schema="fty", platform=PLATFORM)
        print("fty.recent_activity has been updated\n\n")

    def get_matchup_box_score(self):
        # Kept per-league (not batched into one _dispatch call) since leagues
        # can be on different matchup periods — matches the original
        # method's own comment about why this stays league-specific.
        for (sport, platform, league_id), con in self.leagues.items():
            print(f"\n--------------------- {platform};{league_id} fty.matchup_box_score")
            handler = self.handlers[(sport, platform)]
            df = handler.get_matchup_box_score(con)

            self.db.execute(
                f"DELETE FROM fty.matchup_box_score WHERE season = '{con.season}' "
                f"AND platform = '{platform}' AND league_id = {league_id} "
                f"AND matchup = {df['matchup'][0]}",
                PLATFORM,
            )
            self.db.write(df, "matchup_box_score", schema="fty", platform=PLATFORM)
            print(f"{platform};{league_id} fty.matchup_box_score has been updated\n\n")
