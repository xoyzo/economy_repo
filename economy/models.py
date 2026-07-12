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
    )
    catch_base_min = models.IntegerField(
        default=5,
    )
    catch_base_max = models.IntegerField(
        default=15,
    )
    catch_rarity_multiplier = models.FloatField(
        default=50.0,
    )
    catch_special_bonus = models.IntegerField(
        default=20,
    )

    # ── Layer 2: Quick sell ─────────────────────────────────────────────────

    quicksell_enabled = models.BooleanField(
        default=True,
    )
    quicksell_default_min = models.IntegerField(
        default=10,
    )
    quicksell_default_max = models.IntegerField(
        default=50,
    )
    quicksell_special_multiplier = models.FloatField(
        default=2.0,
    )
    quicksell_high_roll_bonus = models.IntegerField(
        default=10,
    )

    # ── Layer 2: Player market listings ────────────────────────────────────

    listings_enabled = models.BooleanField(
        default=True,
    )
    listing_platform_fee = models.FloatField(
        default=0.05,
    )
    listing_min_price = models.IntegerField(
        default=1,
    )
    listing_expiry_hours = models.IntegerField(
        default=48,
    )
    listing_max_per_player = models.IntegerField(
        default=10,
    )

    # ── Layer 3: Passive income ─────────────────────────────────────────────

    passive_enabled = models.BooleanField(
        default=True,
    )
    passive_chance = models.FloatField(
        default=0.05,
    )
    passive_interval_minutes = models.IntegerField(
        default=10,
    )
    passive_min = models.IntegerField(
        default=1,
    )
    passive_max = models.IntegerField(
        default=10,
    )
    passive_rarity_bonus = models.FloatField(
        default=10.0,
    )

    # ── Per-command toggles ─────────────────────────────────────────────────

    pending_enabled = models.BooleanField(
        default=True,
    )
    claim_enabled = models.BooleanField(
        default=True,
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
    )
    min_price = models.IntegerField(
        default=10,
    )
    max_price = models.IntegerField(
        default=50,
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
    )
    ball_instance = models.OneToOneField(
        "bd_models.BallInstance",
        on_delete=models.CASCADE,
        related_name="economy_listing",
    )
    price = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    sold = models.BooleanField(default=False)
    buyer = models.ForeignKey(
        "bd_models.Player",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="economy_purchases",
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
    )
    pending = models.PositiveBigIntegerField(
        default=0,
    )
    last_tick = models.DateTimeField(
        null=True,
        blank=True,
    )
    total_earned = models.PositiveBigIntegerField(
        default=0,
    )

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "economy_passive_pool"
        verbose_name = "Passive Income Pool"
        verbose_name_plural = "Passive Income Pools"

    def __str__(self) -> str:
        return f"{self.player} — {self.pending} pending"


class BallShopPrice(models.Model):
    """
    Admin-configured shop listing — players can buy a ball directly for a set price.
    Unlike BallSellPrice (which controls quick sell), this creates a buyable item in the shop.
    """
    ball = models.ForeignKey(
        "bd_models.Ball",
        on_delete=models.CASCADE,
        related_name="shop_prices",
    )
    price = models.PositiveBigIntegerField()
    stock = models.IntegerField(
        default=-1,
    )
    enabled = models.BooleanField(default=True)
    special = models.ForeignKey(
        "bd_models.Special",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shop_prices",
    )

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "economy_ball_shop_price"
        verbose_name = "Ball Shop Price"
        verbose_name_plural = "Ball Shop Prices"

    def __str__(self) -> str:
        return f"{self.ball.country} — {self.price}"


class SpecialEconomyBonus(models.Model):
    """
    Per-special override for economy multipliers.

    When enabled, replaces the corresponding global EconomySettings multiplier
    for balls carrying this special (quicksell_special_multiplier or the
    implicit passive special bonus). Looked up by special_id via aget(), so
    this is intentionally a one-to-one relationship — at most one bonus
    config per special, enforced at the DB level.
    """

    special = models.OneToOneField(
        "bd_models.Special",
        on_delete=models.CASCADE,
        related_name="economy_bonus",
    )

    quicksell_multiplier_enabled = models.BooleanField(
        default=False,
    )
    quicksell_multiplier = models.FloatField(
        default=2.0,
    )

    passive_multiplier_enabled = models.BooleanField(
        default=False,
    )
    passive_multiplier = models.FloatField(
        default=2.0,
    )

    objects: Manager[Self] = Manager()

    class Meta:
        managed = True
        db_table = "economy_special_bonus"
        verbose_name = "Special Economy Bonus"
        verbose_name_plural = "Special Economy Bonuses"

    def __str__(self) -> str:
        return f"{self.special.name} economy bonus"

    def clean(self) -> None:
        if self.quicksell_multiplier < 1.0:
            raise ValidationError("quicksell_multiplier must be at least 1.0")
        if self.passive_multiplier < 1.0:
            raise ValidationError("passive_multiplier must be at least 1.0")
