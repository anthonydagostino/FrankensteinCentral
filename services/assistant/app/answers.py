"""/ask, answered from the deterministic layer — pure, no I/O, no model.

PRODUCT_IDEAS #9 argues for a language model over `/ask`, with the rule that
"every number in an answer must come from a service response, never from the
model". That rule is worth nothing until the deterministic layer underneath it
is honest, and today it is not: `_get()` returns `{}` for a service that did
not answer, so a down service and a genuinely empty one are the same value.
Every intent then renders that `{}` as a confident zero.

    budget down    -> "You've spent $0 of $0 (0%), $0 left."
    gmail down     -> "Inbox's clear — nothing needs a reply."
    tasks down     -> "You have 0 open task(s)."
    networth down  -> "No accounts set up yet."

Four false statements, each indistinguishable from the true one. That is the
same failure docs/BUDGETS.md already forbids in the money layer — `null` is not
`0`, and a partial read is not a complete one — applied to prose instead of to
figures.

So this module takes sources as `{name: payload | None}`, where **None means
the service did not answer**, and refuses to state anything sourced from a
None. It names what is missing instead. That is idea #9's acceptance signal in
full — "ask it something whose data source is down, and it tells you the source
is down instead of guessing" — and it needs no API key and no egress to reach.

A model layer can sit on top of this later and phrase these answers more
naturally. It cannot be allowed to invent the numbers, and with this module
underneath it does not have to: the figures, and the honesty about which ones
are unavailable, are already decided here.
"""
from __future__ import annotations

# Every service /ask can read, and the human name used when it is down. These
# read as the SUBJECT of a sentence, article included where one is needed —
# "Gmail didn't answer", "the budget service didn't answer". Prepending a fixed
# "the" to all of them produces "the Gmail didn't answer".
SERVICE_NAMES = {
    "budget": "the budget service",
    "emails": "Gmail",
    "tasks": "the tasks service",
    "finance": "the bills service",
    "powerbuy": "PowerBuy",
    "fitness": "the fitness service",
    "availability": "Gmail",
    "deals": "the deals service",
    "networth": "the net worth service",
}


