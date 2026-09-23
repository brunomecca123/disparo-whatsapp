"""Acesso ao Supabase via PostgREST.

Usa a service_role key (as tabelas têm RLS ligado sem policies, então só ela enxerga
os dados). É HTTP puro com `requests` de propósito: em serverless, conexão direta ao
Postgres esbarra em pool/cold start, e o projeto já depende de requests.
"""

import os
from typing import Dict, List, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class DbError(RuntimeError):
    pass


def _session() -> requests.Session:
    sessao = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PATCH", "DELETE"],
    )
    sessao.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=16))
    return sessao


SESSION = _session()
LOTE_INSERCAO = 500  # linhas por requisição ao inserir destinatários


def _config() -> tuple:
    url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    chave = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not url or not chave:
        raise DbError(
            "Supabase não configurado: defina SUPABASE_URL e SUPABASE_SERVICE_KEY "
            "(Project Settings → API → service_role)."
        )
    return f"{url}/rest/v1", chave


def configurado() -> bool:
    return bool(os.getenv("SUPABASE_URL") and (os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")))


def _headers(extra: Optional[Dict] = None) -> Dict:
    _, chave = _config()
    cabecalhos = {
        "apikey": chave,
        "Authorization": f"Bearer {chave}",
        "Content-Type": "application/json",
    }
    if extra:
        cabecalhos.update(extra)
    return cabecalhos


def _pedir(metodo: str, caminho: str, **kwargs) -> requests.Response:
    base, _ = _config()
    resposta = SESSION.request(metodo, f"{base}{caminho}", headers=_headers(kwargs.pop("extra_headers", None)),
                               timeout=kwargs.pop("timeout", 30), **kwargs)
    if resposta.status_code >= 400:
        raise DbError(f"Supabase {resposta.status_code} em {metodo} {caminho}: {resposta.text[:300]}")
    return resposta


def _linhas(resposta: requests.Response) -> List[Dict]:
    if not resposta.content:
        return []
    try:
        return resposta.json()
    except ValueError:
        return []


# ---------------------------------------------------------------- campanhas


def inserir_campanha(campanha: Dict) -> Dict:
    resposta = _pedir("POST", "/campaigns", json=campanha,
                      extra_headers={"Prefer": "return=representation"})
    linhas = _linhas(resposta)
    return linhas[0] if linhas else campanha


def atualizar_campanha(campaign_id: str, campos: Dict, retornar: bool = False) -> Optional[Dict]:
    """Atualiza a campanha. Com retornar=True devolve a linha já gravada.

    O retorno é como a execução descobre, sem requisição extra, que alguém cancelou ou
    mudou o ritmo — em serverless quem cancela está em outra invocação, fora deste processo.
    """
    if not campos:
        return None
    resposta = _pedir(
        "PATCH", f"/campaigns?id=eq.{campaign_id}", json=campos,
        extra_headers={"Prefer": "return=representation" if retornar else "return=minimal"},
    )
    linhas = _linhas(resposta) if retornar else []
    return linhas[0] if linhas else None


def assumir_campanha(campaign_id: str, limite_heartbeat: str) -> Optional[Dict]:
    """Marca a campanha como 'running' e devolve a linha — ou None se outra execução já a tem.

    O UPDATE condicional é a trava: o Postgres resolve o WHERE atomicamente, então duas
    invocações simultâneas nunca disparam para os mesmos pendentes. Um heartbeat velho
    libera a campanha, que é como uma execução cortada no meio volta a ser retomável.
    """
    filtro = (
        f"/campaigns?id=eq.{campaign_id}"
        # Só campanha viva: nunca reabre uma que já terminou ou foi cancelada.
        f"&status=in.(pending,paused,interrupted,running)"
        # Se já está 'running', só assume quando o batimento sumiu — ou seja, quem rodava morreu.
        # quote: sem isso o "+" do fuso viraria espaço na querystring e o Postgres recusaria a data.
        f"&or=(status.neq.running,heartbeat_at.is.null,"
        f"heartbeat_at.lt.{quote(limite_heartbeat, safe='')})"
    )
    resposta = _pedir("PATCH", filtro, json={"status": "running", "heartbeat_at": "now"},
                      extra_headers={"Prefer": "return=representation"})
    linhas = _linhas(resposta)
    return linhas[0] if linhas else None


def buscar_campanha(campaign_id: str) -> Optional[Dict]:
    """Lê da view: já vem com pendentes contados e duração calculada."""
    resposta = _pedir("GET", f"/campaign_overview?id=eq.{campaign_id}&limit=1")
    linhas = _linhas(resposta)
    return linhas[0] if linhas else None


def listar_campanhas(limite: int = 30) -> List[Dict]:
    resposta = _pedir("GET", f"/campaign_overview?order=created_at.desc&limit={limite}")
    return _linhas(resposta)


def uso_de_templates() -> List[Dict]:
    """Uma linha por campanha, só com o necessário para saber quando cada template foi usado."""
    resposta = _pedir(
        "GET",
        "/campaigns?select=nome:template->>name,idioma:template->>language,status,dry_run,sent,created_at"
        "&order=created_at.desc&limit=10000",
    )
    return _linhas(resposta)


def campanhas_por_status(status: List[str]) -> List[Dict]:
    lista = ",".join(status)
    resposta = _pedir("GET", f"/campaigns?status=in.({lista})&select=id,status")
    return _linhas(resposta)


# ------------------------------------------------------------ destinatários


def inserir_destinatarios(campaign_id: str, destinatarios: List[Dict]) -> int:
    """Insere a lista em blocos — uma requisição por bloco, não por contato."""
    total = 0
    for inicio in range(0, len(destinatarios), LOTE_INSERCAO):
        bloco = [
            {
                "campaign_id": campaign_id,
                "idx": inicio + posicao,
                "phone": item["phone"],
                "original": item.get("original", ""),
                "vars": item.get("values", {}),
                "status": "pending",
            }
            for posicao, item in enumerate(destinatarios[inicio:inicio + LOTE_INSERCAO])
        ]
        _pedir("POST", "/recipients", json=bloco, extra_headers={"Prefer": "return=minimal"}, timeout=60)
        total += len(bloco)
    return total


def proximos_pendentes(campaign_id: str, limite: int) -> List[Dict]:
    resposta = _pedir(
        "GET",
        f"/recipients?campaign_id=eq.{campaign_id}&status=eq.pending"
        f"&select=id,idx,phone,original,vars&order=idx.asc&limit={limite}",
        timeout=60,
    )
    return _linhas(resposta)


def gravar_resultados(resultados: List[Dict]) -> None:
    """Grava o desfecho de um lote de envios de uma vez (upsert por id)."""
    if not resultados:
        return
    _pedir("POST", "/recipients", json=resultados,
           extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"}, timeout=60)


def contar_pendentes(campaign_id: str) -> int:
    resposta = _pedir(
        "GET", f"/recipients?campaign_id=eq.{campaign_id}&status=eq.pending&select=id",
        extra_headers={"Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"},
    )
    faixa = resposta.headers.get("content-range", "*/0")
    try:
        return int(faixa.split("/")[-1])
    except ValueError:
        return 0


def destinatarios_por_status(campaign_id: str, status: str, limite: int = 1000) -> List[Dict]:
    resposta = _pedir(
        "GET",
        f"/recipients?campaign_id=eq.{campaign_id}&status=eq.{status}"
        f"&select=idx,phone,original,vars,error,message_id&order=idx.asc&limit={limite}",
        timeout=60,
    )
    return _linhas(resposta)


def todos_destinatarios(campaign_id: str) -> List[Dict]:
    """Usado só na exportação do CSV de resultado."""
    saida, pagina, tamanho = [], 0, 1000
    while True:
        resposta = _pedir(
            "GET",
            f"/recipients?campaign_id=eq.{campaign_id}"
            f"&select=idx,phone,original,status,message_id,error&order=idx.asc"
            f"&limit={tamanho}&offset={pagina * tamanho}",
            timeout=60,
        )
        linhas = _linhas(resposta)
        saida.extend(linhas)
        if len(linhas) < tamanho:
            return saida
        pagina += 1


# ------------------------------------------------------------------ uploads


def salvar_upload(upload_id: str, source: str, colunas: List, linhas: List) -> None:
    _pedir("POST", "/uploads", json={
        "id": upload_id, "source": source, "columns": colunas,
        "rows": linhas, "row_count": len(linhas),
    }, extra_headers={"Prefer": "return=minimal"}, timeout=60)


def buscar_upload(upload_id: str) -> Optional[Dict]:
    resposta = _pedir("GET", f"/uploads?id=eq.{upload_id}&limit=1", timeout=60)
    linhas = _linhas(resposta)
    return linhas[0] if linhas else None


# ---------------------------------------------------------------- blocklist


def buscar_blocklist() -> set:
    resposta = _pedir("GET", "/blocklist?select=phone&limit=10000")
    return {linha["phone"] for linha in _linhas(resposta)}
