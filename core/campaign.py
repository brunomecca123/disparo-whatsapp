"""Execução da campanha com estado no Supabase.

Diferença central para a versão que guardava tudo em memória e em arquivos JSON: aqui
nada depende de o processo continuar vivo. Uma execução pega os pendentes do banco,
envia o que couber no orçamento de tempo e grava o resultado. Se ela morrer no meio —
deploy, timeout da função, app encerrado —, os pendentes continuam pendentes e a
próxima execução retoma de onde parou.
"""

import hashlib
import math
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

from core import db, meta_api
from core.settings import SERVERLESS

# A Meta entrega 80 mensagens/segundo por número no nível STANDARD. Trabalhamos bem abaixo
# disso: o teto da interface é conservador e o motor freia sozinho se ela reclamar (130429).
TAXA_PADRAO = 8.0
TAXA_MAXIMA = 30.0
TAXA_MINIMA = 0.5
MAX_WORKERS = 24
TENTATIVAS_RITMO = 4

# Teto de tempo de uma execução. Em serverless quem manda é a plataforma (Vercel Hobby:
# 300s); a margem existe para gravar o progresso antes de sermos cortados.
ORCAMENTO_PADRAO_S = float(os.getenv("TIME_BUDGET_S") or 0) or None
MARGEM_GRAVACAO_S = 8.0

LOTE_GRAVACAO = 100   # resultados por POST
LOTE_LEITURA = 2000   # pendentes lidos por vez

# Na Vercel a função é congelada assim que responde: uma thread de fundo morre junto.
# O envio então roda dentro da requisição e, quando o tempo acaba, a invocação chama a si
# mesma para continuar de onde parou — sem depender do navegador ficar aberto.
HEARTBEAT_TOLERANCIA_S = 120.0  # sem batimento por este tempo, a execução é dada como morta

_ritmos: Dict[str, "_Ritmo"] = {}
_cancelados = set()
_lock = threading.Lock()


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


# -------------------------------------------------------------------- ritmo


class _Ritmo:
    """Espaça os envios em mensagens/segundo e desacelera sozinho quando a Meta reclama.

    O ritmo é global à campanha: as threads pedem vaga aqui antes de cada requisição,
    então a taxa total sai igual à configurada, independente de quantos workers existem.
    """

    def __init__(self, taxa: float):
        self.base = max(0.01, float(taxa))
        self.taxa = self.base
        self.proximo = time.monotonic()
        self.lock = threading.Lock()
        self.freios = 0

    def vaga(self):
        with self.lock:
            alvo = max(self.proximo, time.monotonic())
            self.proximo = alvo + 1.0 / self.taxa
        espera = alvo - time.monotonic()
        if espera > 0:
            time.sleep(espera)

    def frear(self, pausa: float = 2.0) -> float:
        with self.lock:
            self.taxa = max(TAXA_MINIMA, self.taxa / 2)
            self.proximo = max(self.proximo, time.monotonic() + pausa)
            self.freios += 1
            return self.taxa

    def acelerar(self):
        with self.lock:
            if self.taxa < self.base:
                self.taxa = min(self.base, self.taxa * 1.2)

    def ajustar(self, taxa: float):
        with self.lock:
            self.base = max(0.01, min(float(taxa), TAXA_MAXIMA))
            self.taxa = self.base
            self.proximo = min(self.proximo, time.monotonic() + 1.0 / self.taxa)


def _taxa_efetiva(rate=None, interval=None) -> float:
    if rate:
        return max(0.01, min(float(rate), TAXA_MAXIMA))
    if interval and float(interval) > 0:
        return max(0.01, min(1.0 / float(interval), TAXA_MAXIMA))
    return TAXA_PADRAO


def _workers_para(taxa: float) -> int:
    """Requisição à Meta leva ~0,4s; para sustentar N msg/s são precisos ~N/2 envios em voo."""
    return max(1, min(MAX_WORKERS, math.ceil(taxa * 0.6)))


# ------------------------------------------------------------------ criação


