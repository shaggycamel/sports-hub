import os
import configparser
import sqlalchemy
import polars as pl

TIMEOUT = 3 * 60  # connection timeout, seconds


class Database:
    """
    Owns connection credentials and engines for every platform/section in
    credentials.ini (e.g. 'cockroach' for nba_con, 'fty' for fty_con). Every
    component (NBA, fty, statyx) reads/writes through this one object rather
    than opening its own connections — this is the thing that unifies them.

    Engines are created lazily and cached per platform, so asking for the
    same platform twice reuses the same engine instead of reconnecting.
    """

    def __init__(self, ini_path: str | None = None):
        self.ini_path = ini_path or os.path.join(os.getcwd(), "credentials.ini")
        self._engines: dict[str, sqlalchemy.Engine] = {}

    def connect(self, platform: str) -> sqlalchemy.Engine:
        """Get (or lazily create) the SQLAlchemy engine for a credentials.ini section."""
        if platform not in self._engines:
            self._engines[platform] = self._build_engine(platform)
        return self._engines[platform]

    def _build_engine(self, platform: str) -> sqlalchemy.Engine:
        parser = configparser.ConfigParser()
        parser.read(self.ini_path)
        if not parser.has_section(platform):
            raise ValueError(f"No [{platform}] section found in {self.ini_path}")

        db_creds = dict(parser.items(platform))
        sql_url = "dialect://user:password@host:port/database"
        for key, value in db_creds.items():
            sql_url = sql_url.replace(key, value)

        return sqlalchemy.create_engine(sql_url, connect_args={"connect_timeout": TIMEOUT})

    def read(self, query: str, platform: str, **kwargs) -> pl.DataFrame:
        """Convenience wrapper: run a SQL query, get a Polars DataFrame back.
        Extra kwargs (e.g. schema_overrides) pass through to pl.read_database."""
        return pl.read_database(query, self.connect(platform), **kwargs)

    def write(
        self,
        df: pl.DataFrame,
        table: str,
        schema: str,
        platform: str,
        if_exists: str = "append",
    ) -> None:
        """Convenience wrapper: write a Polars DataFrame to a table."""
        df.write_database(f"{schema}.{table}", self.connect(platform), if_table_exists=if_exists)

    def execute(self, statement: str, platform: str) -> None:
        """Run a non-SELECT statement (e.g. DELETE) and commit."""
        with self.connect(platform).connect() as conn:
            conn.execute(sqlalchemy.sql.text(statement))
            conn.commit()
