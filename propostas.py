"""Proposta comercial: itens (do monitor, do PNCP, lidos do edital pela IA ou digitados), preços, condições e dados
bancários -> documento pronto para assinar (Word/PDF). Só gera o arquivo; nada é enviado à plataforma."""
import json
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path

import documentos

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "propostas.json"
TRAVA = threading.Lock()
PADRAO_COND = {"validade": "60", "entrega": "conforme o edital", "pagamento": "conforme o edital",
               "garantia": "conforme o edital"}


# ------------------------------------------------------------------ armazenamento
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
    return sorted(({**v, "id": k} for k, v in _ler().items()), key=lambda x: x.get("atualizado", ""), reverse=True)


def ler(pid):
    d = _ler().get(pid)
    return {**d, "id": pid} if d else None


def gravar(pid, dados):
    with TRAVA:
        d = _ler()
        dados = {k: v for k, v in dados.items() if k != "id"}
        dados["atualizado"] = datetime.now().strftime("%Y-%m-%dT%H:%M")
        d[pid] = dados
        _gravar(d)


def excluir(pid):
    with TRAVA:
        d = _ler()
        d.pop(pid, None)
        _gravar(d)


def nova(empresa, processo, orgao, modalidade, usuario, modelo=None):
    """Cria a proposta. Condições e dados bancários vêm da última proposta da mesma empresa."""
    ultima = next((p for p in listar() if p.get("empresa") == empresa), None) or {}
    pid = secrets.token_hex(6)
    gravar(pid, {"empresa": empresa, "processo": processo, "orgao": orgao, "modalidade": modalidade or "Pregão Eletrônico",
                 "objeto": "", "itens": [], "usuario": usuario, "criado": datetime.now().strftime("%Y-%m-%dT%H:%M"),
                 "cond": dict(ultima.get("cond") or PADRAO_COND), "banco": dict(ultima.get("banco") or {}),
                 "obs": ultima.get("obs", ""), "cidade_data": "", "data": ""})
    return pid


# ------------------------------------------------------------------ números
def num(v):
    """'1.234,56' / '1234.56' / 1234.56 -> float (0 se vazio)."""
    if isinstance(v, (int, float)):
        return float(v)
    t = re.sub(r"[^\d,.-]", "", str(v or ""))
    if not t:
        return 0.0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", t):   # 1.200 / 1.250.000 = milhar no padrão brasileiro
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return 0.0


def brl(v, simbolo=True):
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("R$ " + s) if simbolo else s


def qtd_txt(v):
    return (f"{v:,.0f}" if float(v).is_integer() else f"{v:,.3f}".rstrip("0")).replace(",", "X").replace(".", ",").replace("X", ".")


