"""Peças com IA (Gemini): rascunhos de recurso, contrarrazões, impugnação, esclarecimento, intenção de recurso,
resposta a diligência e reequilíbrio – e leitura do edital para dizer quais declarações ele exige.
Usa o edital (já analisado em Editais (IA) ou enviado agora) + os fatos que o monitor registrou no processo.
Tudo é RASCUNHO: precisa de revisão humana antes de protocolar. O sistema não envia nada às plataformas."""
import hashlib
import json
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import analise
import documentos

PASTA = Path(__file__).resolve().parent
DIR = PASTA / "pecas"
EMPRESAS = PASTA / "empresas"
TRAVA = threading.Lock()

TIPOS = {
    "recurso": ("Recurso administrativo", "art. 165, I, da Lei 14.133/2021 – prazo de 3 dias úteis após a intimação/lavratura da ata"),
    "contrarrazoes": ("Contrarrazões ao recurso", "art. 165, § 4º, da Lei 14.133/2021 – 3 dias úteis após a intimação do recurso"),
    "intencao": ("Intenção de recurso (texto curto para o chat)", "manifestação imediata na sessão, com a síntese dos motivos"),
    "impugnacao": ("Impugnação ao edital", "art. 164 da Lei 14.133/2021 – até 3 dias úteis antes da abertura"),
    "esclarecimento": ("Pedido de esclarecimento", "art. 164 da Lei 14.133/2021 – até 3 dias úteis antes da abertura"),
    "diligencia": ("Resposta a diligência", "art. 64 da Lei 14.133/2021 – no prazo dado pelo pregoeiro"),
    "exequibilidade": ("Comprovação de exequibilidade da proposta", "art. 59, § 2º, da Lei 14.133/2021"),
    "reequilibrio": ("Pedido de reequilíbrio econômico-financeiro", "art. 124, II, 'd', da Lei 14.133/2021"),
}

REGRAS = """Você é um advogado especialista em licitações públicas brasileiras (Lei 14.133/2021 e LC 123/2006), redigindo
uma peça para uma EMPRESA LICITANTE. Escreva em português formal, claro e objetivo.

REGRAS OBRIGATÓRIAS:
1. Use SOMENTE fatos que estejam nos documentos e no histórico do processo enviados, ou nos pontos informados pela empresa.
   Se faltar um dado (data, número, valor, nome), escreva [CONFERIR: o que falta] no lugar – nunca invente.
2. Cite apenas artigos da Lei 14.133/2021, da LC 123/2006, da Constituição Federal e itens/cláusulas do próprio edital
   (com o número do item quando aparecer no edital). NÃO cite acórdãos, súmulas ou jurisprudência com número; se for útil,
   escreva "[inserir jurisprudência, se houver]".
3. Não use dados pessoais além dos fornecidos.
4. Formato Markdown simples: "# TÍTULO" para o título centralizado, "## Seção" para seções, parágrafos normais, listas com "-".
5. Ao final, inclua local e data (use exatamente: {LOCAL_DATA}) e o bloco de assinatura com os dados da empresa.
6. Depois da peça, escreva uma linha "---" e, abaixo, "### Notas para revisão" com: prazo legal aplicável, pontos marcados
   [CONFERIR], e riscos/fragilidades dos argumentos (essa parte NÃO vai no documento final).
"""

