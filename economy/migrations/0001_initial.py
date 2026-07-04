import django.db.models.deletion
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
                ("catch_income_enabled", models.BooleanField(default=True)),
                ("catch_base_min", models.IntegerField(default=5)),
                ("catch_base_max", models.IntegerField(default=15)),
                ("catch_rarity_multiplier", models.FloatField(default=50.0)),
                ("catch_special_bonus", models.IntegerField(default=20)),
                ("quicksell_enabled", models.BooleanField(default=True)),
                ("quicksell_default_min", models.IntegerField(default=10)),
                ("quicksell_default_max", models.IntegerField(default=50)),
                ("quicksell_special_multiplier", models.FloatField(default=2.0)),
                ("quicksell_high_roll_bonus", models.IntegerField(default=10)),
                ("listings_enabled", models.BooleanField(default=True)),
                ("listing_platform_fee", models.FloatField(default=0.05)),
                ("listing_min_price", models.IntegerField(default=1)),
                ("listing_expiry_hours", models.IntegerField(default=48)),
                ("listing_max_per_player", models.IntegerField(default=10)),
                ("passive_enabled", models.BooleanField(default=True)),
                ("passive_chance", models.FloatField(default=0.05)),
                ("passive_interval_minutes", models.IntegerField(default=10)),
                ("passive_min", models.IntegerField(default=1)),
                ("passive_max", models.IntegerField(default=10)),
                ("passive_rarity_bonus", models.FloatField(default=10.0)),
                ("pending_enabled", models.BooleanField(default=True)),
                ("claim_enabled", models.BooleanField(default=True)),
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
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sell_price",
                        to="bd_models.ball",
                    ),
                ),
                ("min_price", models.IntegerField(default=10)),
                ("max_price", models.IntegerField(default=50)),
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
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="economy_listings",
                        to="bd_models.player",
                    ),
                ),
                (
                    "ball_instance",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="economy_listing",
                        to="bd_models.ballinstance",
                    ),
                ),
                ("price", models.PositiveBigIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("sold", models.BooleanField(default=False)),
                (
                    "buyer",
                    models.ForeignKey(
                        blank=True,
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
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="economy_passive_pool",
                        to="bd_models.player",
                    ),
                ),
                ("pending", models.PositiveBigIntegerField(default=0)),
                ("last_tick", models.DateTimeField(blank=True, null=True)),
                ("total_earned", models.PositiveBigIntegerField(default=0)),
            ],
            options={
                "verbose_name": "Passive Income Pool",
                "verbose_name_plural": "Passive Income Pools",
                "db_table": "economy_passive_pool",
                "managed": True,
            },
        ),
    ]
