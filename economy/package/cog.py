from __future__ import annotations

import asyncio
import logging
import random
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ballsdex.core.utils.transformers import BallInstanceTransformer
from ballsdex.packages.countryballs.countryball import BallSpawnView
from bd_models.models import BallInstance, Player

from ..models import BallListing, EconomyConfig, PassiveIncomePool

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger(__name__)

BallInstanceTransform = app_commands.Transform[BallInstance, BallInstanceTransformer]


async def get_config() -> EconomyConfig | None:
    return await EconomyConfig.objects.afirst()


def compute_sell_price(config: EconomyConfig, ball_instance: BallInstance) -> int:
    base = random.randint(config.sell_base_min, config.sell_base_max)
    price = base + ball_instance.ball.rarity * config.sell_rarity_multiplier
    if ball_instance.special_id is not None:
        price *= config.sell_special_multiplier
    stat_bonus = (ball_instance.attack_bonus + ball_instance.health_bonus)
    price += stat_bonus * config.sell_stat_multiplier
    return max(1, int(price))


class Economy(commands.Cog):
    """
    Economy system — catch income, ball selling and passive income.
    """

    def __init__(self, bot: "BallsDexBot") -> None:
        self.bot = bot
        self._config: EconomyConfig | None = None

    async def cog_load(self) -> None:
        self._config = await get_config()
        if self._config is None:
            log.warning(
                "Economy: No EconomyConfig record found. "
                "Create one in the admin panel to enable the economy system."
            )
        await self._monkeypatch()
        self.passive_income_task.change_interval(
            minutes=self._config.passive_interval_minutes if self._config else 10
        )
        self.passive_income_task.start()
        self.expire_listings_task.start()

    def cog_unload(self) -> None:
        self.passive_income_task.cancel()
        self.expire_listings_task.cancel()

    # ── Monkeypatch — Layer 1: Catch income ─────────────────────────────────

    async def _monkeypatch(self) -> None:
        from ballsdex.packages.countryballs import CountryBallsSpawner
        cog = cast("CountryBallsSpawner", self.bot.get_cog("CountryBallsSpawner"))
        if cog is None:
            log.warning("Economy: CountryBallsSpawner cog not found, catch income disabled.")
            return

        economy_cog = self

        class BallSpawnViewOverride(BallSpawnView):
            async def catch_ball(
                self,
                user: discord.User | discord.Member,
                *,
                player: Player | None,
                guild: discord.Guild | None,
            ):
                ball, is_new = await super().catch_ball(user, player=player, guild=guild)
                config = economy_cog._config
                if config is None or not config.catch_income_enabled:
                    return ball, is_new
                if player is None:
                    return ball, is_new

                earned = random.randint(config.catch_base_min, config.catch_base_max)
                earned += int(ball.ball.rarity * config.catch_rarity_multiplier)
                if ball.special_id is not None:
                    earned += config.catch_special_bonus

                await Player.objects.filter(pk=player.pk).aupdate(
                    money=player.money + earned
                )
                log.debug(
                    "Catch income: player %s earned %d (ball %s)",
                    player.discord_id, earned, ball.ball.country,
                )
                return ball, is_new

        cog.countryball_cls = BallSpawnViewOverride
        log.info("Economy: catch income monkeypatch applied.")

    # ── Background tasks ─────────────────────────────────────────────────────

    @tasks.loop(minutes=10)
    async def passive_income_task(self) -> None:
        config = await get_config()
        if config is None or not config.passive_enabled:
            return

        async for player in Player.objects.all():
            total = 0
            async for bi in BallInstance.objects.filter(player=player, deleted=False).select_related("ball"):
                if random.random() < config.passive_chance:
                    amount = random.randint(config.passive_min, config.passive_max)
                    multiplier = 1.0 + bi.ball.rarity * config.passive_rarity_multiplier
                    total += int(amount * multiplier)

            if total > 0:
                pool, _ = await PassiveIncomePool.objects.aget_or_create(
                    player=player, defaults={"pending": 0}
                )
                pool.pending += total
                await pool.asave(update_fields=["pending", "last_tick"])

    @passive_income_task.before_loop
    async def before_passive(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=30)
    async def expire_listings_task(self) -> None:
        from django.utils import timezone
        now = timezone.now()
        async for listing in BallListing.objects.filter(sold=False, expires_at__lte=now).select_related("ball_instance", "seller"):
            listing.ball_instance.player = listing.seller
            await listing.ball_instance.asave(update_fields=["player"])
            await listing.adelete()
            log.info(
                "Listing expired: ball %s returned to player %s",
                listing.ball_instance_id, listing.seller.discord_id,
            )

    @expire_listings_task.before_loop
    async def before_expire(self) -> None:
        await self.bot.wait_until_ready()

    # ── Commands ─────────────────────────────────────────────────────────────

    economy_group = app_commands.Group(
        name="economy",
        description="Economy commands — sell balls and manage your money.",
    )

    # ── Balance ──────────────────────────────────────────────────────────────

    @economy_group.command(name="balance")
    async def balance(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """Check your current money balance."""
        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                "You don't have a player profile yet. Catch some balls first!", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"## Your Balance\n💰 **{player.money:,}** currency", ephemeral=True
        )

    # ── Layer 2: Quick sell ──────────────────────────────────────────────────

    @economy_group.command(name="quicksell")
    @app_commands.describe(ball="The ball you want to sell to the system.")
    async def quicksell(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        ball: BallInstanceTransform,
    ) -> None:
        """Sell a ball instantly to the system for money."""
        config = self._config or await get_config()
        if config is None or not config.sell_enabled:
            await interaction.response.send_message(
                "Selling is currently disabled.", ephemeral=True
            )
            return

        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                "You don't have a player profile yet.", ephemeral=True
            )
            return

        if ball.player_id != player.pk:
            await interaction.response.send_message(
                "You don't own that ball.", ephemeral=True
            )
            return

        if ball.favorite:
            await interaction.response.send_message(
                "You can't sell a favourited ball. Unfavourite it first.", ephemeral=True
            )
            return

        if hasattr(ball, "listing"):
            await interaction.response.send_message(
                "That ball is currently listed for sale. Delist it first.", ephemeral=True
            )
            return

        await ball.arefresh_from_db()
        price = compute_sell_price(config, ball)

        ball.deleted = True
        await ball.asave(update_fields=["deleted"])
        await Player.objects.filter(pk=player.pk).aupdate(money=player.money + price)

        await interaction.response.send_message(
            f"Sold **{ball.ball.country}** for 💰 **{price:,}** currency.",
            ephemeral=True,
        )

    # ── Layer 2: Player listing ───────────────────────────────────────────────

    @economy_group.command(name="list")
    @app_commands.describe(
        ball="The ball you want to list for sale.",
        price="The price in currency you want to sell it for.",
    )
    async def list_ball(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        ball: BallInstanceTransform,
        price: int,
    ) -> None:
        """List a ball for sale at a price you set. Other players can buy it."""
        if price < 1:
            await interaction.response.send_message(
                "Price must be at least 1.", ephemeral=True
            )
            return

        config = self._config or await get_config()
        if config is None or not config.sell_enabled:
            await interaction.response.send_message(
                "Selling is currently disabled.", ephemeral=True
            )
            return

        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                "You don't have a player profile yet.", ephemeral=True
            )
            return

        if ball.player_id != player.pk:
            await interaction.response.send_message("You don't own that ball.", ephemeral=True)
            return

        if ball.favorite:
            await interaction.response.send_message(
                "You can't list a favourited ball.", ephemeral=True
            )
            return

        already_listed = await BallListing.objects.filter(
            ball_instance=ball, sold=False
        ).aexists()
        if already_listed:
            await interaction.response.send_message(
                "That ball is already listed.", ephemeral=True
            )
            return

        from django.utils import timezone
        expires_at = timezone.now() + timedelta(hours=config.listing_expiry_hours)

        await BallListing.objects.acreate(
            seller=player,
            ball_instance=ball,
            price=price,
            expires_at=expires_at,
        )

        await interaction.response.send_message(
            f"Listed **{ball.ball.country}** for 💰 **{price:,}** currency.\n"
            f"-# Listing expires in {config.listing_expiry_hours} hours if unsold.",
            ephemeral=True,
        )

    @economy_group.command(name="listings")
    async def listings(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """Browse all active ball listings."""
        await interaction.response.defer(ephemeral=True)

        active = await BallListing.objects.filter(
            sold=False
        ).select_related("ball_instance__ball", "ball_instance__special", "seller").alist()

        if not active:
            await interaction.followup.send("No active listings right now.", ephemeral=True)
            return

        lines = []
        for listing in active[:25]:
            bi = listing.ball_instance
            special_tag = f" [{bi.special.name}]" if bi.special_id else ""
            lines.append(
                f"`#{listing.pk}` **{bi.ball.country}**{special_tag} — "
                f"💰 **{listing.price:,}** — seller: <@{listing.seller.discord_id}>"
            )

        text = "## Active Listings\n" + "\n".join(lines)
        if len(active) > 25:
            text += f"\n-# Showing 25 of {len(active)} listings."

        await interaction.followup.send(text, ephemeral=True)

    @economy_group.command(name="buy")
    @app_commands.describe(listing_id="The listing ID shown in /economy listings.")
    async def buy(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        listing_id: int,
    ) -> None:
        """Buy a ball from another player's listing."""
        await interaction.response.defer(ephemeral=True)

        config = self._config or await get_config()
        if config is None:
            await interaction.followup.send("Economy is not configured.", ephemeral=True)
            return

        try:
            listing = await BallListing.objects.select_related(
                "ball_instance__ball", "seller", "ball_instance__special"
            ).aget(pk=listing_id, sold=False)
        except BallListing.DoesNotExist:
            await interaction.followup.send(
                "That listing doesn't exist or has already been sold.", ephemeral=True
            )
            return

        try:
            buyer = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.followup.send(
                "You don't have a player profile yet.", ephemeral=True
            )
            return

        if listing.seller_id == buyer.pk:
            await interaction.followup.send(
                "You can't buy your own listing.", ephemeral=True
            )
            return

        if not buyer.can_afford(listing.price):
            await interaction.followup.send(
                f"You can't afford this. You have 💰 **{buyer.money:,}** "
                f"but this costs 💰 **{listing.price:,}**.",
                ephemeral=True,
            )
            return

        fee = int(listing.price * config.listing_platform_fee)
        seller_receives = listing.price - fee

        await Player.objects.filter(pk=buyer.pk).aupdate(money=buyer.money - listing.price)
        seller = listing.seller
        await Player.objects.filter(pk=seller.pk).aupdate(money=seller.money + seller_receives)

        bi = listing.ball_instance
        bi.player = buyer
        await bi.asave(update_fields=["player"])

        from django.utils import timezone
        listing.sold = True
        listing.buyer = buyer
        listing.sold_at = timezone.now()
        await listing.asave(update_fields=["sold", "buyer", "sold_at"])

        special_tag = f" [{bi.special.name}]" if bi.special_id else ""
        await interaction.followup.send(
            f"Bought **{bi.ball.country}**{special_tag} for 💰 **{listing.price:,}**.\n"
            f"-# Platform fee: {fee:,}. Seller received: {seller_receives:,}.",
            ephemeral=True,
        )

    @economy_group.command(name="delist")
    @app_commands.describe(listing_id="The listing ID you want to remove.")
    async def delist(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        listing_id: int,
    ) -> None:
        """Remove one of your own active listings and get the ball back."""
        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                "You don't have a player profile yet.", ephemeral=True
            )
            return

        try:
            listing = await BallListing.objects.select_related(
                "ball_instance__ball"
            ).aget(pk=listing_id, sold=False, seller=player)
        except BallListing.DoesNotExist:
            await interaction.response.send_message(
                "That listing doesn't exist or doesn't belong to you.", ephemeral=True
            )
            return

        bi = listing.ball_instance
        await listing.adelete()

        await interaction.response.send_message(
            f"Delisted **{bi.ball.country}** — it has been returned to your collection.",
            ephemeral=True,
        )

    @economy_group.command(name="mylistings")
    async def mylistings(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """View your own active listings."""
        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                "You don't have a player profile yet.", ephemeral=True
            )
            return

        active = await BallListing.objects.filter(
            seller=player, sold=False
        ).select_related("ball_instance__ball", "ball_instance__special").alist()

        if not active:
            await interaction.response.send_message(
                "You have no active listings.", ephemeral=True
            )
            return

        lines = []
        for listing in active:
            bi = listing.ball_instance
            special_tag = f" [{bi.special.name}]" if bi.special_id else ""
            lines.append(
                f"`#{listing.pk}` **{bi.ball.country}**{special_tag} — "
                f"💰 **{listing.price:,}** — expires <t:{int(listing.expires_at.timestamp())}:R>"
            )

        await interaction.response.send_message(
            "## Your Listings\n" + "\n".join(lines), ephemeral=True
        )

    # ── Layer 3: Passive income claiming ────────────────────────────────────

    @economy_group.command(name="pending")
    async def pending(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """Check how much passive income is waiting to be claimed."""
        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                "You don't have a player profile yet.", ephemeral=True
            )
            return

        try:
            pool = await PassiveIncomePool.objects.aget(player=player)
            pending = pool.pending
        except PassiveIncomePool.DoesNotExist:
            pending = 0

        await interaction.response.send_message(
            f"## Passive Income\n"
            f"💰 **{pending:,}** currency ready to claim.\n"
            f"-# Use `/economy claim` to collect it.",
            ephemeral=True,
        )

    @economy_group.command(name="claim")
    async def claim(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """Claim all accumulated passive income."""
        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                "You don't have a player profile yet.", ephemeral=True
            )
            return

        try:
            pool = await PassiveIncomePool.objects.aget(player=player)
        except PassiveIncomePool.DoesNotExist:
            await interaction.response.send_message(
                "You have no passive income to claim yet.", ephemeral=True
            )
            return

        if pool.pending == 0:
            await interaction.response.send_message(
                "No passive income to claim right now. Check back later.", ephemeral=True
            )
            return

        earned = pool.pending
        pool.pending = 0
        await pool.asave(update_fields=["pending"])
        await Player.objects.filter(pk=player.pk).aupdate(money=player.money + earned)

        await interaction.response.send_message(
            f"💰 Claimed **{earned:,}** currency from passive income!\n"
            f"New balance: **{player.money + earned:,}**",
            ephemeral=True,
        )