def create_campaign(
    template: Dict,
    recipients: List[Dict],
    header_media: Optional[Dict] = None,
    dry_run: bool = False,
    interval: Optional[float] = None,
    label: str = "",
    parent_id: Optional[str] = None,
    rate: Optional[float] = None,
    time_budget_s: Optional[float] = None,
) -> Dict:
    campaign_id = uuid.uuid4().hex[:12]
    campanha = {
        "id": campaign_id,
        "label": label or template.get("name", "campanha"),
        "template": {"name": template.get("name"), "language": template.get("language")},
        "template_full": template,
        "header_media": header_media,
        "dry_run": dry_run,
        "rate": _taxa_efetiva(rate, interval),
        "interval_s": float(interval) if interval else None,
        "time_budget_s": float(time_budget_s) if time_budget_s else None,
        "status": "pending",
        "total": len(recipients),
        "sent": 0,
        "failed": 0,
        "processed": 0,
        "parent_id": parent_id,
    }
    db.inserir_campanha(campanha)
    db.inserir_destinatarios(campaign_id, recipients)
    return campanha


# ----------------------------------------------------------------- execução


def _tempo_esgotado(estado: Dict) -> bool:
    """Orçamento de tempo da execução — o que sobra fica pendente para a próxima rodada."""
    limite = estado.get("limite")
    if limite and time.monotonic() >= limite:
        estado["estourou"] = True
        return True
    return False


def _parar(estado: Dict) -> bool:
    return estado["parar"] or _tempo_esgotado(estado)


def _resultado(campanha: Dict, item: Dict, desfecho: Dict) -> Dict:
    """Monta a linha do upsert em /recipients.

    Vai completa de propósito: o upsert do PostgREST é um INSERT ... ON CONFLICT, e o
    Postgres valida os NOT NULL ao montar a tupla, antes de detectar o conflito. Mandar
    só as colunas do desfecho faria a gravação estourar em campaign_id/idx/phone.
    """
    return {
        "id": item["id"],
        "campaign_id": campanha["id"],
        "idx": item["idx"],
        "phone": item["phone"],
        "original": item.get("original"),
        "vars": item.get("vars") or {},
        "sent_at": _agora(),
        **desfecho,
    }


def _enviar_item(campanha: Dict, item: Dict, ritmo: _Ritmo, estado: Dict) -> Optional[Dict]:
    """Envia um destinatário respeitando o ritmo, com re-tentativa quando é rate limit.

    Devolve a linha a gravar no banco, ou None se a execução acabou antes da vez dele —
    nesse caso o contato continua pendente e entra na próxima rodada.
    """
    template = campanha["template_full"]
    for tentativa in range(1, TENTATIVAS_RITMO + 1):
        if _parar(estado):
            return None
        ritmo.vaga()
        if _parar(estado):
            return None
        try:
            componentes = meta_api.build_components(
                template, item.get("vars") or {}, campanha.get("header_media")
            )
            if campanha["dry_run"]:
                desfecho = {"status": "dry_run", "message_id": "SIMULADO", "error": None}
            else:
                resposta = meta_api.send_template(
                    item["phone"], template["name"], template["language"], componentes
                )
                desfecho = {"status": "sent", "message_id": resposta["message_id"], "error": None}
            ritmo.acelerar()
            return _resultado(campanha, item, desfecho)
        except meta_api.MetaApiError as erro:
            if erro.rate_limited and tentativa < TENTATIVAS_RITMO:
                ritmo.frear(pausa=2.0 * tentativa)
                continue  # não conta como falha: o mesmo contato será tentado de novo
            return _resultado(campanha, item, {
                "status": "failed", "message_id": None,
                "error": f"[{erro.code}] {erro.message}" if erro.code else erro.message,
            })
        except Exception as erro:  # falha de rede, timeout etc.
            return _resultado(campanha, item, {
                "status": "failed", "message_id": None, "error": str(erro),
            })
    return None


def _blocos(itens: List, tamanho: int):
    for inicio in range(0, len(itens), tamanho):
        yield itens[inicio:inicio + tamanho]


