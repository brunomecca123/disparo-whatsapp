"""Carrega credenciais e preferências a partir do .env (com fallback no config.py legado)."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
# Na Vercel o filesystem e somente leitura, menos /tmp; local fica na raiz do projeto.
UPLOAD_DIR = Path("/tmp/uploads") if os.getenv("VERCEL") else BASE_DIR / "uploads"

load_dotenv(ENV_PATH)


def _legacy(name: str, default: str = "") -> str:
    """Le o valor do config.py antigo, caso o .env ainda nao tenha sido preenchido."""
    try:
        import config

        return str(getattr(config, name, default) or default)
    except Exception:
        return default


class Settings:
    def __init__(self):
        self.reload()

    def reload(self):
        load_dotenv(ENV_PATH, override=True)
        self.token = os.getenv("WHATSAPP_TOKEN", "") or _legacy("WHATSAPP_TOKEN")
        self.phone_id = os.getenv("WHATSAPP_PHONE_ID", "") or _legacy("WHATSAPP_PHONE_ID")
        self.waba_id = os.getenv("WHATSAPP_WABA_ID", "")
        self.api_version = os.getenv("API_VERSION", "") or _legacy("API_VERSION", "v19.0")
        self.send_interval = float(os.getenv("SEND_INTERVAL", "1.0"))
        self.default_country_code = os.getenv("DEFAULT_COUNTRY_CODE", "55")

    @property
    def configured(self) -> bool:
        return bool(self.token and self.phone_id)

    def missing(self) -> list:
        faltando = []
        if not self.token:
            faltando.append("WHATSAPP_TOKEN")
        if not self.phone_id:
            faltando.append("WHATSAPP_PHONE_ID")
        return faltando


settings = Settings()
