"""Cliente da Graph API do WhatsApp Cloud (Meta)."""

import json
import mimetypes
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.settings import settings

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


class MetaApiError(Exception):
    """Erro devolvido pela Graph API, já traduzido para leitura humana."""

    def __init__(self, message: str, code=None, status=None, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.hint = hint

    # Códigos em que a Meta pede para desacelerar (throughput/rate limit), não erros do envio.
    CODIGOS_RITMO = {4, 80007, 130429, 131056, 131048}

    @property
    def rate_limited(self) -> bool:
        return self.code in self.CODIGOS_RITMO or self.status == 429

    def to_dict(self) -> Dict:
        return {"message": self.message, "code": self.code, "status": self.status, "hint": self.hint}


def _session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    session.mount(
        "https://",
        HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=64),
    )
    return session


SESSION = _session()

# Último X-Business-Use-Case-Usage devolvido pela Meta — mostra o quanto da cota horária
# já foi consumida (call_count/total_time em %) e serve de termômetro durante o disparo.
_uso_lock = threading.Lock()
_ULTIMO_USO: Dict = {}


def _registrar_uso(response: requests.Response):
    bruto = response.headers.get("x-business-use-case-usage")
    if not bruto:
        return
    try:
        dados = json.loads(bruto)
    except ValueError:
        return
    for entradas in dados.values():
        for entrada in entradas:
            if entrada.get("type") in ("whatsapp_business_messaging", "whatsapp_business_management"):
                with _uso_lock:
                    _ULTIMO_USO[entrada["type"]] = {
                        "call_count": entrada.get("call_count"),
                        "total_time": entrada.get("total_time"),
                        "total_cputime": entrada.get("total_cputime"),
                        "espera_min": entrada.get("estimated_time_to_regain_access"),
                        "em": _agora_iso(),
                    }


def ultimo_uso() -> Dict:
    with _uso_lock:
        return dict(_ULTIMO_USO)


def _agora_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")

# Dicas para os códigos de erro que mais aparecem no dia a dia da campanha.
ERROR_HINTS = {
    100: "Parâmetro inválido: confira o número de variáveis do template e o phone id.",
    131000: "Template não encontrado ou não aprovado para este idioma.",
    131026: "Número não existe no WhatsApp ou não pode receber mensagens.",
    131047: "Janela de 24h fechada — só é possível enviar template (é o caso aqui).",
    131049: "Meta limitou a entrega para este usuário por qualidade/frequência.",
    130429: "Rate limit atingido: aumente o intervalo entre envios.",
    190: "Token expirado ou revogado. Gere um novo em WHATSAPP_TOKEN.",
    132000: "Quantidade de variáveis enviadas não bate com a do template.",
    132001: "Template não existe com esse nome/idioma.",
    133010: "Número de origem não registrado na Cloud API.",
}


def _raise_for_error(response: requests.Response):
    _registrar_uso(response)
    if response.status_code == 200:
        return
    try:
        payload = response.json().get("error", {})
    except Exception:
        payload = {}
    code = payload.get("code")
    message = payload.get("message") or response.text[:300] or "Erro desconhecido"
    detail = payload.get("error_data", {}).get("details")
    if detail:
        message = f"{message} — {detail}"
    raise MetaApiError(message, code=code, status=response.status_code, hint=ERROR_HINTS.get(code, ""))


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {settings.token}"}


def _base() -> str:
    return f"https://graph.facebook.com/{settings.api_version}"


# ---------------------------------------------------------------- diagnóstico


def check_connection() -> Dict:
    """Valida token e phone id, devolvendo os dados do número conectado."""
    response = SESSION.get(f"{_base()}/{settings.phone_id}", headers=_headers(), timeout=20)
    _raise_for_error(response)
    data = response.json()
    return {
        "phone_id": data.get("id"),
        "display_phone_number": data.get("display_phone_number"),
        "verified_name": data.get("verified_name"),
        "quality_rating": data.get("quality_rating"),
        "throughput": (data.get("throughput") or {}).get("level"),
    }


# ----------------------------------------------------------------- templates


def _parse_placeholders(text: str) -> List[str]:
    """Devolve os placeholders na ordem em que aparecem, sem repetir."""
    encontrados = []
    for nome in PLACEHOLDER_RE.findall(text or ""):
        if nome not in encontrados:
            encontrados.append(nome)
    return encontrados