def executar(campaign_id: str, time_budget_s: Optional[float] = None) -> Optional[Dict]:
    """Envia o que couber no orçamento de tempo. Pode ser chamada quantas vezes for preciso."""
    limite_batimento = (datetime.now(timezone.utc) - timedelta(seconds=HEARTBEAT_TOLERANCIA_S)).isoformat()
    if not db.assumir_campanha(campaign_id, limite_batimento):
        # Ou já terminou, ou outra execução está com ela: sair sem enviar nada é o certo.
        return db.buscar_campanha(campaign_id)

    campanha = db.buscar_campanha(campaign_id)
    if not campanha:
        raise ValueError("campanha não encontrada")

    orcamento = time_budget_s or campanha.get("time_budget_s") or ORCAMENTO_PADRAO_S
    taxa = _taxa_efetiva(campanha.get("rate"), campanha.get("interval_s"))
    ritmo = _Ritmo(taxa)
    with _lock:
        _ritmos[campaign_id] = ritmo
        _cancelados.discard(campaign_id)

    # Folga de workers: permite acelerar no meio do disparo sem recriar o pool.
    workers = max(_workers_para(taxa), min(MAX_WORKERS, _workers_para(TAXA_PADRAO) * 2))
    inicio = time.monotonic()
    estado = {
        "parar": False,
        "estourou": False,
        "limite": inicio + float(orcamento) - MARGEM_GRAVACAO_S if orcamento else None,
    }

    base_sent = campanha["sent"]
    base_failed = campanha["failed"]
    base_processed = campanha["processed"]
    db.atualizar_campanha(campaign_id, {
        "stop_reason": None, "workers": workers, "rate": taxa,
        "started_at": campanha.get("started_at") or _agora(), "finished_at": None,
    })

    enviados = falhas = processados = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while not _parar(estado):
                pendentes = db.proximos_pendentes(campaign_id, LOTE_LEITURA)
                if not pendentes:
                    break
                for bloco in _blocos(pendentes, LOTE_GRAVACAO):
                    if campaign_id in _cancelados:
                        estado["parar"] = True
                    if _parar(estado):
                        break
                    resultados = [r for r in pool.map(
                        lambda item: _enviar_item(campanha, item, ritmo, estado), bloco
                    ) if r]
                    if not resultados:
                        continue
                    db.gravar_resultados(resultados)
                    enviados += sum(1 for r in resultados if r["status"] in ("sent", "dry_run"))
                    falhas += sum(1 for r in resultados if r["status"] == "failed")
                    processados += len(resultados)
                    decorrido = max(0.001, time.monotonic() - inicio)
                    # O retorno traz a linha já gravada: é assim que esta execução fica
                    # sabendo de um cancelamento ou de uma troca de ritmo feita em outro
                    # processo (na Vercel, quem clica em cancelar cai em outra invocação).
                    atual = db.atualizar_campanha(campaign_id, {
                        "sent": base_sent + enviados,
                        "failed": base_failed + falhas,
                        "processed": base_processed + processados,
                        "rate_real": round(processados / decorrido, 2),
                        "freios": (campanha.get("freios") or 0) + ritmo.freios,
                        "heartbeat_at": "now",
                    }, retornar=True) or {}
                    if atual.get("status") == "cancelled":
                        estado["parar"] = True
                    elif atual.get("rate") is not None and float(atual["rate"]) != ritmo.base:
                        ritmo.ajustar(float(atual["rate"]))
    finally:
        with _lock:
            _ritmos.pop(campaign_id, None)
            _cancelados.discard(campaign_id)

    restantes = db.contar_pendentes(campaign_id)
    if estado["parar"]:
        status, motivo = "cancelled", "usuario"
    elif restantes:
        # Sobrou gente na fila: quem acabou foi a execução, não a campanha.
        status, motivo = "paused", "tempo"
    else:
        status, motivo = "finished", None

    decorrido = max(0.001, time.monotonic() - inicio)
    db.atualizar_campanha(campaign_id, {
        "status": status, "stop_reason": motivo,
        "sent": base_sent + enviados,
        "failed": base_failed + falhas,
        "processed": base_processed + processados,
        "freios": (campanha.get("freios") or 0) + ritmo.freios,
        "rate_real": round(processados / decorrido, 2),
        "finished_at": _agora(),
        # Solta a trava: sem batimento, a próxima invocação pode assumir a fila que sobrou.
        "heartbeat_at": None,
    })

    # Estado final gravado antes de chamar a próxima invocação: assim as duas nunca se
    # sobrepõem — quem entra encontra a campanha liberada e o progresso já salvo.
    if status == "paused" and SERVERLESS:
        _agendar_execucao(campaign_id, time_budget_s)
    return db.buscar_campanha(campaign_id)


# ------------------------------------------------- execução em outra invocação


def token_interno() -> str:
    """Segredo compartilhado entre as invocações, derivado de uma chave que já existe.

    Evita mais uma variável de ambiente para configurar, e o hash não permite voltar à
    service key. Só serve para a função provar a si mesma que a chamada veio dela.
    """
    chave = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
    return hashlib.sha256(f"disparo-interno:{chave}".encode()).hexdigest()


