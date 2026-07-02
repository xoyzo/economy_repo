# Installation

```toml
[[ballsdex.packages]]
location = "git+https://github.com/yourname/economy.git@v1.0.0"
path = "economy"
enabled = true
```

# Economy

Three layered money earning systems for BallsDex.

## Layer 1 — Catch Income
Every catch automatically earns money scaled by the ball's rarity and whether it has a special.
Configured via the `EconomyConfig` model in the admin panel.

## Layer 2 — Ball Selling
- `/economy quicksell <ball>` — sell a ball instantly to the system for money based on rarity, special and stat rolls
- `/economy list <ball> <price>` — list a ball for sale at a player-set price
- `/economy listings` — browse all active player listings
- `/economy buy <listing_id>` — buy a listed ball from another player
- `/economy delist <listing_id>` — remove your own listing

All quick sell rates are configurable in the admin panel.

## Layer 3 — Passive Income
Every ball you own has a configurable chance every 10 minutes to accumulate passive currency.
Currency builds up in a claimable pool — it is not added automatically.
- `/economy claim` — claim all accumulated passive income
- `/economy pending` — check how much passive income is waiting to be claimed

## Admin Panel
All rates, multipliers, passive chance and intervals are configured via `EconomyConfig` in the admin panel.
Only one `EconomyConfig` record should exist — it applies globally.
