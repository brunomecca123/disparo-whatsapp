"""Carrega credenciais e preferências a partir do .env (com fallback no config.py legado)."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

# Na Vercel o código roda em função serverless: filesystem somente leitura (fora de /tmp)
# e processo congelado assim que a resposta sai.
#
# A detecção não depende de uma variável só: VERCEL e VERCEL_ENV existem apenas quando o
# projeto expõe as System Environment Variables (dá para desligar isso no painel), enquanto
# AWS_LAMBDA_FUNCTION_NAME vem do runtime e está sempre lá. SERVERLESS=1 é a saída manual.
SERVERLESS = bool(
    os.getenv("VERCEL")
    or os.getenv("VERCEL_ENV")
    or os.getenv("AWS_LAMBDA_FUNCTION_NAME")
    or os.getenv("SERVERLESS")
)
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
        # Só para subir o exemplo de cabeçalho com mídia na criação de template. Vazio, o
        # app descobre sozinho pelo token (GET /app).
        self.app_id = os.getenv("META_APP_ID", "")
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
