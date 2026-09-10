import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SubApp:
    key: str
    name: str
    description: str
    icon: str
    url: str


# The catalog of sub-apps the hub knows about, as data rather than as fifteen
# near-identical constructor calls: (key, name, icon, url env var, default url,
# description). Adding a sub-app to FrankensteinCentral means adding a row here
# and a container in docker-compose.
#
# The env var names are NOT derivable from the key — `plex` reads PLEX_SVC_URL
# and `firefly` reads FIREFLY_URL_SVC, because both names were already taken by
# the upstream services these talk to. So each row names its own variable
# rather than a rule pretending to cover them.
_CATALOG = (
    ('core', 'Core', '🧩',
     'CORE_URL', 'http://core:8000',
     'Your personal state & daily score — study, water, nutrition, Big 3, captures.'),
    ('stocks', 'Stocks', '📈',
     'STOCKS_URL', 'http://stocks:8000',
     'Portfolio & watchlist — value, daily movers, positions. Keyless quotes.'),
    ('assistant', 'Assistant', '🧠',
     'ASSISTANT_URL', 'http://assistant:8000',
     'Your manager. Reads across every sub-app, surfaces deadlines, and routes info where it belongs.'),
    ('powerbuy', 'PowerBuy', '🛒',
     'POWERBUY_URL', 'http://powerbuy:8000',
     'Your arbitrage tracker. Purchases, profit, unpaid & expiring alerts.'),
    ('fitness', 'Fitness', '💪',
     'FITNESS_URL', 'http://fitness:8000',
     'Tracks your gym visits, plans the optimal week, and tells you what to eat and buy.'),
    ('gmail', 'Gmail Checker', '📬',
     'GMAIL_URL', 'http://gmail:8000',
     'Scans your inbox for what actually needs a reply and flags deadlines.'),
    ('schedule', 'Schedule', '🗓️',
     'SCHEDULE_URL', 'http://schedule:8000',
     'Your calendar. The assistant drops interviews, deadlines, and workouts here.'),
    ('finance', 'Finance', '💸',
     'FINANCE_URL', 'http://finance:8000',
     "Your bills & subscriptions. Monthly spend and what's due soon."),
    ('tasks', 'Tasks', '✅',
     'TASKS_URL', 'http://tasks:8000',
     "Your to-do list. Quick capture, check things off, track what's open."),
    ('budget', 'Budget', '📊',
     'BUDGET_URL', 'http://budget:8000',
     "Monthly spending by category. What's left and what's over."),
    ('deals', 'Deals', '🏷️',
     'DEALS_URL', 'http://deals:8000',
     'Real discounts spotted in your inbox — merchant, offer, source email.'),
    ('networth', 'Net Worth', '💎',
     'NETWORTH_URL', 'http://networth:8000',
     'Chase, Marcus, Robinhood, Fidelity, TSP — individual balances and the total.'),
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


def load_registry() -> list[SubApp]:
    """Each sub-app is an independent service reachable at its own URL."""
    return [
        SubApp(key=key, name=name, description=description, icon=icon,
               url=os.environ.get(env, default))
        for key, name, icon, env, default, description in _CATALOG
    ]
