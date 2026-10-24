"""Agenda de prazos e controle de certidões.

Prazos: detectados sozinhos nas mensagens que o monitor registra (chat do pregoeiro, diligências, registros da sessão)
– "prazo de 2 horas", "até 05/11/2026 às 10h", "sessão reaberta amanhã às 10h", "prazo de resposta ...", intenção de
recurso aceita (3 dias úteis) – e também cadastrados à mão (ou pelo botão 📅 nas oportunidades).
Certidões: validade de cada certidão por empresa, com o PDF guardado, avisos antes de vencer e pacote para habilitação.
Avisos no Telegram geral (e ntfy quando falta pouco)."""
import hashlib
import io
import json
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

PASTA = Path(__file__).resolve().parent
EMPRESAS = PASTA / "empresas"
ARQ_PRAZOS = PASTA / "prazos.json"
ARQ_CERT = PASTA / "certidoes.json"
DIR_CERT = PASTA / "certidoes"
TRAVA = threading.Lock()


def norm(t):
    return " ".join(unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower().split())


def _ler(arq, padrao):
    try:
        return json.loads(arq.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return padrao


def _gravar(arq, dados):
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(arq)
    arq.chmod(0o600)


# ================================================================== calendário (dias úteis)
def _pascoa(ano):
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mes = (h + l_ - 7 * m + 114) // 31
    dia = (h + l_ - 7 * m + 114) % 31 + 1
    return date(ano, mes, dia)


def feriados(ano):
    """Feriados nacionais (e Carnaval/Corpus Christi, sem expediente na maioria dos órgãos). Municipais não entram."""
    p = _pascoa(ano)
    fixos = [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25)]
    return {date(ano, m, d) for m, d in fixos} | {p - timedelta(days=48), p - timedelta(days=47), p - timedelta(days=2),
                                                 p + timedelta(days=60)}


def dia_util(d):
    return d.weekday() < 5 and d not in feriados(d.year)


def somar_dias_uteis(inicio, n):
    """Conta a partir do dia útil seguinte (regra do art. 183 da Lei 14.133); termina às 23:59 do último dia."""
    d = inicio.date() if isinstance(inicio, datetime) else inicio
    contados = 0
    while contados < n:
        d += timedelta(days=1)
        if dia_util(d):
            contados += 1
    return datetime.combine(d, datetime.max.time()).replace(microsecond=0, second=0)


def subtrair_dias_uteis(fim, n):
    """N dias úteis ANTES da data (ex.: impugnação até 3 dias úteis antes da abertura); termina às 23:59 desse dia."""
    d = fim.date() if isinstance(fim, datetime) else fim
    contados = 0
    while contados < n:
        d -= timedelta(days=1)
        if dia_util(d):
            contados += 1
    return datetime.combine(d, datetime.max.time()).replace(microsecond=0, second=0)


# ================================================================== detecção de prazos nas mensagens
NUMS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
        "nove": 9, "dez": 10, "doze": 12, "quinze": 15, "vinte": 20, "trinta": 30, "quarenta e oito": 48, "vinte e quatro": 24}
RE_DATA = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\D{0,25}?(\d{1,2})\s*(?:h|:|horas?)\s*(\d{2})?)?")
RE_HORA = re.compile(r"(?:as|a partir das)\s*(\d{1,2})\s*(?:h|:)\s*(\d{2})?")
RE_REL = re.compile(r"(?:prazo|em ate|no prazo|dentro de|prazo maximo|prazo de ate)\D{0,25}?(\d{1,3}|"
                    + "|".join(sorted(NUMS, key=len, reverse=True)) +
                    r")\s*(?:\(\s*[a-z ]+\s*\))?\s*(horas?|minutos?|dias? uteis|dias? corridos|dias?)")
GATILHOS = r"prazo|ate o dia|ate as|ate a data|data final|encerra|reabert|retomad|remarcad|reinicio|sessao|limite|convoc|" \
           r"apresent|envi|anex|entreg|amostra|diligenc|recurso|contrarraz|impugna|esclarec"


