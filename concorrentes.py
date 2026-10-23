"""Inteligência de concorrentes: usa as classificações que o monitor já leu (todas as plataformas) para mostrar
quem disputa com cada empresa, quem ganha dela, por quanto, e os preços praticados por lote/item."""
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path

PASTA = Path(__file__).resolve().parent
EMPRESAS = PASTA / "empresas"
SUFIXOS = r"\b(LTDA|ME|EPP|EIRELI|S\.?A|SA|SS|MEI|LIMITADA|EI|EPP/SS|COMERCIO E|COM)\b"


def norm(t):
    return " ".join(unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().upper().split())


def chave_nome(nome):
    """Agrupa variações do mesmo nome ('X COMERCIAL LTDA' = 'X COMERCIAL LTDA - ME')."""
    t = re.sub(r"[^A-Z0-9 ]", " ", norm(nome))
    t = re.sub(SUFIXOS, " ", t)
    return " ".join(t.split())[:60]


def _env(arq):
    cfg = {}
    for linha in arq.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" in linha and not linha.strip().startswith("#"):
            k, v = linha.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"')
    return cfg


def empresas_proprias():
    """{eid: {"nome": NOME, "apelidos": [...], "cnpj": '...'}} – para reconhecer as NOSSAS linhas no ranking."""
    try:
        dados = json.loads((PASTA / "dados_empresas.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        dados = {}
    out = {}
    for env in sorted(EMPRESAS.glob("*.env")) if EMPRESAS.exists() else []:
        c = _env(env)
        nome = (c.get("EMPRESA_NOME") or env.stem).upper().strip()
        d = dados.get(nome, {})
        apelidos = {norm(x) for x in (c.get("CLASSIFICACAO_NOME"), c.get("EMPRESA_NOME"), d.get("razao")) if x and len(x.strip()) >= 4}
        cnpj = re.sub(r"\D", "", c.get("EMPRESA_CNPJ") or d.get("cnpj") or "")
        out[env.stem] = {"nome": nome, "apelidos": apelidos, "cnpj": cnpj}
    return out


def _eh(entrada, emp):
    n = norm(entrada.get("nome"))
    num = re.sub(r"\D", "", str(entrada.get("num") or ""))
    return any(a in n for a in emp["apelidos"]) or (len(emp["cnpj"]) == 14 and num == emp["cnpj"])


def _data_iso(br):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", br or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def analisar(empresa="", permitidos=None):
    """Estatísticas da empresa (NOME) escolhida – vazio = todas as nossas empresas."""
    proprias = empresas_proprias()
    todos_apelidos = [e for e in proprias.values()]
    conc = defaultdict(lambda: {"nomes": defaultdict(int), "lotes": 0, "ganhou_de_nos": 0, "perdeu_para_nos": 0,
                                "vitorias": 0, "desclass": 0, "difs": [], "orgaos": set(), "ultimo": "", "det": []})
    resumo = {"lotes": 0, "ganhos": 0, "segundos": 0, "gaps": [], "desclass": 0, "por_plat": defaultdict(lambda: [0, 0])}
    perdas = []
    vistos = set()
    for eid, emp in proprias.items():
        if empresa and emp["nome"] != empresa:
            continue
        if permitidos is not None and emp["nome"] not in permitidos:
            continue
        db = EMPRESAS / eid / "bll_monitor.db"
        if not db.exists():
            continue
        plat = (_env(EMPRESAS / f"{eid}.env").get("PLATAFORMA") or "bll").upper()
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            linhas = con.execute("SELECT processo, promotor, lote, descricao, fase, ranking, atualizado FROM classificacao").fetchall()
            con.close()
        except Exception:  # noqa: BLE001
            continue
        for proc, prom, lote, desc, fase, rk, quando in linhas:
            k = (emp["nome"], proc, prom, lote)
            if k in vistos:
                continue
            vistos.add(k)
            try:
                rank = json.loads(rk or "[]")
            except ValueError:
                continue
            nos = next((x for x in rank if _eh(x, emp)), None)
            if not nos or "CANCELAD" in (fase or "").upper():
                continue  # não disputamos este lote
            resumo["lotes"] += 1
            resumo["por_plat"][plat][0] += 1
            if nos.get("pos") == 1:
                resumo["ganhos"] += 1
                resumo["por_plat"][plat][1] += 1
            elif nos.get("pos") == 2:
                resumo["segundos"] += 1
            if nos.get("excluido") or not nos.get("pos"):
                resumo["desclass"] += 1
            lider = next((x for x in rank if x.get("pos") == 1), None)
            if lider and lider is not nos and nos.get("valor") and lider.get("valor"):
                gap = (nos["valor"] - lider["valor"]) / lider["valor"] * 100
                resumo["gaps"].append(gap)
                perdas.append({"processo": proc, "orgao": prom, "lote": lote, "desc": desc or "", "nossa_pos": nos.get("pos"),
                               "nosso": nos["valor"], "lider": lider.get("nome", ""), "valor_lider": lider["valor"], "gap": gap,
                               "quando": quando or "", "propria": any(_eh(lider, o) for o in todos_apelidos)})
            for x in rank:
                if x is nos or any(_eh(x, o) for o in todos_apelidos):
                    continue  # nossas próprias empresas não contam como concorrentes
                c = conc[chave_nome(x.get("nome"))]
                c["nomes"][x.get("nome", "")] += 1
                c["lotes"] += 1
                xp, np_ = x.get("pos"), nos.get("pos")
                if xp and (not np_ or xp < np_):
                    c["ganhou_de_nos"] += 1
                if np_ and (not xp or np_ < xp):
                    c["perdeu_para_nos"] += 1
                if xp == 1:
                    c["vitorias"] += 1
                if x.get("excluido") or not xp:
                    c["desclass"] += 1
                if x.get("valor") and nos.get("valor"):
                    c["difs"].append((x["valor"] - nos["valor"]) / nos["valor"] * 100)
                c["orgaos"].add(prom)
                c["ultimo"] = max(c["ultimo"], _data_iso(quando))
                c["det"].append({"processo": proc, "orgao": prom, "lote": lote, "desc": desc or "", "quando": quando or "",
                                 "pos": xp, "valor": x.get("valor"), "nossa_pos": np_, "nosso": nos.get("valor"),
                                 "marca": x.get("marca", ""), "empresa": emp["nome"]})
    lista = []
    for k, c in conc.items():
        nome = max(c["nomes"].items(), key=lambda t: t[1])[0]
        difs = c["difs"]
        lista.append({"chave": k, "nome": nome, "lotes": c["lotes"], "ganhou_de_nos": c["ganhou_de_nos"],
                      "perdeu_para_nos": c["perdeu_para_nos"], "vitorias": c["vitorias"], "desclass": c["desclass"],
                      "dif_media": sum(difs) / len(difs) if difs else None, "orgaos": len(c["orgaos"]),
                      "ultimo": c["ultimo"], "det": sorted(c["det"], key=lambda d: _data_iso(d["quando"]), reverse=True)})
    lista.sort(key=lambda x: (-x["ganhou_de_nos"], -x["lotes"]))
    gaps = resumo["gaps"]
    resumo["gap_medio"] = sum(gaps) / len(gaps) if gaps else None
    resumo["perto"] = sum(1 for g in gaps if 0 < g <= 3)   # perdidos por até 3%
    perdas.sort(key=lambda p: p["gap"])
    return {"resumo": resumo, "concorrentes": lista, "perdas": perdas, "empresas": sorted({e["nome"] for e in proprias.values()})}
