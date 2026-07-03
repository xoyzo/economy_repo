# Installation

```toml
[[ballsdex.packages]]
location = "git+https://github.com/xoyzo/economy_repo.git@v0.1"
path = "economy"
enabled = true
```

# Economy

Three-layer currency earning system for BallsDex. All commands and features can be individually
enabled or disabled from the admin panel.

⚠️ Currency must be enabled in bot settings first.
Admin panel → Settings → set a Currency Name. All commands return a disabled
message until this is configured.

## How it works

### Layer 1 — Catch Income
Every catch automatically earns currency. Amount scales with ball rarity and whether it has
a special. Uses a proper class-level monkeypatch on `BallSpawnView.catch_ball` so it works
correctly on every catch without interfering with other packages.

### Layer 2 — Ball Selling
Two separate ways to sell:
- **Quick sell** — sell instantly to the system at a calculated price
- **Player market** — list at your own price, other players browse and buy

### Layer 3 — Passive Income
Each ball you own has a configurable chance every N minutes to accumulate currency into a
claimable pool. Players must run `/economy claim` to collect it.

## Commands
- `/economy balance` — view your balance and pending income
- `/economy quicksell <ball>` — sell a ball to the system
- `/economy list <ball> <price>` — list a ball for other players
- `/economy listings` — browse all active listings
- `/economy buy <id>` — purchase a listing
- `/economy delist <id>` — cancel your own listing
- `/economy mylistings` — view your own active listings
- `/economy pending` — check unclaimed passive income
- `/economy claim` — collect all pending passive income

## Admin panel
One `EconomyConfig` record is created automatically on install with sensible defaults.
Every rate, toggle, multiplier and interval is configurable there.
Individual commands can be disabled via the toggle fields on `EconomyConfig`.