def _base(horario, visto):
    for t in (horario, visto):
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s*\|?\s*(\d{2}):(\d{2})", t or "")
        if m:
            d, mo, a, h, mi = map(int, m.groups())
            try:
                return datetime(a, mo, d, h, mi)
            except ValueError:
                pass
    return None


def _tipo(t):
    if re.search(r"reabert|retomad|remarcad|reinicio|sessao (sera|fica)|prosseguimento", t):
        return "🗓️ Sessão"
    if re.search(r"contrarraz", t):
        return "⚖️ Contrarrazões"
    if re.search(r"recurso", t):
        return "⚖️ Recurso"
    if re.search(r"diligenc|exequib", t):
        return "🔎 Diligência"
    if re.search(r"amostra", t):
        return "📦 Amostras"
    if re.search(r"impugna|esclarec", t):
        return "❓ Impugnação/esclarecimento"
    if re.search(r"proposta (ajustada|readequada|atualizada)|readequ|ajustad", t):
        return "💰 Proposta ajustada"
    if re.search(r"document|anex|habilita|certid|atestado", t):
        return "📎 Documentos"
    return "⏰ Prazo"


def extrair(texto, base):
    """[(quando: datetime, tipo, estimado: bool)] encontrados no texto da mensagem."""
    if not texto or not base:
        return []
    t = norm(texto)
    if not re.search(GATILHOS, t):
        return []
    tipo = _tipo(t)
    out = []
    # datas explícitas (só as futuras em relação à mensagem e até 120 dias)
    for m in RE_DATA.finditer(t):
        d, mo, a, h, mi = m.groups()
        try:
            dt = datetime(int(a), int(mo), int(d), int(h) if h else 23, int(mi or 0) if h else 59)
        except ValueError:
            continue
        antes = t[max(0, m.start() - 70):m.start()]
        if not re.search(GATILHOS, antes) and not re.search(r"prazo|ate|final|limite", t[m.start() - 15:m.start()] if m.start() >= 15 else antes):
            continue  # data solta sem relação com prazo
        if base - timedelta(hours=1) <= dt <= base + timedelta(days=120):
            out.append((dt, tipo, False))
    # "amanhã às 10h"
    m = re.search(r"amanha\D{0,40}?" + RE_HORA.pattern, t)
    if m and not out:
        dt = (base + timedelta(days=1)).replace(hour=int(m.group(1)), minute=int(m.group(2) or 0), second=0)
        out.append((dt, tipo, False))
    # prazos relativos ("prazo de 2 (duas) horas", "em até 3 dias úteis") – só se não houver data explícita
    for m in ([] if out else RE_REL.finditer(t)):
        q, unid = m.group(1), m.group(2)
        n = int(q) if q.isdigit() else NUMS.get(q, 0)
        if not n or n > 90:
            continue
        if unid.startswith("hora"):
            dt = base + timedelta(hours=n)
        elif unid.startswith("minuto"):
            dt = base + timedelta(minutes=n)
        elif "uteis" in unid:
            dt = somar_dias_uteis(base, n)
        else:
            dt = datetime.combine((base + timedelta(days=n)).date(), datetime.max.time()).replace(microsecond=0, second=0)
        out.append((dt, tipo, False))
    # intenção de recurso aceita: razões em 3 dias úteis (art. 165, § 1º, I)
    if not out and re.search(r"intencao de recurso", t) and re.search(r"aceit|acolh|admit|registrad|manifest", t):
        out.append((somar_dias_uteis(base, 3), "⚖️ Razões do recurso (estimado: 3 dias úteis)", True))
    # sem duplicar o mesmo instante
    vistos, res = set(), []
    for x in out:
        k = x[0].strftime("%Y%m%d%H%M")
        if k not in vistos:
            vistos.add(k)
            res.append(x)
    return res[:3]


# ================================================================== prazos (armazenamento)
def prazos():
    return _ler(ARQ_PRAZOS, {"itens": {}, "marcas": {}})


def _id(*p):
    return hashlib.sha1("|".join(str(x) for x in p).encode()).hexdigest()[:12]


