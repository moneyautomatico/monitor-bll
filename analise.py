"""Análise de edital com IA gratuita (Google Gemini – faixa gratuita do Google AI Studio).

Baixa os arquivos da licitação no PNCP (ou usa um PDF enviado pelo painel), manda para o Gemini e
guarda o resultado em analises/<chave>.json. Só leitura: nada é enviado às plataformas de licitação.
"""
import base64
import hashlib
import html
import io
import json
import re
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

import buscas

PASTA = Path(__file__).resolve().parent
DIR = PASTA / "analises"
LIMITE_PDF = 18 * 1024 * 1024        # o Gemini aceita até ~20 MB por pedido
MODELOS_PADRAO = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]
EM_ANDAMENTO = set()
TRAVA = threading.Lock()

PROMPT = """Você é um analista de licitações públicas brasileiras (Lei 14.133/2021) ajudando uma empresa
fornecedora a decidir se participa e a se preparar. Leia TODO o edital e anexos enviados e responda em
português do Brasil, em Markdown, EXATAMENTE com as seções abaixo. Para cada informação, cite entre
parênteses o item/cláusula ou página do edital (ex.: "(item 8.3.1)"). Se algo não constar, escreva
"não informado no edital". Não invente. Não use tabelas; use listas.

## 1. Resumo
- Órgão, cidade/UF, número do processo/edital, modalidade, plataforma/sistema da disputa
- Objeto (em 1–2 frases)
- Critério de julgamento, modo de disputa, se é registro de preços (SRP), por item ou lote
- Valor estimado total (se divulgado)
- Exclusivo ou com cota para ME/EPP?

## 2. Datas e prazos
- Abertura da sessão / limite para envio de propostas
- Prazo para impugnação e pedidos de esclarecimento (e e-mail/canal)
- Prazo para enviar proposta ajustada e documentos após convocação
- Validade mínima da proposta

## 3. Itens principais
Liste os itens/lotes mais relevantes com quantidade, unidade e valor estimado (até 15; se houver mais,
diga quantos existem no total).

## 4. Documentos de habilitação (checklist)
Use "- [ ]" para cada documento, agrupados em:
### Habilitação jurídica
### Regularidade fiscal, social e trabalhista
### Qualificação econômico-financeira
(inclua índices exigidos como LG, SG, LC, capital ou patrimônio líquido mínimo, balanço)
### Qualificação técnica
(atestados de capacidade técnica: quantidade/percentual mínimo, registros em conselho, licenças, alvarás sanitários, ANVISA, INMETRO etc.)
### Declarações
### Outros (SICAF, cadastro na plataforma, procuração)

## 5. Proposta comercial – exigências
- O que deve constar na proposta (marca, modelo, fabricante, catálogo, ficha técnica, registro, amostra)
- Amostras ou prova de conceito: prazo e local
- Prazo e local de entrega/execução, garantia, frete
- Condições e prazo de pagamento

## 6. Garantias, sanções e riscos
- Garantia de proposta ou contratual (percentual)
- Multas e penalidades principais
- Visita técnica obrigatória?
- Pontos de atenção que podem desclassificar ou gerar prejuízo (seja específico)

## 7. Conclusão rápida
3 a 6 linhas: o que a empresa precisa providenciar com mais urgência e se há alguma exigência incomum.
"""


# ------------------------------------------------------------------ util
def chave_pncp(cnpj, ano, seq):
    return f"{re.sub(r'[^0-9]', '', str(cnpj))}-{re.sub(r'[^0-9]', '', str(ano))}-{re.sub(r'[^0-9]', '', str(seq))}"


def arquivo(chave):
    return DIR / f"{re.sub(r'[^a-z0-9-]', '', chave)}.json"


def ler(chave):
    try:
        return json.loads(arquivo(chave).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def gravar(chave, dados):
    DIR.mkdir(exist_ok=True)
    tmp = arquivo(chave).with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(arquivo(chave))


def _baixar(url, limite=60 * 1024 * 1024, timeout=90):
    req = urllib.request.Request(url, headers=buscas.UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(limite + 1), r.headers.get("Content-Type", ""), r.headers.get("Content-Disposition", "")
    except Exception:  # noqa: BLE001
        import subprocess
        r = subprocess.run(["curl", "-sL", "-m", str(timeout), "-A", buscas.UA["User-Agent"], "-D", "-", url],
                           capture_output=True, timeout=timeout + 5)
        if r.returncode != 0:
            raise
        cab, _, corpo = r.stdout.partition(b"\r\n\r\n")
        while corpo.startswith(b"HTTP/"):  # redirecionamentos
            cab, _, corpo = corpo.partition(b"\r\n\r\n")
        txt = cab.decode("latin-1").lower()
        tipo = re.search(r"content-type:\s*([^\r\n]+)", txt)
        disp = re.search(r"content-disposition:\s*([^\r\n]+)", txt)
        return corpo, tipo.group(1) if tipo else "", disp.group(1) if disp else ""


def texto_docx(dados):
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def texto_pdf(dados):
    try:
        from pypdf import PdfReader  # opcional
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(dados)).pages)
    except Exception:  # noqa: BLE001
        return ""


