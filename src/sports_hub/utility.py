import difflib
import logging
import polars as pl
from sqlalchemy import text
from pathlib import Path
from yfpy.query import YahooFantasySportsQuery  as yfpy

logger = logging.getLogger(__name__)


def deduplicate_tables(db_con):

    # ----------------------- Tables
    df_tables = pl.read_database(
        """
        SELECT DISTINCT table_schema, table_name
        FROM information_schema.columns
        WHERE column_name = 'season'
            AND table_name NOT LIKE '%%_vw'
            AND table_name NOT ILIKE '%%_retired'
        """,
        db_con.db_con,
    )

    ls_tables = list(df_tables.get_column('table_schema') + '.' + df_tables.get_column('table_name'))

    # ----------------------- Dedup block
    for table in ls_tables:
        df = pl.read_database(
            f"SELECT * FROM {table} WHERE season = '{db_con.cur_season}'",
            db_con.db_con,
            infer_schema_length=None,
        )

        if df.unique().height == df.height:
            continue

        df_dedup = df.unique()
        with db_con.db_con.begin() as conn:  # single transaction — commits on success, rolls back on exception
            conn.execute(text(f"DELETE FROM {table} WHERE season = '{db_con.cur_season}'"))
            df_dedup.write_database(table, conn, if_table_exists='append')

        logger.info("%s has been deduplicated", table)


def name_match(
    df_left: pl.DataFrame,
    df_right: pl.DataFrame,
    left_on: str,
    right_on: str,
) -> pl.DataFrame:
    """
    Fuzzy-match names between two DataFrames using difflib.

    For each name in df_left[left_on], finds close matches in
    df_right[right_on]. Results are pivoted wide so each match is its own
    column (match_1, match_2, ...) appended to df_left.

    Unmatched names from both sides are included with null values.

    Parameters
    ----------
    df_left : pl.DataFrame
        Primary DataFrame containing names to match from.
    df_right : pl.DataFrame
        Reference DataFrame containing names to match against.
    left_on : str
        Column name in df_left with names to match.
    right_on : str
        Column name in df_right with names to match against.
    """
    left_names = df_left[left_on].unique().to_list()
    right_names = df_right[right_on].unique().to_list()

    rows = []
    matched_right = set()
    for name in left_names:
        close = difflib.get_close_matches(name, right_names)
        if close and close[0] == name:
            rows.append({left_on: name, "match_rank": 1, "match_name": close[0]})
            matched_right.add(close[0])
        elif close:
            for rank, match in enumerate(close, 1):
                rows.append({left_on: name, "match_rank": rank, "match_name": match})
                matched_right.add(match)
        else:
            rows.append({left_on: name, "match_rank": 1, "match_name": None})

    if not rows:
        return df_left

    df_matches = (
        pl.DataFrame(rows)
        .with_columns(pl.col("match_rank").cast(pl.Utf8))
        .pivot(on="match_rank", index=left_on, values="match_name")
    )

    max_rank = df_matches.width - 1
    df_matches = df_matches.rename({str(i): f"match_{i}" for i in range(1, max_rank + 1)})

    # Join match columns back to full df_left
    df = df_left.join(df_matches, on=left_on, how="left")

    # Join df_right columns for each match column
    right_extra_cols = [c for c in df_right.columns if c != right_on]
    match_cols = [c for c in df.columns if c.startswith("match_")]
    for match_col in match_cols:
        df_right_renamed = df_right.rename(
            {right_on: match_col, **{c: f"{c}_{match_col}" for c in right_extra_cols}}
        )
        df = df.join(df_right_renamed, on=match_col, how="left")

    # Add unmatched right names as separate rows
    unmatched = [name for name in right_names if name not in matched_right]
    if unmatched:
        df_unmatched = (
            df_right.filter(pl.col(right_on).is_in(unmatched))
            .rename({right_on: "match_1", **{c: f"{c}_match_1" for c in right_extra_cols}})
        )
        df = pl.concat([df, df_unmatched], how="diagonal")

    return df


def generate_yahoo_access_token(league):
    """ TODO """

    # Enter correct info
    query =  yfpy(
        league_id=league.league_id,
        game_code="nba",
        yahoo_consumer_key=league.yahoo_consumer_key,
        yahoo_consumer_secret=league.yahoo_consumer_secret,
    )

    # Instead of saving here, overwrite entry in database
    query.save_access_token_data_to_env_file(
        # env_file_location=Path('/Users/fred/git/nba_cockroach_db'), 
        # save_json_to_var_only=True
    )