def adicionar(titulo, quando, empresa="", processo="", promotor="", obs="", origem="manual", eid="", extra=None):
    with TRAVA:
        d = prazos()
        pid = _id(origem, empresa, processo, titulo, quando.isoformat(), time.time() if origem == "manual" else "")
        if pid in d["itens"]:
            return pid
        d["itens"][pid] = {"titulo": titulo[:160], "quando": quando.strftime("%Y-%m-%dT%H:%M"), "empresa": empresa,
                           "eid": eid, "processo": processo, "promotor": promotor, "obs": obs[:1500], "origem": origem,
                           "status": "aberto", "avisos": [], "criado": datetime.now().strftime("%Y-%m-%dT%H:%M"),
                           **(extra or {})}
        _gravar(ARQ_PRAZOS, d)
        return pid


def alterar(pid, **campos):
    with TRAVA:
        d = prazos()
        if pid in d["itens"]:
            if "quando" in campos and campos["quando"] != d["itens"][pid].get("quando"):
                d["itens"][pid]["avisos"] = []   # data nova: avisa de novo
            d["itens"][pid].update(campos)
            _gravar(ARQ_PRAZOS, d)


def excluir(pid):
    with TRAVA:
        d = prazos()
        d["itens"].pop(pid, None)
        _gravar(ARQ_PRAZOS, d)


def sincronizar_cartao(cartao, itens, base):
    """Prazos legais de um cartão de licitação na agenda: cria, move (se a data mudou) e remove os que não valem mais.
    itens = [(tipo, titulo, quando)]; base = empresa, processo, promotor, criado_por."""
    with TRAVA:
        d = prazos()
        meus = {it.get("legal"): pid for pid, it in d["itens"].items() if it.get("cartao") == cartao and it.get("legal")}
        mudou = False
        querer = {t for t, _, _ in itens}
        for tipo, pid in meus.items():
            if tipo not in querer:
                d["itens"].pop(pid, None)
                mudou = True
        for tipo, titulo, quando in itens:
            q = quando.strftime("%Y-%m-%dT%H:%M")
            titulo = f"{titulo} – {base.get('processo', '')}"[:160]
            pid = meus.get(tipo)
            if pid and pid in d["itens"]:
                it = d["itens"][pid]
                if it.get("quando") != q or it.get("titulo") != titulo:
                    if it.get("quando") != q:
                        it["avisos"], it["status"] = [], "aberto"
                    it["quando"], it["titulo"] = q, titulo
                    mudou = True
                continue
            if quando < datetime.now():   # não cria aviso de prazo que já passou
                continue
            pid = _id("legal", cartao, tipo)
            d["itens"][pid] = {"titulo": titulo, "quando": q, "empresa": base.get("empresa", ""), "eid": "",
                               "processo": base.get("processo", ""), "promotor": base.get("promotor", ""),
                               "obs": base.get("obs", {}).get(tipo, "")[:1500] if isinstance(base.get("obs"), dict) else "",
                               "origem": "legal", "status": "aberto", "avisos": [], "cartao": cartao, "legal": tipo,
                               "criado_por": base.get("criado_por", ""), "criado": datetime.now().strftime("%Y-%m-%dT%H:%M")}
            mudou = True
        if mudou:
            _gravar(ARQ_PRAZOS, d)
        return mudou


def _chave_dup(it):
    """O mesmo prazo do mesmo processo (ex.: duas empresas nossas na mesma licitação) vira um item só."""
    return (it.get("processo", ""), it.get("promotor", ""), it.get("quando", ""), it.get("titulo", ""))


def _juntar_empresa(destino, nome, eid):
    nomes = [x.strip() for x in (destino.get("empresa") or "").split(",") if x.strip()]
    if nome and nome not in nomes:
        nomes.append(nome)
    destino["empresa"] = ", ".join(nomes)
    eids = destino.get("eids") or ([destino["eid"]] if destino.get("eid") else [])
    if eid and eid not in eids:
        eids.append(eid)
    destino["eids"] = eids