def preparar(nome, dados):
    """Converte um arquivo em partes para o Gemini: [('pdf', bytes) | ('txt', str)]."""
    n = nome.lower()
    if dados[:4] == b"%PDF" or n.endswith(".pdf"):
        return [("pdf", nome, dados)]
    if dados[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(dados))
        except zipfile.BadZipFile:
            return []
        if "word/document.xml" in z.namelist():
            return [("txt", nome, texto_docx(dados))]
        partes = []
        for info in z.infolist()[:30]:
            if info.file_size and info.file_size < 40 * 1024 * 1024:
                partes += preparar(info.filename, z.read(info))
        return partes
    if n.endswith((".txt", ".html", ".htm")):
        return [("txt", nome, re.sub(r"<[^>]+>", " ", dados.decode("utf-8", "ignore")))]
    return []


def arquivos_pncp(cnpj, ano, seq):
    """Baixa os documentos da licitação no PNCP, edital e termo de referência primeiro."""
    lista = buscas._get(f"https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos") or []
    def prioridade(a):
        t = buscas.norm(f"{a.get('tipoDocumentoNome', '')} {a.get('titulo', '')}")
        for i, chave in enumerate(["edital", "termo de referencia", "anexo", "projeto basico", "estudo"]):
            if chave in t:
                return i
        return 9
    lista = [a for a in lista if a.get("statusAtivo", True)]
    lista.sort(key=prioridade)
    partes, nomes = [], []
    for a in lista[:8]:
        url = a.get("url") or a.get("uri") or (f"https://pncp.gov.br/pncp-api/v1/orgaos/{cnpj}/compras/{ano}/{seq}"
                                               f"/arquivos/{a.get('sequencialDocumento')}")
        try:
            dados, _, disp = _baixar(url)
        except Exception:  # noqa: BLE001
            continue
        nome = (re.search(r'filename\*?="?([^";]+)', disp or "") or [None, a.get("titulo") or "documento"])[1]
        p = preparar(nome, dados)
        if p:
            partes += p
            nomes.append(f"{a.get('tipoDocumentoNome') or ''}: {a.get('titulo') or nome}".strip(": "))
    return partes, nomes


# ------------------------------------------------------------------ Gemini
def gemini(chave_api, modelo, partes, contexto=""):
    conteudo, usados, total = [], [], 0
    for tipo, nome, dado in partes:
        if tipo == "pdf":
            if total + len(dado) > LIMITE_PDF:
                txt = texto_pdf(dado)
                if txt.strip():
                    conteudo.append({"text": f"=== {nome} (texto) ===\n{txt[:400000]}"})
                    usados.append(nome + " (texto)")
                continue
            conteudo.append({"inline_data": {"mime_type": "application/pdf",
                                             "data": base64.b64encode(dado).decode()}})
            total += len(dado)
            usados.append(nome)
        else:
            conteudo.append({"text": f"=== {nome} ===\n{dado[:400000]}"})
            usados.append(nome)
    if not conteudo:
        raise RuntimeError("Nenhum arquivo legível (PDF, DOCX ou ZIP) foi encontrado para esta licitação.")
    conteudo.append({"text": (contexto + "\n\n" if contexto else "") + PROMPT})
    corpo = json.dumps({"contents": [{"role": "user", "parts": conteudo}],
                        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192}}).encode()
    erros = []
    for m in [modelo] + [x for x in MODELOS_PADRAO if x != modelo]:
        if not m:
            continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
        req = urllib.request.Request(url, corpo, {"Content-Type": "application/json", "x-goog-api-key": chave_api})
        for tentativa in range(2):
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    resp = json.load(r)
                cand = (resp.get("candidates") or [{}])[0]
                txt = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
                if txt.strip():
                    return txt, m, usados
                erros.append(f"{m}: resposta vazia ({cand.get('finishReason', '')})")
                break
            except urllib.error.HTTPError as ex:
                msg = ex.read().decode("utf-8", "ignore")[:300]
                erros.append(f"{m}: HTTP {ex.code} {msg}")
                if ex.code == 429 and tentativa == 0:
                    time.sleep(30)
                    continue
                if ex.code in (400, 401, 403) and "API key" in msg:
                    raise RuntimeError("Chave do Gemini inválida. Confira em Configurações.") from ex
                break
            except Exception as ex:  # noqa: BLE001
                erros.append(f"{m}: {ex}")
                break
    raise RuntimeError("O Gemini não conseguiu analisar: " + " | ".join(erros[-3:]))


