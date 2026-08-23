# Pass leagues as tuples into SportsHub

## Goal

Replace auto-discovery of leagues in `FtyComponent._connect_leagues()` with explicit `(platform, league_id)` tuples passed through the `SportsHub` constructor. The user controls which leagues are in scope; no database query at init.

## Current flow

1. `SportsHub.__init__` creates `FtyComponent(db, ctx)`
2. `FtyComponent.__init__` calls `_connect_leagues()`, which queries the database for all active leagues in the current season
3. Each discovered league is connected via its handler

## Proposed flow

1. `SportsHub.__init__` accepts a new `sport` param (e.g. `"nba"`) and a `leagues` param — a list of `(platform, league_id)` tuples, e.g. `[("ESPN", 123), ("Yahoo", 456)]`
2. These are passed through to `FtyComponent.__init__`
3. `_connect_leagues()` no longer queries the database — it just loops over the provided tuples, looks up the handler by `(sport, platform)`, reads API creds from `credentials.ini`, and connects each one
4. `_dispatch` and all downstream methods remain unchanged

## Changes

### 1. `hub.py` — add `sport` and `leagues` params

```python
def __init__(self, ini_path=None, db_con=None, sport="nba", leagues=None):
    self.db = Database(ini_path, db_con)
    self.ctx = Context()
    self.sport = sport

    self.nba = NBAComponent(self.db, self.ctx)
    self.fty = FtyComponent(self.db, self.ctx, sport, leagues or [])
```

### 2. `fty.py` — accept `sport` and `leagues`, simplify `_connect_leagues`

- `__init__` takes `sport: str` and `leagues: list[tuple[str, int]]`
- `_connect_leagues` loops over the provided tuples instead of querying the DB
- Remove the database query and the date/season filtering logic
- Keep the handler lookup, creds reading, and `handler.connect()` call

### 3. No changes needed to:
- `_dispatch`, `get_free_agents`, `get_competitor_roster`, etc. — they iterate `self.leagues` the same way
- Handler classes — `connect()` signature is unchanged
- `db.py`, `context.py`, `nba.py`, `statyx.py`

## Usage

```python
hub = SportsHub(
    db_con="cockroach",
    sport="nba",
    leagues=[("ESPN", 12345), ("Yahoo", 67890)],
)

hub.fty.get_free_agents()  # runs for both leagues
```

If no leagues are passed, `FtyComponent` initialises with an empty dict — no connections, no errors.