def _arrumar(d):
    """Uma vez só: junta os prazos repetidos e conclui os que já tinham vencido quando foram detectados."""
    if d.get("versao") == 2:
        return False
    vistos = {}
    for pid in sorted(d["itens"], key=lambda k: d["itens"][k].get("criado", "")):
        it = d["itens"].get(pid)
        if not it or it.get("origem") != "auto":
            continue
        if it.get("quando", "") < it.get("criado", "") and it.get("status") == "aberto":
            it["status"] = "feito"
            it["auto_vencido"] = True
        k = _chave_dup(it)
        if k in vistos:
            dest = d["itens"][vistos[k]]
            _juntar_empresa(dest, it.get("empresa", ""), it.get("eid", ""))
            dest["avisos"] = sorted(set(dest.get("avisos", [])) | set(it.get("avisos", [])))
            if it.get("status") == "aberto":
                dest["status"] = "aberto" if not dest.get("auto_vencido") else dest["status"]
            del d["itens"][pid]
        else:
            vistos[k] = pid
            _juntar_empresa(it, it.get("empresa", ""), it.get("eid", ""))
    d["versao"] = 2
    return True


def varrer_mensagens():
    """Lê as mensagens novas de cada empresa (desde a última varredura) e cria os prazos encontrados."""
    novos = []
    if not EMPRESAS.exists():
        return novos
    with TRAVA:
        d = prazos()
        if _arrumar(d):
            _gravar(ARQ_PRAZOS, d)
    marcas = d.setdefault("marcas", {})
    indice = {_chave_dup(it): pid for pid, it in d["itens"].items() if it.get("origem") == "auto"}
    juntados = {}
    for env in sorted(EMPRESAS.glob("*.env")):
        eid = env.stem
        db = EMPRESAS / eid / "bll_monitor.db"
        if not db.exists():
            continue
        nome = eid
        for linha in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if linha.startswith("EMPRESA_NOME="):
                nome = linha.split("=", 1)[1].strip() or eid
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            ultimo = marcas.get(eid)
            primeira = ultimo is None
            if primeira:  # 1ª vez: olha só os últimos 15 dias e não avisa como "novo" (evita enxurrada)
                ultimo = 0
                desde = datetime.now() - timedelta(days=15)
            else:
                desde = None
            linhas = con.execute("SELECT rowid, id, processo, promotor, escopo, horario, autor, texto, visto_em FROM mensagens "
                                 "WHERE rowid > ? ORDER BY rowid", (ultimo,)).fetchall()
            con.close()
        except Exception as ex:  # noqa: BLE001
            print("agenda: não li", db, ex, flush=True)
            continue
        for rowid, mid, proc, prom, esc, hor, aut, txt, visto in linhas:
            marcas[eid] = max(marcas.get(eid, 0), rowid)
            if (esc or "").startswith(("Classifica", "📊")) or "Mudança de situação" in (esc or ""):
                continue
            base = _base(hor, visto)
            if not base or (desde and base < desde):
                continue
            for quando, tipo, estimado in extrair(txt, base):
                # 1ª varredura: só prazos que ainda vão vencer; depois, até 2 dias de atraso (mensagem lida com demora)
                if quando < (datetime.now() if primeira else datetime.now() - timedelta(days=2)):
                    continue
                pid = _id("auto", eid, mid, quando.isoformat())
                if pid in d["itens"]:
                    continue
                item = {"titulo": f"{tipo} – {proc}", "quando": quando.strftime("%Y-%m-%dT%H:%M"), "empresa": nome, "eid": eid,
                        "eids": [eid], "processo": proc, "promotor": prom, "obs": f"{esc} · {aut} · {hor}\n{(txt or '')[:900]}",
                        "origem": "auto", "estimado": estimado, "status": "aberto", "avisos": [],
                        "criado": datetime.now().strftime("%Y-%m-%dT%H:%M")}
                k = _chave_dup(item)
                if k in indice:   # já existe (outra empresa nossa no mesmo processo): só acrescenta a empresa
                    dest = d["itens"].get(indice[k]) or next((i for p_, i, _ in novos if p_ == indice[k]), None)
                    if dest is not None:
                        _juntar_empresa(dest, nome, eid)
                        juntados[indice[k]] = (dest["empresa"], dest["eids"])
                    continue
                indice[k] = pid
                d["itens"][pid] = item
                novos.append((pid, item, primeira))
    with TRAVA:
        atual = prazos()
        atual["marcas"] = marcas
        for pid, item, _ in novos:
            atual["itens"].setdefault(pid, item)
        for pid, (emp, eids) in juntados.items():
            if pid in atual["itens"]:
                atual["itens"][pid]["empresa"], atual["itens"][pid]["eids"] = emp, eids
        _gravar(ARQ_PRAZOS, atual)
    return [(pid, item) for pid, item, primeira in novos if not primeira]


