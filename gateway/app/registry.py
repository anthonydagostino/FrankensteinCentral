import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SubApp:
    key: str
    name: str
    description: str
    icon: str
    url: str


# The catalog of sub-apps the hub knows about, as data rather than as a dozen
# near-identical constructor calls: (key, name, icon, url env var, default url,
# description). Adding a sub-app to FrankensteinCentral means adding a row here
# and a container in docker-compose.
#
# fitness, tasks, deals, finance and networth were removed on 2026-10-06: each
# existed only to feed a home-screen card that nobody read (habit scores, a
# to-do list, inbox coupons, a manual bills table, a manual balances table
# that Firefly had already replaced). Their Postgres tables are untouched.
#
# The env var names are NOT derivable from the key — `plex` reads PLEX_SVC_URL
# and `firefly` reads FIREFLY_URL_SVC, because both names were already taken by
# the upstream services these talk to. So each row names its own variable
# rather than a rule pretending to cover them.
_CATALOG = (
    ('core', 'Core', '🧩',
     'CORE_URL', 'http://core:8000',
     'The settings store — holdings, budgets, the pay cycle, which accounts are cash.'),
    ('stocks', 'Stocks', '📈',
     'STOCKS_URL', 'http://stocks:8000',
     'Portfolio & watchlist — value, daily movers, positions. Keyless quotes.'),
    ('assistant', 'Assistant', '🧠',
     'ASSISTANT_URL', 'http://assistant:8000',
     'Builds the home screen from every other service, and books interviews from your mail onto the calendar.'),
    ('powerbuy', 'PowerBuy', '🛒',
     'POWERBUY_URL', 'http://powerbuy:8000',
     'Your arbitrage tracker. Purchases, profit, unpaid & expiring alerts.'),
    ('gmail', 'Gmail Checker', '📬',
     'GMAIL_URL', 'http://gmail:8000',
     'Scans your inbox for what needs a reply, and holds the Google login the calendar sync uses.'),
    ('schedule', 'Schedule', '🗓️',
     'SCHEDULE_URL', 'http://schedule:8000',
     'Your calendar — a mirror of Google Calendar, plus the interviews the assistant books.'),
    ('amex', 'Amex Credits', '💳',
     'AMEX_URL', 'http://amex:8000',
     'Platinum and Gold statement credits — what is unused, and how long before it expires.'),
    ('weather', 'Weather', '🌤️',
     'WEATHER_URL', 'http://weather:8000',
     'Current conditions, the next twelve hours and ten days, for a place you pick.'),
    ('budget', 'Budget', '📊',
     'BUDGET_URL', 'http://budget:8000',
     "Monthly spending by category. What's left and what's over."),
    ('vault', 'Vault', '🔐',
     'VAULT_URL', 'http://vault:8000',
     'Password health from your Vaultwarden — weak, reused, old, no-2FA. No secrets stored.'),
    ('plex', 'Plex', '🎬',
     'PLEX_SVC_URL', 'http://plex:8000',
     'The Plex server shared with you — continue watching, recently added, libraries.'),
    ('firefly', 'Firefly', '📒',
     'FIREFLY_URL_SVC', 'http://firefly:8000',
     "Your Firefly III finances — net worth, this month's spend/income, accounts, recent transactions."),
)


# Registered so the gateway can proxy to them, but not offered as tiles in the
# launcher: `core` is the settings store (its one UI is the ⚙ modal) and
# `assistant` IS the home screen. A tile that opens a description of the page
# you are already on is not an app.
HIDDEN_FROM_LAUNCHER = frozenset({"core", "assistant"})


def load_registry() -> list[SubApp]:
    """Each sub-app is an independent service reachable at its own URL."""
    return [
        SubApp(key=key, name=name, description=description, icon=icon,
               url=os.environ.get(env, default))
        for key, name, icon, env, default, description in _CATALOG
    ]