def _url_base() -> Optional[str]:
    if os.getenv("APP_URL"):
        return os.getenv("APP_URL").rstrip("/")
    # A URL do próprio deployment: a invocação seguinte roda exatamente este mesmo código.
    host = os.getenv("VERCEL_URL") or os.getenv("VERCEL_PROJECT_PRODUCTION_URL")
    return f"https://{host}" if host else None


def _agendar_execucao(campaign_id: str, time_budget_s: Optional[float] = None) -> bool:
    """Pede a outra invocação que continue o disparo, sem esperar ela terminar."""
    base = _url_base()
    if not base:
        return False
    try:
        # Sessão sem retry de propósito: uma re-tentativa aqui poderia pôr duas invocações
        # na mesma campanha. A trava do banco barraria a segunda, mas nem chegamos lá.
        cabecalhos = {"x-internal-token": token_interno()}
        # Deployment Protection (padrão em preview) barraria a chamada da função para ela
        # mesma; o segredo de automação da própria Vercel é o jeito oficial de passar.
        bypass = os.getenv("VERCEL_AUTOMATION_BYPASS_SECRET")
        if bypass:
            cabecalhos["x-vercel-protection-bypass"] = bypass
            cabecalhos["x-vercel-set-bypass-cookie"] = "false"
        requests.post(
            f"{base}/api/internal/run",
            json={"campaign_id": campaign_id, "time_budget_s": time_budget_s},
            headers=cabecalhos,
            timeout=(5, 1.5),
        )
        return True
    except requests.exceptions.ReadTimeout:
        return True  # esperado: o pedido chegou, a resposta é que não interessa
    except Exception:
        return False


def start_campaign(campaign_id: str, time_budget_s: Optional[float] = None):
    """Local: thread, para a interface não travar. Vercel: outra invocação assume o envio."""
    if SERVERLESS and _agendar_execucao(campaign_id, time_budget_s):
        return
    threading.Thread(target=executar, args=(campaign_id, time_budget_s), daemon=True).start()


# ------------------------------------------------------------------ controle


def set_rate(campaign_id: str, taxa: float) -> Optional[float]:
    """Muda a velocidade. Vale na hora se a campanha roda aqui; senão, no próximo lote."""
    if not db.buscar_campanha(campaign_id):
        return None
    nova = max(0.1, min(float(taxa), TAXA_MAXIMA))
    db.atualizar_campanha(campaign_id, {"rate": nova})
    ritmo = _ritmos.get(campaign_id)
    if ritmo:
        ritmo.ajustar(nova)
    return nova


def cancel_campaign(campaign_id: str) -> bool:
    campanha = db.buscar_campanha(campaign_id)
    if not campanha or campanha["status"] not in ("running", "pending"):
        return False
    # A execução em andamento vê isso no próximo bloco e encerra gravando o que já fez.
    _cancelados.add(campaign_id)
    if campaign_id not in _ritmos:
        db.atualizar_campanha(campaign_id, {
            "status": "cancelled", "stop_reason": "usuario", "finished_at": _agora(),
        })
    return True


# ------------------------------------------------------------------- leitura


def get_campaign(campaign_id: str) -> Optional[Dict]:
    return db.buscar_campanha(campaign_id)


def get_status(campaign_id: str) -> Optional[Dict]:
    campanha = db.buscar_campanha(campaign_id)
    if not campanha:
        return None
    falhas = db.destinatarios_por_status(campaign_id, "failed", limite=100)
    taxa = float(campanha["rate"]) if campanha.get("rate") is not None else None
    return {
        "id": campanha["id"],
        "label": campanha["label"],
        "status": campanha["status"],
        "stop_reason": campanha.get("stop_reason"),
        "total": campanha["total"],
        "processed": campanha["processed"],
        "sent": campanha["sent"],
        "failed": campanha["failed"],
        "pending": campanha.get("pending", 0),
        "dry_run": campanha["dry_run"],
        "template": campanha["template"],
        "rate": taxa,
        "rate_atual": taxa,
        "rate_real": float(campanha["rate_real"]) if campanha.get("rate_real") is not None else None,
        "workers": campanha.get("workers"),
        "freios": campanha.get("freios") or 0,
        "interval": campanha.get("interval_s"),
        "time_budget_s": campanha.get("time_budget_s"),
        "duracao_s": campanha.get("duracao_s"),
        "uso_meta": meta_api.ultimo_uso(),
        "created_at": campanha.get("created_at"),
        "started_at": campanha.get("started_at"),
        "finished_at": campanha.get("finished_at"),
        "interrupted_at": campanha.get("interrupted_at"),
        "parent_id": campanha.get("parent_id"),
        "resumed_by": campanha.get("resumed_by"),
        "can_resume": (campanha.get("pending", 0) > 0
                       and campanha["status"] not in ("running", "pending")
                       and not campanha.get("resumed_by")),
        "errors": [{"phone": f["phone"], "error": f["error"]} for f in falhas],
        "error_count": len(falhas),
    }