def situacao_prazo(item, agora=None):
    agora = agora or datetime.now()
    q = datetime.fromisoformat(item["quando"])
    if item.get("status") != "aberto":
        return "feito"
    if q < agora:
        return "atrasado"
    if q.date() == agora.date():
        return "hoje"
    if q - agora <= timedelta(days=7):
        return "semana"
    return "depois"


def falta_txt(q, agora=None):
    agora = agora or datetime.now()
    s = (q - agora).total_seconds()
    if s < 0:
        s = -s
        pre = "há "
    else:
        pre = "em "
    if s < 3600:
        return pre + f"{int(s // 60)} min"
    if s < 86400:
        return pre + f"{int(s // 3600)} h {int(s % 3600 // 60)} min"
    return pre + f"{int(s // 86400)} dia(s)"


# ================================================================== certidões
TIPOS_CERT = [
    ("federal", "CND Federal (Receita/PGFN)", "https://solucoes.receita.fazenda.gov.br/Servicos/certidaointernet/PJ/Emitir"),
    ("fgts", "CRF – FGTS (Caixa)", "https://consulta-crf.caixa.gov.br/consultacrf/pages/consultaEmpregador.jsf"),
    ("trabalhista", "CNDT – Trabalhista (TST)", "https://cndt-certidao.tst.jus.br/inicio.faces"),
    ("estadual", "Certidão Estadual (SEFAZ)", ""),
    ("municipal", "Certidão Municipal", ""),
    ("falencia", "Falência e recuperação judicial (TJ)", ""),
    ("balanco", "Balanço patrimonial", ""),
    ("alvara", "Alvará de funcionamento", ""),
    ("sanitario", "Alvará/licença sanitária", ""),
    ("contrato", "Contrato social / alterações", ""),
    ("atestado", "Atestado de capacidade técnica", ""),
    ("outra", "Outra", ""),
]
NOME_CERT = {k: n for k, n, _ in TIPOS_CERT}
AVISAR_DIAS = (15, 7, 3, 1, 0)


def certidoes():
    return _ler(ARQ_CERT, {"itens": {}})


def salvar_cert(cid, dados, arquivo=None):
    """cid vazio = nova. arquivo = (nome, bytes) do PDF."""
    with TRAVA:
        d = certidoes()
        cid = cid or _id("cert", dados.get("empresa"), dados.get("tipo"), time.time())
        atual = d["itens"].get(cid, {"avisos": []})
        if dados.get("validade") != atual.get("validade"):
            atual["avisos"] = []
        atual.update({k: str(v)[:300] for k, v in dados.items()})
        if arquivo and arquivo[1]:
            pasta = DIR_CERT / cid
            pasta.mkdir(parents=True, exist_ok=True)
            DIR_CERT.chmod(0o700)
            for velho in pasta.glob("*"):
                velho.unlink()
            nome = re.sub(r"[^\w.\- ]", "_", arquivo[0])[:120] or "certidao.pdf"
            (pasta / nome).write_bytes(arquivo[1])
            atual["arquivo"] = nome
        d["itens"][cid] = atual
        _gravar(ARQ_CERT, d)
        return cid


def excluir_cert(cid):
    with TRAVA:
        d = certidoes()
        d["itens"].pop(cid, None)
        _gravar(ARQ_CERT, d)
    pasta = DIR_CERT / cid
    if pasta.is_dir():
        for f in pasta.glob("*"):
            f.unlink()
        pasta.rmdir()


