import configparser
import difflib
import hashlib
import json
import logging
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import polars as pl
import requests
from sqlalchemy import text
from yfpy.query import YahooFantasySportsQuery  as yfpy

logger = logging.getLogger(__name__)

# Letters NFKD does not decompose, folded to a single ASCII base. Needed so the
# audit detector groups "ø/æ/đ" spellings the same way on every platform.
_LATIN_FOLD = str.maketrans({
    "æ": "a", "Æ": "A", "ø": "o", "Ø": "O", "ß": "s", "đ": "d", "Đ": "D",
    "ł": "l", "Ł": "L", "þ": "t", "Þ": "T", "ð": "d", "Ð": "D", "œ": "o",
    "Œ": "O", "ħ": "h", "ı": "i", "ŋ": "n", "Ŋ": "N",
})

# JSON schema handed to Ollama's `format` so the model returns parseable rows.
_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "platform": {"type": ["string", "null"]},
                    "source_id": {"type": ["integer", "null"]},
                    "source_name": {"type": ["string", "null"]},
                    "subject_player_key": {"type": ["integer", "null"]},
                    "proposal": {
                        "type": "string",
                        "enum": ["link", "merge", "new", "no_action"],
                    },
                    "target_player_key": {"type": ["integer", "null"]},
                    "proposed_name": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "platform",
                    "source_id",
                    "proposal",
                    "confidence",
                    "target_player_key",
                    "proposed_name",
                ],
            },
        }
    },
    "required": ["proposals"],
}


