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
# reserva, se a lista do Google não puder ser consultada (o Google aposenta modelos de tempos em tempos)
MODELOS_PADRAO = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-2.5-flash"]
_MODELOS_CACHE = {}


def _versao(nome):
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", nome)
    return float(m.group(1)) if m else 0.0


def modelos_disponiveis(chave_api):
    """Modelos 'flash' que a chave pode usar agora (consulta o Google; guarda 6 h). Mais novos primeiro."""
    c = _MODELOS_CACHE.get(chave_api)
    if c and time.time() - c[0] < 6 * 3600:
        return c[1]
    nomes = []
    try:
        url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200"
        req = urllib.request.Request(url, headers={"x-goog-api-key": chave_api})
        with urllib.request.urlopen(req, timeout=30) as r:
            for m in json.load(r).get("models", []):
                n = (m.get("name") or "").split("/")[-1]
                if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                    continue
                if "flash" not in n or re.search(r"image|tts|audio|live|embed|thinking|exp|preview", n):
                    continue
                nomes.append(n)
    except Exception as ex:  # noqa: BLE001
        print("analise: não listei os modelos do Gemini:", ex, flush=True)
    # mais nova primeiro; 'lite' (mais fraco) depois do flash normal da mesma versão
    nomes.sort(key=lambda n: (-_versao(n), "lite" in n, len(n)))
    _MODELOS_CACHE[chave_api] = (time.time(), nomes)
    return nomes
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


ARQ_INSTRUCOES = PASTA / "analise_instrucoes.txt"
ARQ_EMPRESA = PASTA / "analise_empresa.txt"


GLOBAIS = set()   # logins que usam os arquivos antigos (o painel preenche com o dono das empresas antigas)


def _arq_usuario(base, login):
    lg = re.sub(r"[^a-z0-9._-]", "", login or "")
    if not lg or lg in GLOBAIS:
        return base
    return base.with_name(f"{base.stem}_{lg}{base.suffix}")


def instrucoes(login=""):
    """Instruções editáveis pelo painel (vazio = padrão). Cada usuário tem as suas."""
    try:
        t = _arq_usuario(ARQ_INSTRUCOES, login).read_text(encoding="utf-8").strip()
        return t or PROMPT
    except Exception:  # noqa: BLE001
        return PROMPT


def instrucoes_personalizadas(login=""):
    a = _arq_usuario(ARQ_INSTRUCOES, login)
    return a.exists() and bool(a.read_text(encoding="utf-8").strip())


def perfil_empresa(login=""):
    try:
        return _arq_usuario(ARQ_EMPRESA, login).read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return ""


def salvar_config_ia(texto_instrucoes, texto_empresa, login=""):
    t = (texto_instrucoes or "").replace("\r\n", "\n").strip()
    _arq_usuario(ARQ_INSTRUCOES, login).write_text("" if t == PROMPT.strip() else t, encoding="utf-8")
    _arq_usuario(ARQ_EMPRESA, login).write_text((texto_empresa or "").replace("\r\n", "\n").strip(), encoding="utf-8")


def montar_pedido(contexto="", extra="", login=""):
    blocos = []
    if contexto:
        blocos.append(contexto)
    emp = perfil_empresa(login)
    if emp:
        blocos.append("SOBRE A NOSSA EMPRESA (use para avaliar se atendemos às exigências e o que falta):\n" + emp)
    blocos.append(instrucoes(login))
    if extra:
        blocos.append("PEDIDO ADICIONAL PARA ESTA ANÁLISE (tem prioridade):\n" + extra)
    return "\n\n".join(blocos)


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


RE_ETP = re.compile(r"estudos? tecnicos? preliminar|\betp\b|estudo preliminar")


def eh_etp(texto):
    """Nome/título de documento que é o Estudo Técnico Preliminar."""
    return bool(RE_ETP.search(buscas.norm(texto or "").replace("_", " ").replace("-", " ")))


def url_arquivo_pncp(cnpj, ano, seq, a):
    return a.get("url") or a.get("uri") or (f"https://pncp.gov.br/pncp-api/v1/orgaos/{cnpj}/compras/{ano}/{seq}"
                                            f"/arquivos/{a.get('sequencialDocumento')}")


def etps_pncp(cnpj, ano, seq, lista=None):
    """ETPs publicados no PNCP: [{"titulo", "url"}]."""
    if lista is None:
        lista = buscas._get(f"https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos") or []
    return [{"titulo": f"{a.get('tipoDocumentoNome') or ''}: {a.get('titulo') or ''}".strip(": ")[:160], "url": url_arquivo_pncp(cnpj, ano, seq, a)}
            for a in lista if a.get("statusAtivo", True) and eh_etp(f"{a.get('tipoDocumentoNome', '')} {a.get('titulo', '')}")]


