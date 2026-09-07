# Documentation Index

FrankensteinCentral is a personal life-OS dashboard: a thin FastAPI gateway in
front of fifteen independent microservices and one Postgres, deployed with
`docker compose` on a home OptiPlex.

This branch — **`production`** — is the branch the box actually deploys.

## Start here

| Document | What it answers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the system is put together, what each service owns, how the Gmail → Assistant → Calendar pipeline works, and where the trust boundaries are. |
| [DEPLOYMENT-BASELINE.md](DEPLOYMENT-BASELINE.md) | **What is actually running right now** — commit, containers, branch topology, and the drift found while checking. Measured, not assumed. |
| [OPERATIONS.md](OPERATIONS.md) | The runbook. Deploy, roll back, diagnose. Start with "first five commands". |

## Reference

| Document | What it answers |
|---|---|
| [API-REFERENCE.md](API-REFERENCE.md) | Every HTTP endpoint in the system, service by service. |
| [DATA-MODEL.md](DATA-MODEL.md) | Every Postgres table, who owns it, and the timestamp/timezone conventions that will bite you. |
| [CONFIGURATION.md](CONFIGURATION.md) | Every environment variable, its default, and what happens when it is missing. |
| [TESTING.md](TESTING.md) | The test suite, and the 2026-09-01 date bug that explains why date-dependent code is never tested against today. |
| [BUDGETS.md](BUDGETS.md) | The honesty rules in the money layer: zero ≠ unknown, suppressed values are `null`, partial windows are never presented as complete. |

## Setup

| Document | For |
|---|---|
| [SETUP-DEPLOY.md](SETUP-DEPLOY.md) | Auto-deploy on the box, and the review/production boundary. |
| [SETUP-GMAIL.md](SETUP-GMAIL.md) | Google OAuth for the gmail sub-app. |
| [SETUP-FIREFLY.md](SETUP-FIREFLY.md) | Connecting a self-hosted Firefly III. |
| [SETUP-PLEX.md](SETUP-PLEX.md) | Connecting a shared Plex server. |
| [SETUP-VAULT.md](SETUP-VAULT.md) | Read-only Vaultwarden password health. |
| [SETUP-NOTIFICATIONS.md](SETUP-NOTIFICATIONS.md) | Telegram / WhatsApp / SMS / webhook digests. |

## Process

| Document | For |
|---|---|
| [`.frankenstein/PROTOCOL.md`](../.frankenstein/PROTOCOL.md) | **The rules.** Product Owner ↔ Claude turns, the deployment gates, high-risk actions. Canonical and complete. |
| [PROTOCOL-BOOTSTRAP.md](PROTOCOL-BOOTSTRAP.md) | How the protocol was migrated onto the live box. |
| [AUDIT.md](AUDIT.md) | Internal working doc — audit of the system as-is and the redesign plan. |

## The two rules that catch people out

**Pushing is not deploying.** Task branches can be pushed freely and deploy
nothing. Only the `production` branch is deployed, and only `scripts/promote.sh`
moves it — after acceptance, and only when the directive says
`deploy-approved`.

**Check whose turn it is before changing the product.**

```bash
bash scripts/frankenstein-status.sh
```

Reading the repo, answering questions, and explaining existing behavior are
always allowed.