ESTRUTURA = {
    "recurso": "Endereçamento ao(à) Pregoeiro(a)/Agente de Contratação e à autoridade superior; qualificação da recorrente; "
               "## Da tempestividade; ## Dos fatos; ## Das razões (do direito) – rebatendo ponto a ponto a decisão recorrida; "
               "## Dos pedidos (reforma da decisão, ex.: desclassificação/inabilitação do vencedor ou reabilitação da recorrente; "
               "subsidiariamente, remessa à autoridade superior).",
    "contrarrazoes": "Endereçamento; qualificação da recorrida; ## Da tempestividade; ## Síntese do recurso; "
                     "## Das contrarrazões – rebatendo cada alegação do recurso; ## Dos pedidos (não provimento e manutenção "
                     "da decisão).",
    "intencao": "APENAS um parágrafo curto (no máximo 480 caracteres, sem título, sem assinatura, sem Markdown) manifestando a "
                "intenção de recorrer e resumindo os motivos, pronto para colar no chat da sessão. Depois, a linha '---' e as notas.",
    "impugnacao": "Endereçamento; qualificação; ## Da tempestividade; ## Dos fatos (o que o edital exige, com o número do item); "
                  "## Do direito (por que restringe a competitividade ou é ilegal); ## Dos pedidos (alteração/exclusão do item, "
                  "republicação com reabertura de prazo).",
    "esclarecimento": "Endereçamento; qualificação; ## Dos questionamentos – perguntas numeradas, objetivas, cada uma citando o "
                      "item do edital; ## Do pedido (resposta e divulgação a todos os interessados).",
    "diligencia": "Endereçamento; qualificação; ## Do objeto da diligência (o que foi pedido, com data/prazo); ## Dos "
                  "esclarecimentos e documentos (resposta ponto a ponto, listando os anexos); ## Do pedido (aceitação e "
                  "prosseguimento).",
    "exequibilidade": "Endereçamento; qualificação; ## Da convocação; ## Da exequibilidade da proposta (composição de custos, "
                      "margem, fornecedores, notas fiscais – usar [CONFERIR] para números não informados); ## Dos anexos; "
                      "## Do pedido (reconhecimento da exequibilidade e manutenção da proposta).",
    "reequilibrio": "Endereçamento; qualificação (contratada, nº do contrato/ata); ## Dos fatos (evento imprevisível/extraordinário "
                    "e variação de custos, com datas e percentuais [CONFERIR]); ## Do direito; ## Dos documentos; ## Do pedido "
                    "(revisão dos preços a partir da data do evento).",
}

PROMPT_DECL = """Leia o edital enviado e responda SOMENTE com um JSON válido (sem texto antes ou depois), neste formato:
{"exigidas": ["id", ...], "especificas": [{"titulo": "...", "texto": "..."}], "onde": "...", "observacoes": "..."}

- "exigidas": ids desta lista que o edital pede (por item do edital ou por anexo): %s
- "especificas": declarações que o edital pede e que NÃO estão na lista acima, ou que o edital exige em MODELO PRÓPRIO
  (anexo). Em "texto", transcreva o conteúdo do modelo do edital (só o corpo, sem cabeçalho e sem assinatura), trocando os
  campos em branco por: {RAZAO}, {CNPJ}, {ENDERECO}, {REP_NOME}, {REP_CPF}, {ORGAO}, {PROCESSO}.
- "onde": itens/anexos do edital que tratam das declarações (ex.: "item 9.3 e Anexo IV").
- "observacoes": se o edital exige declaração em modelo próprio, assinatura digital, reconhecimento de firma, ou se as
  declarações são feitas em campo próprio da plataforma (sem documento), diga aqui em até 3 frases.
"""


# ------------------------------------------------------------------ armazenamento
def arquivo(pid):
    return DIR / f"{pid}.json"


