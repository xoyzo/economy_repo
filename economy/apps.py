from django.apps import AppConfig


class EconomyConfig(AppConfig):
    name = "economy"
    dpy_package = "economy.package"

    def ready(self) -> None:
        from django.db.models.signals import post_migrate
        post_migrate.connect(_create_default_config, sender=self)


def _create_default_config(sender, **kwargs) -> None:
    import logging
    log = logging.getLogger(__name__)

    # Only run if the economy_settings table actually exists.
    # During the very first migration run the post_migrate signal fires
    # after each app's migrations complete — if our migrations haven't run
    # yet (or ran with no changes) the table won't exist and we must skip.
    from django.db import connection
    if "economy_settings" not in connection.introspection.table_names():
        return

    from .models import EconomySettings
    if not EconomySettings.objects.exists():
        EconomySettings.objects.create()
        log.info(
            "Economy: created default EconomySettings record. "
            "Adjust rates in the admin panel under Economy > Economy Settings."
        )
