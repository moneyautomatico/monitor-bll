"""Pós-venda: depois de vencer, acompanha empenho, entregas, notas fiscais e pagamentos de cada contrato/ata,
com aviso de entrega perto do prazo e de pagamento atrasado (no Telegram do dono da empresa)."""
import json
import secrets
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "contratos.json"
TRAVA = threading.Lock()


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
    return sorted(({**v, "id": k} for k, v in _ler().items()), key=lambda x: x.get("criado", ""), reverse=True)


def ler(cid):
    d = _ler().get(cid)
    return {**d, "id": cid} if d else None


def gravar(cid, dados):
    with TRAVA:
        d = _ler()
        d[cid] = {k: v for k, v in dados.items() if k != "id"}
        _gravar(d)


def excluir(cid):
    with TRAVA:
        d = _ler()
        d.pop(cid, None)
        _gravar(d)


def novo(dados):
    cid = secrets.token_hex(6)
    base = {"empenhos": [], "entregas": [], "notas": [], "status": "ativo", "obs": "",
            "criado": datetime.now().strftime("%Y-%m-%dT%H:%M")}
    gravar(cid, {**base, **dados})
    return cid


def novo_id():
    return secrets.token_hex(4)


def _d(txt):
    try:
        return date.fromisoformat((txt or "")[:10])
    except ValueError:
        return None


def br(txt):
    d = _d(txt)
    return d.strftime("%d/%m/%Y") if d else ""


def vencimento(nota):
    if nota.get("vencimento"):
        return _d(nota["vencimento"])
    em = _d(nota.get("emissao"))
    try:
        dias = int(nota.get("prazo_dias") or 30)
    except ValueError:
        dias = 30
    return em + timedelta(days=dias) if em else None


def situacao_nota(n, hoje=None):
    """'paga' | 'atrasada' | 'vence' (até 5 dias) | 'aberta'."""
    hoje = hoje or date.today()
    if n.get("pago_em"):
        return "paga"
    v = vencimento(n)
    if v and v < hoje:
        return "atrasada"
    if v and (v - hoje).days <= 5:
        return "vence"
    return "aberta"


def situacao_entrega(en, hoje=None):
    hoje = hoje or date.today()
    if en.get("entregue_em"):
        return "entregue"
    p = _d(en.get("prazo"))
    if p and p < hoje:
        return "atrasada"
    if p and (p - hoje).days <= 2:
        return "perto"
    return "aberta"


from propostas import num   # noqa: E402  (mesma leitura de valores: 1.234,56 / 1234.56 / 1.200)


def totais(c, hoje=None):
    hoje = hoje or date.today()
    emp = sum(num(x.get("valor")) for x in c.get("empenhos") or [])
    fat = sum(num(x.get("valor")) for x in c.get("notas") or [])
    rec = sum(num(x.get("valor_pago") or x.get("valor")) for x in c.get("notas") or [] if x.get("pago_em"))
    atras = [x for x in c.get("notas") or [] if situacao_nota(x, hoje) == "atrasada"]
    return {"contratado": num(c.get("valor")), "empenhado": emp, "faturado": fat, "recebido": rec,
            "a_receber": sum(num(x.get("valor")) for x in c.get("notas") or [] if not x.get("pago_em")),
            "atrasado": sum(num(x.get("valor")) for x in atras), "n_atrasadas": len(atras),
            "saldo": num(c.get("valor")) - fat,
            "entregas_atrasadas": sum(1 for x in c.get("entregas") or [] if situacao_entrega(x, hoje) == "atrasada"),
            "entregas_perto": sum(1 for x in c.get("entregas") or [] if situacao_entrega(x, hoje) == "perto")}


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ------------------------------------------------------------------ avisos
def ciclo(enviar):
    """A cada 30 min: entrega a 2 dias ou atrasada; pagamento atrasado (no 1º dia e depois a cada 7 dias)."""
    import html as h
    time.sleep(200)
    while True:
        try:
            hoje = date.today()
            mudou = False
            d = _ler()
            for cid, c in d.items():
                if c.get("status") != "ativo":
                    continue
                titulo = f"{h.escape(c.get('processo') or '')} – {h.escape((c.get('orgao') or '')[:60])}"
                for en in c.get("entregas") or []:
                    st = situacao_entrega(en, hoje)
                    chave = f"{st}|{hoje.isoformat() if st == 'atrasada' else en.get('prazo')}"
                    if st in ("perto", "atrasada") and chave not in (en.get("avisos") or []) and \
                            (st == "perto" or not any(a.startswith("atrasada|") and (hoje - _d(a.split("|")[1])).days < 3
                                                      for a in en.get("avisos") or [] if _d(a.split("|")[1]))):
                        enviar(f"🚚 <b>Entrega {'ATRASADA' if st == 'atrasada' else 'perto do prazo'}</b>\n{titulo}\n"
                               f"{h.escape(en.get('descricao') or 'Entrega')} – prazo {br(en.get('prazo'))}\n"
                               f"<i>{h.escape(c.get('empresa', ''))}</i>", c.get("empresa", ""))
                        en.setdefault("avisos", []).append(chave)
                        mudou = True
                for n in c.get("notas") or []:
                    if situacao_nota(n, hoje) != "atrasada":
                        continue
                    ult = max((_d(a) for a in n.get("avisos") or [] if _d(a)), default=None)
                    if ult and (hoje - ult).days < 7:
                        continue
                    v = vencimento(n)
                    enviar(f"💰 <b>Pagamento atrasado</b>\n{titulo}\nNF {h.escape(str(n.get('numero') or ''))} – {brl(num(n.get('valor')))}\n"
                           f"Venceu em {v.strftime('%d/%m/%Y')} (há {(hoje - v).days} dia(s)).\n"
                           f"<i>{h.escape(c.get('empresa', ''))}</i> – cobre o órgão e registre o pagamento no painel → Contratos.",
                           c.get("empresa", ""))
                    n.setdefault("avisos", []).append(hoje.isoformat())
                    mudou = True
            if mudou:
                with TRAVA:
                    atual = _ler()
                    for cid, c in d.items():
                        if cid in atual:
                            atual[cid]["entregas"], atual[cid]["notas"] = c.get("entregas", []), c.get("notas", [])
                    _gravar(atual)
        except Exception as ex:  # noqa: BLE001
            print("Erro nos avisos de contratos:", ex, flush=True)
        time.sleep(1800)
