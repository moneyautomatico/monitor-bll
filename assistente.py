"""Pergunte ao Expert Licitante: assistente com IA (Gemini) que responde em português usando os dados que o sistema
já coleta – processos, situação, classificação, mensagens do chat, prazos, certidões, concorrentes e oportunidades.
Só lê os dados; não muda nada. Não envia CPF nem senhas para a IA."""
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

import agenda
import analise
import oportunidades

PASTA = Path(__file__).resolve().parent
EMPRESAS = PASTA / "empresas"
ARQ = PASTA / "conversa_ia.json"
TRAVA = threading.Lock()
LIMITE_CONTEXTO = 150_000   # caracteres de dados enviados à IA por pergunta (menos = resposta mais rápida e barata)

REGRAS = """Você é o assistente do Expert Licitante, sistema que acompanha licitações públicas de um grupo de empresas
(mensagens do pregoeiro, classificação dos lotes, prazos, certidões, concorrentes e oportunidades).
Responda em português do Brasil, de forma direta e organizada (listas curtas e tabelas simples em Markdown quando ajudar).

REGRAS:
- Use SOMENTE os DADOS DO SISTEMA enviados. Se a resposta não estiver nos dados, diga claramente que o sistema não tem essa
  informação e sugira onde conferir (ex.: na plataforma, no edital). Nunca invente números, datas, nomes ou valores.
- Cite sempre o processo (nº e órgão), a empresa e o lote (L1, L2…) quando falar de um caso.
- Valores em reais no formato R$ 1.234,56. Datas no formato dd/mm/aaaa.
- "Nossas empresas" são as listadas em EMPRESAS. "Concorrentes" são as demais.
- Se a pergunta pedir opinião (ex.: "vale a pena?"), responda com base nos dados e deixe claro o que é estimativa.
- Hoje é {HOJE}.
"""


# ------------------------------------------------------------------ conversa (histórico curto)
def _arq(login=""):
    return PASTA / f"conversa_ia_{login}.json" if re.fullmatch(r"[a-z0-9._-]{3,32}", login or "") else ARQ