def arquivo_cert(cid):
    c = certidoes()["itens"].get(cid) or {}
    p = DIR_CERT / cid / (c.get("arquivo") or "")
    return p if c.get("arquivo") and p.is_file() else None


def status_cert(c, hoje=None):
    hoje = hoje or date.today()
    if not c.get("validade"):
        return "sem", None
    try:
        v = date.fromisoformat(c["validade"])
    except ValueError:
        return "sem", None
    dias = (v - hoje).days
    return ("vencida" if dias < 0 else "vence" if dias <= 15 else "ok"), dias


def ler_pdf_certidao(dados):
    """Tenta achar tipo, número, emissão e validade no PDF da certidão."""
    try:
        from pypdf import PdfReader
        texto = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(dados)).pages)
    except Exception:  # noqa: BLE001
        return {}
    t = norm(texto)
    out = {}
    if "fgts" in t or "regularidade do empregador" in t:
        out["tipo"] = "fgts"
    elif "debitos trabalhistas" in t or "cndt" in t:
        out["tipo"] = "trabalhista"
    elif "divida ativa da uniao" in t or "tributos federais" in t:
        out["tipo"] = "federal"
    elif "falencia" in t or "recuperacao judicial" in t:
        out["tipo"] = "falencia"
    elif "municipal" in t or "prefeitura" in t:
        out["tipo"] = "municipal"
    elif "estadual" in t or "sefaz" in t or "secretaria da fazenda" in t:
        out["tipo"] = "estadual"
    datas = lambda s: [date(int(a), int(m), int(d)) for d, m, a in re.findall(r"(\d{2})/(\d{2})/(\d{4})", s)]  # noqa: E731
    m = re.search(r"(?:valid[ao]\w*\s*(?:ate|:)?|validade\s*:?)[^0-9]{0,30}(\d{2}/\d{2}/\d{4})(?:\s*a\s*(\d{2}/\d{2}/\d{4}))?", t)
    if m:
        v = m.group(2) or m.group(1)
        out["validade"] = datas(v)[0].isoformat() if datas(v) else ""
    m = re.search(r"(?:emitida|expedi[cd]\w*|emissao|data da consulta)[^/]{0,40}?(\d{2}/\d{2}/\d{4})", t)
    if m and datas(m.group(1)):
        out["emissao"] = datas(m.group(1))[0].isoformat()
    for rx in (r"codigo de controle[^:]{0,30}:\s*([a-z0-9][a-z0-9.\-/]{5,40})",
               r"(?:certidao|certificado|crf|cndt)\s*(?:n[o.º°]*|numero)\s*:?\s*([0-9][0-9./-]{4,40})",
               r"certificacao\s*(?:numero|n[o.º°]*)?\s*:?\s*([0-9]{8,})"):
        m = re.search(rx, t)
        if m and re.search(r"\d", m.group(1)):
            out["numero"] = m.group(1).upper().strip(".-/")
            break
    if "validade" not in out:  # "válida por 180 dias"
        m = re.search(r"valid[ao] por\s*(\d{1,3})\s*dias", t)
        if m and out.get("emissao"):
            out["validade"] = (date.fromisoformat(out["emissao"]) + timedelta(days=int(m.group(1)))).isoformat()
    return out


VALIDADE_PADRAO = {"federal": 180, "fgts": 30, "trabalhista": 180}   # dias, quando o PDF não traz a validade


def validade_estimada(tipo, emissao):
    """Validade usual da certidão (CND federal 180 dias, CRF-FGTS 30, CNDT 180) a partir da emissão (ou de hoje)."""
    if tipo not in VALIDADE_PADRAO:
        return ""
    try:
        base = date.fromisoformat(emissao) if emissao else date.today()
    except ValueError:
        base = date.today()
    return (base + timedelta(days=VALIDADE_PADRAO[tipo])).isoformat()


