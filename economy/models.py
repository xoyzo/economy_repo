from typing import Self

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.manager import Manager


class EconomySettings(models.Model):
    """
    Global economy configuration. Only one record should exist.
    Created automatically on first migration.
    All rates, toggles and intervals are controlled here.
    """

    # ── Layer 1: Catch income ───────────────────────────────────────────────

    catch_income_enabled = models.BooleanField(
        default=True,
        help_text="Enable or disable catch income entirely.",
    )
    catch_base_min = models.IntegerField(
        default=5,
        help_text="Minimum base currency earned per catch before rarity scaling.",
    )
    catch_base_max = models.IntegerField(
        default=15,
        help_text="Maximum base currency earned per catch before rarity scaling.",
    )
    catch_rarity_multiplier = models.FloatField(
        default=50.0,
        help_text=(
            "Ball rarity is a float from 0.0 to 1.0. "
            "Extra currency earned = rarity * catch_rarity_multiplier. "
            "Default 50.0 means a rarity-1.0 ball adds 50 on top of base."
        ),
    )
    catch_special_bonus = models.IntegerField(
        default=20,
        help_text="Flat bonus currency added when the caught ball has a special applied.",
    )

    # ── Layer 2: Quick sell ─────────────────────────────────────────────────

    quicksell_enabled = models.BooleanField(
        default=True,
        help_text="Enable or disable the /economy quicksell command.",
    )
    quicksell_default_min = models.IntegerField(
        default=10,
        help_text=(
            "Default minimum quick sell price for balls that do not have a specific "
            "BallSellPrice record. Set both min and max the same for a fixed price."
        ),
    )
    quicksell_default_max = models.IntegerField(
        default=50,
        help_text=(
            "Default maximum quick sell price for balls that do not have a specific "
            "BallSellPrice record."
        ),
    )
    quicksell_special_multiplier = models.FloatField(
        default=2.0,
        help_text=(
            "Multiplier applied to the quick sell price when the ball has a special. "
            "Default 2.0 = double price for specials."
        ),
    )
    quicksell_high_roll_bonus = models.IntegerField(
        default=10,
        help_text=(
            "Extra currency added when both attack_bonus and health_bonus are positive "
            "(above-zero rolls). Rewards good rolls."
        ),
    )

    # ── Layer 2: Player market listings ────────────────────────────────────

    listings_enabled = models.BooleanField(
        default=True,
        help_text="Enable or disable the player-to-player ball market entirely.",
    )
    listing_platform_fee = models.FloatField(
        default=0.05,
        help_text=(
            "Fraction of the sale price deducted as a platform fee when a listing sells. "
            "0.05 = 5%. The seller receives (price - fee). Acts as a currency sink."
        ),
    )
    listing_min_price = models.IntegerField(
        default=1,
        help_text="Minimum price a player can set when listing a ball.",
    )
    listing_expiry_hours = models.IntegerField(
        default=48,
        help_text="Hours before an unsold listing expires and the ball is returned to the seller.",
    )
    listing_max_per_player = models.IntegerField(
        default=10,
        help_text="Maximum number of active listings a single player may have at once.",
    )

    # ── Layer 3: Passive income ─────────────────────────────────────────────

    passive_enabled = models.BooleanField(
        default=True,
        help_text="Enable or disable the passive income system entirely.",
    )
    passive_chance = models.FloatField(
        default=0.05,
        help_text=(
            "Chance (0.0–1.0) that each ball generates passive income during each interval tick. "
            "0.05 = 5% per ball per tick."
        ),
    )
    passive_interval_minutes = models.IntegerField(
        default=10,
        help_text="How often in minutes the passive income background task runs.",
    )
    passive_min = models.IntegerField(
        default=1,
        help_text="Minimum currency generated per ball per successful passive tick.",
    )
    passive_max = models.IntegerField(
        default=10,
        help_text="Maximum currency generated per ball per successful passive tick.",
    )
    passive_rarity_bonus = models.FloatField(
        default=10.0,
        help_text=(
            "Extra passive currency added based on rarity. "
            "Total per tick = random(min, max) + rarity * passive_rarity_bonus. "
            "Rarity is 0.0–1.0 so default 10.0 adds 0–10 extra per tick."
        ),
    )

    # ── Per-command toggles ─────────────────────────────────────────────────

    balance_enabled = models.BooleanField(
        default=True,
        help_text="Enable or disable the /economy balance command.",
    )
    pending_enabled = models.BooleanField(
        default=True,
        help_text="Enable or disable the /economy pending command.",
    )
    claim_enabled = models.BooleanField(
        default=True,
        help_text="Enable or disable the /economy claim command.",
    )

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "economy_settings"
        verbose_name = "Economy Settings"
        verbose_name_plural = "Economy Settings"

    def __str__(self) -> str:
        return "Economy Settings"

    def clean(self) -> None:
        if self.catch_base_min > self.catch_base_max:
            raise ValidationError("catch_base_min must be less than or equal to catch_base_max")
        if self.quicksell_default_min > self.quicksell_default_max:
            raise ValidationError("quicksell_default_min must be less than or equal to quicksell_default_max")
        if not (0.0 <= self.listing_platform_fee < 1.0):
            raise ValidationError("listing_platform_fee must be between 0.0 and 1.0")
        if self.listing_min_price < 1:
            raise ValidationError("listing_min_price must be at least 1")
        if self.listing_expiry_hours < 1:
            raise ValidationError("listing_expiry_hours must be at least 1")
        if self.listing_max_per_player < 1:
            raise ValidationError("listing_max_per_player must be at least 1")
        if not (0.0 <= self.passive_chance <= 1.0):
            raise ValidationError("passive_chance must be between 0.0 and 1.0")
        if self.passive_min > self.passive_max:
            raise ValidationError("passive_min must be less than or equal to passive_max")
        if self.passive_interval_minutes < 1:
            raise ValidationError("passive_interval_minutes must be at least 1")
        if self.passive_rarity_bonus < 0:
            raise ValidationError("passive_rarity_bonus must be 0 or greater")
        if self.catch_rarity_multiplier < 0:
            raise ValidationError("catch_rarity_multiplier must be 0 or greater")
        if self.quicksell_special_multiplier < 1.0:
            raise ValidationError("quicksell_special_multiplier must be at least 1.0")

    def compute_catch_income(self, rarity: float, has_special: bool) -> int:
        """Calculate currency earned from a catch."""
        import random
        base = random.randint(self.catch_base_min, self.catch_base_max)
        earned = base + int(rarity * self.catch_rarity_multiplier)
        if has_special:
            earned += self.catch_special_bonus
        return max(1, earned)

    def compute_quicksell_price(
        self,
        rarity: float,
        has_special: bool,
        attack_bonus: int,
        health_bonus: int,
        ball_min: int | None,
        ball_max: int | None,
    ) -> int:
        """
        Calculate the quick sell price for a ball instance.
        Uses per-ball BallSellPrice if set, otherwise falls back to defaults.
        """
        import random
        min_price = ball_min if ball_min is not None else self.quicksell_default_min
        max_price = ball_max if ball_max is not None else self.quicksell_default_max
        price = random.randint(min_price, max_price)
        if has_special:
            price = int(price * self.quicksell_special_multiplier)
        if attack_bonus > 0 and health_bonus > 0:
            price += self.quicksell_high_roll_bonus
        return max(1, price)

    def compute_passive_tick(self, rarity: float) -> int:
        """Calculate currency from one passive tick for one ball."""
        import random
        base = random.randint(self.passive_min, self.passive_max)
        return max(1, base + int(rarity * self.passive_rarity_bonus))


