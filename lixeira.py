"""Lixeira: o que o usuário exclui (proposta, contrato, ata, cartão de licitação) fica 30 dias aqui e pode ser restaurado."""
import json
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "lixeira.json"
TRAVA = threading.Lock()
DIAS = 30
NOMES = {"proposta": "💰 Proposta", "contrato": "💼 Contrato", "ata": "📑 Ata", "licitacao": "🗂️ Licitação"}


def _ler():
    try:
        return json.loads(ARQ.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _gravar(d):
    tmp = ARQ.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(ARQ)


def guardar(tipo, oid, dados, titulo, por=""):
    limite = (datetime.now() - timedelta(days=DIAS)).strftime("%Y-%m-%dT%H:%M")
    with TRAVA:
        d = {k: v for k, v in _ler().items() if v.get("quando", "") >= limite}   # o que passou de 30 dias some
        d[secrets.token_hex(6)] = {"tipo": tipo, "oid": oid, "dados": {k: v for k, v in dados.items() if k != "id"}, "titulo": str(titulo)[:160],
                                   "empresa": dados.get("empresa", ""), "usuario": dados.get("usuario", ""), "por": por,
                                   "quando": datetime.now().strftime("%Y-%m-%dT%H:%M")}
        _gravar(d)


def listar():
    limite = (datetime.now() - timedelta(days=DIAS)).strftime("%Y-%m-%dT%H:%M")
    return sorted(({**v, "id": k} for k, v in _ler().items() if v.get("quando", "") >= limite), key=lambda x: x["quando"], reverse=True)


def ler(lid):
    v = _ler().get(lid)
    return {**v, "id": lid} if v else None


def remover(lid):
    with TRAVA:
        d = _ler()
        d.pop(lid, None)
        _gravar(d)