def _money(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "$?"


def _unavailable(missing: list[str], subject: str) -> dict:
    """What we say instead of a number we do not have.

    Naming the service matters: "I don't know" invites a retry, "the budget
    service didn't answer" tells you which container to look at.
    """
    names = sorted({SERVICE_NAMES.get(m, m) for m in missing})
    who = names[0] if len(names) == 1 else (", ".join(names[:-1]) + " and " + names[-1])
    return {
        "answer": f"I can't tell you {subject} — {who} didn't answer. "
                  f"That's unknown, not zero.",
        "known": False,
        "unavailable": sorted(set(missing)),
        "used": [],
    }


def _ok(answer: str, used: list[str]) -> dict:
    return {"answer": answer, "known": True, "unavailable": [], "used": sorted(set(used))}


# ---- the intents -------------------------------------------------------
# (keywords, required sources, subject used in the "can't tell you" line)

def _budget(s):
    b = s["budget"]
    over = b.get("over_budget") or []
    out = (f"You've spent {_money(b.get('total_spent'))} of "
           f"{_money(b.get('total_budget'))} ({b.get('percent_used', 0)}%), "
           f"{_money(b.get('remaining'))} left.")
    if over:
        out += f" Over budget on: {', '.join(over)}."
    return _ok(out, ["budget"])


def _emails(s, short, sender):
    ems = s["emails"].get("emails") or []
    if not ems:
        return _ok("Inbox's clear — nothing needs a reply.", ["emails"])
    shown = ems[:5]
    lines = [f"{len(ems)} email(s) need a reply:"]
    lines += [f"• {short(e.get('subject'))} — {sender(e.get('from'))}" for e in shown]
    if len(ems) > len(shown):
        lines.append(f"…and {len(ems) - len(shown)} more.")
    return _ok("\n".join(lines), ["emails"])


def _tasks(s):
    t = s["tasks"]
    top = ", ".join(t.get("top") or [])
    n = t.get("open", 0)
    return _ok(f"You have {n} open task(s)" + (f": {top}." if top else "."), ["tasks"])


def _finance(s):
    f = s["finance"]
    up = f.get("upcoming") or []
    if not up:
        return _ok("No bills due in the next week. You spend "
                   f"{_money(f.get('monthly_total'))}/month on bills.", ["finance"])
    names = ", ".join(f"{b['name']} ({_money(b['amount'])}, in {b['days_until']}d)"
                      for b in up)
    return _ok(f"Coming up: {names}.", ["finance"])


def _powerbuy(s):
    p = (s["powerbuy"].get("summary") or {})
    return _ok(f"Expected profit is {_money(p.get('expected_profit'))}, with "
               f"{p.get('unpaid_count', 0)} unpaid and "
               f"{p.get('expiring_soon_count', 0)} expiring soon.", ["powerbuy"])


def _fitness(s):
    tp = s["fitness"].get("today_plan") or {}
    lifts = ", ".join(tp.get("lifts") or []) or "recovery"
    return _ok(f"Today is {tp.get('focus', 'rest')} day: {lifts}.", ["fitness"])


def _availability(s):
    threads = s["availability"].get("threads") or []
    pend = [t for t in threads if t.get("status") in ("pending", "countered")]
    if not pend:
        return _ok("Nothing awaiting a reply — every proposed time has been "
                   "confirmed or fell through.", ["availability"])
    lines = [f"{len(pend)} thread(s) awaiting a reply:"]
    lines += [f"• {t.get('subject') or 'a thread'}" for t in pend[:5]]
    return _ok("\n".join(lines), ["availability"])


def _deals(s):
    top = [d.get("title") or d.get("name") or "a deal"
           for d in (s["deals"].get("top") or s["deals"].get("deals") or [])][:3]
    if not top:
        return _ok("No deals spotted right now.", ["deals"])
    return _ok(f"Deals spotted: {'; '.join(top)}.", ["deals"])


def _networth(s):
    nw = s["networth"]
    accts = nw.get("accounts") or []
    if not accts:
        return _ok("No accounts set up yet.", ["networth"])
    lines = [f"Net worth: {_money(nw.get('total'))}"]
    lines += [f"• {a['name']}: {_money(a['balance'])}" for a in accts]
    return _ok("\n".join(lines), ["networth"])


# ORDER MATTERS: first match wins, so specific phrases must precede the broad
# single words that contain them. "anything awaiting a reply" contains "reply",
# which is an email keyword — with the email intent first, the availability
# intent was unreachable and that question was always answered about the inbox.
INTENTS = [
    (("awaiting", "waiting on", "proposed", "did they reply", "pending", "confirm"),
     ("availability",), "what's awaiting a reply", _availability),
    (("budget", "spending", "spent", "over budget", "left to spend"),
     ("budget",), "what you've spent", _budget),
    (("email", "reply", "inbox", "mail"),
     ("emails",), "what's in your inbox", None),          # needs helpers
    (("task", "todo", "to-do", "to do"),
     ("tasks",), "your tasks", _tasks),
    (("bill", "due", "subscription", "finance", "money"),
     ("finance",), "what bills are due", _finance),
    (("profit", "powerbuy", "arbitrage", "purchase", "unpaid"),
     ("powerbuy",), "your resale numbers", _powerbuy),
    (("workout", "gym", "lift", "train", "exercise"),
     ("fitness",), "today's training", _fitness),
    (("deal", "discount", "sale"),
     ("deals",), "what deals are about", _deals),
    (("net worth", "worth", "balance", "chase", "marcus", "robinhood",
      "fidelity", "tsp", "savings"),
     ("networth",), "your balances", _networth),
]


def answer(question: str, sources: dict, *, short=None, sender=None) -> dict:
    """Answer `question` from `sources`.

    `sources` maps a service name to its payload, or to **None** when that
    service did not answer. A None is never rendered as a figure.

    Returns {answer, known, used, unavailable}. `known` is False whenever the
    answer had to fall back to naming a missing service, so a caller can style
    it differently instead of having to parse the prose.
    """
    ql = (question or "").lower().strip()
    short = short or (lambda s: str(s or "")[:60])
    sender = sender or (lambda s: str(s or ""))

    for keywords, needs, subject, render in INTENTS:
        if not any(k in ql for k in keywords):
            continue
        missing = [n for n in needs if sources.get(n) is None]
        if missing:
            return _unavailable(missing, subject)
        if render is None:                       # the email intent needs helpers
            return _emails(sources, short, sender)
        return render(sources)

    return rundown(sources)


def rundown(sources: dict) -> dict:
    """The default answer: everything at a glance.

    Unlike the intents, this one is still worth giving when a service is down —
    the other half of your plate is real. But it must not silently drop the
    missing half, so what it could not read is named at the end rather than
    quietly counted as nothing.
    """
    parts, missing = [], []

    def count(name, key, label, sub=None):
        payload = sources.get(name)
        if payload is None:
            missing.append(name)
            return
        block = payload.get(sub) if sub else payload
        n = (block or {}).get(key) if isinstance(block, dict) else None
        if isinstance(n, list):
            n = len(n)
        if n:
            parts.append(f"{n} {label}")

    count("emails", "emails", "emails to reply")
    count("tasks", "open", "open tasks")
    count("finance", "upcoming", "bills due soon")
    count("powerbuy", "unpaid_count", "unpaid buys", sub="summary")
    count("deals", "count", "deals spotted")

    names = sorted({SERVICE_NAMES.get(m, m) for m in missing})
    joined = (names[0] if len(names) == 1
              else ", ".join(names[:-1]) + " and " + names[-1]) if names else ""
    if parts:
        out = f"Here's your plate: {'; '.join(parts)}."
        if missing:
            out += f" I couldn't reach {joined}, so anything there isn't counted."
    elif missing:
        # Nothing to report AND something unreachable: "nothing urgent" would be
        # a claim about services that were never read.
        out = (f"I couldn't reach {joined}, so I can't tell you what's on your "
               f"plate. Nothing urgent came back from the rest.")
    else:
        out = "Here's your plate: nothing urgent."
    return {"answer": out, "known": not missing,
            "unavailable": sorted(set(missing)),
            "used": sorted({k for k, v in sources.items() if v is not None})}
