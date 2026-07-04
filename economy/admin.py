from django.contrib import admin

from .models import BallListing, BallSellPrice, BallShopPrice, EconomySettings, PassiveIncomePool


@admin.register(EconomySettings)
class EconomySettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        (
            "Layer 1 — Catch Income",
            {
                "description": (
                    "Currency earned automatically on every catch via monkeypatch. "
                    "Formula: random(catch_base_min, catch_base_max) + rarity * catch_rarity_multiplier. "
                    "Ball rarity is 0.0–1.0, so catch_rarity_multiplier of 50 adds 0–50 extra."
                ),
                "fields": (
                    "catch_income_enabled",
                    "catch_base_min",
                    "catch_base_max",
                    "catch_rarity_multiplier",
                    "catch_special_bonus",
                ),
            },
        ),
        (
            "Layer 2 — Quick Sell",
            {
                "description": (
                    "Per-ball prices are set in Ball Sell Prices below. "
                    "These defaults apply to any ball without a specific price record. "
                    "Price = random(default_min, default_max), multiplied if special, bonus if high roll."
                ),
                "fields": (
                    "quicksell_enabled",
                    "quicksell_default_min",
                    "quicksell_default_max",
                    "quicksell_special_multiplier",
                    "quicksell_high_roll_bonus",
                ),
            },
        ),
        (
            "Layer 2 — Player Market",
            {
                "description": (
                    "Player-to-player ball listings. "
                    "The platform fee is deducted from the seller's payout and acts as a currency sink."
                ),
                "fields": (
                    "listings_enabled",
                    "listing_platform_fee",
                    "listing_min_price",
                    "listing_expiry_hours",
                    "listing_max_per_player",
                ),
            },
        ),
        (
            "Layer 3 — Passive Income",
            {
                "description": (
                    "Background task runs every passive_interval_minutes minutes. "
                    "Each ball a player owns rolls passive_chance. On success: "
                    "random(passive_min, passive_max) + rarity * passive_rarity_bonus. "
                    "Currency accumulates in a pool until the player runs /economy claim."
                ),
                "fields": (
                    "passive_enabled",
                    "passive_chance",
                    "passive_interval_minutes",
                    "passive_min",
                    "passive_max",
                    "passive_rarity_bonus",
                ),
            },
        ),
        (
            "Per-Command Toggles",
            {
                "description": "Individually enable or disable specific player-facing commands.",
                "fields": (
                    "pending_enabled",
                    "claim_enabled",
                ),
            },
        ),
    )


@admin.register(BallSellPrice)
class BallSellPriceAdmin(admin.ModelAdmin):
    list_display = ("ball", "min_price", "max_price")
    search_fields = ("ball__country",)
    autocomplete_fields = ("ball",)


@admin.register(BallListing)
class BallListingAdmin(admin.ModelAdmin):
    list_display = ("pk", "ball_instance", "seller", "price", "sold", "buyer", "created_at", "expires_at")
    list_filter = ("sold",)
    search_fields = ("seller__discord_id", "buyer__discord_id")
    readonly_fields = ("seller", "ball_instance", "buyer", "sold_at", "created_at", "expires_at")
    ordering = ("-created_at",)


@admin.register(PassiveIncomePool)
class PassiveIncomePoolAdmin(admin.ModelAdmin):
    list_display = ("player", "pending", "total_earned", "last_tick")
    search_fields = ("player__discord_id",)
    readonly_fields = ("player", "total_earned", "last_tick")
    ordering = ("-pending",)


@admin.register(BallShopPrice)
class BallShopPriceAdmin(admin.ModelAdmin):
    list_display = ("ball", "special", "price", "stock", "enabled")
    list_filter = ("enabled",)
    search_fields = ("ball__country",)
    autocomplete_fields = ("ball", "special")
