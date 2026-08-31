#!/usr/bin/env python3
"""
DisparoMais — disparador de campanhas de WhatsApp, aplicação web local.

Uso:
    ./venv/bin/python app.py        (abre em http://127.0.0.1:8777)
"""

import re
import uuid
import webbrowser
from typing import Dict, List, Optional

import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from core import campaign as campaign_service
from core import contacts, db, meta_api
from core.settings import BASE_DIR, UPLOAD_DIR, settings

app = FastAPI(title="DisparoMais", docs_url=None, redoc_url=None)

# A lista carregada fica no banco entre o upload e o disparo: em serverless cada
# requisição pode cair num processo diferente, então memória local não serve.
BLOCKLIST_FILE = BASE_DIR / "blocklist.txt"


def _guardar_upload(upload_id: str, source: str, colunas: List, linhas: List):
    db.salvar_upload(upload_id, source, colunas, linhas)


def _ler_upload(upload_id: str) -> Optional[Dict]:
    registro = db.buscar_upload(upload_id)
    if not registro:
        return None
    return {"columns": registro["columns"], "rows": registro["rows"], "source": registro.get("source")}


def _blocklist() -> set:
    """Telefones em opt-out: tabela blocklist, com o blocklist.txt local ainda valendo."""
    numeros = set()
    try:
        for telefone in db.buscar_blocklist():
            resultado = contacts.normalize_phone(telefone)
            if resultado["phone"]:
                numeros.add(resultado["phone"])
    except db.DbError:
        pass  # sem banco configurado, vale só o arquivo

    if BLOCKLIST_FILE.exists():
        for linha in BLOCKLIST_FILE.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#"):
                resultado = contacts.normalize_phone(linha)
                if resultado["phone"]:
                    numeros.add(resultado["phone"])
    return numeros


