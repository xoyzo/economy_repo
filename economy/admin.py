from django.contrib import admin

from .models import BallListing, EconomyConfig, PassiveIncomePool


@admin.register(EconomyConfig)
class EconomyConfigAdmin(admin.ModelAdmin):
    fieldsets = (
        (
            "Layer 1 — Catch Income",
            {
                "description": "Money earned automatically on every catch via monkeypatch.",
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
            "Layer 2 — Ball Selling",
            {
                "description": "Quick sell rates and player listing settings.",
                "fields": (
                    "sell_enabled",
                    "sell_base_min",
                    "sell_base_max",
                    "sell_rarity_multiplier",
                    "sell_special_multiplier",
                    "sell_stat_multiplier",
                    "listing_platform_fee",
                    "listing_expiry_hours",
                ),
            },
        ),
        (
            "Layer 3 — Passive Income",
            {
                "description": (
                    "Background task runs every passive_interval_minutes minutes. "
                    "Each ball the player owns has a passive_chance to generate "
                    "passive_min to passive_max currency, scaled by rarity."
                ),
                "fields": (
                    "passive_enabled",
                    "passive_chance",
                    "passive_interval_minutes",
                    "passive_min",
                    "passive_max",
                    "passive_rarity_multiplier",
                ),
            },
        ),
    )


@admin.register(PassiveIncomePool)
class PassiveIncomePoolAdmin(admin.ModelAdmin):
    list_display = ("player", "pending", "last_tick")
    search_fields = ("player__discord_id",)
    readonly_fields = ("player", "pending", "last_tick")


@admin.register(BallListing)
class BallListingAdmin(admin.ModelAdmin):
    list_display = ("ball_instance", "seller", "price", "sold", "buyer", "created_at", "expires_at")
    list_filter = ("sold",)
    search_fields = ("seller__discord_id", "buyer__discord_id")
    readonly_fields = ("seller", "ball_instance", "buyer", "sold_at", "created_at")
    autocomplete_fields = ()
