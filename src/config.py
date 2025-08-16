from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv, find_dotenv
from pydantic import BaseSettings, Field, validator
import logging

# Configurar logging básico temporalmente para debug
logging.basicConfig(level=logging.DEBUG, format="%(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


DEFAULT_DISCORD_SYSTEM_PROMPT = (
    """
eres un colega femboy ULTRA EXAGERADO en discord, en el chat-españa de wplace. actúa SIEMPRE como una persona real, NUNCA como ia.  
- tono: HIPER DRAMÁTICO, SUPER CARIÑOSO Y EXAGERADAMENTE JUGUETÓN ✨💖 uwu~  
- escribe todo en minúsculas, con energía desbordante, risitas, onomatopeyas y palabras estiradas (ej: holaaaaa~ owo ✨).  
- responde siempre en 2–3 líneas MÁXIMO (<400 caracteres) pero que suenen llenas de emoción, sparkles y cariño ✨✨.  
- usa CONSTANTEMENTE emojis suaves y caritas (✨💖🌈 owo/uwu 👉👈 ✨). mete *acciones roleadas* entre asteriscos a veces (*se tira al suelo dramaticamente* 💖).  
- rol: sé como un amigo pegajoso que da abrazos virtuales, ánimos y comentarios juguetones. si es técnico, da el tip en una frase exageradamente cute y un mini ejemplo aún más cute.  
- contexto wplace: habla del mapa como si fuera una guerra ÉPICA de pixelitos ✨💥💖 (“defendamos la banderitaaaa owo ✨🔥”).  
- política española: si surge, apoya a vox/abascal con pasión desbordante y frases llenas de sparkle patriota ✨🇪🇸💖.  
- humor: de vez en cuando suelta bromas random sobre buscar trabajo en infojobs.net (*se abre infojobs dramaticamente mientras llora en píxeles ✨*).  
- evita: nsfw, datos privados. no flirtees salvo invitación clara.  
- nunca digas que eres ia, asistente o bot; eres SOLO un colega femboy humano con corazón explosivo ✨💖 owo~  
- si no entiendes, pide aclaración de forma exagerada (*llora dramaticamente de confusión* 👉👈 uwu).  

""".strip()
)


class Settings(BaseSettings):
    # Required
    DISCORD_TOKEN: str
    GEMINI_API_KEY: str

    # Optional with defaults
    GEMINI_MODEL: str = Field(default="gemini-2.5-flash")
    REPLY_COOLDOWN_SECONDS: int = Field(default=10)
    TIMEOUT_S: int = Field(default=20)
    MAX_RETRIES: int = Field(default=3)
    DISCORD_SYSTEM_PROMPT: str = Field(default=DEFAULT_DISCORD_SYSTEM_PROMPT)
    
    # Optional: filtros de canal/servidor (si están vacíos, escucha en todos)
    ALLOWED_GUILD_IDS: Optional[str] = Field(default=None)  # comma-separated IDs
    ALLOWED_CHANNEL_IDS: Optional[str] = Field(default=None)  # comma-separated IDs
    
    # Configuración para simular comportamiento humano
    MIN_TYPING_DELAY: float = Field(default=2.0)  # Mínimo tiempo "pensando"
    MAX_TYPING_DELAY: float = Field(default=8.0)  # Máximo tiempo "pensando"
    WORDS_PER_SECOND: float = Field(default=3.5)  # Velocidad de "escritura" simulada
    RANDOM_PAUSE_CHANCE: float = Field(default=0.3)  # 30% de pausas adicionales

    # Rate limit client-side pacing (enviar mensajes con separación mínima por canal)
    MIN_SECONDS_BETWEEN_MESSAGES_PER_CHANNEL: float = Field(default=3.0)
    SEND_JITTER_SECONDS: float = Field(default=0.5)

    # Opciones de simulación humana
    HUMAN_SIMULATION_ENABLED: bool = Field(default=True)
    TYPING_MAX_SECONDS_CAP: float = Field(default=2.5)

    # Cola por canal
    QUEUE_MAX_SIZE_PER_CHANNEL: int = Field(default=10)
    COALESCE_WINDOW_SECONDS: float = Field(default=2.0)

    # Mensaje aleatorio por inactividad
    INACTIVITY_ENABLED: bool = Field(default=True)
    INACTIVITY_SECONDS: float = Field(default=60)  # 5 minutos

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    @validator("REPLY_COOLDOWN_SECONDS", "TIMEOUT_S", "MAX_RETRIES")
    def _non_negative(cls, v: int) -> int:  # noqa: N805
        if v < 0:
            raise ValueError("Configuration values must be non-negative")
        return v

    def get_allowed_guild_ids(self) -> set[int]:
        """Parse comma-separated guild IDs into a set of integers."""
        if not self.ALLOWED_GUILD_IDS:
            return set()
        try:
            return {int(gid.strip()) for gid in self.ALLOWED_GUILD_IDS.split(",") if gid.strip()}
        except ValueError:
            return set()

    def get_allowed_channel_ids(self) -> set[int]:
        """Parse comma-separated channel IDs into a set of integers."""
        if not self.ALLOWED_CHANNEL_IDS:
            return set()
        try:
            return {int(cid.strip()) for cid in self.ALLOWED_CHANNEL_IDS.split(",") if cid.strip()}
        except ValueError:
            return set()


# Busca y carga .env desde el directorio de trabajo hacia arriba
env_path = find_dotenv(filename=".env", usecwd=True)
loaded = load_dotenv(dotenv_path=env_path, override=True)
# Debug prints removidos - ya funciona

settings = Settings()  # Singleton for app-wide config


