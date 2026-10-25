"""Atas de registro de preço (Lei 14.133/2021, arts. 82 a 86): itens registrados, pedidos (empenhos/ordens de fornecimento)
do órgão gerenciador e dos participantes, saldo por item, adesões ("carona") com os limites legais e avisos de vigência.
Só controle interno – nada é enviado às plataformas ou aos órgãos."""
import json
import secrets
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from propostas import num

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "atas.json"
TRAVA = threading.Lock()
LIMITE_ADESAO_ORGAO = 0.5   # art. 86 §4º: cada órgão aderente – até 50% do quantitativo registrado de cada item
LIMITE_ADESAO_TOTAL = 2.0   # art. 86 §5º: todas as adesões somadas – até o dobro do quantitativo registrado
AVISAR_VIGENCIA = (60, 30, 15, 7, 1)


def _ler():
    try:
        return json.loads(ARQ.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _gravar(d):
    tmp = ARQ.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(ARQ)


def listar():
    return sorted(({**v, "id": k} for k, v in _ler().items()), key=lambda x: x.get("vigencia_fim") or "9999")


def ler(aid):
    d = _ler().get(aid)
    return {**d, "id": aid} if d else None


def gravar(aid, dados):
    with TRAVA:
        d = _ler()
        d[aid] = {k: v for k, v in dados.items() if k != "id"}
        _gravar(d)


def atualizar(aid, mudar):
    with TRAVA:
        d = _ler()
        if aid not in d:
            return None
        it = {**d[aid], "id": aid}
        mudar(it)
        d[aid] = {k: v for k, v in it.items() if k != "id"}
        _gravar(d)
        return it


def excluir(aid):
    with TRAVA:
        d = _ler()
        d.pop(aid, None)
        _gravar(d)


def novo(dados):
    aid = secrets.token_hex(6)
    base = {"itens": [], "pedidos": [], "adesoes": [], "status": "ativa", "prorrogada": False, "avisos": [],
            "criado": datetime.now().strftime("%Y-%m-%dT%H:%M")}
    gravar(aid, {**base, **dados})
    return aid


def novo_id():
    return secrets.token_hex(4)


def _d(txt):
    try:
        return date.fromisoformat((txt or "")[:10])
    except ValueError:
        return None


def dias_vigencia(a, hoje=None):
    f = _d(a.get("vigencia_fim"))
    return (f - (hoje or date.today())).days if f else None


def mais_um_ano(txt):
    d = _d(txt)
    if not d:
        return ""
    try:
        return d.replace(year=d.year + 1).isoformat()
    except ValueError:   # 29/02
        return (d + timedelta(days=365)).isoformat()


def saldos(a):
    """Por item: registrado, consumido (gerenciador/participantes), saldo, aderido (aprovado), limite de adesão e % usado."""
    out = []
    for it in a.get("itens") or []:
        reg = num(it.get("qtd"))
        cons = sum(num(p.get("itens", {}).get(it["id"])) for p in a.get("pedidos") or [])
        ader = sum(num(x.get("itens", {}).get(it["id"])) for x in a.get("adesoes") or [] if x.get("status") == "autorizada")
        pend = sum(num(x.get("itens", {}).get(it["id"])) for x in a.get("adesoes") or [] if x.get("status") == "pendente")
        out.append({**it, "_reg": reg, "_cons": cons, "_saldo": reg - cons, "_ader": ader, "_ader_pend": pend,
                    "_lim_ader_total": reg * LIMITE_ADESAO_TOTAL, "_lim_ader_orgao": reg * LIMITE_ADESAO_ORGAO,
                    "_saldo_ader": reg * LIMITE_ADESAO_TOTAL - ader, "_unit": num(it.get("valor")),
                    "_pct": (100 * cons / reg) if reg else 0})
    return out


def totais(a):
    s = saldos(a)
    return {"registrado": sum(x["_reg"] * x["_unit"] for x in s), "consumido": sum(x["_cons"] * x["_unit"] for x in s),
            "saldo": sum(x["_saldo"] * x["_unit"] for x in s), "aderido": sum(x["_ader"] * x["_unit"] for x in s),
            "pct": (100 * sum(x["_cons"] * x["_unit"] for x in s) / sum(x["_reg"] * x["_unit"] for x in s))
            if sum(x["_reg"] * x["_unit"] for x in s) else 0,
            "baixo": [x for x in s if x["_reg"] and x["_saldo"] / x["_reg"] < 0.2],
            "pendentes": sum(1 for x in a.get("adesoes") or [] if x.get("status") == "pendente")}


def conferir_adesao(a, pedido_itens, orgao, ignorar=""):
    """Problemas do pedido de adesão frente aos limites legais: [(item, texto)]."""
    s = {x["id"]: x for x in saldos(a)}
    problemas = []
    for iid, q in pedido_itens.items():
        q = num(q)
        x = s.get(iid)
        if not x or not q:
            continue
        mesmo_orgao = sum(num(ad.get("itens", {}).get(iid)) for ad in a.get("adesoes") or []
                          if ad.get("status") == "autorizada" and ad.get("id") != ignorar
                          and (ad.get("orgao") or "").strip().upper() == (orgao or "").strip().upper())
        ja = sum(num(ad.get("itens", {}).get(iid)) for ad in a.get("adesoes") or []
                 if ad.get("status") == "autorizada" and ad.get("id") != ignorar)
        rot = x.get("item") or x.get("descricao", "")[:30]
        if mesmo_orgao + q > x["_lim_ader_orgao"] + 1e-9:
            problemas.append((rot, f"passa de 50% do registrado para este órgão ({fmt(x['_lim_ader_orgao'])} no máximo, "
                                   f"já aderido {fmt(mesmo_orgao)}) – art. 86 §4º"))
        if ja + q > x["_lim_ader_total"] + 1e-9:
            problemas.append((rot, f"as adesões somadas passam do dobro do registrado ({fmt(x['_lim_ader_total'])} no máximo, "
                                   f"já aderido {fmt(ja)}) – art. 86 §5º"))
    return problemas


def fmt(v):
    v = float(v or 0)
    return (f"{v:,.0f}" if v == int(v) else f"{v:,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")


def brl(v):
    return "R$ " + f"{float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ------------------------------------------------------------------ avisos
def ciclo(enviar):
    """A cada hora (8h–20h): vigência perto do fim (60/30/15/7/1 dia), saldo abaixo de 20% e pedido de adesão parado."""
    import html as h
    time.sleep(260)
    while True:
        try:
            if 8 <= datetime.now().hour <= 20:
                hoje = date.today()
                d = _ler()
                mudou = False
                for aid, a in d.items():
                    if a.get("status") != "ativa":
                        continue
                    a = {**a, "id": aid}
                    avisos = d[aid].setdefault("avisos", [])
                    tit = f"Ata {h.escape(a.get('numero') or '')} – {h.escape((a.get('orgao') or '')[:60])}"
                    dias = dias_vigencia(a, hoje)
                    if dias is not None:
                        marco = next((m for m in AVISAR_VIGENCIA if 0 <= dias <= m), None)
                        chave = f"vig|{a.get('vigencia_fim')}|{marco}"
                        if marco is not None and chave not in avisos:
                            prorr = "" if a.get("prorrogada") else "\nDá para prorrogar por mais 1 ano (art. 84) se o preço continuar vantajoso – fale com o órgão."
                            enviar(f"📑 <b>Ata vence em {dias} dia(s)</b>\n{tit}\nVigência até {_d(a['vigencia_fim']):%d/%m/%Y}.{prorr}\n"
                                   f"<i>{h.escape(a.get('empresa', ''))}</i>", a.get("empresa", ""))
                            avisos.append(chave)
                            mudou = True
                    for x in totais(a)["baixo"]:
                        chave = f"saldo|{x['id']}|{hoje:%Y-%m}"
                        if chave not in avisos and x["_saldo"] >= 0:
                            enviar(f"📉 <b>Saldo baixo na ata</b>\n{tit}\nItem {h.escape(str(x.get('item') or ''))} – "
                                   f"{h.escape((x.get('descricao') or '')[:70])}: resta {fmt(x['_saldo'])} de {fmt(x['_reg'])} "
                                   f"({100 * x['_saldo'] / x['_reg']:.0f}%).\n<i>{h.escape(a.get('empresa', ''))}</i>", a.get("empresa", ""))
                            avisos.append(chave)
                            mudou = True
                    for ad in a.get("adesoes") or []:
                        pedido = _d(ad.get("data"))
                        chave = f"ades|{ad.get('id')}"
                        if ad.get("status") == "pendente" and pedido and (hoje - pedido).days >= 3 and chave not in avisos:
                            enviar(f"🤝 <b>Pedido de adesão aguardando você</b>\n{tit}\nÓrgão: {h.escape(ad.get('orgao', ''))} "
                                   f"(pedido em {pedido:%d/%m/%Y}). Responda o aceite do fornecedor e registre no painel → Contratos → Atas.\n"
                                   f"<i>{h.escape(a.get('empresa', ''))}</i>", a.get("empresa", ""))
                            avisos.append(chave)
                            mudou = True
                if mudou:
                    with TRAVA:
                        atual = _ler()
                        for aid, a in d.items():
                            if aid in atual:
                                atual[aid]["avisos"] = a.get("avisos", [])
                        _gravar(atual)
        except Exception as ex:  # noqa: BLE001
            print("Erro nos avisos das atas:", ex, flush=True)
        time.sleep(3600)