def arquivos_pncp(cnpj, ano, seq, so_etp=False):
    """Baixa os documentos da licitação no PNCP: edital, termo de referência e ETP primeiro (o ETP nunca fica de fora)."""
    lista = buscas._get(f"https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos") or []
    def prioridade(a):
        t = buscas.norm(f"{a.get('tipoDocumentoNome', '')} {a.get('titulo', '')}")
        if eh_etp(t):
            return 2
        for i, chave in ((0, "edital"), (1, "termo de referencia"), (3, "anexo"), (4, "projeto basico"), (5, "estudo")):
            if chave in t:
                return i
        return 9
    lista = [a for a in lista if a.get("statusAtivo", True)]
    if so_etp:
        lista = [a for a in lista if eh_etp(f"{a.get('tipoDocumentoNome', '')} {a.get('titulo', '')}")]
    lista.sort(key=prioridade)
    partes, nomes = [], []
    for a in lista[:8]:
        url = url_arquivo_pncp(cnpj, ano, seq, a)
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
def gemini(chave_api, modelo, partes, pedido, prazo=None):
    """prazo = segundos no total (None = sem limite). Passou do prazo: desiste com uma mensagem clara."""
    fim = time.time() + prazo if prazo else None

    def resta():
        return (fim - time.time()) if fim else 300
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
    conteudo.append({"text": pedido})
    corpo = json.dumps({"contents": [{"role": "user", "parts": conteudo}],
                        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192}}).encode()
    erros = []
    fila = [modelo] + modelos_disponiveis(chave_api)[:4] + MODELOS_PADRAO
    tentados = set()
    while fila:
        m = fila.pop(0)
        if not m or m in tentados:
            continue
        if resta() < 5:
            erros.append("tempo esgotado")
            break
        tentados.add(m)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
        req = urllib.request.Request(url, corpo, {"Content-Type": "application/json", "x-goog-api-key": chave_api})
        for tentativa in range(3):
            try:
                with urllib.request.urlopen(req, timeout=max(5, min(300, resta()))) as r:
                    resp = json.load(r)
                cand = (resp.get("candidates") or [{}])[0]
                txt = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
                try:  # registra o custo deste uso (tokens informados pelo próprio Google)
                    import custos_ia
                    custos_ia.registrar(m, resp.get("usageMetadata"))
                except Exception as ex_c:  # noqa: BLE001
                    print("custos_ia:", ex_c, flush=True)
                if txt.strip():
                    return txt, m, usados
                erros.append(f"{m}: resposta vazia ({cand.get('finishReason', '')})")
                break
            except urllib.error.HTTPError as ex:
                msg = ex.read().decode("utf-8", "ignore")[:300]
                erros.append(f"{m}: HTTP {ex.code} {msg}")
                if ex.code == 429 and tentativa == 0 and resta() > 45:
                    time.sleep(30)
                    continue
                if ex.code in (500, 503) and tentativa < 2 and resta() > 10 * (tentativa + 1) + 15:   # "muita demanda"
                    time.sleep(10 * (tentativa + 1))
                    continue
                if ex.code == 404:  # modelo aposentado: o próprio Google diz qual usar
                    sug = re.findall(r"models/(gemini-[\w.-]+)", msg)
                    fila[:0] = [x for x in sug if x not in tentados]
                if ex.code in (400, 401, 403) and "API key" in msg:
                    raise RuntimeError("Chave do Gemini inválida. Confira em Configurações.") from ex
                break
            except Exception as ex:  # noqa: BLE001
                erros.append(f"{m}: {ex}")
                break
    if fim and time.time() >= fim - 5:
        raise RuntimeError("A IA (Google Gemini) demorou demais para responder – provavelmente está sobrecarregada. "
                           "Tente de novo em alguns minutos. (" + " | ".join(erros[-2:]) + ")")
    raise RuntimeError("O Gemini não conseguiu analisar: " + " | ".join(erros[-3:]))


# ------------------------------------------------------------------ execução
QUEM = lambda: ""   # noqa: E731 – o painel troca por "login de quem pediu"


