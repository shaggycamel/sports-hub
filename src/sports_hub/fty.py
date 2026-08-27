import configparser
import polars as pl
from sports_hub.fty_handlers import HANDLERS


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

    def __init__(self, db, ctx, sport: str, leagues: pl.DataFrame):
        self.db = db
        self.ctx = ctx
        self.sport = sport
        self.handlers = {key: cls(db) for key, cls in HANDLERS.items()}
        self.leagues = self._connect_leagues(leagues)

    def connect_leagues(self, leagues: pl.DataFrame) -> None:
        """
        (Re)connect to a set of leagues after this component has already
        been constructed — e.g. when `leagues` isn't known until after
        `hub = SportsHub()` has run. Replaces any leagues currently
        connected; call this instead of reaching into `_connect_leagues`
        or `self.leagues` directly.
        """
        self.leagues = self._connect_leagues(leagues)
        self.league_ids = ', '.join(map(str, {league_id for _, _, league_id in self.leagues}))

    def _season_year_for(self, sport: str) -> int:
        # Season semantics are sport-specific. Only NBA is wired up today —
        # add a branch here (not inside a handler) when a new sport's season
        # source is decided, so this stays the one place that knowledge lives.
        if sport == "nba":
            return self.ctx.cur_season_year
        raise NotImplementedError(f"No season source configured for sport '{sport}'")

    def _connect_leagues(self, leagues: pl.DataFrame) -> dict:
        """
        Build one live API connection per row of `leagues` (columns:
        league_id, platform, credentials — a per-league struct of
        secret values, e.g. {swid, espn_s2} for ESPN).

        credentials.ini (same file `db` and `statyx` use) holds one
        section per platform (e.g. [espn_api], [yahoo_api]) that is a
        PLACEHOLDER TEMPLATE only — no real secrets committed to it.
        Every value is a token like `{swid}` or `{league_id}`. At
        connect time, each row's `credentials` struct is merged with
        `league_id`/`cur_year` into a substitution context, and every
        `{key}` placeholder found in the template is replaced with that
        context's value for `key`. Plain string replacement is used
        (not str.format()) since real values — e.g. ESPN's `swid`,
        which is itself wrapped in literal curly braces like
        "{65841A9D-...}" — get substituted into the template text
        rather than the other way around, so there's no risk of them
        being misparsed as format fields.

        Keyed by (sport, platform, league_id) so callers can dispatch to
        the right handler and know which league a result belongs to.
        """
        if leagues.is_empty():
            return {}

        parser = configparser.ConfigParser()
        parser.read(self.db.ini_path)

        connected = {}
        for row in leagues.iter_rows(named=True):
            platform, league_id, row_creds = row["platform"], row["league_id"], row["credentials"]
            key = (self.sport, platform)

            if key not in self.handlers:
                print(f"No handler registered for {key} (league {league_id}) — skipping")
                continue

            season_year = self._season_year_for(self.sport)
            context = {**row_creds, "league_id": league_id, "cur_year": season_year}

            section = platform.lower() + "_api"
            creds = {}
            for k, template_val in parser.items(section):
                value = template_val
                for ctx_key, ctx_val in context.items():
                    value = value.replace("{" + ctx_key + "}", str(ctx_val))
                creds[k] = value

            con = self.handlers[key].connect(league_id, season_year, creds)
            connected[(self.sport, platform, league_id)] = con

        return connected

    def _dispatch(self, method_name: str) -> list:
        dfs = []
        for (sport, platform, league_id), con in self.leagues.items():
            print(f"\n--------------------- {platform};{league_id} fty.{method_name}")
            handler = self.handlers[(sport, platform)]
            dfs.append(getattr(handler, method_name)(con))
        return dfs

    def get_free_agents(self):
        league_ids = {league_id for _, _, league_id in self.leagues}
        self.db.execute(
            f"DELETE FROM fty.free_agents "
            f"WHERE season = '{self.ctx.cur_season}' AND league_id IN ({self.league_ids})"
        )
        df = pl.concat(self._dispatch("get_free_agents"))
        self.db.write(df, "free_agents", schema="fty")
        print("fty.free_agents has been updated\n\n")

    def get_league_competitor(self):
        self.db.execute(
            f"DELETE FROM fty.league_competitor "
            f"WHERE season = '{self.ctx.cur_season}' AND league_id IN ({self.league_ids})"
        )
        df = pl.concat(self._dispatch("get_league_competitor"))
        self.db.write(df, "league_competitor", schema="fty")
        print("\nfty.league_competitor has been updated\n\n")

    def get_league_matchup(self):
        self.db.execute(
            "DELETE FROM fty.league_matchup "
            f"WHERE season = '{self.ctx.cur_season}' AND league_id IN ({self.league_ids})"
        )
        df = pl.concat(self._dispatch("get_league_matchup"))
        self.db.write(df, "league_matchup", schema="fty")
        print("fty.league_matchup has been updated\n\n")

    def get_competitor_roster(self):
        col_order = self.db.read(
            "SELECT column_name "
            "FROM util.table_column_order "
            "WHERE table_name = 'competitor_roster' "
            "ORDER BY column_order",
        )["column_name"].to_list()

        df_mup = self.db.read(
            "SELECT * "
            "FROM fty.league_matchup_dates "
            f"WHERE '{self.ctx.date_est}' BETWEEN matchup_start AND matchup_end",
        )

        self.db.execute(
            "DELETE FROM fty.competitor_roster "
            f"WHERE assigned_date = '{self.ctx.date_est}' AND league_id IN ({self.league_ids})",
        )

        df = (
            pl.concat(self._dispatch("get_competitor_roster"))
            .with_columns(pl.lit(self.ctx.date_est).alias("assigned_date"))
            .join(df_mup, on=["platform", "league_id"], how="left")
            .select(col_order)
        )

        self.db.write(df, "competitor_roster", schema="fty")
        print("fty.competitor_roster has been updated\n\n")

    def get_recent_activity(self):
        df = pl.concat(self._dispatch("get_recent_activity"))
        self.db.write(df, "recent_activity", schema="fty")
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
                "DELETE FROM fty.matchup_box_score "
                f"WHERE season = '{con.season}' "
                f"AND platform = '{platform}' AND league_id = {league_id} "
                f"AND matchup = {df['matchup'][0]}",
            )
            self.db.write(df, "matchup_box_score", schema="fty")
            print(f"{platform};{league_id} fty.matchup_box_score has been updated\n\n")
            return df