import os
import configparser
import sqlalchemy
import polars as pl

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
            print(msg)
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

    def execute(self, statement: str) -> None:
        """Run a non-SELECT statement (e.g. DELETE) and commit."""
        with self.engine.connect() as conn:
            conn.execute(sqlalchemy.sql.text(statement))
            conn.commit()