def iniciar(chave, cfg, origem):
    """origem = {'tipo': 'pncp', 'cnpj','ano','seq', 'titulo'} ou {'tipo': 'upload', 'arquivos': [(nome, bytes)], 'titulo'}"""
    origem["usuario"] = QUEM() or origem.get("usuario", "")
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
            try:
                base["etp"] = etps_pncp(origem["cnpj"], origem["ano"], origem["seq"])
            except Exception:  # noqa: BLE001
                pass
        else:
            partes = [p for nome, dados in origem["arquivos"] for p in preparar(nome, dados)]
            nomes = [n for n, _ in origem["arquivos"]]
            contexto = ""
            base["etp"] = [{"titulo": n, "arquivo": n} for n in nomes if eh_etp(n)]
        import custos_ia
        custos_ia.contexto("analise", "", origem.get("titulo", ""), origem.get("usuario", ""))
        md, modelo, usados = gemini(api, cfg.get("GEMINI_MODELO", "").strip(), partes,
                                    montar_pedido(contexto, origem.get("extra", ""), origem.get("usuario", "")))
        base.update(custo=custos_ia.ultimo())
        base.update(status="pronto", md=md, extra=origem.get("extra", ""), modelo=modelo, arquivos=usados or nomes,
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
    out, lista, tabela = [], False, []

    def fecha_tabela():
        if not tabela:
            return
        linhas = [[c.strip() for c in t.strip().strip("|").split("|")] for t in tabela
                  if not re.fullmatch(r"\|?[\s:|-]+\|?", t.strip())]
        if linhas:
            cab = "".join(f"<th>{_inline(c)}</th>" for c in linhas[0])
            corpo = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in ln) + "</tr>" for ln in linhas[1:])
            out.append(f"<table class='md'><tr>{cab}</tr>{corpo}</table>")
        tabela.clear()

    for linha in md.splitlines():
        s = linha.strip()
        if s.startswith("|") and s.count("|") >= 2:   # tabela em Markdown
            if lista:
                out.append("</ul>")
                lista = False
            tabela.append(s)
            continue
        fecha_tabela()
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
    fecha_tabela()
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


# ------------------------------------------------------------------ ETP explicado (para o licitante)
PROMPT_ETP = """Você ajuda uma EMPRESA LICITANTE (fornecedora) a entender o ESTUDO TÉCNICO PRELIMINAR (ETP) de uma licitação
(Lei 14.133/2021, art. 18). Os arquivos enviados são o ETP e, quando houver, um resumo da análise do edital.
Explique em português simples e objetivo. Use SOMENTE o que está nos documentos; se não houver, escreva "não informado".
Responda SOMENTE com um JSON válido (sem markdown), neste formato:
{"necessidade": "qual problema/necessidade o órgão quer resolver (1-3 frases)",
 "solucao": "a solução escolhida e por quê (1-3 frases)",
 "alternativas": ["outras soluções que o órgão considerou e descartou"],
 "quantidades": "como chegaram às quantidades (consumo histórico, projeção) – com números se houver",
 "estimativa": {"valor": "valor estimado total ou 'não informado'", "metodo": "como o preço foi estimado (painel de preços, cotações com fornecedores, contratações anteriores, mídia especializada...)",
   "fontes": ["fontes citadas"], "data_referencia": "data/mês das pesquisas de preço ou 'não informado'",
   "avaliacao": "atual|possivelmente defasado|não dá para saber", "motivo": "por que (ex.: pesquisa com mais de 6 meses, poucas cotações, só 1 fonte)"},
 "requisitos": ["requisitos/especificações importantes da solução, de forma curta"],
 "riscos": ["riscos que o ETP aponta"],
 "contradicoes": [{"etp": "o que o ETP diz", "edital": "o que o edital/termo de referência exige diferente", "item": "nº do item ou ''"}],
 "pontos_esclarecimento": ["perguntas objetivas que o licitante pode fazer ao órgão com base no ETP"],
 "pontos_impugnacao": ["exigências que parecem restringir a competição ou não têm justificativa no ETP (só se houver base nos documentos)"],
 "dicas": ["3 a 5 dicas práticas para o licitante usar o ETP (preço, quantidade, entrega, marca...)"]}
"""


def ler_json_etp(txt):
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return {}
    t = lambda v, n=400: str(v or "")[:n]   # noqa: E731
    li = lambda v, k=8: [t(x, 300) for x in (v if isinstance(v, list) else []) if str(x).strip()][:k]   # noqa: E731
    est = d.get("estimativa") if isinstance(d.get("estimativa"), dict) else {}
    contr = [{"etp": t(c.get("etp"), 300), "edital": t(c.get("edital"), 300), "item": t(c.get("item"), 30)}
             for c in (d.get("contradicoes") or []) if isinstance(c, dict) and (c.get("etp") or c.get("edital"))][:8]
    return {"necessidade": t(d.get("necessidade"), 600), "solucao": t(d.get("solucao"), 600), "alternativas": li(d.get("alternativas"), 5),
            "quantidades": t(d.get("quantidades"), 600),
            "estimativa": {"valor": t(est.get("valor"), 80), "metodo": t(est.get("metodo"), 400), "fontes": li(est.get("fontes"), 6),
                           "data_referencia": t(est.get("data_referencia"), 60), "avaliacao": t(est.get("avaliacao"), 40), "motivo": t(est.get("motivo"), 300)},
            "requisitos": li(d.get("requisitos"), 10), "riscos": li(d.get("riscos"), 6), "contradicoes": contr,
            "pontos_esclarecimento": li(d.get("pontos_esclarecimento"), 8), "pontos_impugnacao": li(d.get("pontos_impugnacao"), 6),
            "dicas": li(d.get("dicas"), 5)}
