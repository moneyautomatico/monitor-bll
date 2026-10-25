"""Catálogo de produtos (marca, modelo, custo, fornecedor, ficha técnica) e formação de preço
(custo + frete + impostos + comissão + despesas + margem -> preço da proposta e lance mínimo)."""
import json
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path

import buscas
import precos

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "catalogo.json"
DIR = PASTA / "catalogo"
TRAVA = threading.Lock()
CAMPOS = ("empresa", "nome", "descricao", "marca", "modelo", "unidade", "custo", "fornecedor", "codigo", "palavras", "obs")
# percentuais sobre o PREÇO DE VENDA (o jeito usual em licitação: markup divisor)
PARAM_PADRAO = {"impostos": "6", "comissao": "0", "despesas": "3", "margem": "15", "margem_min": "3", "frete": "0"}


def _ler():
    try:
        d = json.loads(ARQ.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    d.setdefault("produtos", {})
    d.setdefault("param", {})
    return d


def _gravar(d):
    tmp = ARQ.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(ARQ)


def listar(empresa=None):
    out = [{**v, "id": k} for k, v in _ler()["produtos"].items() if empresa is None or v.get("empresa") == empresa]
    return sorted(out, key=lambda x: (x.get("empresa", ""), x.get("nome", "").lower()))


def ler(pid):
    v = _ler()["produtos"].get(pid)
    return {**v, "id": pid} if v else None


def salvar(pid, dados, arquivo=None):
    with TRAVA:
        d = _ler()
        pid = pid if pid in d["produtos"] else secrets.token_hex(6)
        atual = d["produtos"].get(pid, {"criado": datetime.now().strftime("%Y-%m-%dT%H:%M")})
        atual.update({k: str(dados.get(k, ""))[:2000] for k in CAMPOS if k in dados})
        atual["atualizado"] = datetime.now().strftime("%Y-%m-%dT%H:%M")
        if arquivo and arquivo[1]:
            pasta = DIR / pid
            pasta.mkdir(parents=True, exist_ok=True)
            DIR.chmod(0o700)
            for velho in pasta.glob("*"):
                velho.unlink()
            nome = re.sub(r"[^\w.\- ]", "_", arquivo[0])[:120] or "ficha.pdf"
            (pasta / nome).write_bytes(arquivo[1])
            atual["ficha"] = nome
        d["produtos"][pid] = atual
        _gravar(d)
        return pid


def excluir(pid):
    with TRAVA:
        d = _ler()
        d["produtos"].pop(pid, None)
        _gravar(d)
    pasta = DIR / pid
    if re.fullmatch(r"[0-9a-f]{12}", pid) and pasta.is_dir():
        for f in pasta.glob("*"):
            f.unlink()
        pasta.rmdir()


def ficha(pid):
    p = ler(pid) or {}
    a = DIR / pid / (p.get("ficha") or "")
    return a if p.get("ficha") and a.is_file() else None


def sugerir(empresa, descricao, n=3):
    """Produtos do catálogo parecidos com a descrição do item do edital: [(pontos, produto)]."""
    chaves = precos.palavras(descricao, 6)
    if not chaves:
        return []
    out = []
    for p in listar(empresa):
        texto = buscas.norm(" ".join(str(p.get(k, "")) for k in ("nome", "descricao", "palavras", "marca", "modelo")))
        pts = sum(1 for c in chaves if c in texto)
        if pts >= max(1, min(2, len(chaves))):
            out.append((pts, p))
    return [x for x in sorted(out, key=lambda x: -x[0])][:n]


# ------------------------------------------------------------------ formação de preço
def num(v):
    from propostas import num as _n
    return _n(v)


def param(empresa):
    return {**PARAM_PADRAO, **(_ler()["param"].get(empresa) or {})}


def salvar_param(empresa, valores):
    with TRAVA:
        d = _ler()
        d["param"][empresa] = {k: str(valores.get(k, PARAM_PADRAO.get(k, "")))[:20] for k in PARAM_PADRAO}
        _gravar(d)


def calcular(custo, frete, prm):
    """Preço pelo markup divisor: (custo + frete) / (1 - (impostos + comissão + despesas + margem) / 100).
    Lance mínimo = mesma conta com a margem mínima. Devolve dict ou None se os percentuais passam de 100%."""
    base = num(custo) + num(frete)
    if base <= 0:
        return None
    soma = sum(num(prm.get(k)) for k in ("impostos", "comissao", "despesas"))
    m, mm = num(prm.get("margem")), num(prm.get("margem_min"))
    if soma + max(m, mm) >= 100:
        return None
    preco = base / (1 - (soma + m) / 100)
    minimo = base / (1 - (soma + mm) / 100)
    return {"base": base, "preco": round(preco, 2), "minimo": round(minimo + 0.004, 2), "pct_desp": soma}


def margem_de(preco, custo, frete, prm):
    """Margem líquida real (% do preço) com o preço atual."""
    preco = num(preco)
    if preco <= 0:
        return None
    soma = sum(num(prm.get(k)) for k in ("impostos", "comissao", "despesas"))
    return 100 * (preco - num(custo) - num(frete)) / preco - soma