class UtilComponent:
    """
    Cross-domain reference data — writes into the util.* schema.

    Sits alongside NBAComponent/FtyComponent/StatyxComponent for the same reason
    they are separate: util.player and util.player_source_id belong to no single
    domain, and every other component reads them (through ctx.active_ids) rather
    than owning them.
    """

    def __init__(self, db, ctx):
        self.db = db
        self.ctx = ctx

    def conform_player_ids(self) -> None:
        """
        Resolve the current season's identity backlog — the routine call.

        Safe to run unconditionally and as often as you like: it is a no-op when
        util.unmatched_player_source_vw is empty. Run it after the jobs that
        write the source tables, since those are what put new ids in the
        directory in the first place.
        """
        self._conform(self.ctx.cur_season)

    def backfill_player_ids(self) -> None:
        """
        Resolve every season in the backlog rather than only the current one.

        The one-off for seeding history, and the repair after the identity tables
        have been rebuilt. Named to match FtyComponent.backfill_matchups, which
        stands in the same relation to get_matchup_result.
        """
        self._conform(None)

    def _conform(self, season: str | None) -> None:
        """
        Give every source id in util.unmatched_player_source_vw a util.player to
        belong to. season=None covers every season in the backlog.

        Two statements, no staging table and no upsert. The first mints a player
        for any unmatched name nobody owns yet; the second attaches every
        unmatched source id to the player carrying that name. Running it twice is
        a no-op, because the second statement's output is precisely what empties
        the view the first statement reads.

        Matching is on util.norm_name — casefolded, with accents and punctuation
        stripped, which is what reconciles nba's "Egor Dëmin" with espn's "Egor
        Demin", and "P.J. Hairston" with "PJ Hairston". It is never fuzzy. Fuzzy
        auto-linking is what split 30 players across two rows in the old
        util.conformed_player_id, so where a platform has genuinely renamed
        someone (ESPN's "Bub Carrington" became "Carlton Carrington"; Yahoo's
        "Jakob Poeltl" became "Jakob Pöltl", which normalisation does not
        reconcile because oe and ö differ in length) the id stays in the backlog
        rather than inventing a second player. Resolve those by hand — see
        name_match() and dev/player_identity_ops.sql.

        A name matching more than one player is left unmatched rather than
        guessed at, so genuine namesakes surface as a backlog entry for a human
        instead of being silently merged.
        """
        scope = "" if season is None else f" AND u.season = '{season}'"

        before = self.db.read(
            "SELECT count(*) AS n FROM util.unmatched_player_source_vw"
        ).item()

        # needs_review marks a player this invented rather than one carried over
        # from the seed: a name no existing player had. Most are legitimate new
        # arrivals, but a platform rename or a namesake also lands here, which is
        # why it is flagged rather than trusted.
        self.db.execute(
            "INSERT INTO util.player (conformed_name, needs_review) "
            "SELECT min(u.source_name), true "
            "FROM util.unmatched_player_source_vw u "
            "WHERE u.source_name IS NOT NULL"
            f"{scope} "
            "  AND NOT EXISTS (SELECT 1 FROM util.player p "
            "                  WHERE util.norm_name(p.conformed_name) "
            "                        = util.norm_name(u.source_name)) "
            "GROUP BY util.norm_name(u.source_name)"
        )

        self.db.execute(
            "INSERT INTO util.player_source_id (platform, source_id, source_name, player_key) "
            "SELECT u.platform, u.source_id, u.source_name, p.player_key "
            "FROM util.unmatched_player_source_vw u "
            "JOIN util.player p "
            "  ON util.norm_name(p.conformed_name) = util.norm_name(u.source_name) "
            "WHERE (SELECT count(*) FROM util.player p2 "
            "       WHERE util.norm_name(p2.conformed_name) "
            "             = util.norm_name(u.source_name)) = 1"
            f"{scope}"
        )

        after = self.db.read(
            "SELECT count(*) AS n FROM util.unmatched_player_source_vw"
        ).item()
        logger.info(
            "util.player_source_id: %d source id(s) resolved, %d still unmatched",
            before - after, after,
        )