def ler(pid):
    try:
        return json.loads(arquivo(pid).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def gravar(pid, dados):
    DIR.mkdir(exist_ok=True)
    DIR.chmod(0o700)   # tem dados da empresa (CPF do representante)
    tmp = arquivo(pid).with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(arquivo(pid))


def listar(limite=60):
    out = []
    for a in sorted(DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limite] if DIR.exists() else []:
        try:
            d = json.loads(a.read_text(encoding="utf-8"))
            d["id"] = a.stem
            out.append(d)
        except Exception:  # noqa: BLE001
            pass
    return out


def novo_id(*partes):
    return hashlib.sha1(("|".join(partes) + str(time.time())).encode()).hexdigest()[:14]


# ------------------------------------------------------------------ fontes
def partes_edital(chave_analise):
    """Arquivos do edital já usados numa análise de Editais (IA)."""
    if not chave_analise:
        return [], []
    d = analise.ler(chave_analise) or {}
    o = d.get("origem") or {}
    if o.get("tipo") == "pncp":
        return analise.arquivos_pncp(o["cnpj"], o["ano"], o["seq"])
    pasta = analise.DIR / chave_analise
    partes, nomes = [], []
    for f in sorted(pasta.glob("*")) if pasta.is_dir() else []:
        partes += analise.preparar(f.name, f.read_bytes())
        nomes.append(f.name)
    return partes, nomes


def historico(ids_empresa, processo, promotor, limite=60000):
    """Fatos registrados pelo monitor: situação, classificação, diligências, eventos e mensagens do pregoeiro."""
    linhas, classif, situacao = [], [], ""
    for eid in ids_empresa:
        db = EMPRESAS / eid / "bll_monitor.db"
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            r = con.execute("SELECT situacao FROM processos WHERE processo=? AND promotor=?", (processo, promotor)).fetchone()
            situacao = situacao or (r[0] if r else "")
            for esc, hor, aut, txt in con.execute(
                    "SELECT escopo, horario, autor, texto FROM mensagens WHERE processo=? AND promotor=? ORDER BY rowid",
                    (processo, promotor)):
                linhas.append(f"[{hor or ''}] ({esc}) {aut}: {txt}")
            try:
                for lote, desc, fase, rk, pos in con.execute(
                        "SELECT lote, descricao, fase, ranking, nossa_pos FROM classificacao WHERE processo=? AND promotor=?",
                        (processo, promotor)):
                    try:
                        rank = json.loads(rk or "[]")
                    except ValueError:
                        rank = []
                    top = "; ".join(f"{x.get('pos') or 'DESCLASSIFICADA'} – {x.get('nome')} – R$ {x.get('valor')}" for x in rank[:8])
                    classif.append(f"Lote {lote} ({(desc or '')[:80]}) – fase {fase} – nossa posição: {pos or 'fora/desclassificada'}"
                                   f"\n   {top}")
            except sqlite3.OperationalError:
                pass
            con.close()
        except Exception:  # noqa: BLE001
            pass
    vistos, unicas = set(), []
    for x in linhas:
        if x not in vistos:
            vistos.add(x)
            unicas.append(x)
    txt = (f"Situação atual do processo: {situacao or 'não informada'}\n\nCLASSIFICAÇÃO POR LOTE:\n" + ("\n".join(classif) or "—")
           + "\n\nHISTÓRICO (mensagens do chat, registros da sessão, diligências e eventos), do mais antigo ao mais novo:\n"
           + "\n".join(unicas))
    if len(txt) > limite:  # guarda o começo (contexto) e o fim (o mais recente)
        txt = txt[:limite // 3] + "\n[... trecho do meio omitido ...]\n" + txt[-2 * limite // 3:]
    return txt


# ------------------------------------------------------------------ execução
def iniciar(pid, cfg, pedido):
    gravar(pid, {**{k: v for k, v in pedido.items() if k != "anexos"}, "status": "gerando",
                 "inicio": datetime.now().strftime("%d/%m/%Y %H:%M"),
                 "anexos": [n for n, _ in pedido.get("anexos", [])]})
    threading.Thread(target=_rodar, args=(pid, cfg, pedido), daemon=True).start()


def _qualificacao(ctx):
    return (f"Empresa: {ctx.get('razao')} – CNPJ {documentos.fmt_cnpj(ctx.get('cnpj'))} – sede: {ctx.get('endereco')}\n"
            f"Representante legal: {ctx.get('rep_nome')} ({ctx.get('rep_cargo') or 'representante legal'}), "
            f"CPF {documentos.fmt_cpf(ctx.get('rep_cpf'))}\n"
            f"Órgão: {ctx.get('orgao') or '[CONFERIR]'} – {ctx.get('modalidade') or 'Pregão Eletrônico'} nº "
            f"{ctx.get('processo') or '[CONFERIR]'}")


def _rodar(pid, cfg, pedido):
    base = ler(pid) or {}
    try:
        api = cfg.get("GEMINI_API_KEY", "")
        if not api:
            raise RuntimeError("Falta a chave do Gemini. Cadastre em Configurações → Inteligência artificial.")
        partes, nomes = partes_edital(pedido.get("edital", ""))
        for nome, dados in pedido.get("anexos", []):
            partes += analise.preparar(nome, dados)
            nomes.append(nome)
        ctx = pedido["ctx"]
        if pedido["tipo"] == "declaracoes":
            if not partes:
                raise RuntimeError("Escolha um edital já analisado ou envie o PDF do edital.")
            ids = ", ".join(f"{i} ({t})" for i, t, _ in documentos.DECLARACOES)
            md, modelo, usados = analise.gemini(api, cfg.get("GEMINI_MODELO", "").strip(), partes, PROMPT_DECL % ids)
            m = re.search(r"\{.*\}", md, re.S)
            res = json.loads(m.group(0)) if m else {}
            validos = {i for i, _, _ in documentos.DECLARACOES}
            base.update(status="pronto", resultado={
                "exigidas": [x for x in res.get("exigidas", []) if x in validos],
                "especificas": [{"titulo": str(x.get("titulo", ""))[:150], "texto": str(x.get("texto", ""))[:6000]}
                                for x in res.get("especificas", []) if isinstance(x, dict) and x.get("texto")][:12],
                "onde": str(res.get("onde", ""))[:300], "observacoes": str(res.get("observacoes", ""))[:800]},
                modelo=modelo, arquivos=usados or nomes, fim=datetime.now().strftime("%d/%m/%Y %H:%M"))
        else:
            titulo, prazo = TIPOS[pedido["tipo"]]
            hist = historico(pedido.get("ids_empresa", []), pedido.get("proc", ""), pedido.get("prom", "")) \
                if pedido.get("proc") else "Sem histórico do monitor para este processo."
            local_data = f"{ctx.get('cidade_data') or ctx.get('cidade') or '[CIDADE]'}, {documentos.data_extenso()}"
            texto = (REGRAS.replace("{LOCAL_DATA}", local_data) +
                     f"\n\nPEÇA A REDIGIR: {titulo} ({prazo}).\nESTRUTURA: {ESTRUTURA[pedido['tipo']]}\n\n"
                     f"DADOS DA EMPRESA E DO PROCESSO:\n{_qualificacao(ctx)}\n\n"
                     f"PONTOS QUE A EMPRESA QUER DEFENDER/PEDIR (prioridade máxima):\n{pedido.get('pontos') or '(não informados – use os fatos do histórico)'}\n\n"
                     f"HISTÓRICO REGISTRADO PELO MONITOR NA PLATAFORMA:\n{hist}\n\n"
                     "Os arquivos anexados acima são o edital e/ou documentos enviados pela empresa (ex.: recurso do "
                     "concorrente, ata, pedido de diligência). Redija agora a peça completa.")
            if not partes:
                partes = [("txt", "observação", "Nenhum arquivo do edital foi enviado; use apenas o histórico e os pontos.")]
            md, modelo, usados = analise.gemini(api, cfg.get("GEMINI_MODELO", "").strip(), partes, texto)
            peca, _, notas = md.partition("\n---")
            base.update(status="pronto", md=peca.strip(), notas=notas.strip().lstrip("-").strip(), modelo=modelo,
                        arquivos=usados or nomes, fim=datetime.now().strftime("%d/%m/%Y %H:%M"))
    except Exception as ex:  # noqa: BLE001
        base.update(status="erro", erro=str(ex)[:800], fim=datetime.now().strftime("%d/%m/%Y %H:%M"))
    gravar(pid, base)


# ------------------------------------------------------------------ Markdown da peça -> blocos (HTML e Word)
def blocos_md(md):
    out = []
    for linha in (md or "").splitlines():
        s = linha.strip()
        if not s:
            continue
        if s.startswith("# "):
            out.append(("titulo", s[2:].strip().upper()))
        elif s.startswith("## ") or s.startswith("### "):
            out.append(("secao", s.lstrip("#").strip()))
        elif re.match(r"^[-*•]\s+", s):
            out.append(("item", "• " + re.sub(r"^[-*•]\s+", "", s)))
        elif re.match(r"^\d+[.)]\s+", s):
            out.append(("item", s))
        else:
            out.append(("p", s))
    return out