def conversa(login=""):
    try:
        return json.loads(_arq(login).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def salvar_conversa(msgs, login=""):
    with TRAVA:
        _arq(login).write_text(json.dumps(msgs[-30:], ensure_ascii=False), encoding="utf-8")
        _arq(login).chmod(0o600)


def limpar_conversa(login=""):
    salvar_conversa([], login)


# ------------------------------------------------------------------ dados para a IA
def _env(arq):
    c = {}
    for linha in arq.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" in linha and not linha.strip().startswith("#"):
            k, v = linha.split("=", 1)
            c[k.strip()] = v.strip()
    return c


def _termos(pergunta):
    t = pergunta or ""
    procs = re.findall(r"\b\d{1,5}\s*[/.-]\s*\d{4}\b", t)
    palavras = [w for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", t)
                if w.lower() not in {"quais", "qual", "quanto", "quantos", "quando", "onde", "como", "esta", "estao", "está",
                                     "estão", "sobre", "para", "processo", "processos", "empresa", "empresas", "lote", "lotes",
                                     "isso", "esse", "essa", "tem", "temos", "nossa", "nosso", "nossas", "nossos", "ganhamos",
                                     "perdemos", "hoje", "amanhã", "semana", "dias", "mais", "menos", "todos", "todas"}]
    return [re.sub(r"\s", "", p) for p in procs], palavras[:12]


def montar_dados(pergunta, permitidos=None, ve_analise=None):
    def ok(nomes):
        lista = [x.strip().upper() for x in (nomes or "").split(",") if x.strip()]
        return permitidos is None or not lista or any(x in permitidos for x in lista)

    procs_q, palavras = _termos(pergunta)
    blocos = []
    agora = datetime.now()

    # empresas e processos
    emps, linhas_proc, linhas_classif, msgs_rel, msgs_rec = [], [], [], [], []
    for env in sorted(EMPRESAS.glob("*.env")) if EMPRESAS.exists() else []:
        c = _env(env)
        nome = (c.get("EMPRESA_NOME") or env.stem).upper()
        if permitidos is not None and nome.strip() not in permitidos:
            continue
        plat = (c.get("PLATAFORMA") or "bll").upper()
        emps.append(f"- {nome} ({plat})")
        db = EMPRESAS / env.stem / "bll_monitor.db"
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            for proc, prom, sit, disp in con.execute("SELECT processo, promotor, situacao, disputa FROM processos"):
                linhas_proc.append(f"{nome} | {plat} | {proc} | {prom} | situação: {sit or '—'}{' | sessão: ' + disp if disp else ''}")
            try:
                for proc, prom, lote, desc, fase, prim, melhor, rk, pos, lance, quando in con.execute(
                        "SELECT processo, promotor, lote, descricao, fase, primeiro, melhor, ranking, nossa_pos, nosso_lance, atualizado "
                        "FROM classificacao"):
                    try:
                        rank = json.loads(rk or "[]")
                    except ValueError:
                        rank = []
                    top = "; ".join(f"{x.get('pos') or 'DESCLASS.'}º {x.get('nome', '')[:45]} R$ {x.get('valor')}" for x in rank[:6])
                    linhas_classif.append(f"{nome} | {proc} | {prom[:50]} | L{lote} {(desc or '')[:60]} | fase {fase} | "
                                          f"nossa posição: {pos or 'fora/desclass.'} | nosso lance: {lance or '—'} | "
                                          f"1º: {prim} {melhor} | ranking: {top} | lido {quando}")
            except sqlite3.OperationalError:
                pass
            # mensagens: as do(s) processo(s) citado(s) / com as palavras da pergunta, e as das últimas 48 h
            for proc, prom, esc, hor, aut, txt, visto in con.execute(
                    "SELECT processo, promotor, escopo, horario, autor, texto, visto_em FROM mensagens ORDER BY rowid DESC LIMIT 6000"):
                linha = f"{nome} | {proc} | {prom[:40]} | [{hor or visto}] ({esc}) {aut}: {(txt or '')[:600]}"
                pn = re.sub(r"\s", "", proc or "")
                alvo = any(p in pn or pn in p for p in procs_q) if procs_q else False
                if alvo or (palavras and any(w.lower() in (txt or "").lower() or w.lower() in (prom or "").lower() for w in palavras)):
                    msgs_rel.append(linha)
                try:
                    if datetime.strptime((visto or "")[:16], "%d/%m/%Y %H:%M") >= agora - timedelta(hours=48):
                        msgs_rec.append(linha)
                except ValueError:
                    pass
            con.close()
        except Exception:  # noqa: BLE001
            continue
    def bloco(titulo, linhas, limite):
        txt = "\n".join(linhas)
        if len(txt) > limite:
            txt = txt[:limite] + "\n[... cortado por tamanho ...]"
        return f"{titulo}\n{txt or 'nenhum'}"

    blocos.append(bloco("EMPRESAS (nossas):", sorted(set(emps)), 3000))
    blocos.append(bloco("PROCESSOS (empresa | plataforma | nº | órgão | situação):", linhas_proc, 40000))
    blocos.append(bloco("MENSAGENS RELACIONADAS À PERGUNTA (processo citado ou palavras da pergunta; mais novas primeiro):",
                        msgs_rel[:500], 90000))
    # lotes dos processos citados primeiro
    linhas_classif.sort(key=lambda x: not any(p in re.sub(r"\s", "", x) for p in procs_q))
    blocos.append(bloco("CLASSIFICAÇÃO POR LOTE/ITEM (oficial da plataforma):", linhas_classif, 110000))

    # prazos e certidões
    pz = []
    for it in agenda.prazos()["itens"].values():
        q = datetime.fromisoformat(it["quando"])
        if it.get("status") == "aberto" and q > agora - timedelta(days=7) and ok(it.get("empresa")):
            pz.append(f"{q:%d/%m/%Y %H:%M} | {it.get('titulo')} | {it.get('empresa')} | {it.get('promotor', '')[:50]}"
                      f"{' | estimado' if it.get('estimado') else ''}")
    blocos.append("PRAZOS ABERTOS (agenda):\n" + ("\n".join(sorted(pz)) or "nenhum"))
    ct = []
    for c in agenda.certidoes()["itens"].values():
        if not ok(c.get("empresa")):
            continue
        st, dias = agenda.status_cert(c)
        ct.append(f"{c.get('empresa')} | {agenda.NOME_CERT.get(c.get('tipo'), c.get('tipo'))} | validade {c.get('validade') or '—'} | "
                  f"{ {'ok': 'válida', 'vence': 'vence em breve', 'vencida': 'VENCIDA', 'sem': 'sem validade'}[st] }"
                  f"{f' ({dias} dias)' if dias is not None else ''}")
    blocos.append("CERTIDÕES CADASTRADAS:\n" + ("\n".join(ct) or "nenhuma cadastrada"))

    # concorrentes
    try:
        import concorrentes
        d = concorrentes.analisar("", permitidos)
        r = d["resumo"]
        linhas = [f"Resumo: {r['lotes']} lotes disputados, {r['ganhos']} em 1º, gap médio quando perde: {r['gap_medio']}"]
        for x in d["concorrentes"][:60]:
            linhas.append(f"{x['nome']} | lotes juntos {x['lotes']} | à nossa frente {x['ganhou_de_nos']} | atrás {x['perdeu_para_nos']} | "
                          f"1º lugares {x['vitorias']} | preço médio vs nosso {x['dif_media']}% | desclass. {x['desclass']}")
        blocos.append("CONCORRENTES:\n" + "\n".join(linhas))
    except Exception:  # noqa: BLE001
        pass

    # oportunidades
    try:
        ops = [r for r in oportunidades.ler().get("itens", {}).values() if not r.get("descartado") and r.get("nivel") in ("alto", "medio")
               and (permitidos is None or not r.get("empresas") or set(r.get("empresas")) & permitidos)]
        ops.sort(key=lambda r: -r.get("pontos", 0))
        blocos.append("OPORTUNIDADES ABERTAS NO PNCP (encaixe alto/médio):\n" + "\n".join(
            f"{r.get('nivel')} | {r.get('orgao', '')[:50]} | {r.get('municipio')}/{r.get('uf')} | {(r.get('objeto') or '')[:140]} | "
            f"valor {r.get('valor')} | propostas até {r.get('fim', '')[:16]} | {r.get('link', '')}" for r in ops[:80]))
    except Exception:  # noqa: BLE001
        pass

    # análises de editais (títulos; o texto inteiro só da que combinar com a pergunta)
    an = [a for a in analise.listar() if a.get("status") == "pronto" and (ve_analise is None or ve_analise(a))]
    if an:
        blocos.append("EDITAIS ANALISADOS PELA IA:\n" + "\n".join(f"- {a.get('titulo', '')}" for a in an[:40]))
        for a in an[:40]:
            tit = (a.get("titulo") or "").lower()
            if any(p.split("/")[0] in tit for p in procs_q) or any(w.lower() in tit for w in palavras[:6]):
                blocos.append(f"ANÁLISE DO EDITAL “{a.get('titulo')}”:\n{(a.get('md') or '')[:30000]}")
                break

    blocos.append(bloco("MENSAGENS DAS ÚLTIMAS 48 HORAS:", msgs_rec[:300], 50000))
    texto = "\n\n".join(blocos)
    return texto[:LIMITE_CONTEXTO]


def perguntar(cfg, pergunta, login="", permitidos=None, ve_analise=None):
    api = cfg.get("GEMINI_API_KEY", "")
    if not api:
        raise RuntimeError("Falta a chave do Gemini. Cadastre em ⚙️ Configurações → Inteligência artificial.")
    hist = conversa(login)
    dados = montar_dados(pergunta + " " + " ".join(m["texto"] for m in hist[-4:] if m["papel"] == "voce"), permitidos, ve_analise)
    historico = "\n".join(f"{'USUÁRIO' if m['papel'] == 'voce' else 'ASSISTENTE'}: {m['texto'][:2000]}" for m in hist[-8:])
    pedido = (REGRAS.replace("{HOJE}", datetime.now().strftime("%d/%m/%Y %H:%M")) +
              (f"\nCONVERSA ATÉ AGORA:\n{historico}\n" if historico else "") +
              f"\nPERGUNTA DO USUÁRIO: {pergunta}")
    import custos_ia
    custos_ia.contexto("pergunta", "", pergunta[:100], login)
    md, modelo, _ = analise.gemini(api, cfg.get("GEMINI_MODELO", "").strip(), [("txt", "DADOS DO SISTEMA", dados)], pedido, prazo=110)
    custo = custos_ia.ultimo()
    hist += [{"papel": "voce", "texto": pergunta, "quando": datetime.now().strftime("%d/%m %H:%M")},
             {"papel": "ia", "texto": md.strip(), "quando": datetime.now().strftime("%d/%m %H:%M"), "modelo": modelo, "custo": custo}]
    salvar_conversa(hist, login)
    return md.strip(), modelo, custo