def _erro_meta(erro: meta_api.MetaApiError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": erro.to_dict()})


# ------------------------------------------------------------------ interface


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


# Só estes arquivos da pasta imagens/ são expostos — evita servir qualquer caminho.
IMAGENS_PUBLICAS = {"logo.png", "logos.png"}


@app.get("/imagens/{nome}")
def imagem(nome: str):
    """Serve as imagens da marca usadas no cabeçalho e no favicon."""
    if nome not in IMAGENS_PUBLICAS:
        raise HTTPException(status_code=404, detail="imagem não encontrada")
    return FileResponse(BASE_DIR / "imagens" / nome, media_type="image/png")


# ----------------------------------------------------------------- diagnóstico


@app.get("/api/health")
def health():
    return {
        "configured": settings.configured,
        "missing": settings.missing(),
        "has_waba": bool(settings.waba_id),
        "phone_id": settings.phone_id,
        "api_version": settings.api_version,
        "send_interval": settings.send_interval,
    }


class WabaPayload(BaseModel):
    waba_id: str


@app.post("/api/config/waba")
def salvar_waba(payload: WabaPayload):
    """Grava o WABA ID no .env para não precisar editar o arquivo na mão."""
    waba_id = "".join(c for c in payload.waba_id if c.isdigit())
    if not waba_id:
        raise HTTPException(400, "O ID da conta deve conter apenas números.")

    # Testa o ID antes de gravar, para não deixar um valor quebrado no .env.
    anterior = settings.waba_id
    settings.waba_id = waba_id
    try:
        quantidade = len(meta_api.list_templates())
    except meta_api.MetaApiError as erro:
        settings.waba_id = anterior
        if erro.code == 100:
            erro.hint = "Esse ID não é de uma conta WhatsApp Business. Confira em WhatsApp Manager → Configurações da conta."
        return _erro_meta(erro)

    env = BASE_DIR / ".env"
    linhas = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    nova = f"WHATSAPP_WABA_ID={waba_id}"
    for indice, linha in enumerate(linhas):
        if linha.startswith("WHATSAPP_WABA_ID="):
            linhas[indice] = nova
            break
    else:
        linhas.append(nova)
    env.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return {"ok": True, "waba_id": waba_id, "templates": quantidade}


@app.get("/api/connection")
def connection():
    if not settings.configured:
        raise HTTPException(400, f"Configuração incompleta: {', '.join(settings.missing())}")
    try:
        return meta_api.check_connection()
    except meta_api.MetaApiError as erro:
        return _erro_meta(erro)


# ------------------------------------------------------------------- templates


@app.get("/api/templates")
def templates():
    try:
        return {"templates": meta_api.list_templates()}
    except meta_api.MetaApiError as erro:
        return _erro_meta(erro)


# -------------------------------------------------------------------- contatos


@app.post("/api/contacts/upload")
async def upload_contacts(file: UploadFile = File(...)):
    conteudo = await file.read()
    try:
        colunas, linhas = contacts.parse_upload(file.filename, conteudo)
    except Exception as erro:
        raise HTTPException(400, f"Não consegui ler o arquivo: {erro}")

    if not linhas:
        raise HTTPException(400, "O arquivo não tem nenhuma linha de dados.")

    upload_id = uuid.uuid4().hex[:12]
    _guardar_upload(upload_id, file.filename, colunas, linhas)
    return {
        "upload_id": upload_id,
        "source": file.filename,
        "columns": colunas,
        "row_count": len(linhas),
        "phone_column_guess": contacts.guess_phone_column(colunas),
        "preview": linhas[:5],
    }


class PastePayload(BaseModel):
    text: str


@app.post("/api/contacts/paste")
def paste_contacts(payload: PastePayload):
    colunas, linhas = contacts.parse_pasted(payload.text)
    if not linhas:
        raise HTTPException(400, "Nenhum telefone encontrado no texto colado.")
    upload_id = uuid.uuid4().hex[:12]
    _guardar_upload(upload_id, "lista colada", colunas, linhas)
    return {
        "upload_id": upload_id,
        "source": "lista colada",
        "columns": colunas,
        "row_count": len(linhas),
        "phone_column_guess": "telefone",
        "preview": linhas[:5],
    }


def _render_preview(template: Dict, valores: Dict[str, str]) -> Dict[str, str]:
    """Substitui os placeholders pelo valor real, para o preview da mensagem."""

    def substituir(texto: str, secao: str) -> str:
        def troca(match):
            nome = match.group(1)
            return valores.get(f"{secao}:{nome}") or match.group(0)

        return meta_api.PLACEHOLDER_RE.sub(troca, texto or "")

    return {
        "header": substituir(template["header"]["text"], "header") if template["header"]["format"] == "TEXT" else "",
        "body": substituir(template["body"]["text"], "body"),
        "footer": template.get("footer", ""),
    }


class PreviewPayload(BaseModel):
    upload_id: str
    phone_column: str
    mapping: Dict[str, Dict] = {}
    add_ninth_digit: bool = False
    template: Optional[Dict] = None


@app.post("/api/contacts/preview")
def preview_contacts(payload: PreviewPayload):
    upload = _ler_upload(payload.upload_id)
    if not upload:
        raise HTTPException(404, "Lista não encontrada — recarregue o arquivo.")

    resultado = contacts.build_recipients(
        upload["rows"],
        payload.phone_column,
        payload.mapping,
        payload.add_ninth_digit,
        _blocklist(),
    )

    amostras = []
    if payload.template:
        for destinatario in resultado["recipients"][:3]:
            amostras.append(
                {
                    "phone": destinatario["phone"],
                    "rendered": _render_preview(payload.template, destinatario["values"]),
                }
            )

    return {
        "summary": resultado["summary"],
        "warnings": resultado["warnings"],
        "invalid": resultado["invalid"],
        "duplicates": resultado["duplicates"],
        "blocked": resultado["blocked"],
        "samples": amostras,
        "first_recipients": resultado["recipients"][:10],
    }


# ----------------------------------------------------------------------- mídia


@app.post("/api/media/upload")
async def upload_media(file: UploadFile = File(...), media_type: str = Form("image")):
    UPLOAD_DIR.mkdir(exist_ok=True)
    destino = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{file.filename}"
    destino.write_bytes(await file.read())
    try:
        media_id = meta_api.upload_media(str(destino), media_type)
    except meta_api.MetaApiError as erro:
        return _erro_meta(erro)
    finally:
        destino.unlink(missing_ok=True)
    return {"media_id": media_id, "media_type": media_type, "filename": file.filename}


# ------------------------------------------------------------------- campanhas


class CampaignPayload(BaseModel):
    upload_id: str
    phone_column: str
    template: Dict
    mapping: Dict[str, Dict] = {}
    add_ninth_digit: bool = False
    header_media: Optional[Dict] = None
    dry_run: bool = False
    interval: Optional[float] = None  # formato antigo: segundos entre envios
    rate: Optional[float] = None      # formato atual: mensagens por segundo
    time_budget_s: Optional[float] = None  # para a execução após N segundos (o resto fica pendente)
    test_phone: Optional[str] = None  # envia só para este número, mesmo fora da lista


def _montar_teste(telefone: str, destinatarios: List[Dict], payload: "CampaignPayload") -> Dict:
    """
    Monta um destinatário avulso para o envio de teste.

    O número não precisa estar na lista: as variáveis ligadas a coluna são preenchidas com os
    valores da primeira linha válida, para a mensagem sair igual à que o cliente receberia.
    """
    resultado = contacts.normalize_phone(telefone, payload.add_ninth_digit)
    if resultado["status"] == "invalid":
        raise HTTPException(400, f"Número de teste inválido: {resultado['reason']}")

    modelo = destinatarios[0]["values"] if destinatarios else {}
    valores = {}
    for chave, origem in (payload.mapping or {}).items():
        if origem.get("type") == "fixed":
            valores[chave] = str(origem.get("value", "") or "")
        else:
            valores[chave] = modelo.get(chave, "")

    return {"phone": resultado["phone"], "original": telefone, "values": valores}


@app.post("/api/campaigns")
def create_campaign(payload: CampaignPayload):
    if not settings.configured:
        raise HTTPException(400, f"Configuração incompleta: {', '.join(settings.missing())}")

    upload = _ler_upload(payload.upload_id)
    if not upload:
        raise HTTPException(404, "Lista não encontrada — recarregue o arquivo.")

    resultado = contacts.build_recipients(
        upload["rows"],
        payload.phone_column,
        payload.mapping,
        payload.add_ninth_digit,
        _blocklist(),
    )
    destinatarios = resultado["recipients"]

    if payload.test_phone:
        destinatarios = [_montar_teste(payload.test_phone, destinatarios, payload)]
    elif not destinatarios:
        raise HTTPException(400, "Nenhum destinatário válido para enviar.")

    template = payload.template
    formato = template["header"]["format"]
    if formato in ("IMAGE", "VIDEO", "DOCUMENT") and not payload.header_media:
        raise HTTPException(400, f"Este template tem header de {formato.lower()} — envie o arquivo antes de disparar.")

    campanha = campaign_service.create_campaign(
        template=template,
        recipients=destinatarios,
        header_media=payload.header_media,
        dry_run=payload.dry_run,
        interval=payload.interval,
        rate=payload.rate,
        time_budget_s=payload.time_budget_s,
        label=(
            f"{template['name']} — teste para {destinatarios[0]['phone']}"
            if payload.test_phone
            else f"{template['name']} ({len(destinatarios)} contatos)"
        ),
    )
    campaign_service.start_campaign(campanha["id"])
    return {"campaign_id": campanha["id"], "total": campanha["total"]}


@app.get("/api/campaigns")
def list_campaigns():
    return {"campaigns": campaign_service.list_campaigns()}


@app.get("/api/campaigns/{campaign_id}")
def campaign_status(campaign_id: str):
    status = campaign_service.get_status(campaign_id)
    if not status:
        raise HTTPException(404, "Campanha não encontrada.")
    return status


@app.post("/api/campaigns/{campaign_id}/cancel")
def cancel_campaign(campaign_id: str):
    if not campaign_service.cancel_campaign(campaign_id):
        raise HTTPException(400, "Campanha não está em execução.")
    return {"ok": True}


class RatePayload(BaseModel):
    rate: float


@app.post("/api/campaigns/{campaign_id}/retry")
def retry_failed(campaign_id: str, payload: Optional[RatePayload] = None):
    if not campaign_service.get_campaign(campaign_id):
        raise HTTPException(404, "Campanha não encontrada.")
    nova = campaign_service.retry_failed(campaign_id, rate=payload.rate if payload else None)
    if not nova:
        raise HTTPException(400, "Não há falhas para reenviar.")
    campaign_service.start_campaign(nova["id"])
    return {"campaign_id": nova["id"], "total": nova["total"]}


@app.post("/api/campaigns/{campaign_id}/rate")
def set_campaign_rate(campaign_id: str, payload: RatePayload):
    """Muda a velocidade de uma campanha que já está disparando."""
    nova = campaign_service.set_rate(campaign_id, payload.rate)
    if nova is None:
        raise HTTPException(status_code=404, detail="campanha não está em execução neste processo")
    return {"rate": nova}


@app.post("/api/campaigns/{campaign_id}/resume")
def resume_campaign(campaign_id: str, payload: Optional[RatePayload] = None):
    """Retoma uma campanha interrompida: dispara só para quem ficou pendente."""
    if not campaign_service.get_campaign(campaign_id):
        raise HTTPException(status_code=404, detail="campanha não encontrada")
    nova = campaign_service.resume_campaign(campaign_id, rate=payload.rate if payload else None)
    if not nova:
        raise HTTPException(status_code=400, detail="campanha sem pendentes ou ainda em execução")
    campaign_service.start_campaign(nova["id"])
    return {"campaign_id": nova["id"], "total": nova["total"]}


@app.get("/api/campaigns/{campaign_id}/results.csv")
def download_results(campaign_id: str):
    csv_texto = campaign_service.results_csv(campaign_id)
    if not csv_texto:
        raise HTTPException(404, "Campanha não encontrada.")
    return PlainTextResponse(
        csv_texto,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="resultado_{campaign_id}.csv"'},
    )


def main():
    import os

    try:
        orfas = campaign_service.marcar_interrompidas()
        if orfas:
            print(f"\n  {orfas} campanha(s) do processo anterior marcada(s) como interrompida(s).")
    except db.DbError as erro:
        print(f"\n  ATENÇÃO: {erro}")

    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8777"))
    print(f"\n  DisparoMais rodando em http://{host}:{port}\n")
    # Só abre o navegador quando pedido: reiniciar o app não deve roubar o foco nem
    # empilhar abas (APP_OPEN_BROWSER=1 para voltar ao comportamento antigo).
    if os.getenv("APP_OPEN_BROWSER") == "1":
        try:
            webbrowser.open(f"http://{host}:{port}")
        except Exception:
            pass
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
