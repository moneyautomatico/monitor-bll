"""Extensão do Chrome "Expert Licitante – Monitor": ponte entre o navegador do licitante e o painel.

Quando a plataforma bloqueia o servidor (ex.: Licitanet responde 403 para o IP do servidor), o licitante fica logado
no Chrome do próprio computador e a extensão SÓ LÊ o que já está na tela (lista de processos, chat da sala) e manda
para o painel. Nada é clicado nem enviado na plataforma.

- Cada empresa tem um código de conexão (só o resumo SHA-256 fica gravado; o código aparece uma vez no painel).
- O painel grava os eventos numa fila da empresa; o monitor da empresa consome a fila e manda os alertas.
"""
import fcntl
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
import zipfile
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
EMPRESAS = PASTA / "empresas"
FONTE = PASTA / "extensao"           # arquivos da extensão (manifest, scripts, ícones)
TIPOS = {"ping", "estado", "processos", "mensagens", "diagnostico"}
MAX_MSGS = 300
VERSAO_MIN = "1.0.0"


def _pasta(eid):
    p = EMPRESAS / eid
    p.mkdir(parents=True, exist_ok=True)
    return p


def _hash(codigo):
    return hashlib.sha256(("expert-extensao|" + codigo).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ código de conexão
def gerar_codigo(eid, login):
    """Novo código (invalida o anterior). Devolve o código em texto – mostrar só uma vez."""
    codigo = "EL-" + secrets.token_urlsafe(24)
    a = _pasta(eid) / "extensao.json"
    a.write_text(json.dumps({"hash": _hash(codigo), "criado": datetime.now().strftime("%d/%m/%Y %H:%M"), "por": login}),
                 encoding="utf-8")
    os.chmod(a, 0o600)
    return codigo


def tem_codigo(eid):
    return (EMPRESAS / eid / "extensao.json").exists()


def info_codigo(eid):
    try:
        return json.loads((EMPRESAS / eid / "extensao.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def revogar(eid):
    (EMPRESAS / eid / "extensao.json").unlink(missing_ok=True)


def empresa_do_codigo(codigo):
    """eid dono do código, ou None. Compara o resumo de todas (poucas empresas) em tempo constante."""
    if not codigo or not codigo.startswith("EL-") or len(codigo) > 80:
        return None
    h = _hash(codigo)
    achou = None
    for a in EMPRESAS.glob("*/extensao.json") if EMPRESAS.exists() else []:
        try:
            if hmac.compare_digest(json.loads(a.read_text(encoding="utf-8")).get("hash", ""), h):
                achou = a.parent.name
        except Exception:  # noqa: BLE001
            continue
    return achou


# ------------------------------------------------------------------ fila de eventos (painel grava, monitor consome)
def _limpar_texto(v, n):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v or ""))[:n]


def validar_evento(ev):
    """Normaliza o que veio do navegador: só os campos esperados e com limites."""
    if not isinstance(ev, dict) or ev.get("tipo") not in TIPOS:
        return None
    out = {"tipo": ev["tipo"], "plataforma": _limpar_texto(ev.get("plataforma"), 20) or "licitanet",
           "url": _limpar_texto(ev.get("url"), 300), "versao": _limpar_texto(ev.get("versao"), 12)}
    if ev["tipo"] == "estado":
        out["estado"] = _limpar_texto(ev.get("estado"), 30)
    elif ev["tipo"] == "processos":
        out["lista"] = [{"processo": _limpar_texto(p.get("processo"), 80), "promotor": _limpar_texto(p.get("promotor"), 200),
                         "situacao": _limpar_texto(p.get("situacao"), 80), "disputa": _limpar_texto(p.get("disputa"), 40),
                         "sala": _limpar_texto(p.get("sala"), 20)}
                        for p in (ev.get("lista") or [])[:500] if isinstance(p, dict)]
    elif ev["tipo"] == "mensagens":
        out["sala"] = _limpar_texto(ev.get("sala"), 20)
        out["escopo"] = _limpar_texto(ev.get("escopo"), 120)
        out["cabecalho"] = _limpar_texto(ev.get("cabecalho"), 600)
        out["mensagens"] = [{"id": _limpar_texto(m.get("id"), 40), "cab": _limpar_texto(m.get("cab"), 200),
                             "msg": _limpar_texto(m.get("msg"), 4000)}
                            for m in (ev.get("mensagens") or [])[:MAX_MSGS] if isinstance(m, dict) and m.get("msg")]
    elif ev["tipo"] == "diagnostico":
        est = ev.get("estrutura") if isinstance(ev.get("estrutura"), dict) else {}
        out["estrutura"] = est if len(json.dumps(est, ensure_ascii=False)) <= 20000 else {"aviso": "estrutura grande demais"}
    return out


def registrar(eid, ev):
    """Painel: grava o evento na fila e atualiza a situação da extensão."""
    ev["recebido"] = time.time()
    if ev["tipo"] == "diagnostico":
        a = _pasta(eid) / "extensao_diag.jsonl"
        with open(a, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        os.chmod(a, 0o600)
    elif ev["tipo"] != "ping":
        a = _pasta(eid) / "extensao_fila.jsonl"
        with open(a, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
        os.chmod(a, 0o600)
    st = situacao(eid)
    st["ultima"] = time.time()
    st["versao"] = ev.get("versao") or st.get("versao", "")
    if ev["tipo"] == "estado":
        st["estado"] = ev.get("estado", "")
    elif ev["tipo"] in ("processos", "mensagens"):
        st["estado"] = "lendo"
        st["ultimo_" + ev["tipo"]] = time.time()
    if ev["tipo"] == "processos":
        st["n_processos"] = len(ev.get("lista", []))
        salas = st.setdefault("salas", {})
        for p in ev.get("lista", []):
            if p.get("sala"):
                salas[p["sala"]] = {"processo": p["processo"], "promotor": p["promotor"]}
        if len(salas) > 2000:
            st["salas"] = dict(list(salas.items())[-2000:])
    _gravar_situacao(eid, st)


def situacao(eid):
    try:
        return json.loads((EMPRESAS / eid / "extensao_status.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _gravar_situacao(eid, st):
    a = _pasta(eid) / "extensao_status.json"
    tmp = a.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(a)


def consumir(eid):
    """Monitor: pega todos os eventos pendentes da fila (e esvazia)."""
    a = EMPRESAS / eid / "extensao_fila.jsonl"
    if not a.exists() or a.stat().st_size == 0:
        return []
    with open(a, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        linhas = f.read().splitlines()
        f.seek(0)
        f.truncate()
        fcntl.flock(f, fcntl.LOCK_UN)
    out = []
    for ln in linhas:
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            continue
    return out


def resumo(eid):
    """Texto curto para o painel."""
    if not tem_codigo(eid):
        return "⚪ extensão não conectada"
    st = situacao(eid)
    if not st.get("ultima"):
        return "🟡 código gerado – aguardando a extensão"
    min_ = int((time.time() - st["ultima"]) // 60)
    quando = "agora" if min_ < 1 else f"há {min_} min" if min_ < 120 else f"há {min_ // 60} h"
    if st.get("estado") == "deslogado":
        return f"🔴 a Licitanet saiu do login no seu Chrome ({quando}) – entre de novo"
    return ("🟢" if min_ < 15 else "🔴") + f" extensão ativa · último contato {quando}"


# ------------------------------------------------------------------ pacote para baixar (.zip)
def pacote_zip(servidor):
    """A extensão em .zip, com o endereço do painel já preenchido."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arq in sorted(FONTE.rglob("*")):
            if arq.is_file():
                dados = arq.read_bytes()
                if arq.name == "config.js":
                    dados = dados.replace(b"__SERVIDOR__", servidor.encode("utf-8"))
                z.writestr("expert-licitante-extensao/" + str(arq.relative_to(FONTE)), dados)
    return buf.getvalue()
