from sports_hub.fty_handlers.espn_nba import EspnNbaHandler
from sports_hub.fty_handlers.yahoo_nba import YahooNbaHandler

HANDLERS = {
    ("nba", "ESPN"): EspnNbaHandler,
    ("nba", "Yahoo"): YahooNbaHandler,
}