def _fold(name: str) -> str:
    """
    Casefold to ASCII letters, keeping spaces and punctuation: the shared base
    for norm_name() and for tokenising. NFKD decomposes accents to base +
    combining mark, the mark is dropped; _LATIN_FOLD covers letters NFKD does
    not decompose.
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in decomposed if not unicodedata.combining(c))
    return folded.translate(_LATIN_FOLD).lower()


def norm_name(name: str) -> str:
    """
    The canonical name fold. Every match and collision check goes through this,
    in Python rather than SQL, so the identity pipeline behaves identically on
    Postgres and CockroachDB and needs no user-defined function on either.

    NFKD prescinds the hand-maintained translate() list a SQL version needs —
    the one that silently DELETED any diacritic it did not name (ć, ā, đ, …),
    splitting Boban Marjanović from Boban Marjanovic. Casefolded, with every
    non-letter removed. Never fuzzy.
    """
    return "".join(c for c in _fold(name) if c.isascii() and c.isalpha())


def _name_tokens(name: str) -> set[str]:
    """Folded name split on non-letters — the unit the LLM shortlist matches on.
    Drops single letters, so "P.J." contributes 'washington', not 'p'/'j'."""
    return {t for t in re.split(r"[^a-z]+", _fold(name)) if len(t) > 1}


# Generational suffixes: on hundreds of players, useless as a shortlist signal.
_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv", "v"}


def _compatible_names(a: str | None, b: str | None) -> bool:
    """
    Whether two names could plausibly be the same person: they fold equal, or
    share a name token (ignoring suffix tokens). A rename shares the surname
    ("Bub" -> "Carlton" Carrington), a nickname shares it too (Bones/Nah'Shon
    Hyland), a word-order swap shares both. Used to reject a model's link/merge
    target that has no name in common with the source — a batch-contamination
    hallucination (Bones Hyland -> Alexandre Sarr) rather than a real match.
    """
    if not a or not b:
        return False
    if norm_name(a) == norm_name(b):
        return True
    return bool((_name_tokens(a) - _SUFFIX_TOKENS) & (_name_tokens(b) - _SUFFIX_TOKENS))


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
        the backlog is empty. Run it after the jobs that write the source tables,
        since those are what put new ids in the directory in the first place.

        It deliberately does NOT take a season. An earlier version defaulted to
        ctx.cur_season, which measured worse than useless: scoping saved nothing
        and permanently stranded any id whose most recent directory season was
        not the current one. That is not hypothetical — it is what happens at
        every season rollover to an id seen late in the old season and not yet
        resolved. Verified: with yahoo 5642 (last seen 2024-25) unmapped, a
        cur_season-scoped run resolved 0 and left it in the backlog forever.

        Matching is done here in Python, on norm_name(), rather than in SQL. That
        keeps the fold identical on Postgres and CockroachDB and removes the
        user-defined function both would otherwise need; 2.7k players and a
        handful of backlog rows are nothing to fold in Polars. The logic is the
        same two moves as before: mint a player for any unmatched norm nobody
        owns, then attach every unmatched id to the player holding that norm.
        Re-running is a no-op, because the attach is what empties the view the
        mint reads.

        The fold reconciles nba's "Egor Dëmin" with espn's "Egor Demin" and
        "P.J. Hairston" with "PJ Hairston", and (unlike the old SQL version) also
        folds ć/ā/đ, so Boban Marjanović and Boban Marjanovic finally meet. It is
        never fuzzy: a genuine platform rename (ESPN's "Bub Carrington" became
        "Carlton Carrington") stays in the backlog for a human. A name matching
        more than one player is left unmatched rather than guessed at, so genuine
        namesakes (Jameer Nelson Sr/Jr) surface rather than being silently
        merged. Resolve those with review_player_identities() and
        dev/player_identity_ops.sql.
        """
        before = self.db.read(
            "SELECT count(*) AS n FROM util.unmatched_player_source_vw"
        ).item()

        backlog = self.db.read(
            "SELECT platform, source_id, source_name "
            "FROM util.unmatched_player_source_vw WHERE source_name IS NOT NULL"
        )
        if backlog.is_empty():
            logger.info(
                "util.player_source_id: 0 source id(s) resolved, %d still unmatched",
                before,
            )
            return

        backlog = backlog.with_columns(
            pl.col("source_name").map_elements(norm_name, return_dtype=pl.Utf8).alias("norm")
        ).filter(pl.col("norm") != "")

        def read_players() -> pl.DataFrame:
            return self.db.read(
                "SELECT player_key, conformed_name FROM util.player"
            ).with_columns(
                pl.col("conformed_name").map_elements(norm_name, return_dtype=pl.Utf8).alias("norm")
            ).filter(pl.col("norm") != "")

        players = read_players()

        # needs_review marks a player this invented rather than one carried over
        # from the seed: a name no existing player had. Most are legitimate new
        # arrivals, but a platform rename or a namesake also lands here, which is
        # why it is flagged rather than trusted. One player per distinct norm;
        # min() picks a stable representative spelling.
        owned = set(players["norm"].to_list())
        to_mint = (
            backlog.filter(~pl.col("norm").is_in(owned))
            .group_by("norm")
            .agg(pl.col("source_name").min().alias("conformed_name"))
        )
        for r in to_mint.iter_rows(named=True):
            self._mint_player(r["conformed_name"])
        if not to_mint.is_empty():
            players = read_players()

        # Attach each backlog id to the single player holding its norm. A norm on
        # more than one player (namesakes) is left unmatched, not guessed at.
        counts = players.group_by("norm").len().rename({"len": "norm_players"})
        matches = (
            backlog.join(players, on="norm", how="inner")
            .join(counts, on="norm", how="inner")
            .filter(pl.col("norm_players") == 1)
        )

        with self.db.engine.begin() as conn:
            for r in matches.iter_rows(named=True):
                conn.execute(
                    text(
                        "INSERT INTO util.player_source_id "
                        "(platform, source_id, source_name, player_key) "
                        "VALUES (:p, :sid, :sn, :pk) "
                        "ON CONFLICT (platform, source_id) DO NOTHING"
                    ),
                    {
                        "p": r["platform"],
                        "sid": r["source_id"],
                        "sn": r["source_name"],
                        "pk": r["player_key"],
                    },
                )

        resolved = matches.height
        logger.info(
            "util.player_source_id: %d source id(s) resolved, %d still unmatched",
            resolved, before - resolved,
        )

    def check_player_identity(self) -> pl.DataFrame:
        """
        The integrity assertions for util.player / util.player_source_id, as a
        frame. Every failures value must be 0 — run it after any apply or manual
        edit:

            check_player_identity().filter(pl.col("failures") > 0)   # want no rows

        colliding_names folds names through norm_name(), which SQL cannot do
        portably (no user-defined function on Cockroach), so this replaces the
        util.norm_name half of the old util.player_identity_check view; that view
        keeps the two DB-native checks.
        """
        db = self.db
        orphans = db.read(
            "SELECT count(*) AS n FROM util.player p WHERE NOT EXISTS "
            "(SELECT 1 FROM util.player_source_id m WHERE m.player_key = p.player_key)"
        ).item()
        backlog = db.read("SELECT count(*) AS n FROM util.unmatched_player_source_vw").item()

        norms = db.read("SELECT conformed_name FROM util.player")["conformed_name"].map_elements(
            norm_name, return_dtype=pl.Utf8
        )
        collisions = (
            norms.filter(norms != "").value_counts().filter(pl.col("count") > 1).height
        )

        return pl.DataFrame(
            {
                "check_name": ["players_owning_nothing", "backlog", "colliding_names"],
                "failures": [orphans, backlog, collisions],
            },
            schema={"check_name": pl.Utf8, "failures": pl.Int64},
        )

    def build_player_identity(self) -> None:
        """
        Rebuild util.player / util.player_source_id from the retired wide
        util."conformed_player_id_RETIRED". Assumes both tables are empty; pair
        it with dev/build_player_identity.sql (the DDL) and then
        conform_player_ids() for anything the directory reports beyond the
        retired table.

        The seed is Python for the same reason the matcher is: no user-defined
        function, so it runs on Postgres or CockroachDB unchanged.

        TODO (future self): when util."conformed_player_id_RETIRED" (postgres)
        / util.conformed_player_id (cockroach) is dropped, DELETE this method —
        this is the only thing that reads the old table, and its whole purpose
        is the rebuild path that the drop gives up. Also remove the matching
        dev/README.md entries.
        """
        retired = self.db.read(
            'SELECT * FROM util."conformed_player_id_RETIRED" '
            "WHERE conformed_name IS NOT NULL"
        ).with_columns(
            pl.col("conformed_name").map_elements(norm_name, return_dtype=pl.Utf8).alias("norm")
        )

        players = retired.group_by("norm").agg(
            pl.col("conformed_name").min().alias("conformed_name")
        )
        key_by_norm = {
            r["norm"]: self._mint_player(r["conformed_name"], needs_review=False)
            for r in players.iter_rows(named=True)
        }

        rows = []
        for r in retired.iter_rows(named=True):
            player_key = key_by_norm[r["norm"]]
            for platform, id_col, name_col in (
                ("nba", "nba_id", "nba_name"),
                ("espn", "espn_id", "espn_name"),
                ("yahoo", "yahoo_id", "yahoo_name"),
                ("statyx", "statyx_id", "statyx_name"),
            ):
                if r[id_col] is None:
                    continue
                rows.append((platform, r[id_col], r[name_col] or None, player_key))

        with self.db.engine.begin() as conn:
            for platform, source_id, source_name, player_key in rows:
                conn.execute(
                    text(
                        "INSERT INTO util.player_source_id "
                        "(platform, source_id, source_name, player_key) "
                        "VALUES (:p, :sid, :sn, :pk) ON CONFLICT DO NOTHING"
                    ),
                    {"p": platform, "sid": source_id, "sn": source_name, "pk": player_key},
                )

        logger.info(
            "build_player_identity: %d player(s), %d mapping(s)",
            len(key_by_norm), len(rows),
        )

    def review_player_identities(self, origin: str = "backlog", batch_size: int = 20, limit: int | None = None) -> int:
        """
        Ask the local model to reconcile names the deterministic matcher cannot,
        and write its proposals to util.player_identity_review. Never touches
        util.player or util.player_source_id — apply_player_identity_reviews()
        is the only writer.

        origin='backlog' reviews util.unmatched_player_source_vw, the ids that
        failed norm_name matching — the ongoing path, run after the daily
        source jobs. origin='audit' instead reads ids that are ALREADY mapped
        but inconsistent (the same person under two players, or two people on
        one player), which the backlog cannot see — the one-time backfill.

        Idempotent: every proposal is keyed by input_hash, so a re-run over an
        unchanged candidate inserts nothing. Returns the number of new rows.
        """
        if origin == "backlog":
            candidates = self.db.read(
                "SELECT platform, source_id, source_name, "
                "       NULL::integer AS subject_player_key "
                "FROM util.unmatched_player_source_vw "
                "WHERE source_name IS NOT NULL"
            )
        elif origin == "audit":
            candidates = self._audit_candidates()
        else:
            raise ValueError(f"origin must be 'backlog' or 'audit', got {origin!r}")

        if limit is not None:
            candidates = candidates.head(limit)

        if candidates.is_empty():
            logger.info("review_player_identities(%s): nothing to review", origin)
            return 0

        players = self.db.read(
            "SELECT player_key, conformed_name FROM util.player ORDER BY player_key"
        )
        name_by_key = {
            r["player_key"]: r["conformed_name"] for r in players.iter_rows(named=True)
        }
        owned_norms = {norm_name(n) for n in name_by_key.values()}

        staged = 0
        for chunk in candidates.iter_slices(batch_size):
            # The identifying fields come from the candidate row, not the model:
            # it echoes them unreliably (and omits them when thinking is off) but
            # only ever needs to answer proposal/target/name/confidence/reason.
            cand_by_id = {
                (r["platform"], r["source_id"]): r for r in chunk.iter_rows(named=True)
            }
            relevant = self._shortlist_players(players, chunk)
            proposals, model = self._ask_ollama(relevant, chunk, origin)
            for p in proposals:
                cand = cand_by_id.get((p.get("platform"), p.get("source_id")))
                if cand is None:
                    continue

                # Reject a link/merge whose target shares no name with the
                # source: a model mixing up candidates in a batch (Bones Hyland
                # -> Alexandre Sarr) rather than a real match.
                proposal = p.get("proposal")
                target = p.get("target_player_key")
                if proposal == "link" and not _compatible_names(
                    cand["source_name"], name_by_key.get(target)
                ):
                    logger.info(
                        "review: dropped implausible link %r -> %s",
                        cand["source_name"], target,
                    )
                    continue
                if proposal == "merge" and not _compatible_names(
                    name_by_key.get(cand["subject_player_key"]), name_by_key.get(target)
                ):
                    continue

                # Self-targets and duplicate-name 'new' are model noise, not
                # actions: a merge with subject == target would delete the
                # player, and a 'new' for a name that already exists would mint
                # a duplicate and steal the id.
                if proposal in ("link", "merge") and target == cand["subject_player_key"]:
                    continue
                if proposal == "new":
                    proposed_name = p.get("proposed_name")
                    if not proposed_name or norm_name(proposed_name) in owned_norms:
                        continue

                input_hash = hashlib.sha256(
                    f"{origin}|{cand['platform']}|{cand['source_id']}".encode()
                ).hexdigest()
                with self.db.engine.begin() as conn:
                    inserted = conn.execute(
                        text(
                            "INSERT INTO util.player_identity_review "
                            "(origin, platform, source_id, source_name, "
                            " subject_player_key, proposal, target_player_key, "
                            " proposed_name, confidence, reason, model, "
                            " model_version, input_hash) "
                            "VALUES (:origin, :platform, :source_id, :source_name, "
                            " :subject, :proposal, :target, :name, :confidence, "
                            " :reason, :model, :model_version, :hash) "
                            "ON CONFLICT (input_hash) DO NOTHING "
                            "RETURNING review_id"
                        ),
                        {
                            "origin": origin,
                            "platform": cand["platform"],
                            "source_id": cand["source_id"],
                            "source_name": cand["source_name"],
                            "subject": cand["subject_player_key"],
                            "proposal": p.get("proposal"),
                            "target": p.get("target_player_key"),
                            "name": p.get("proposed_name"),
                            "confidence": p.get("confidence"),
                            "reason": p.get("reason"),
                            "model": model,
                            "model_version": model,
                            "hash": input_hash,
                        },
                    ).scalar()
                if inserted is not None:
                    staged += 1

        logger.info(
            "review_player_identities(%s): %d candidate(s), %d new proposal(s) staged",
            origin, candidates.height, staged,
        )
        return staged

    def apply_player_identity_reviews(self, auto: bool = True, min_confidence: float = 0.9) -> int:
        """
        Promote staged proposals into util.player / util.player_source_id.

        'link' and 'new' apply automatically at or above min_confidence (set
        auto=False to require an explicit status='accepted'). 'merge' and
        different-name links are NEVER automatic: a human must set
        status='accepted', because the model cannot be trusted to keep genuine
        namesakes (Jameer Nelson Sr/Jr) apart. Applied rows are marked 'applied'.
        """
        rows = self.db.read(
            "SELECT * FROM util.player_identity_review "
            "WHERE status IN ('pending', 'accepted') ORDER BY review_id"
        )

        applied = 0
        for r in rows.iter_rows(named=True):
            proposal = r["proposal"]

            if proposal == "no_action":
                self._mark_review(r["review_id"], "applied")
                continue

            if proposal in ("link", "new"):
                if proposal == "link":
                    if r["target_player_key"] is None:
                        continue
                    target = r["target_player_key"]
                    if target == r["subject_player_key"]:
                        continue  # already on that player; nothing to do
                    same_name = norm_name(r["source_name"] or "") == norm_name(
                        self._player_name(target)
                    )
                else:
                    if not r["proposed_name"]:
                        continue
                    if self._norm_is_owned(r["proposed_name"]):
                        continue  # a player with that name exists; not 'new'
                    target = self._mint_player(r["proposed_name"])
                    same_name = True

                # A same-name link may auto-apply on confidence. A rename — a
                # link whose name does not fold to the target's — is a merge in
                # disguise and needs a human, as does any merge.
                trusted = r["status"] == "accepted" or (
                    auto and same_name and (r["confidence"] or 0) >= min_confidence
                )
                if not trusted:
                    continue
                self._link_source_id(
                    r["platform"], r["source_id"], r["source_name"], target
                )
                self._mark_review(r["review_id"], "applied")
                applied += 1

            elif proposal == "merge":
                if r["status"] != "accepted":
                    continue
                if r["subject_player_key"] is None or r["target_player_key"] is None:
                    continue
                if r["subject_player_key"] == r["target_player_key"]:
                    continue  # a merge needs two distinct players
                self._merge_players(
                    r["subject_player_key"], r["target_player_key"], r["proposed_name"]
                )
                self._mark_review(r["review_id"], "applied")
                applied += 1

        logger.info("apply_player_identity_reviews: %d proposal(s) applied", applied)
        return applied

    def sync_player_identity(self, review: bool = True, apply: bool = True) -> dict:
        """
        One call for the routine, to run after the jobs that write the source
        tables (daily is right — it is a no-op most days): resolve what the
        deterministic matcher can, and only if a backlog remains, run the model
        review and apply. Then report the integrity checks.

        Never destructive on its own: the review only stages, and apply will not
        merge or rename without a human's status='accepted'. Returns a summary;
        an empty backlog means the model was not called at all.
        """
        self.conform_player_ids()

        backlog = self.db.read(
            "SELECT count(*) AS n FROM util.unmatched_player_source_vw"
        ).item()

        staged = 0
        if review and backlog:
            staged = self.review_player_identities(origin="backlog")

        applied = self.apply_player_identity_reviews() if apply else 0
        checks = self.check_player_identity()

        summary = {
            "backlog": backlog,
            "staged": staged,
            "applied": applied,
            "checks": {r["check_name"]: r["failures"] for r in checks.iter_rows(named=True)},
        }
        logger.info("sync_player_identity: %s", summary)
        return summary

    def _audit_candidates(self) -> pl.DataFrame:
        """
        Already-mapped ids that look wrong, for the one-time backfill.

        Flags a source id when either its player holds names that do not fold
        together (a false merge or a benign alias) or its folded name appears on
        more than one player (a split). The result is a superset; the model and
        a human sort out which is which.
        """
        src = self.db.read(
            "SELECT player_key, platform, source_id, source_name "
            "FROM util.player_source_id WHERE source_name IS NOT NULL"
        )
        rows = list(src.iter_rows(named=True))

        by_player: dict[int, set[str]] = defaultdict(set)
        by_norm: dict[str, set[int]] = defaultdict(set)
        for r in rows:
            norm = norm_name(r["source_name"])
            if norm:
                by_player[r["player_key"]].add(norm)
                by_norm[norm].add(r["player_key"])

        out, seen = [], set()
        for r in rows:
            norm = norm_name(r["source_name"])
            inconsistent = len(by_player[r["player_key"]]) > 1
            split = bool(norm) and len(by_norm[norm]) > 1
            if inconsistent or split:
                key = (r["platform"], r["source_id"])
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "platform": r["platform"],
                        "source_id": r["source_id"],
                        "source_name": r["source_name"],
                        "subject_player_key": r["player_key"],
                    }
                )

        return pl.DataFrame(
            out,
            schema={
                "platform": pl.Utf8,
                "source_id": pl.Int64,
                "source_name": pl.Utf8,
                "subject_player_key": pl.Int64,
            },
        )

    def _shortlist_players(self, players: pl.DataFrame, candidates: pl.DataFrame, limit: int = 25) -> pl.DataFrame:
        """
        The players worth showing the model for this batch, ranked by how many
        folded name tokens they share with a candidate and capped tight. Sending
        all ~2.7k names every call is slow and distracting; token overlap keeps
        every real target — a rename or namesake shares the surname, a word-order
        swap shares both tokens — while dropping the prompt to a couple of dozen.

        Suffix tokens (jr/sr/ii/iii/iv) are ignored: they are on hundreds of
        players and match nothing useful.
        """
        token_index: dict[str, set[int]] = defaultdict(set)
        for r in players.iter_rows(named=True):
            for token in _name_tokens(r["conformed_name"]) - _SUFFIX_TOKENS:
                token_index[token].add(r["player_key"])

        score: dict[int, int] = defaultdict(int)
        for c in candidates.iter_rows(named=True):
            for token in _name_tokens(c["source_name"]) - _SUFFIX_TOKENS:
                for player_key in token_index.get(token, ()):
                    score[player_key] += 1

        if not score:
            return players.head(0)
        top = sorted(score, key=lambda key: (-score[key], key))[:limit]
        return players.filter(pl.col("player_key").is_in(top))

    def _ask_ollama(self, players: pl.DataFrame, chunk: pl.DataFrame, origin: str):
        """Send one batch of candidates and the shortlisted players, get JSON back."""
        host, model, timeout, think = self._ollama_config()

        known = "\n".join(
            f'{r["player_key"]}: {r["conformed_name"]}'
            for r in players.iter_rows(named=True)
        )
        candidates = chunk.select(
            ["platform", "source_id", "source_name", "subject_player_key"]
        ).to_dicts()

        system = (
            "You reconcile sports player names across data platforms. For each "
            "candidate choose exactly one proposal. Never link or merge two "
            "different people; genuine namesakes (e.g. a father and son) stay "
            "separate. Answer only with JSON, no commentary."
        )
        if origin == "audit":
            options = (
                "Each candidate is ALREADY mapped to subject_player_key. Choose:\n"
                "- no_action: that mapping is correct (the usual answer here)\n"
                "- link: the id belongs to a DIFFERENT existing player -> target_player_key\n"
                "- merge: subject_player_key and target_player_key are the same person "
                "-> target_player_key\n"
                "- new: no existing player matches (rare)"
            )
        else:
            options = (
                "Each candidate is NOT yet mapped. Choose:\n"
                "- link: the id belongs to an existing player -> target_player_key\n"
                "- new: no existing player matches -> proposed_name\n"
                "- no_action: leave it for a human (rare)"
            )
        user = (
            f"Known players (player_key: name):\n{known}\n\n"
            "Candidates:\n" + json.dumps(candidates) + "\n\n" +
            options + "\n\n"
            "For every candidate return platform, source_id, proposal, a confidence "
            "in [0,1], target_player_key (null when unused), proposed_name (null when "
            "unused), and a reason under 12 words. A 'link' or 'merge' must carry a "
            "target_player_key."
        )

        body = {
            "model": model,
            "stream": False,
            "format": _PROPOSAL_SCHEMA,
            # Reasoning models otherwise emit hundreds of hidden thinking tokens
            # (glm-4.7-flash: ~850 for three candidates, at ~11 tok/s locally).
            # Older Ollama builds reject `think`, so fall back without it.
            "think": think,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        resp = requests.post(f"{host}/api/chat", json=body, timeout=timeout)
        if resp.status_code == 400 and "think" in resp.text.lower():
            body.pop("think", None)
            resp = requests.post(f"{host}/api/chat", json=body, timeout=timeout)
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        return json.loads(content).get("proposals", []), model

    def _ollama_config(self) -> tuple[str, str, int, bool]:
        """host, model, timeout, think — env vars win, then an [ollama] section."""
        host = os.environ.get("OLLAMA_HOST")
        model = os.environ.get("OLLAMA_MODEL")
        timeout = os.environ.get("OLLAMA_TIMEOUT")
        think = os.environ.get("OLLAMA_THINK")
        if Path(self.db.ini_path).exists():
            parser = configparser.ConfigParser()
            parser.read(self.db.ini_path)
            if parser.has_section("ollama"):
                host = host or parser.get("ollama", "host", fallback=None)
                model = model or parser.get("ollama", "model", fallback=None)
                timeout = timeout or parser.get("ollama", "timeout", fallback=None)
                think = think or parser.get("ollama", "think", fallback=None)
        return (
            (host or "http://localhost:11434").rstrip("/"),
            model or "llama3.1",
            int(timeout or 600),
            str(think).strip().lower() not in ("false", "0", "no") if think is not None else False,
        )

    def _link_source_id(self, platform, source_id, source_name, player_key) -> None:
        with self.db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO util.player_source_id "
                    "(platform, source_id, source_name, player_key) "
                    "VALUES (:p, :sid, :sn, :pk) "
                    "ON CONFLICT (platform, source_id) DO UPDATE "
                    "SET player_key = EXCLUDED.player_key, "
                    "    source_name = EXCLUDED.source_name"
                ),
                {"p": platform, "sid": source_id, "sn": source_name, "pk": player_key},
            )

    def _player_name(self, player_key: int) -> str:
        with self.db.engine.connect() as conn:
            return conn.execute(
                text("SELECT conformed_name FROM util.player WHERE player_key = :k"),
                {"k": player_key},
            ).scalar()

    def _norm_is_owned(self, name: str) -> bool:
        """Whether any player already folds to this name — i.e. it is not 'new'."""
        norm = norm_name(name or "")
        if not norm:
            return False
        return any(
            norm_name(r["conformed_name"]) == norm
            for r in self.db.read("SELECT conformed_name FROM util.player").iter_rows(named=True)
        )

    def _mint_player(self, name: str, needs_review: bool = True) -> int:
        with self.db.engine.begin() as conn:
            return conn.execute(
                text(
                    "INSERT INTO util.player (conformed_name, needs_review) "
                    "VALUES (:n, :nr) RETURNING player_key"
                ),
                {"n": name, "nr": needs_review},
            ).scalar()

    def _merge_players(self, subject_key: int, target_key: int, name: str | None = None) -> None:
        # A self-merge would DELETE the player and orphan its mappings.
        if subject_key == target_key:
            return
        with self.db.engine.begin() as conn:
            conn.execute(
                text("UPDATE util.player_source_id SET player_key = :t "
                     "WHERE player_key = :s"),
                {"t": target_key, "s": subject_key},
            )
            conn.execute(
                text("DELETE FROM util.player WHERE player_key = :s"),
                {"s": subject_key},
            )
            conn.execute(
                text("UPDATE util.player SET needs_review = false, "
                     "conformed_name = COALESCE(:n, conformed_name) "
                     "WHERE player_key = :t"),
                {"t": target_key, "n": name},
            )

    def _mark_review(self, review_id: int, status: str) -> None:
        with self.db.engine.begin() as conn:
            conn.execute(
                text("UPDATE util.player_identity_review SET status = :st "
                     "WHERE review_id = :r"),
                {"st": status, "r": review_id},
            )
