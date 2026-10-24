"""Preço de referência no PNCP: quanto outros órgãos pagaram (valor homologado) e estimaram pelo mesmo item.
Busca licitações já encerradas com as palavras principais da descrição, abre os itens, pega os parecidos e lê o resultado
(valor unitário homologado e fornecedor). Só leitura, API pública. Guarda 7 dias em cache."""
import json
import re
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import buscas

PASTA = Path(__file__).resolve().parent
ARQ_CACHE = PASTA / "precos_cache.json"
TRAVA = threading.Lock()
VALIDADE_CACHE = 7 * 86400
PARADAS = set("""a o e de da do das dos em no na nos nas para por com sem tipo cor tamanho medindo medida aproximadamente
cada unidade und un uni ref referencia marca modelo similar ou equivalente qualidade primeira linha conforme especificacao
produto material item minimo maximo contendo deve ser sera tendo que mm cm ml kg gr g lt l x embalagem embalado plastica
plastico pacote caixa frasco galao fardo rolo pct cx fr gl novo original lacrado validade composicao aplicacao uso""".split())


def _cache():
    try:
        return json.loads(ARQ_CACHE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _gravar_cache(c):
    tmp = ARQ_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ARQ_CACHE)


def palavras(desc, n=5):
    """Palavras que identificam o item (sem medidas, marcas genéricas e palavras de ligação)."""
    out = []
    for p in re.findall(r"[a-z0-9]+", buscas.norm(desc)):
        if p in PARADAS or len(p) < 3 or p.isdigit():
            continue
        if p not in out:
            out.append(p)
    return out[:n]


def parecido(desc_alvo, desc_outro, chaves):
    d = buscas.norm(desc_outro)
    achadas = sum(1 for p in chaves if p in d)
    return achadas >= max(2, int(len(chaves) * 0.6 + 0.5)) if len(chaves) >= 2 else achadas == len(chaves)


def _resultado_item(cnpj, ano, seq, numero):
    try:
        res = buscas._get(f"https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens/{numero}/resultados", timeout=20)
    except Exception:  # noqa: BLE001
        return []
    return res if isinstance(res, list) else []


def consultar(descricao, unidade="", maximo_compras=40):
    """Devolve {"chaves", "pagos": [...], "estimados": [...], "resumo_pago", "resumo_estimado", "quando"}."""
    chave_cache = buscas.norm(descricao)[:160] + "|" + buscas.norm(unidade)
    with TRAVA:
        c = _cache().get(chave_cache)
    if c and time.time() - c.get("ts", 0) < VALIDADE_CACHE:
        return c
    chaves = palavras(descricao)
    if not chaves:
        return {"chaves": [], "pagos": [], "estimados": [], "erro": "descrição curta demais para buscar"}
    compras = []
    for consulta in (" ".join(chaves[:4]), " ".join(chaves[:2])):
        for status in ("encerradas", ""):
            try:
                its, _, _ = buscas.buscar({"q": consulta, "status": status}, 1, 40)
            except Exception:  # noqa: BLE001
                its = []
            for it in its:
                if it.get("id") not in {x.get("id") for x in compras}:
                    compras.append(it)
            if len(compras) >= maximo_compras:
                break
        if len(compras) >= 15:
            break
    compras = compras[:maximo_compras]

    def itens_da_compra(it):
        cnpj, ano, seq = buscas.ref(it)
        try:
            lista = buscas.listar_itens(cnpj, ano, seq, maximo=300)
        except Exception:  # noqa: BLE001
            lista = []
        return [(it, x) for x in lista if parecido(descricao, x.get("descricao"), chaves)]

    candidatos = []
    with ThreadPoolExecutor(8) as ex:
        for achados in ex.map(itens_da_compra, compras):
            candidatos += achados
    candidatos = candidatos[:60]
    estimados, pagos = [], []
    for it, x in candidatos:
        if x.get("valorUnitarioEstimado"):
            estimados.append({"valor": float(x["valorUnitarioEstimado"]), "orgao": it.get("orgao_nome", ""), "uf": it.get("uf", ""),
                              "data": (it.get("data_publicacao_pncp") or "")[:10], "unidade": x.get("unidadeMedida", ""),
                              "desc": (x.get("descricao") or "")[:200], "link": buscas.link_pncp(it)})

    def resultado(par):
        it, x = par
        if not x.get("temResultado"):
            return []
        cnpj, ano, seq = buscas.ref(it)
        out = []
        for r in _resultado_item(cnpj, ano, seq, x.get("numeroItem")):
            v = r.get("valorUnitarioHomologado")
            if v:
                out.append({"valor": float(v), "orgao": it.get("orgao_nome", ""), "uf": it.get("uf", ""),
                            "data": (r.get("dataResultado") or r.get("dataInclusao") or "")[:10],
                            "fornecedor": r.get("nomeRazaoSocialFornecedor", ""), "unidade": x.get("unidadeMedida", ""),
                            "desc": (x.get("descricao") or "")[:200], "link": buscas.link_pncp(it)})
        return out

    with ThreadPoolExecutor(8) as ex:
        for r in ex.map(resultado, candidatos):
            pagos += r
    res = {"chaves": chaves, "pagos": _sem_extremos(pagos), "estimados": _sem_extremos(estimados), "ts": time.time(),
           "quando": datetime.now().strftime("%d/%m/%Y %H:%M"), "compras": len(compras)}
    res["resumo_pago"], res["resumo_estimado"] = resumo(res["pagos"]), resumo(res["estimados"])
    if res["pagos"] or res["estimados"]:
        with TRAVA:
            c = _cache()
            c[chave_cache] = res
            if len(c) > 3000:   # limpa os mais antigos
                for k in sorted(c, key=lambda k: c[k].get("ts", 0))[:500]:
                    c.pop(k, None)
            _gravar_cache(c)
    return res


def _sem_extremos(lista):
    """Tira valores absurdos (unidade diferente, erro de digitação): fora de 1/5 a 5x a mediana."""
    if len(lista) < 4:
        return sorted(lista, key=lambda x: x["valor"])
    med = statistics.median(x["valor"] for x in lista)
    return sorted((x for x in lista if med / 5 <= x["valor"] <= med * 5), key=lambda x: x["valor"])


def resumo(lista):
    if not lista:
        return None
    v = [x["valor"] for x in lista]
    return {"n": len(v), "min": min(v), "max": max(v), "media": sum(v) / len(v), "mediana": statistics.median(v)}
