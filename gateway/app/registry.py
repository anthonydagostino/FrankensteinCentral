import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SubApp:
    key: str
    name: str
    description: str
    icon: str
    url: str


def load_registry() -> list[SubApp]:
    """The catalog of sub-apps the hub knows about.

    Each sub-app is an independent service reachable at its own URL. Adding a
    new sub-app to FrankensteinCentral means adding a service here (and a
    container in docker-compose).
    """
    return [
        SubApp(
            key="assistant",
            name="Assistant",
            description="Your manager. Reads across every sub-app, surfaces deadlines, and routes info where it belongs.",
            icon="🧠",
            url=os.environ.get("ASSISTANT_URL", "http://assistant:8000"),
        ),
        SubApp(
            key="powerbuy",
            name="PowerBuy",
            description="Your arbitrage tracker. Purchases, profit, unpaid & expiring alerts.",
            icon="🛒",
            url=os.environ.get("POWERBUY_URL", "http://powerbuy:8000"),
        ),
        SubApp(
            key="fitness",
            name="Fitness",
            description="Tracks your gym visits, plans the optimal week, and tells you what to eat and buy.",
            icon="💪",
            url=os.environ.get("FITNESS_URL", "http://fitness:8000"),
        ),
        SubApp(
            key="gmail",
            name="Gmail Checker",
            description="Scans your inbox for what actually needs a reply and flags deadlines.",
            icon="📬",
            url=os.environ.get("GMAIL_URL", "http://gmail:8000"),
        ),
        SubApp(
            key="schedule",
            name="Schedule",
            description="Your calendar. The assistant drops interviews, deadlines, and workouts here.",
            icon="🗓️",
            url=os.environ.get("SCHEDULE_URL", "http://schedule:8000"),
        ),
        SubApp(
            key="finance",
            name="Finance",
            description="Your bills & subscriptions. Monthly spend and what's due soon.",
            icon="💸",
            url=os.environ.get("FINANCE_URL", "http://finance:8000"),
        ),
        SubApp(
            key="tasks",
            name="Tasks",
            description="Your to-do list. Quick capture, check things off, track what's open.",
            icon="✅",
            url=os.environ.get("TASKS_URL", "http://tasks:8000"),
        ),
        SubApp(
            key="budget",
            name="Budget",
            description="Monthly spending by category. What's left and what's over.",
            icon="📊",
            url=os.environ.get("BUDGET_URL", "http://budget:8000"),
        ),
    ]