class BallSellPrice(models.Model):
    """
    Admin-configured quick sell price range for a specific ball.
    If no record exists for a ball, EconomySettings defaults are used.
    """
    ball = models.OneToOneField(
        "bd_models.Ball",
        on_delete=models.CASCADE,
        related_name="sell_price",
        help_text="The ball this price applies to.",
    )
    min_price = models.IntegerField(
        default=10,
        help_text="Minimum quick sell price for this ball.",
    )
    max_price = models.IntegerField(
        default=50,
        help_text="Maximum quick sell price for this ball.",
    )

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "economy_ball_sell_price"
        verbose_name = "Ball Sell Price"
        verbose_name_plural = "Ball Sell Prices"

    def __str__(self) -> str:
        return f"{self.ball.country} ({self.min_price}–{self.max_price})"

    def clean(self) -> None:
        if self.min_price > self.max_price:
            raise ValidationError("min_price must be less than or equal to max_price")
        if self.min_price < 1:
            raise ValidationError("min_price must be at least 1")


class BallListing(models.Model):
    """
    A player-to-player ball listing on the market.
    The ball instance is effectively held by the market until sold or expired.
    """
    seller = models.ForeignKey(
        "bd_models.Player",
        on_delete=models.CASCADE,
        related_name="economy_listings",
        help_text="The player who listed this ball.",
    )
    ball_instance = models.OneToOneField(
        "bd_models.BallInstance",
        on_delete=models.CASCADE,
        related_name="economy_listing",
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
        related_name="economy_purchases",
        help_text="The player who bought this listing.",
    )
    sold_at = models.DateTimeField(null=True, blank=True)

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "economy_ball_listing"
        verbose_name = "Ball Listing"
        verbose_name_plural = "Ball Listings"

    def __str__(self) -> str:
        return f"Listing #{self.pk} — {self.ball_instance} for {self.price}"


class PassiveIncomePool(models.Model):
    """
    Tracks accumulated passive income per player waiting to be claimed.
    One record per player, updated by the background task.
    """
    player = models.OneToOneField(
        "bd_models.Player",
        on_delete=models.CASCADE,
        related_name="economy_passive_pool",
        help_text="The player this pool belongs to.",
    )
    pending = models.PositiveBigIntegerField(
        default=0,
        help_text="Currency accumulated but not yet claimed.",
    )
    last_tick = models.DateTimeField(
        auto_now=True,
        help_text="Last time the passive task updated this pool.",
    )
    total_earned = models.PositiveBigIntegerField(
        default=0,
        help_text="Total passive income ever earned by this player (for stats).",
    )

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "economy_passive_pool"
        verbose_name = "Passive Income Pool"
        verbose_name_plural = "Passive Income Pools"

    def __str__(self) -> str:
        return f"{self.player} — {self.pending} pending"