def link_emissao(tipo, uf="", cidade=""):
    """(url, rótulo) para emitir a certidão. Federal/FGTS/CNDT: site oficial; as outras dependem do estado/cidade –
    abre a busca já com o estado/cidade para achar o site oficial."""
    oficial = {k: u for k, _, u in TIPOS_CERT if u}
    if tipo in oficial:
        return oficial[tipo], "emitir no site oficial"
    termos = {"estadual": f"certidão negativa de débitos estaduais SEFAZ {uf}",
              "municipal": f"certidão negativa de débitos municipais prefeitura {cidade} {uf}",
              "falencia": f"certidão negativa de falência e recuperação judicial Tribunal de Justiça {uf}"}.get(tipo)
    if not termos or not (uf or cidade):
        return "", ""
    return "https://www.google.com/search?q=" + urllib.parse.quote(termos.strip()), "achar o site oficial"


def pacote_zip(empresa):
    """ZIP com todas as certidões VÁLIDAS (com PDF) da empresa – para a habilitação."""
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for cid, c in certidoes()["itens"].items():
            if c.get("empresa") != empresa or status_cert(c)[0] == "vencida":
                continue
            p = arquivo_cert(cid)
            if p:
                nome = re.sub(r"[^\w.-]+", "_", f"{NOME_CERT.get(c.get('tipo'), 'certidao')}_{c.get('validade', '')}")
                z.write(p, f"{nome}{p.suffix or '.pdf'}")
                n += 1
    return buf.getvalue(), n


# ================================================================== avisos (Telegram / ntfy)
def ntfy(cfg, titulo, texto, prioridade="4"):
    topico = (cfg.get("NTFY_TOPICO") or "").strip()
    if not topico:
        return
    try:
        url = f"https://ntfy.sh/{topico}?" + urllib.parse.urlencode({"title": titulo, "priority": prioridade, "tags": "alarm_clock"})
        urllib.request.urlopen(urllib.request.Request(url, texto.encode("utf-8")[:3500], method="POST"), timeout=20).read()
    except Exception as ex:  # noqa: BLE001
        print("agenda ntfy:", ex, flush=True)


def _fmt(q):
    return q.strftime("%d/%m/%Y") + ("" if q.hour == 23 and q.minute == 59 else q.strftime(" às %H:%M"))


ROTEAR = None   # o painel define: (empresas, criado_por) -> ([(token, chat)], [tópicos ntfy]) – alerta vai para o dono


