import datetime as dt

import polars as pl
import polars.selectors as cs


def flatten_hit_rates(row: dict) -> list[dict]:
    """
    hit-rates responses nest per-window stats:
    {market, line, side, season, sample_size, windows: {L5: {...}, L10: {...}, ...}}
    Expand into one tidy row per window rather than one wide row per player.
    """
    base = {k: v for k, v in row.items() if k != "windows"}
    return [
        {**base, "window": window, **stats}
        for window, stats in row.get("windows", {}).items()
    ]


def infer_dtypes(df: pl.DataFrame, threshold: float = 1.0) -> pl.DataFrame:
    """
    Best-guess cast every string column to Int64 -> Float64 -> Date -> Datetime,
    in that order, keeping the first one where enough values parse cleanly.

    threshold: fraction of non-null values that must parse successfully for
    a cast to be accepted (1.0 = every non-null value must parse).
    """
    for name in df.select(cs.string()).columns:
        col = df[name]
        n = col.drop_nulls().len()
        if n == 0:
            continue

        for parse in (
            pl.col(name).cast(pl.Int64, strict=False),
            pl.col(name).cast(pl.Float64, strict=False),
            pl.col(name).str.to_date(strict=False),
            pl.col(name).str.to_datetime(strict=False),
            pl.col(name).str.to_datetime(strict=False, time_zone="UTC"),
        ):
            try:
                casted = df.select(parse)[name]
            except pl.exceptions.ComputeError:
                continue  # format doesn't apply to this column at all

            if casted.null_count() <= col.null_count() + n * (1 - threshold):
                df = df.with_columns(parse)
                break

    # Downcast Datetime columns to Date if every time component is midnight
    for name in df.select(cs.datetime()).columns:
        col = df[name]
        if col.drop_nulls().len() == 0:
            continue
        if (col.dt.time().drop_nulls() == dt.time(0, 0)).all():
            df = df.with_columns(pl.col(name).dt.date().alias(name))

    return df
