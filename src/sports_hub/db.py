import os
import logging
import configparser
import sqlalchemy
import polars as pl

logger = logging.getLogger(__name__)

TIMEOUT = 3 * 60  # connection timeout, seconds

class Database:
    """
    Owns connection credentials and a single SQLAlchemy engine for one
    database platform (identified by a section in credentials.ini).
    Every component (NBA, fty, statyx) reads/writes through this one
    object rather than opening its own connections.
    """

    def __init__(self, ini_path: str | None = None, db_con: str | None = None):
        self.ini_path = ini_path or os.path.join(os.getcwd(), "credentials.ini")
        self.engine: sqlalchemy.Engine | None = None
        if db_con:
            self.connect(db_con)
        else:
            msg = "Database: no connection specified — call db.connect(db_con) to connect"
            logger.warning(msg)
            self.engine = None
            

    def connect(self, db_con: str) -> None:
        """Build and store the engine for a credentials.ini section."""
        parser = configparser.ConfigParser()
        parser.read(self.ini_path)
        if not parser.has_section(db_con):
            raise ValueError(f"No [{db_con}] section found in {self.ini_path}")

        db_creds = dict(parser.items(db_con))
        sql_url = "dialect://user:password@host:port/database"
        for key, value in db_creds.items():
            sql_url = sql_url.replace(key, value)

        self.engine = sqlalchemy.create_engine(sql_url, connect_args={"connect_timeout": TIMEOUT})

    def read(self, query: str, **kwargs) -> pl.DataFrame:
        """Run a SQL query, get a Polars DataFrame back.
        Extra kwargs (e.g. schema_overrides) pass through to pl.read_database."""
        return pl.read_database(query, self.engine, infer_schema_length=None, **kwargs)

    def write(
        self,
        df: pl.DataFrame,
        table: str,
        schema: str,
        if_exists: str = "append",
    ) -> None:
        """Write a Polars DataFrame to a table."""
        df.write_database(f"{schema}.{table}", self.engine, if_table_exists=if_exists)

    def conform(self, df: pl.DataFrame, table: str, schema: str) -> pl.DataFrame:
        """
        Shape a frame to a registration in util.table_column_order: rename source
        columns via origin_name, then select in column_order.

        Rows with a null origin_name are left alone, which covers columns derived
        in code rather than read from the source (nba box scores build player_name
        from two source fields). An unregistered table is returned untouched, so a
        new table can land before its ordering has been curated.
        """
        reg = self.read(
            "SELECT column_name, origin_name FROM util.table_column_order "
            f"WHERE schema = '{schema}' AND table_name = '{table}' "
            "ORDER BY column_order",
        )

        if reg.is_empty():
            logger.info(
                "%s.%s has no util.table_column_order rows — keeping all %d columns as-is",
                schema, table, df.width,
            )
            return df

        mapped = reg.drop_nulls()
        renames = dict(zip(mapped["origin_name"], mapped["column_name"]))
        return df.rename(renames).select(reg["column_name"].to_list())

    def write_ordered(self, df: pl.DataFrame, table: str, schema: str, order_key: str | None = None) -> None:
        """
        Conform a frame to its registration, then write to <schema>.<table>.

        order_key overrides which registration to read when one table is fed by
        more than one method (nba.league_game_schedule has a /past and a /future
        column set), and otherwise defaults to the table's own name.
        """
        df = self.conform(df, order_key or table, schema)
        self.write(df, table, schema=schema)
        logger.info("%s.%s has been updated (%d rows)", schema, table, len(df))

    def execute(self, statement: str) -> None:
        """Run a non-SELECT statement (e.g. DELETE) and commit."""
        with self.engine.connect() as conn:
            conn.execute(sqlalchemy.sql.text(statement))
            conn.commit()