def _enviar_rota(enviar, cfg, nomes, criador, txt, ntfy_tit=None, ntfy_txt="", prio="4"):
    if ROTEAR is None:
        enviar(txt)
        if ntfy_tit:
            ntfy(cfg, ntfy_tit, ntfy_txt, prio)
        return
    tgs, topicos = ROTEAR(nomes, criador)
    for tok, chat in tgs:
        try:
            dados = urllib.parse.urlencode({"chat_id": chat, "text": txt, "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", dados, timeout=20).read()
        except Exception as ex:  # noqa: BLE001
            print("agenda telegram:", ex, flush=True)
    if ntfy_tit:
        for t in topicos:
            ntfy({"NTFY_TOPICO": t}, ntfy_tit, ntfy_txt, prio)


def avisar_prazos(enviar, cfg, novos=()):
    import html as h
    agora = datetime.now()
    with TRAVA:
        d = prazos()
    mudou = False
    for pid, it in d["itens"].items():
        if it.get("status") != "aberto":
            continue
        q = datetime.fromisoformat(it["quando"])
        falta = q - agora
        marcos = []
        if pid in dict(novos) and timedelta(0) < falta <= timedelta(days=3):
            marcos.append("novo")
        if timedelta(0) < falta <= timedelta(hours=24):
            marcos.append("24h")
        if timedelta(0) < falta <= timedelta(hours=2):
            marcos.append("2h")
        if falta <= timedelta(0) and falta > timedelta(hours=-12):
            marcos.append("venceu")
        pendentes = [mk for mk in marcos if mk not in it["avisos"]]
        if not pendentes:
            continue
        # uma mensagem só, com o marco mais urgente (os anteriores ficam marcados como avisados)
        mk = pendentes[-1]
        it["avisos"] += pendentes
        mudou = True
        topo = {"novo": "📅 <b>Novo prazo detectado</b>", "24h": "⏰ <b>Prazo em menos de 24 h</b>",
                "2h": "🚨 <b>Prazo em menos de 2 h</b>", "venceu": "❗ <b>Prazo vencido</b>"}[mk]
        if mk != "novo" and "novo" in pendentes:
            topo = "📅 <b>Novo prazo</b> – " + topo.split("<b>")[1].replace("</b>", "").lower()
            topo = topo.replace("prazo em", "<b>termina em").replace(" h", " h</b>", 1) if "em menos" in topo else topo
        txt = (f"{topo}\n<b>{h.escape(it['titulo'])}</b>\n{h.escape(it.get('empresa', ''))} · {h.escape(it.get('promotor', ''))}\n"
               f"Até: <b>{_fmt(q)}</b> ({falta_txt(q, agora)})"
               + ("\n<i>Data estimada – confira na ata/chat.</i>" if it.get("estimado") else "")
               + (f"\n\n{h.escape(it.get('obs', '')[:400])}" if "novo" in pendentes else ""))
        toca = mk in ("2h", "venceu") or ("novo" in pendentes and falta <= timedelta(hours=6))
        _enviar_rota(enviar, cfg, it.get("empresa", ""), it.get("criado_por", ""), txt,
                     f"Prazo {it['titulo']}" if toca else None, f"{it.get('empresa', '')} – até {_fmt(q)}", "5" if mk == "2h" else "4")
    if mudou:
        with TRAVA:
            atual = prazos()
            for pid, it in d["itens"].items():
                if pid in atual["itens"]:
                    atual["itens"][pid]["avisos"] = it["avisos"]
            _gravar(ARQ_PRAZOS, atual)


def avisar_certidoes(enviar):
    import html as h
    with TRAVA:
        d = certidoes()
    mudou = False
    for cid, c in d["itens"].items():
        st, dias = status_cert(c)
        if dias is None:
            continue
        marco = next((x for x in AVISAR_DIAS if dias <= x), None) if dias >= 0 else "vencida"
        if marco is None or str(marco) in c.setdefault("avisos", []):
            continue
        c["avisos"].append(str(marco))
        mudou = True
        nome = NOME_CERT.get(c.get("tipo"), c.get("tipo", "Certidão"))
        if marco == "vencida":
            msg = f"❗ <b>Certidão VENCIDA</b>: {h.escape(nome)}\n{h.escape(c.get('empresa', ''))} – venceu em {date.fromisoformat(c['validade']):%d/%m/%Y}"
        else:
            msg = (f"📑 <b>Certidão vence {'HOJE' if dias == 0 else f'em {dias} dia(s)'}</b>: {h.escape(nome)}\n"
                   f"{h.escape(c.get('empresa', ''))} – validade {date.fromisoformat(c['validade']):%d/%m/%Y}")
        url, _ = link_emissao(c.get("tipo"))
        msg += f'\n🔗 Emitir: {url}' if url else ""
        _enviar_rota(enviar, {}, c.get("empresa", ""), "", msg + "\nDepois envie o PDF no painel → Documentos → Certidões (ele lê a validade sozinho).")
    if mudou:
        with TRAVA:
            atual = certidoes()
            for cid, c in d["itens"].items():
                if cid in atual["itens"]:
                    atual["itens"][cid]["avisos"] = c["avisos"]
            _gravar(ARQ_CERT, atual)


def ciclo(enviar, cfg_func):
    """Roda no painel: a cada 3 min procura prazos novos e manda os avisos; certidões 1x por hora (8h–20h)."""
    time.sleep(45)
    ultima_cert = 0
    while True:
        try:
            novos = varrer_mensagens()
            avisar_prazos(enviar, cfg_func(), novos)
            h = datetime.now().hour
            if time.time() - ultima_cert > 3600 and 8 <= h <= 20:
                avisar_certidoes(enviar)
                ultima_cert = time.time()
        except Exception as ex:  # noqa: BLE001
            print("Erro na agenda:", ex, flush=True)
        time.sleep(180)
