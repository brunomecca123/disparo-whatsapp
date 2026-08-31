"""Upload de mídia grande via Supabase Storage.

A função da Vercel aceita no máximo 4,5 MB por requisição, e um vídeo de template pode
chegar a 16 MB. Por isso o arquivo grande não passa pelo servidor na subida: o navegador
o envia direto ao Storage com uma URL assinada (nunca vê a service key) e o servidor
apenas busca de lá para repassar à Meta, o que não tem limite de tamanho.
"""

import mimetypes
import os
import re
import uuid
from typing import Dict, Tuple

from core.db import SESSION, DbError, configurado  # noqa: F401  (configurado é reexportado)

BUCKET = "media"
PASTA = "headers"  # todo objeto vive aqui: facilita limpar sobras e barra path traversal
LIMITE_BUCKET_BYTES = 50 * 1024 * 1024


class StorageError(RuntimeError):
    pass


def _config() -> Tuple[str, str]:
    url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    chave = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not url or not chave:
        raise DbError(
            "Supabase não configurado: defina SUPABASE_URL e SUPABASE_SERVICE_KEY "
            "(Project Settings → API → service_role)."
        )
    return f"{url}/storage/v1", chave


def _headers() -> Dict:
    _, chave = _config()
    return {"apikey": chave, "Authorization": f"Bearer {chave}"}


def _nome_seguro(filename: str) -> str:
    """Mantém só o que o Storage aceita em uma chave, preservando a extensão."""
    base = os.path.basename(filename or "").strip() or "arquivo"
    limpo = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "arquivo"
    return limpo[-80:]


def _validar_path(path: str) -> str:
    """Só aceita chaves que este módulo mesmo gerou."""
    if not path.startswith(f"{PASTA}/") or ".." in path or path.count("/") != 1:
        raise StorageError(f"Caminho inválido no Storage: {path}")
    return path


def assinar_upload(filename: str) -> Dict:
    """Cria uma URL de upload assinada (validade de 2h) para o navegador usar direto."""
    base, _ = _config()
    path = f"{PASTA}/{uuid.uuid4().hex[:8]}_{_nome_seguro(filename)}"
    resposta = SESSION.post(f"{base}/object/upload/sign/{BUCKET}/{path}", headers=_headers(), timeout=30)
    if not resposta.ok:
        raise StorageError(_mensagem(resposta, "não foi possível preparar o upload"))
    caminho_assinado = resposta.json().get("url", "")
    return {"path": path, "upload_url": f"{base}{caminho_assinado}"}


def baixar(path: str) -> bytes:
    """Lê o objeto com a service key (o bucket é privado)."""
    base, _ = _config()
    resposta = SESSION.get(f"{base}/object/{BUCKET}/{_validar_path(path)}", headers=_headers(), timeout=600)
    if not resposta.ok:
        raise StorageError(_mensagem(resposta, "arquivo não encontrado no Storage"))
    return resposta.content


def remover(path: str) -> None:
    """Apaga o objeto. O Storage é só um repasse: depois do media id o arquivo não serve mais."""
    base, _ = _config()
    try:
        SESSION.delete(f"{base}/object/{BUCKET}/{_validar_path(path)}", headers=_headers(), timeout=60)
    except Exception:
        pass  # sobra no bucket não quebra o disparo; a limpeza pode esperar


def content_type(filename: str, media_type: str) -> str:
    mime, _ = mimetypes.guess_type(filename or "")
    return mime or {
        "image": "image/jpeg",
        "video": "video/mp4",
        "document": "application/pdf",
    }.get(media_type, "application/octet-stream")


def _mensagem(resposta, padrao: str) -> str:
    try:
        corpo = resposta.json()
        return corpo.get("message") or corpo.get("error") or padrao
    except Exception:
        return f"{padrao} (HTTP {resposta.status_code})"