_UN = ["", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez", "onze", "doze", "treze",
       "quatorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
_DZ = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"]
_CT = ["", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos", "setecentos", "oitocentos", "novecentos"]


def _ate_mil(n):
    if n == 0:
        return ""
    if n == 100:
        return "cem"
    c, r = divmod(n, 100)
    partes = [_CT[c]] if c else []
    if r:
        if r < 20:
            partes.append(_UN[r])
        else:
            d, u = divmod(r, 10)
            partes.append(_DZ[d] + (f" e {_UN[u]}" if u else ""))
    return " e ".join(partes)


def _inteiro(n):
    if n == 0:
        return "zero"
    grupos = []   # (valor, ordem) do menor para o maior
    ordem = 0
    while n:
        n, g = divmod(n, 1000)
        grupos.append((g, ordem))
        ordem += 1
    nomes = {1: ("mil", "mil"), 2: ("milhão", "milhões"), 3: ("bilhão", "bilhões")}
    partes = []
    for g, o in reversed(grupos):
        if not g:
            continue
        if o == 0:
            partes.append((g, _ate_mil(g)))
        elif o == 1:
            partes.append((g, "mil" if g == 1 else f"{_ate_mil(g)} mil"))
        else:
            partes.append((g, f"{_ate_mil(g)} {nomes[o][0] if g == 1 else nomes[o][1]}"))
    if len(partes) == 1:
        return partes[0][1]
    ult_valor = partes[-1][0]
    sep = " e " if ult_valor < 100 or ult_valor % 100 == 0 else " "
    return " ".join(t for _, t in partes[:-1]) + sep + partes[-1][1]


def extenso(v):
    """1234.56 -> 'um mil, duzentos e trinta e quatro reais e cinquenta e seis centavos'."""
    v = round(v + 1e-9, 2)
    reais, cent = int(v), int(round((v - int(v)) * 100))
    txt = []
    if reais:
        r = _inteiro(reais)
        de = " de" if reais % 1_000_000 == 0 else ""
        txt.append(f"{r}{de} {'real' if reais == 1 else 'reais'}")
    if cent:
        txt.append(f"{_inteiro(cent)} {'centavo' if cent == 1 else 'centavos'}")
    return " e ".join(txt) or "zero reais"


# ------------------------------------------------------------------ cálculo e documento
def calcular(p):
    itens = []
    for it in p.get("itens") or []:
        q, u = num(it.get("qtd")), num(it.get("unit"))
        itens.append({**it, "_q": q, "_u": u, "_t": round(q * u, 2)})
    return itens, round(sum(i["_t"] for i in itens), 2)


def montar(p, ctx):
    """Blocos no formato de documentos.html/docx."""
    itens, total = calcular(p)
    c = {**PADRAO_COND, **(p.get("cond") or {})}
    b = {k: (p.get("banco") or {}).get(k) or ctx.get(k, "") for k in ("banco", "agencia", "conta", "pix")}   # vazio = o da empresa
    tem_marca = any((i.get("marca") or "").strip() for i in itens)
    cab = ["Item", "Descrição"] + (["Marca/Modelo"] if tem_marca else []) + ["Unid.", "Qtd.", "Valor unit. (R$)", "Valor total (R$)"]
    larg = [600, 3500 if tem_marca else 4700] + ([1200] if tem_marca else []) + [700, 800, 1300, 1400]
    linhas = [cab]
    lote_atual, subtotal, por_lote = None, 0.0, {}
    for i in itens:
        lote = (i.get("lote") or "").strip()
        por_lote[lote] = por_lote.get(lote, 0) + i["_t"]
    for i in itens:
        lote = (i.get("lote") or "").strip()
        if lote and lote != lote_atual:
            linhas.append(["§", f"LOTE {lote} – subtotal {brl(por_lote[lote])}" if len(por_lote) > 1 else f"LOTE {lote}"])
            lote_atual = lote
        linhas.append([i.get("item") or "", i.get("descricao") or ""] + ([i.get("marca") or ""] if tem_marca else []) +
                      [i.get("unidade") or "", qtd_txt(i["_q"]), brl(i["_u"], False), brl(i["_t"], False)])
    linhas.append(["", "**VALOR TOTAL DA PROPOSTA**"] + ([""] if tem_marca else []) + ["", "", "", f"**{brl(total, False)}**"])
    ref = []
    if p.get("orgao"):
        ref = [("dir", f"À\n{p['orgao']}"),
               ("dir", f"Ref.: {p.get('modalidade') or 'Pregão Eletrônico'} nº {p.get('processo') or '____/____'}")]
    ident = (f"**{ctx.get('razao', '')}**, inscrita no CNPJ sob o nº **{documentos.fmt_cnpj(ctx.get('cnpj'))}**, com sede em "
             f"{ctx.get('endereco', '')}, neste ato representada por **{ctx.get('rep_nome', '')}**, "
             f"{ctx.get('rep_cargo') or 'representante legal'}, CPF nº **{documentos.fmt_cpf(ctx.get('rep_cpf'))}**, apresenta "
             f"a sua proposta de preços para o certame em referência, conforme abaixo:")
    blocos = ref + [("titulo", "PROPOSTA COMERCIAL"), ("p", ident)]
    if (p.get("objeto") or "").strip():
        blocos.append(("p", f"**Objeto:** {p['objeto'].strip()}"))
    blocos.append(("tabela", {"linhas": linhas, "larguras": larg, "alinhar": "cl" + ("l" if tem_marca else "") + "crrr"}))
    blocos.append(("p", f"**Valor total da proposta: {brl(total)}** ({extenso(total)})."))
    blocos.append(("secao", "CONDIÇÕES"))
    blocos.append(("item", f"a) Validade da proposta: {c['validade']} (" + _inteiro(int(num(c['validade']) or 0)) +
                   ") dias, contados da data de abertura da sessão." if num(c["validade"]) else f"a) Validade da proposta: {c['validade']}."))
    blocos.append(("item", f"b) Prazo de entrega/execução: {c['entrega']}."))
    blocos.append(("item", f"c) Condições de pagamento: {c['pagamento']}."))
    blocos.append(("item", f"d) Garantia: {c['garantia']}."))
    blocos.append(("item", "e) Nos preços propostos estão incluídos todos os custos diretos e indiretos, tributos, encargos "
                           "sociais e trabalhistas, fretes, seguros e demais despesas necessárias ao cumprimento do objeto."))
    blocos.append(("item", "f) Declaramos que a proposta atende a todas as especificações e condições do edital e seus anexos."))
    if any((b.get(k) or "").strip() for k in ("banco", "agencia", "conta", "pix")):
        blocos.append(("secao", "DADOS BANCÁRIOS"))
        partes = [f"Banco: {b['banco']}" if b.get("banco") else "", f"Agência: {b['agencia']}" if b.get("agencia") else "",
                  f"Conta: {b['conta']}" if b.get("conta") else "", f"PIX: {b['pix']}" if b.get("pix") else ""]
        blocos.append(("p", " · ".join(x for x in partes if x)))
    if (p.get("obs") or "").strip():
        blocos.append(("secao", "OBSERVAÇÕES"))
        blocos += [("p", ln.strip()) for ln in p["obs"].splitlines() if ln.strip()]
    blocos += [("vazio", ""), ("data", f"{p.get('cidade_data') or ctx.get('cidade') or '__________'}, "
                                      f"{p.get('data') or documentos.data_extenso()}."),
               ("vazio", ""), ("vazio", ""), ("assinatura", "")]
    return [("Proposta comercial", blocos)], total


# ------------------------------------------------------------------ importar itens
def itens_pncp(lista):
    """Itens da API do PNCP -> itens da proposta (o valor estimado do órgão vai como referência)."""
    out = []
    for x in lista:
        out.append({"lote": "", "item": str(x.get("numeroItem") or ""), "descricao": (x.get("descricao") or "").strip(),
                    "unidade": x.get("unidadeMedida") or "", "qtd": x.get("quantidade") or "", "unit": "",
                    "ref": x.get("valorUnitarioEstimado") or "", "marca": ""})
    return out


def itens_monitor(linhas):
    """Lotes lidos pelo monitor (classificação): descrição e o nosso último lance como ponto de partida."""
    out = []
    for lote, desc, lance in linhas:
        out.append({"lote": str(lote or ""), "item": str(lote or ""), "descricao": (desc or "").strip(), "unidade": "",
                    "qtd": "1", "unit": lance or "", "ref": "", "marca": ""})
    return out


PROMPT_ITENS = """Leia o edital (e o termo de referência, se houver) e extraia a TABELA DE ITENS/LOTES que a empresa precisa cotar
na proposta de preços. Responda SOMENTE com JSON válido, sem texto antes ou depois, neste formato:
{"objeto": "objeto da licitação em uma frase",
 "itens": [{"lote": "1", "item": "1", "descricao": "descrição completa do item", "unidade": "UN", "qtd": 10,
            "valor_estimado": 12.5}],
 "validade": "prazo mínimo de validade da proposta, em dias (só o número) ou vazio",
 "entrega": "prazo e local de entrega/execução, resumido", "pagamento": "condição de pagamento, resumida",
 "garantia": "garantia exigida, resumida (ou vazio)"}
Regras: não invente itens; se não houver lotes, deixe "lote" vazio; "valor_estimado" = valor unitário de referência do
órgão se o edital informar (senão null); copie a descrição como está no edital (pode resumir só se passar de 400 caracteres);
no máximo 400 itens."""


def extrair_json(txt):
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        raise ValueError("a IA não devolveu a tabela de itens")
    return json.loads(m.group(0))


def itens_ia(resultado):
    out = []
    for x in (resultado.get("itens") or [])[:400]:
        out.append({"lote": str(x.get("lote") or ""), "item": str(x.get("item") or ""),
                    "descricao": str(x.get("descricao") or "").strip()[:1500], "unidade": str(x.get("unidade") or ""),
                    "qtd": x.get("qtd") or "", "unit": "", "ref": x.get("valor_estimado") or "", "marca": ""})
    return out


# ------------------------------------------------------------------ conferência antes de enviar
def cnpj_valido(c):
    n = re.sub(r"\D", "", c or "")
    if len(n) != 14 or len(set(n)) == 1:
        return False
    def dv(base, pesos):
        s = sum(int(a) * b for a, b in zip(base, pesos)) % 11
        return "0" if s < 2 else str(11 - s)
    d1 = dv(n[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return n[12] == d1 and n[13] == dv(n[:12] + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])


def cpf_valido(c):
    n = re.sub(r"\D", "", c or "")
    if len(n) != 11 or len(set(n)) == 1:
        return False
    for t in (9, 10):
        s = sum(int(n[i]) * (t + 1 - i) for i in range(t)) % 11
        if n[t] != ("0" if s < 2 else str(11 - s)):
            return False
    return True


def conferir(p, ctx, certidoes=()):
    """Checagens automáticas. Devolve [(nivel, texto)] com nivel 'erro' | 'atencao' | 'ok'.
    certidoes = [(nome, status, dias)] da empresa (status 'ok' | 'vence' | 'vencida' | 'sem')."""
    out = []
    falta = documentos.faltando(ctx)
    out.append(("erro", "Dados da empresa incompletos: " + ", ".join(falta)) if falta else ("ok", "Dados da empresa completos"))
    if ctx.get("cnpj"):
        out.append(("ok", "CNPJ válido") if cnpj_valido(ctx["cnpj"]) else ("erro", f"CNPJ {ctx['cnpj']} é inválido (dígitos não conferem)"))
    if ctx.get("rep_cpf"):
        out.append(("ok", "CPF do representante válido") if cpf_valido(ctx["rep_cpf"])
                   else ("erro", "CPF do representante é inválido (dígitos não conferem)"))
    if not (p.get("orgao") or "").strip():
        out.append(("erro", "Falta o órgão"))
    if not (p.get("processo") or "").strip():
        out.append(("erro", "Falta o nº do processo/edital"))
    itens, total = calcular(p)
    if not itens:
        out.append(("erro", "A proposta não tem itens"))
    else:
        sem_preco = [i.get("item") or str(n + 1) for n, i in enumerate(itens) if i["_u"] <= 0]
        sem_qtd = [i.get("item") or str(n + 1) for n, i in enumerate(itens) if i["_q"] <= 0]
        sem_desc = [i.get("item") or str(n + 1) for n, i in enumerate(itens) if not (i.get("descricao") or "").strip()]
        acima = [i.get("item") or str(n + 1) for n, i in enumerate(itens) if num(i.get("ref")) and i["_u"] > num(i.get("ref"))]
        if sem_preco:
            out.append(("erro", "Itens sem preço: " + ", ".join(sem_preco[:15])))
        if sem_qtd:
            out.append(("erro", "Itens sem quantidade: " + ", ".join(sem_qtd[:15])))
        if sem_desc:
            out.append(("erro", "Itens sem descrição: " + ", ".join(sem_desc[:15])))
        if acima:
            out.append(("atencao", "Preço acima do valor de referência do órgão nos itens: " + ", ".join(acima[:15]) +
                        " – pode ser desclassificado"))
        if not (sem_preco or sem_qtd or sem_desc):
            out.append(("ok", f"{len(itens)} item(ns) com descrição, quantidade e preço · total {brl(total)}"))
        sem_marca = [i.get("item") or str(n + 1) for n, i in enumerate(itens) if not (i.get("marca") or "").strip()]
        if sem_marca and len(sem_marca) < len(itens):
            out.append(("atencao", "Alguns itens têm marca/modelo e outros não: " + ", ".join(sem_marca[:15])))
    c = {**PADRAO_COND, **(p.get("cond") or {})}
    val, minimo = num(c.get("validade")), num((p.get("exig") or {}).get("validade"))
    if minimo and val < minimo:
        out.append(("erro", f"Validade da proposta ({val:g} dias) é menor que a exigida pelo edital ({minimo:g} dias)"))
    elif not val:
        out.append(("atencao", "Validade da proposta não está em dias – confira com o edital"))
    elif val < 60 and not minimo:
        out.append(("atencao", f"Validade de {val:g} dias – a maioria dos editais pede pelo menos 60"))
    else:
        out.append(("ok", f"Validade da proposta: {val:g} dias" + (f" (edital pede {minimo:g})" if minimo else "")))
    for k, nome in (("entrega", "prazo de entrega"), ("pagamento", "condição de pagamento")):
        if (c.get(k) or "").strip().lower() in ("", "conforme o edital"):
            out.append(("atencao", f"O {nome} está como “conforme o edital” – se o edital pedir o prazo exato, escreva"))
    for nome, st, dias in certidoes:
        if st == "vencida":
            out.append(("erro", f"Certidão vencida: {nome}"))
        elif st == "vence":
            out.append(("atencao", f"Certidão vence em {dias} dia(s): {nome}"))
    if certidoes and all(st == "ok" for _, st, _ in certidoes):
        out.append(("ok", f"{len(certidoes)} certidão(ões) válidas"))
    elif not certidoes:
        out.append(("atencao", "Nenhuma certidão cadastrada para esta empresa (Documentos → Certidões)"))
    return out


def texto_para_ia(p, ctx):
    itens, total = calcular(p)
    c = {**PADRAO_COND, **(p.get("cond") or {})}
    linhas = [f"EMPRESA: {ctx.get('razao', '')} – CNPJ {ctx.get('cnpj', '')} – porte {ctx.get('porte') or 'demais'}",
              f"ÓRGÃO: {p.get('orgao', '')} · PROCESSO: {p.get('processo', '')} · MODALIDADE: {p.get('modalidade', '')}",
              f"OBJETO: {p.get('objeto', '')}", "ITENS (lote | item | descrição | marca | unidade | qtd | unitário | total):"]
    for i in itens:
        linhas.append(f"{i.get('lote', '')} | {i.get('item', '')} | {(i.get('descricao') or '')[:300]} | {i.get('marca', '')} | "
                      f"{i.get('unidade', '')} | {qtd_txt(i['_q'])} | {brl(i['_u'])} | {brl(i['_t'])}")
    linhas += [f"TOTAL: {brl(total)}", f"VALIDADE: {c['validade']} dias · ENTREGA: {c['entrega']} · PAGAMENTO: {c['pagamento']} · "
               f"GARANTIA: {c['garantia']}"]
    return "\n".join(linhas)


PROMPT_CONFERIR = """Você é um analista de licitações. Compare a PROPOSTA DA EMPRESA com o EDITAL (e anexos) e aponte o que pode
causar desclassificação ou pedido de correção. Responda SOMENTE com JSON válido, neste formato:
{"resumo": "uma frase com a conclusão",
 "achados": [{"nivel": "erro" | "atencao" | "ok", "onde": "item 3 / validade / declaração X…", "problema": "o que está errado",
              "sugestao": "como corrigir"}]}
Verifique: itens/lotes faltando ou sobrando; descrição, unidade ou quantidade diferente do edital; preço acima do valor máximo;
validade, prazo de entrega e pagamento compatíveis; exigência de marca/modelo, catálogo, amostra ou planilha de custos;
documentos e declarações que o edital pede junto da proposta. Não invente: se o edital não falar de algo, não aponte.
No máximo 25 achados, os mais importantes primeiro."""