def list_campaigns(limite: int = 30) -> List[Dict]:
    return [
        {
            "id": c["id"], "label": c["label"], "template": c["template"],
            "status": c["status"], "stop_reason": c.get("stop_reason"),
            "total": c["total"], "sent": c["sent"], "failed": c["failed"],
            "pending": c.get("pending", 0), "dry_run": c["dry_run"],
            "created_at": c.get("created_at"), "finished_at": c.get("finished_at"),
            "duracao_s": c.get("duracao_s"),
            "parent_id": c.get("parent_id"), "resumed_by": c.get("resumed_by"),
        }
        for c in db.listar_campanhas(limite)
    ]


# ------------------------------------------------------- retomada e reenvio


def _como_destinatarios(linhas: List[Dict]) -> List[Dict]:
    return [
        {"phone": l["phone"], "original": l.get("original", ""), "values": l.get("vars") or {}}
        for l in linhas
    ]


def _recriar(original: Dict, destinatarios: List[Dict], sufixo: str,
             rate: Optional[float] = None) -> Optional[Dict]:
    if not destinatarios:
        return None
    base = original["label"].split(" · retomada")[0].split(" — reenvio")[0]
    return create_campaign(
        template=original["template_full"],
        recipients=destinatarios,
        header_media=original.get("header_media"),
        dry_run=original["dry_run"],
        rate=rate or (float(original["rate"]) if original.get("rate") else None),
        time_budget_s=original.get("time_budget_s"),
        label=f"{base} {sufixo}",
        parent_id=original["id"],
    )


def pending_recipients(campaign_id: str) -> List[Dict]:
    return _como_destinatarios(db.destinatarios_por_status(campaign_id, "pending", limite=100000))


def failed_recipients(campaign_id: str) -> List[Dict]:
    return _como_destinatarios(db.destinatarios_por_status(campaign_id, "failed", limite=100000))


def resume_campaign(campaign_id: str, rate: Optional[float] = None) -> Optional[Dict]:
    """Cria uma campanha nova só com os pendentes. Não reenvia para quem já recebeu."""
    original = db.buscar_campanha(campaign_id)
    if not original or original["status"] in ("running", "pending") or original.get("resumed_by"):
        return None
    pendentes = pending_recipients(campaign_id)
    nova = _recriar(original, pendentes, f"· retomada ({len(pendentes)} pendentes)", rate)
    if nova:
        db.atualizar_campanha(campaign_id, {"resumed_by": nova["id"]})
    return nova


def retry_failed(campaign_id: str, rate: Optional[float] = None) -> Optional[Dict]:
    original = db.buscar_campanha(campaign_id)
    if not original:
        return None
    falhas = failed_recipients(campaign_id)
    return _recriar(original, falhas, f"— reenvio de {len(falhas)} falhas", rate)


def marcar_interrompidas() -> int:
    """Campanha 'running' sem execução viva é de um processo que morreu.

    Em serverless isso acontece quando a função é cortada; localmente, quando o app é
    encerrado. Nos dois casos os pendentes seguem no banco, prontos para retomar.
    """
    orfas = [c for c in db.campanhas_por_status(["running"]) if c["id"] not in _ritmos]
    for campanha in orfas:
        db.atualizar_campanha(campanha["id"], {
            "status": "interrupted", "stop_reason": "processo", "interrupted_at": _agora(),
        })
    return len(orfas)


def results_csv(campaign_id: str) -> str:
    import csv
    import io

    buffer = io.StringIO()
    escritor = csv.writer(buffer)
    escritor.writerow(["telefone", "original", "status", "message_id", "erro"])
    for item in db.todos_destinatarios(campaign_id):
        escritor.writerow([
            item["phone"], item.get("original", ""), item["status"],
            item.get("message_id") or "", item.get("error") or "",
        ])
    return buffer.getvalue()
