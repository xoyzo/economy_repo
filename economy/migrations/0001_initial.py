import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("bd_models", "0014_alter_ball_options_alter_ballinstance_options_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="EconomySettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("catch_income_enabled", models.BooleanField(default=True, help_text="Enable or disable catch income entirely.")),
                ("catch_base_min", models.IntegerField(default=5, help_text="Minimum base currency earned per catch before rarity scaling.")),
                ("catch_base_max", models.IntegerField(default=15, help_text="Maximum base currency earned per catch before rarity scaling.")),
                ("catch_rarity_multiplier", models.FloatField(default=50.0, help_text="Ball rarity is 0.0-1.0. Extra = rarity * catch_rarity_multiplier.")),
                ("catch_special_bonus", models.IntegerField(default=20, help_text="Flat bonus currency added when the caught ball has a special.")),
                ("quicksell_enabled", models.BooleanField(default=True, help_text="Enable or disable the /economy quicksell command.")),
                ("quicksell_default_min", models.IntegerField(default=10, help_text="Default minimum quick sell price for balls without a BallSellPrice record.")),
                ("quicksell_default_max", models.IntegerField(default=50, help_text="Default maximum quick sell price for balls without a BallSellPrice record.")),
                ("quicksell_special_multiplier", models.FloatField(default=2.0, help_text="Multiplier applied to quick sell price when ball has a special.")),
                ("quicksell_high_roll_bonus", models.IntegerField(default=10, help_text="Extra currency added when both attack_bonus and health_bonus are positive.")),
                ("listings_enabled", models.BooleanField(default=True, help_text="Enable or disable the player market entirely.")),
                ("listing_platform_fee", models.FloatField(default=0.05, help_text="Fraction of sale price deducted as platform fee.")),
                ("listing_min_price", models.IntegerField(default=1, help_text="Minimum listing price a player can set.")),
                ("listing_expiry_hours", models.IntegerField(default=48, help_text="Hours before an unsold listing expires.")),
                ("listing_max_per_player", models.IntegerField(default=10, help_text="Max active listings per player at once.")),
                ("passive_enabled", models.BooleanField(default=True, help_text="Enable or disable passive income.")),
                ("passive_chance", models.FloatField(default=0.05, help_text="Chance each ball generates income per interval tick.")),
                ("passive_interval_minutes", models.IntegerField(default=10, help_text="How often in minutes the passive task runs.")),
                ("passive_min", models.IntegerField(default=1, help_text="Minimum currency per successful passive tick.")),
                ("passive_max", models.IntegerField(default=10, help_text="Maximum currency per successful passive tick.")),
                ("passive_rarity_bonus", models.FloatField(default=10.0, help_text="Extra passive income = rarity * passive_rarity_bonus.")),
                ("balance_enabled", models.BooleanField(default=True, help_text="Enable or disable /economy balance.")),
                ("pending_enabled", models.BooleanField(default=True, help_text="Enable or disable /economy pending.")),
                ("claim_enabled", models.BooleanField(default=True, help_text="Enable or disable /economy claim.")),
            ],
            options={
                "verbose_name": "Economy Settings",
                "verbose_name_plural": "Economy Settings",
                "db_table": "economy_settings",
                "managed": True,
            },
        ),
        migrations.CreateModel(
            name="BallSellPrice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "ball",
                    models.OneToOneField(
                        help_text="The ball this price applies to.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sell_price",
                        to="bd_models.ball",
                    ),
                ),
                ("min_price", models.IntegerField(default=10, help_text="Minimum quick sell price for this ball.")),
                ("max_price", models.IntegerField(default=50, help_text="Maximum quick sell price for this ball.")),
            ],
            options={
                "verbose_name": "Ball Sell Price",
                "verbose_name_plural": "Ball Sell Prices",
                "db_table": "economy_ball_sell_price",
                "managed": True,
            },
        ),
        migrations.CreateModel(
            name="BallListing",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "seller",
                    models.ForeignKey(
                        help_text="The player who listed this ball.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="economy_listings",
                        to="bd_models.player",
                    ),
                ),
                (
                    "ball_instance",
                    models.OneToOneField(
                        help_text="The ball instance being listed.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="economy_listing",
                        to="bd_models.ballinstance",
                    ),
                ),
                ("price", models.PositiveBigIntegerField(help_text="Price in currency set by the seller.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(help_text="When this listing expires and ball is returned.")),
                ("sold", models.BooleanField(default=False)),
                (
                    "buyer",
                    models.ForeignKey(
                        blank=True,
                        help_text="The player who bought this.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="economy_purchases",
                        to="bd_models.player",
                    ),
                ),
                ("sold_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "verbose_name": "Ball Listing",
                "verbose_name_plural": "Ball Listings",
                "db_table": "economy_ball_listing",
                "managed": True,
            },
        ),
        migrations.CreateModel(
            name="PassiveIncomePool",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "player",
                    models.OneToOneField(
                        help_text="The player this pool belongs to.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="economy_passive_pool",
                        to="bd_models.player",
                    ),
                ),
                ("pending", models.PositiveBigIntegerField(default=0, help_text="Currency accumulated but not yet claimed.")),
                ("last_tick", models.DateTimeField(blank=True, null=True, help_text="Last time the passive task updated this pool.")),
                ("total_earned", models.PositiveBigIntegerField(default=0, help_text="Total passive income ever earned.")),
            ],
            options={
                "verbose_name": "Passive Income Pool",
                "verbose_name_plural": "Passive Income Pools",
                "db_table": "economy_passive_pool",
                "managed": True,
            },
        ),
    ]
