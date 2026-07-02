from typing import Self

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.manager import Manager


class EconomyConfig(models.Model):
    """
    Global configuration for the economy package.
    Only one record should exist. All rates are configurable here.
    """

    # ── Layer 1: Catch income ───────────────────────────────────────────────

    catch_income_enabled = models.BooleanField(
        default=True,
        help_text="Whether catching balls earns money.",
    )
    catch_base_min = models.IntegerField(
        default=1,
        help_text="Minimum base money earned per catch before rarity scaling.",
    )
    catch_base_max = models.IntegerField(
        default=10,
        help_text="Maximum base money earned per catch before rarity scaling.",
    )
    catch_rarity_multiplier = models.FloatField(
        default=100.0,
        help_text=(
            "Rarity is a float 0-1. Earned money = random(base_min, base_max) + "
            "rarity * catch_rarity_multiplier. Higher = more money for rare balls."
        ),
    )
    catch_special_bonus = models.IntegerField(
        default=25,
        help_text="Flat bonus money added when the caught ball has a special.",
    )

    # ── Layer 2: Quick sell rates ───────────────────────────────────────────

    sell_enabled = models.BooleanField(
        default=True,
        help_text="Whether players can sell balls.",
    )
    sell_base_min = models.IntegerField(
        default=5,
        help_text="Minimum base sell price before rarity and stat scaling.",
    )
    sell_base_max = models.IntegerField(
        default=20,
        help_text="Maximum base sell price before rarity and stat scaling.",
    )
    sell_rarity_multiplier = models.FloatField(
        default=200.0,
        help_text=(
            "Base sell price = random(sell_base_min, sell_base_max) + "
            "rarity * sell_rarity_multiplier."
        ),
    )
    sell_special_multiplier = models.FloatField(
        default=1.5,
        help_text="Multiplier applied to the sell price when the ball has a special.",
    )
    sell_stat_multiplier = models.FloatField(
        default=0.5,
        help_text=(
            "Each point of (attack_bonus + health_bonus) on the ball adds "
            "sell_stat_multiplier money to the sell price."
        ),
    )
    listing_platform_fee = models.FloatField(
        default=0.05,
        help_text="Fraction of the sale price taken as a platform fee on player listings (e.g. 0.05 = 5%).",
    )
    listing_expiry_hours = models.IntegerField(
        default=48,
        help_text="Hours before an unsold player listing expires and the ball is returned.",
    )

    # ── Layer 3: Passive income ─────────────────────────────────────────────

    passive_enabled = models.BooleanField(
        default=True,
        help_text="Whether balls generate passive income.",
    )
    passive_chance = models.FloatField(
        default=0.05,
        help_text="Chance (0.0-1.0) that each ball generates passive income in each interval. Default 0.05 = 5%.",
    )
    passive_interval_minutes = models.IntegerField(
        default=10,
        help_text="How often (in minutes) the passive income background task runs.",
    )
    passive_min = models.IntegerField(
        default=1,
        help_text="Minimum currency generated per ball per successful passive tick.",
    )
    passive_max = models.IntegerField(
        default=20,
        help_text="Maximum currency generated per ball per successful passive tick.",
    )
    passive_rarity_multiplier = models.FloatField(
        default=1.0,
        help_text=(
            "Passive income is multiplied by (1 + rarity * passive_rarity_multiplier). "
            "Set to 0 to make passive income flat regardless of rarity."
        ),
    )

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "economy_config"
        verbose_name = "Economy Config"
        verbose_name_plural = "Economy Config"

    def __str__(self) -> str:
        return "Economy Configuration"

    def clean(self) -> None:
        if self.catch_base_min > self.catch_base_max:
            raise ValidationError("catch_base_min must be less than or equal to catch_base_max")
        if self.sell_base_min > self.sell_base_max:
            raise ValidationError("sell_base_min must be less than or equal to sell_base_max")
        if not (0.0 <= self.passive_chance <= 1.0):
            raise ValidationError("passive_chance must be between 0.0 and 1.0")
        if self.passive_min > self.passive_max:
            raise ValidationError("passive_min must be less than or equal to passive_max")
        if self.passive_interval_minutes < 1:
            raise ValidationError("passive_interval_minutes must be at least 1")
        if not (0.0 <= self.listing_platform_fee < 1.0):
            raise ValidationError("listing_platform_fee must be between 0.0 and 1.0")


class PassiveIncomePool(models.Model):
    """
    Tracks accumulated passive income per player waiting to be claimed.
    One record per player, updated by the background task.
    """
    player = models.OneToOneField(
        "bd_models.Player",
        on_delete=models.CASCADE,
        related_name="passive_pool",
    )
    pending = models.PositiveBigIntegerField(
        default=0,
        help_text="Currency accumulated but not yet claimed by the player.",
    )
    last_tick = models.DateTimeField(
        auto_now_add=True,
        help_text="Last time the passive task ran for this player.",
    )

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "economy_passive_pool"

    def __str__(self) -> str:
        return f"{self.player} — {self.pending} pending"


class BallListing(models.Model):
    """
    A player-to-player ball listing. The ball instance is held here until
    bought or expired, then returned to the seller.
    """
    seller = models.ForeignKey(
        "bd_models.Player",
        on_delete=models.CASCADE,
        related_name="listings",
    )
    ball_instance = models.OneToOneField(
        "bd_models.BallInstance",
        on_delete=models.CASCADE,
        related_name="listing",
        help_text="The ball instance being listed for sale.",
    )
    price = models.PositiveBigIntegerField(
        help_text="Price in currency set by the seller.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        help_text="When this listing expires and the ball is returned to the seller.",
    )
    sold = models.BooleanField(default=False)
    buyer = models.ForeignKey(
        "bd_models.Player",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchases",
    )
    sold_at = models.DateTimeField(null=True, blank=True)

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "economy_ball_listing"

    def __str__(self) -> str:
        return f"{self.ball_instance} listed for {self.price} by {self.seller}"