# ------------------------------------------------------------------ execução
def iniciar(chave, cfg, origem):
    """origem = {'tipo': 'pncp', 'cnpj','ano','seq', 'titulo'} ou {'tipo': 'upload', 'arquivos': [(nome, bytes)], 'titulo'}"""
    with TRAVA:
        if chave in EM_ANDAMENTO:
            return
        EM_ANDAMENTO.add(chave)
    gravar(chave, {"status": "analisando", "inicio": datetime.now().strftime("%d/%m/%Y %H:%M"),
                   "titulo": origem.get("titulo", ""), "origem": {k: v for k, v in origem.items() if k != "arquivos"}})
    threading.Thread(target=_rodar, args=(chave, cfg, origem), daemon=True).start()


def _rodar(chave, cfg, origem):
    base = ler(chave) or {}
    try:
        api = cfg.get("GEMINI_API_KEY", "")
        if not api:
            raise RuntimeError("Falta a chave gratuita do Gemini. Cadastre em Configurações → Inteligência artificial.")
        if origem["tipo"] == "pncp":
            partes, nomes = arquivos_pncp(origem["cnpj"], origem["ano"], origem["seq"])
            contexto = f"Licitação publicada no PNCP: {origem.get('titulo', '')}"
        else:
            partes = [p for nome, dados in origem["arquivos"] for p in preparar(nome, dados)]
            nomes = [n for n, _ in origem["arquivos"]]
            contexto = ""
        md, modelo, usados = gemini(api, cfg.get("GEMINI_MODELO", "").strip(), partes, contexto)
        base.update(status="pronto", md=md, modelo=modelo, arquivos=usados or nomes,
                    fim=datetime.now().strftime("%d/%m/%Y %H:%M"))
    except Exception as ex:  # noqa: BLE001
        base.update(status="erro", erro=str(ex)[:800], fim=datetime.now().strftime("%d/%m/%Y %H:%M"))
    gravar(chave, base)
    with TRAVA:
        EM_ANDAMENTO.discard(chave)


def chave_upload(arquivos):
    h = hashlib.sha1()
    for nome, dados in arquivos:
        h.update(nome.encode())
        h.update(dados)
    return "up-" + h.hexdigest()[:16]


def listar():
    out = []
    for arq in sorted(DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100] if DIR.exists() else []:
        try:
            d = json.loads(arq.read_text(encoding="utf-8"))
            d["chave"] = arq.stem
            out.append(d)
        except Exception:  # noqa: BLE001
            pass
    return out


# ------------------------------------------------------------------ Markdown -> HTML (simples e seguro)
def _inline(t):
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<i>\1</i>", t)
    return t


def md_html(md):
    out, lista = [], False
    for linha in md.splitlines():
        s = linha.strip()
        m = re.match(r"^[-*]\s+\[( |x|X)\]\s+(.*)", s)
        b = re.match(r"^(?:[-*•]|\d+[.)])\s+(.*)", s)
        if (m or b) and not lista:
            out.append("<ul class='md'>")
            lista = True
        if not (m or b) and lista:
            out.append("</ul>")
            lista = False
        if m:
            out.append(f"<li class='chk'><label><input type='checkbox' {'checked' if m.group(1) != ' ' else ''}> "
                       f"{_inline(m.group(2))}</label></li>")
        elif b:
            out.append(f"<li>{_inline(b.group(1))}</li>")
        elif s.startswith("### "):
            out.append(f"<h4>{_inline(s[4:])}</h4>")
        elif s.startswith("## "):
            out.append(f"<h3>{_inline(s[3:])}</h3>")
        elif s.startswith("# "):
            out.append(f"<h3>{_inline(s[2:])}</h3>")
        elif s:
            out.append(f"<p>{_inline(s)}</p>")
    if lista:
        out.append("</ul>")
    return "\n".join(out)


def md_telegram(md, titulo="", limite=3800):
    """Lista de mensagens (HTML do Telegram) com até `limite` caracteres cada, cortando entre linhas."""
    linhas = [f"🤖 <b>Análise do edital</b>" + (f"\n{html.escape(titulo)}" if titulo else "")]
    for l in md.splitlines():
        t = html.escape(l.rstrip())
        t = re.sub(r"^#+\s*(.+)$", r"<b>\1</b>", t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"^\s*[-*]\s+\[[ xX]\]\s+", "☐ ", t)
        t = re.sub(r"^\s*[-*]\s+", "• ", t)
        if t.count("<b>") != t.count("</b>"):
            t = t.replace("<b>", "").replace("</b>", "")
        linhas.append(t)
    msgs, atual = [], ""
    for t in linhas:
        if len(atual) + len(t) + 1 > limite and atual:
            msgs.append(atual)
            atual = ""
        atual += t[:limite] + "\n"
    if atual.strip():
        msgs.append(atual)
    return msgs
