from __future__ import annotations

from dotenv import load_dotenv

from .logging_config import setup_logging


def main() -> None:
    load_dotenv()
    setup_logging()
    # importa después de configurar logging para ver mensajes de depuración
    from .config import settings
    from .discord_client import bot
    bot.run(settings.DISCORD_TOKEN)


if __name__ == "__main__":
    main()


