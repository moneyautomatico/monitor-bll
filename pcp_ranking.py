"""Lê o PDF "Ranking nos Itens" do Portal de Compras Públicas (classificação completa por item)."""
import io
import re
import unicodedata

import pdfplumber

RE_CNPJ = re.compile(r"^\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}$|^\d{3}\.\d{3}\.\d{3}-\d{2}$|^\*{3}")
RODAPE = re.compile(r"autenticidade|gerado eletronicamente|C[oó]digo verificador|^P[aá]gina \d", re.I)

def valor(t):
    t = re.sub(r"[^\d,]", "", t or "")
    try:
        return float(t.replace(",", ".")) if t else None
    except ValueError:
        return None

def ranking_pdf(dados):
    """Lê o PDF 'Ranking do processo' do Portal de Compras: {item: {'descricao', 'referencia', 'ranking': [...]}}"""
    linhas = []  # (pagina, top, [words])
    with pdfplumber.open(io.BytesIO(dados)) as pdf:
        for n, pg in enumerate(pdf.pages):
            ws = pg.extract_words(x_tolerance=1.2, keep_blank_chars=False, use_text_flow=False)
            grupos = {}
            for w in ws:
                grupos.setdefault(round(w["top"]), []).append(w)
            for top in sorted(grupos):
                g = sorted(grupos[top], key=lambda w: w["x0"])
                txt = " ".join(w["text"] for w in g)
                if RODAPE.search(txt):
                    continue
                linhas.append((n, top, g, txt))
    itens, atual, cols, modo = {}, None, None, None
    for n, top, g, txt in linhas:
        m = re.match(r"^(\d{3,5})\s*-\s*(.*)$", txt)
        if m and g[0]["x0"] < 70 and not RE_CNPJ.match(g[0]["text"]):
            atual = {"item": m.group(1), "descricao": m.group(2), "referencia": None, "ranking": []}
            itens[m.group(1)] = atual
            modo = "desc"
            continue
        if txt.startswith("Fornecedor") and "CNPJ" in txt.replace(" ", ""):
            cols = {w["text"].replace(" ", "")[:6].upper(): w["x0"] for w in g}
            modo = "tab"
            continue
        if atual is None:
            continue
        if modo == "desc":
            atual["descricao"] += " " + txt
            continue
        if modo != "tab" or not cols:
            continue
        x_cnpj = cols.get("CNPJ/C", 140) - 3
        cnpj = next((w for w in g if RE_CNPJ.match(w["text"]) and abs(w["x0"] - (x_cnpj + 3)) < 30), None)
        nome_ws = [w["text"] for w in g if w["x0"] < x_cnpj]
        if cnpj:
            resto = [w for w in g if w["x0"] > cnpj["x0"]]
            col = lambda chave: " ".join(w["text"] for w in resto if chave[0] <= w["x0"] < chave[1])
            xs = sorted(cols.values())
            def faixa(nome):
                x = cols.get(nome)
                if x is None: return (9e9, 9e9)
                prox = [v for v in xs if v > x]
                return (x - 3, (prox[0] - 3) if prox else 9e9)
            atual["ranking"].append({"pos": len(atual["ranking"]) + 1, "nome": " ".join(nome_ws), "cnpj": cnpj["text"],
                                     "valor": valor(col(faixa("VALORU") if "VALORU" in cols else (cnpj["x0"]+60, cnpj["x0"]+140))),
                                     "marca": col(faixa("MARCA/")), "tipo": col(faixa("TIPO"))})
        elif nome_ws and atual["ranking"]:
            atual["ranking"][-1]["nome"] += " " + " ".join(nome_ws)
    for it in itens.values():
        d = it["descricao"]
        mref = re.search(r"\|\s*R\$\s*([\d.,]+)\s*$", d)
        if mref:
            it["referencia"] = valor(mref.group(1))
            it["descricao"] = d[:mref.start()].strip()
    return itens

RE_DESC = re.compile(r"\(\s*Desc/Inab/Rejeitado\s*\)", re.I)


def cabecalho(dados):
    """(número do processo, órgão) tirados do topo do PDF."""
    with pdfplumber.open(io.BytesIO(dados)) as pdf:
        txt = pdf.pages[0].extract_text() or ""
    num = re.search(r"-\s*(\d+/\d{4})", txt)
    linhas = [l.strip() for l in txt.splitlines() if l.strip()]
    orgao = ""
    for i, l in enumerate(linhas):
        if l.upper().startswith("RANKING DO PROCESSO") and i + 1 < len(linhas):
            orgao = linhas[i + 1]
            break
    return (num.group(1) if num else ""), orgao


def _norm(t):
    return " ".join(unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().upper().split())


def classificar(itens, nome_empresa="", cnpj_empresa=""):
    """Acrescenta posição final (sem as desclassificadas) e marca a nossa empresa."""
    cnpj = re.sub(r"\D", "", cnpj_empresa or "")
    alvo = _norm(nome_empresa)
    for it in itens.values():
        pos = 0
        for x in it["ranking"]:
            x["excluido"] = bool(RE_DESC.search(x["nome"]))
            x["nome"] = RE_DESC.sub("", x["nome"]).strip()
            if not x["excluido"]:
                pos += 1
            x["pos"] = None if x["excluido"] else pos
            x["nossa"] = (bool(cnpj) and re.sub(r"\D", "", x["cnpj"]) == cnpj) or (bool(alvo) and alvo in _norm(x["nome"]))
    return itens


if __name__ == "__main__":
    import sys, json
    r = ranking_pdf(open(sys.argv[1], "rb").read())
    for k, v in r.items():
        print(k, v["descricao"][:60], v["referencia"])
        for x in v["ranking"]:
            print("   ", x["pos"], x["nome"], x["cnpj"], x["valor"], x["marca"], x["tipo"])