def _parse_template(raw: Dict) -> Dict:
    """Normaliza um template da API para o formato que a interface consome."""
    header = {"format": "NONE", "text": "", "variables": []}
    body = {"text": "", "variables": []}
    footer = ""
    buttons = []

    for component in raw.get("components", []):
        tipo = component.get("type", "").upper()
        if tipo == "HEADER":
            formato = component.get("format", "TEXT").upper()
            header = {
                "format": formato,
                "text": component.get("text", ""),
                "variables": _parse_placeholders(component.get("text", "")) if formato == "TEXT" else [],
            }
        elif tipo == "BODY":
            body = {
                "text": component.get("text", ""),
                "variables": _parse_placeholders(component.get("text", "")),
            }
        elif tipo == "FOOTER":
            footer = component.get("text", "")
        elif tipo == "BUTTONS":
            for indice, botao in enumerate(component.get("buttons", [])):
                url = botao.get("url", "")
                buttons.append(
                    {
                        "index": indice,
                        "type": botao.get("type", ""),
                        "text": botao.get("text", ""),
                        "url": url,
                        "variables": _parse_placeholders(url),
                    }
                )

    variaveis = []
    for nome in header["variables"]:
        variaveis.append({"key": f"header:{nome}", "section": "header", "name": nome})
    for nome in body["variables"]:
        variaveis.append({"key": f"body:{nome}", "section": "body", "name": nome})
    for botao in buttons:
        for nome in botao["variables"]:
            variaveis.append(
                {
                    "key": f"button:{botao['index']}:{nome}",
                    "section": "button",
                    "name": nome,
                    "button_index": botao["index"],
                }
            )

    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "language": raw.get("language"),
        "status": raw.get("status"),
        "category": raw.get("category"),
        "quality": (raw.get("quality_score") or {}).get("score"),
        "header": header,
        "body": body,
        "footer": footer,
        "buttons": buttons,
        "variables": variaveis,
        # Named params usam nomes não numéricos dentro de {{ }} e exigem outro formato no payload.
        "named_params": any(not v["name"].isdigit() for v in variaveis),
    }


def list_templates() -> List[Dict]:
    """Lista todos os templates da WABA, paginando até o fim."""
    if not settings.waba_id:
        raise MetaApiError(
            "WHATSAPP_WABA_ID não configurado.",
            hint="Pegue o ID da conta em business.facebook.com → WhatsApp Manager → Configurações da conta.",
        )

    templates = []
    url = f"{_base()}/{settings.waba_id}/message_templates"
    params = {"limit": 100, "fields": "id,name,language,status,category,components,quality_score"}

    while url:
        response = SESSION.get(url, headers=_headers(), params=params, timeout=30)
        _raise_for_error(response)
        payload = response.json()
        templates.extend(_parse_template(item) for item in payload.get("data", []))
        url = (payload.get("paging") or {}).get("next")
        params = None  # a URL de paginação já vem com os parâmetros embutidos

    # A Meta devolve do mais recente para o mais antigo; o sort estável preserva essa ordem
    # dentro de cada grupo, deixando os templates novos no topo.
    return sorted(templates, key=lambda t: t["status"] != "APPROVED")


# --------------------------------------------------------------------- mídia


def upload_media(file_path: str, media_type: str = "image") -> str:
    """Sobe um arquivo local para o Meta e devolve o media id."""
    caminho = Path(file_path)
    if not caminho.exists():
        raise MetaApiError(f"Arquivo não encontrado: {file_path}")

    mime_type, _ = mimetypes.guess_type(str(caminho))
    if not mime_type:
        mime_type = {
            "image": "image/jpeg",
            "video": "video/mp4",
            "document": "application/pdf",
        }.get(media_type, "application/octet-stream")

    with caminho.open("rb") as arquivo:
        response = SESSION.post(
            f"{_base()}/{settings.phone_id}/media",
            headers=_headers(),
            data={"messaging_product": "whatsapp"},
            files={"file": (caminho.name, arquivo, mime_type)},
            timeout=600,
        )
    _raise_for_error(response)
    return response.json().get("id", "")


# ------------------------------------------------------------------- mensagem


def build_components(template: Dict, valores: Dict[str, str], header_media: Optional[Dict] = None) -> List[Dict]:
    """Monta o array `components` do payload a partir dos valores resolvidos por variável."""
    componentes = []
    usa_nomeados = template.get("named_params", False)

    def parametro(nome: str, valor: str) -> Dict:
        item = {"type": "text", "text": valor if valor != "" else " "}
        if usa_nomeados and not nome.isdigit():
            item["parameter_name"] = nome
        return item

    formato_header = template["header"]["format"]
    if formato_header in ("IMAGE", "VIDEO", "DOCUMENT"):
        if header_media:
            componentes.append({"type": "header", "parameters": [header_media]})
    elif formato_header == "TEXT" and template["header"]["variables"]:
        componentes.append(
            {
                "type": "header",
                "parameters": [
                    parametro(nome, valores.get(f"header:{nome}", ""))
                    for nome in template["header"]["variables"]
                ],
            }
        )

    if template["body"]["variables"]:
        componentes.append(
            {
                "type": "body",
                "parameters": [
                    parametro(nome, valores.get(f"body:{nome}", ""))
                    for nome in template["body"]["variables"]
                ],
            }
        )

    for botao in template.get("buttons", []):
        if not botao["variables"]:
            continue
        componentes.append(
            {
                "type": "button",
                "sub_type": "url",
                "index": str(botao["index"]),
                "parameters": [
                    {"type": "text", "text": valores.get(f"button:{botao['index']}:{nome}", "")}
                    for nome in botao["variables"]
                ],
            }
        )

    return componentes


def send_template(phone: str, template_name: str, language: str, components: List[Dict]) -> Dict:
    """Envia a mensagem de template e devolve o id/status retornado pela API."""
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language, "policy": "deterministic"},
            "components": components,
        },
    }
    response = SESSION.post(
        f"{_base()}/{settings.phone_id}/messages",
        headers={**_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    _raise_for_error(response)
    data = response.json()
    mensagem = (data.get("messages") or [{}])[0]
    return {"message_id": mensagem.get("id"), "status": mensagem.get("message_status", "accepted")}
