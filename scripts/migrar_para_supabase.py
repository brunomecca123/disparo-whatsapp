#!/usr/bin/env python3
"""Leva o histórico de campaigns/*.json para o Supabase.

Roda uma vez, depois de aplicar supabase/schema.sql. É idempotente: campanha que já
existe no banco é pulada, então dá para rodar de novo sem duplicar nada.

    ./venv/bin/python scripts/migrar_para_supabase.py [--dry-run]
"""

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from core import db  # noqa: E402

PASTA = RAIZ / "campaigns"


def converter(dados: dict) -> dict:
    """Campos do JSON antigo para as colunas da tabela campaigns."""
    return {
        "id": dados["id"],
        "label": dados.get("label") or dados.get("template", {}).get("name", "campanha"),
        "template": dados.get("template") or {},
        "template_full": dados.get("template_full") or {},
        "header_media": dados.get("header_media"),
        "dry_run": bool(dados.get("dry_run")),
        "rate": dados.get("rate"),
        "rate_real": dados.get("rate_real"),
        "interval_s": dados.get("interval"),
        "time_budget_s": dados.get("time_budget_s"),
        "workers": dados.get("workers"),
        "freios": dados.get("freios") or 0,
        "status": dados.get("status", "finished"),
        "stop_reason": dados.get("stop_reason"),
        "total": dados.get("total") or len(dados.get("results", [])),
        "sent": dados.get("sent") or 0,
        "failed": dados.get("failed") or 0,
        "processed": dados.get("processed") or 0,
        "parent_id": dados.get("parent_id"),
        "resumed_by": dados.get("resumed_by"),
        "created_at": dados.get("created_at"),
        "started_at": dados.get("started_at"),
        "finished_at": dados.get("finished_at"),
        "interrupted_at": dados.get("interrupted_at"),
    }


def main():
    simulacao = "--dry-run" in sys.argv
    arquivos = sorted(PASTA.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not arquivos:
        print("Nada em campaigns/ para migrar.")
        return

    # parent_id aponta para outra campanha: mães antes das filhas evita erro de FK.
    campanhas = []
    for arquivo in arquivos:
        try:
            campanhas.append(json.loads(arquivo.read_text(encoding="utf-8")))
        except Exception as erro:
            print(f"  ignorado {arquivo.name}: {erro}")
    campanhas.sort(key=lambda c: (c.get("parent_id") is not None, c.get("created_at") or ""))

    migradas = puladas = 0
    for dados in campanhas:
        if db.buscar_campanha(dados["id"]):
            puladas += 1
            continue
        resultados = dados.get("results", [])
        print(f"  {dados['id']} · {dados.get('label', '')[:50]} · {len(resultados)} contatos")
        if simulacao:
            migradas += 1
            continue

        db.inserir_campanha(converter(dados))
        # Preserva o desfecho de cada contato (quem recebeu não pode ser reenviado).
        for inicio in range(0, len(resultados), db.LOTE_INSERCAO):
            bloco = [
                {
                    "campaign_id": dados["id"],
                    "idx": inicio + posicao,
                    "phone": item["phone"],
                    "original": item.get("original", ""),
                    "vars": item.get("values", {}),
                    "status": item.get("status", "pending"),
                    "message_id": item.get("message_id"),
                    "error": item.get("error"),
                }
                for posicao, item in enumerate(resultados[inicio:inicio + db.LOTE_INSERCAO])
            ]
            db._pedir("POST", "/recipients", json=bloco,
                      extra_headers={"Prefer": "return=minimal"}, timeout=120)
        migradas += 1

    print(f"\n{migradas} campanha(s) migrada(s), {puladas} já estavam no banco"
          f"{' (simulação, nada gravado)' if simulacao else ''}.")


if __name__ == "__main__":
    main()
