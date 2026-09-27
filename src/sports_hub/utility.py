import difflib
import logging
import polars as pl
from sqlalchemy import text
from pathlib import Path
from yfpy.query import YahooFantasySportsQuery  as yfpy

logger = logging.getLogger(__name__)


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

    def deduplicate_tables(self, season: str | None = None, dry_run: bool = False) -> list[str]:
        """
        Remove exact duplicate rows, one season at a time, from every table on
        this connection that has a season column.

        Scoped to a season because that is the only partition every candidate
        table shares, and it keeps each rewrite small: the method reads one
        season, drops duplicate rows in Polars, then replaces just that season
        inside a transaction, so a failure leaves the table as it was.

        Only tables carrying a season column are considered, so anything keyed
        differently — util.player, util.player_source_id, util.table_column_order
        — is skipped rather than silently rewritten. Views and _RETIRED tables are
        excluded by name.

        Beware what "duplicate" means here: it is an exact match across every
        column, and the table is rewritten from the deduplicated frame. A table
        where identical rows are legitimately distinct records would lose data, so
        dry_run=True first — it logs and returns what would change without
        writing anything.

        Returns the list of tables affected (or that would be, under dry_run).
        """
        season = season or self.ctx.cur_season

        tables = self.db.read(
            "SELECT DISTINCT table_schema, table_name "
            "FROM information_schema.columns "
            "WHERE column_name = 'season' "
            "  AND table_name NOT LIKE '%_vw' "
            "  AND table_name NOT ILIKE '%_retired'"
        )
        names = sorted(
            f"{r['table_schema']}.{r['table_name']}" for r in tables.iter_rows(named=True)
        )
        logger.info(
            "deduplicate_tables: %d candidate table(s) for season %s%s",
            len(names), season, " (dry run)" if dry_run else "",
        )

        changed = []
        for table in names:
            df = self.db.read(f"SELECT * FROM {table} WHERE season = '{season}'")

            # maintain_order so a rewrite is deterministic rather than reordering
            # the season's rows on every run.
            deduped = df.unique(maintain_order=True)
            if deduped.height == df.height:
                continue

            changed.append(table)
            logger.info(
                "%s: %d row(s) -> %d, dropping %d duplicate(s)%s",
                table, df.height, deduped.height, df.height - deduped.height,
                " (dry run, not written)" if dry_run else "",
            )
            if dry_run:
                continue

            # One transaction: the delete and the rewrite either both land or
            # neither does, so an interrupted run cannot leave a season empty.
            with self.db.engine.begin() as conn:
                conn.execute(
                    text(f"DELETE FROM {table} WHERE season = '{season}'")
                )
                deduped.write_database(table, conn, if_table_exists="append")

        if not changed:
            logger.info("deduplicate_tables: no duplicates found")
        return changed

    def conform_player_ids(self) -> None:
        """
        Give every source id in util.unmatched_player_source_vw a util.player to
        belong to. Covers every season in the backlog, deliberately.

        Safe to run unconditionally and as often as you like: it is a no-op when
        the backlog is empty, and costs about five seconds either way — the time
        goes on scanning the box scores through util.player_directory_vw, not on
        the work. Run it after the jobs that write the source tables, since those
        are what put new ids in the directory in the first place.

        It deliberately does NOT take a season. An earlier version defaulted to
        ctx.cur_season, which measured worse than useless: scoping saved nothing
        (same five seconds) and permanently stranded any id whose most recent
        directory season was not the current one. That is not hypothetical — it
        is what happens at every season rollover to an id seen late in the old
        season and not yet resolved. Verified: with yahoo 5642 (last seen
        2024-25) unmapped, a cur_season-scoped run resolved 0 and left it in the
        backlog, where it would have sat forever.

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
            "WHERE u.source_name IS NOT NULL "
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
        )

        after = self.db.read(
            "SELECT count(*) AS n FROM util.unmatched_player_source_vw"
        ).item()
        logger.info(
            "util.player_source_id: %d source id(s) resolved, %d still unmatched",
            before - after, after,
        )
