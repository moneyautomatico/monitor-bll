"""“Por que perdi?”: compara, lote a lote, o nosso lance com o do vencedor (classificação lida pelo monitor) e tira dicas.
Também guarda o histórico das nossas disputas para o preço sugerido da próxima proposta. Só leitura dos bancos do monitor."""
import json
import re
import sqlite3
import statistics

import buscas
import precos


def valor(txt):
    """'R$ 1.205.000,00' / '16,50' / 16.5 -> float."""
    if isinstance(txt, (int, float)):
        return float(txt)
    t = re.sub(r"[^\d,.-]", "", str(txt or ""))
    if not t:
        return 0.0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return 0.0


def _ranking(txt):
    try:
        r = json.loads(txt or "[]")
        return r if isinstance(r, list) else []
    except ValueError:
        return []


def ler_lotes(db, processo, promotor):
    """Linhas da classificação deste processo no banco do monitor."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        rows = con.execute("SELECT lote, descricao, fase, primeiro, melhor, ranking, nossa_pos, nosso_lance, atualizado "
                           "FROM classificacao WHERE processo=? AND promotor=?", (processo, promotor)).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    return rows


def _ordem_lote(x):
    n = re.sub(r"\D", "", str(x or ""))
    return (int(n) if n else 10 ** 9, str(x))


def analisar(rows, nosso_porte=""):
    """Devolve {"lotes": [...], "resumo": {...}, "dicas": [...]}."""
    lotes = []
    for lote, desc, fase, primeiro, melhor, ranking, pos, nosso_txt, atual in rows:
        rk = _ranking(ranking)
        validos = sorted([r for r in rk if not r.get("excluido") and valor(r.get("valor"))], key=lambda r: r.get("pos") or 999)
        venc = validos[0] if validos else None
        v_venc = valor(venc.get("valor")) if venc else valor(melhor)
        nosso = valor(nosso_txt)
        nosso_rk = next((r for r in rk if pos and r.get("pos") == pos), None)
        if not nosso and nosso_rk:
            nosso = valor(nosso_rk.get("valor"))
        # com lance mas sem posição = a plataforma tirou a empresa da classificação (desclassificada/inabilitada)
        excluido = bool(nosso_rk and nosso_rk.get("excluido")) or (not pos and bool(nosso))
        gap = ((nosso - v_venc) / v_venc * 100) if nosso and v_venc else None
        if pos == 1 and not excluido:
            sit = "ganhou"
        elif excluido:
            sit = "desclassificado"
        elif not pos and not nosso:
            sit = "sem lance"
        elif gap is not None and gap <= 1:
            sit = "por um triz"
        elif gap is not None and gap <= 5:
            sit = "perto"
        else:
            sit = "longe"
        tipo_v = (venc or {}).get("tipo", "")
        ficto = (sit in ("por um triz", "perto") and re.search(r"\b(ME|EPP)", tipo_v or "")
                 and (nosso_porte or "").upper() not in ("ME", "EPP"))
        segundo = validos[1] if len(validos) > 1 else None
        lotes.append({"lote": lote, "descricao": desc or "", "fase": fase or "", "pos": pos, "nosso": nosso,
                      "vencedor": (venc or {}).get("nome") or primeiro or "", "venc_valor": v_venc, "venc_marca": (venc or {}).get("marca", ""),
                      "venc_tipo": tipo_v, "gap": gap, "dif": (nosso - v_venc) if nosso and v_venc else None,
                      "sit": sit, "ficto": bool(ficto), "participantes": len(rk), "atualizado": atual or "",
                      "segundo": (segundo or {}).get("nome", ""), "seg_valor": valor((segundo or {}).get("valor"))})
    lotes.sort(key=lambda x: _ordem_lote(x["lote"]))
    disputados = [x for x in lotes if x["sit"] != "sem lance"]
    perdidos = [x for x in disputados if x["sit"] in ("por um triz", "perto", "longe")]
    gaps = [x["gap"] for x in perdidos if x["gap"] is not None]
    venc_freq = {}
    for x in perdidos:
        if x["vencedor"]:
            venc_freq[x["vencedor"]] = venc_freq.get(x["vencedor"], 0) + 1
    resumo = {"lotes": len(lotes), "disputados": len(disputados), "ganhos": sum(1 for x in lotes if x["sit"] == "ganhou"),
              "perdidos": len(perdidos), "desclass": sum(1 for x in lotes if x["sit"] == "desclassificado"),
              "triz": sum(1 for x in perdidos if x["sit"] == "por um triz"), "perto": sum(1 for x in perdidos if x["sit"] == "perto"),
              "gap_mediano": statistics.median(gaps) if gaps else None, "sem_lance": sum(1 for x in lotes if x["sit"] == "sem lance"),
              "vencedores": sorted(venc_freq.items(), key=lambda kv: -kv[1])}
    return {"lotes": lotes, "resumo": resumo, "dicas": dicas(lotes, resumo)}


def dicas(lotes, r):
    out = []
    if not r["disputados"]:
        return ["Não há lance da empresa registrado nesta classificação – confira se o monitor está lendo a sala de disputa (ou envie o PDF do ranking)."]
    if r["perdidos"] == 0 and r["ganhos"]:
        out.append("🏆 Você ficou em 1º em todos os lotes em que deu lance. Agora é manter a habilitação em dia até a homologação.")
    if r["triz"]:
        out.append(f"🤏 Em {r['triz']} lote(s) você perdeu por <b>1% ou menos</b>. Revise custo de frete, impostos e margem desses itens: "
                   "um lance um pouco mais firme teria levado.")
    if r["perto"]:
        out.append(f"📉 Em {r['perto']} lote(s) a diferença ficou entre 1% e 5% – dá para competir com ajuste de fornecedor/marca.")
    longe = [x for x in lotes if x["sit"] == "longe" and x["gap"] is not None]
    if longe:
        pior = max(longe, key=lambda x: x["gap"])
        out.append(f"🔎 Em {len(longe)} lote(s) você ficou mais de 5% acima (o maior: lote {pior['lote']}, +{pior['gap']:.0f}%). "
                   "Confira se a especificação e a unidade são as mesmas, ou se o mercado está mais barato (veja o preço sugerido na proposta).")
    ficto = [x for x in lotes if x["ficto"]]
    if ficto:
        out.append(f"⚖️ Em {len(ficto)} lote(s) o vencedor é ME/EPP e a diferença foi pequena: ele pode ter usado o direito de "
                   "<b>cobrir o seu lance</b> (empate ficto, LC 123 art. 44/45 – até 5% acima do melhor lance, no pregão).")
    if r["desclass"]:
        out.append(f"❌ Você foi <b>desclassificado/inabilitado</b> em {r['desclass']} lote(s). Leia o motivo nas mensagens do pregoeiro – "
                   "se cabe recurso, a intenção é na sessão e as razões em 3 dias úteis.")
    if r["vencedores"]:
        nome, n = r["vencedores"][0]
        if n >= 2:
            out.append(f"🥇 <b>{nome}</b> venceu {n} dos lotes que você perdeu – veja o histórico dele em Concorrentes.")
    if r["sem_lance"] and r["disputados"]:
        out.append(f"ℹ️ {r['sem_lance']} lote(s) sem lance da empresa (não participou ou o monitor não leu).")
    return out


# ------------------------------------------------------------------ histórico para o preço sugerido
def carregar_historico(dbs):
    """dbs = [(nome_empresa, caminho_db)]. Devolve todas as linhas de classificação com lance nosso ou vencedor."""
    out = []
    for nome, db in dbs:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            rows = con.execute("SELECT processo, promotor, lote, descricao, ranking, melhor, nossa_pos, nosso_lance, atualizado "
                               "FROM classificacao").fetchall()
            con.close()
        except sqlite3.Error:
            continue
        for pr, po, lote, desc, rk, melhor, pos, nosso, atual in rows:
            validos = sorted([r for r in _ranking(rk) if not r.get("excluido") and valor(r.get("valor"))], key=lambda r: r.get("pos") or 999)
            v = valor(validos[0].get("valor")) if validos else valor(melhor)
            if not desc or not v:
                continue
            out.append({"empresa": nome, "processo": pr, "orgao": po, "lote": lote, "descricao": desc, "norm": buscas.norm(desc),
                        "venc": v, "venc_nome": validos[0].get("nome", "") if validos else "", "nosso": valor(nosso), "pos": pos,
                        "quando": atual or ""})
    return out


def semelhantes(historico, descricao, maximo=8):
    """Disputas nossas com item parecido (mesmas palavras principais)."""
    chaves = precos.palavras(descricao)
    if not chaves:
        return []
    achados = [h for h in historico if precos.parecido(descricao, h["norm"], chaves)]
    return sorted(achados, key=lambda h: h["quando"], reverse=True)[:maximo]
