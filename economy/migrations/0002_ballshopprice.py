import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bd_models", "0014_alter_ball_options_alter_ballinstance_options_and_more"),
        ("economy", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BallShopPrice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "ball",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shop_prices",
                        to="bd_models.ball",
                    ),
                ),
                ("price", models.PositiveBigIntegerField()),
                ("stock", models.IntegerField(default=-1)),
                ("enabled", models.BooleanField(default=True)),
                (
                    "special",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shop_prices",
                        to="bd_models.special",
                    ),
                ),
            ],
            options={
                "verbose_name": "Ball Shop Price",
                "verbose_name_plural": "Ball Shop Prices",
                "db_table": "economy_ball_shop_price",
                "managed": True,
            },
        ),
    ]
