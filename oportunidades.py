"""Oportunidades: licitações ABERTAS no PNCP que combinam com o que cada empresa já costuma disputar.

O perfil é montado sozinho com os dados que o monitor já guarda:
  - descrição dos lotes/itens disputados (tabela classificacao) -> produtos e categorias;
  - órgãos com quem a empresa já disputou (processos) -> municípios e estados (UF).
O usuário pode acrescentar ou tirar palavras e escolher os estados. Só leitura (API pública do PNCP).
"""
import html
import json
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import buscas

PASTA = Path(__file__).resolve().parent
EMPRESAS = PASTA / "empresas"
ARQ = PASTA / "oportunidades.json"
TRAVA = threading.Lock()
RODANDO = threading.Event()
norm = buscas.norm

# categoria -> (trechos dos itens que indicam a categoria, termo pesquisado no PNCP)
CATEGORIAS = {
    "Material de limpeza": (r"papel higien|papel toalha|sabao|sabonete|detergente|desinfetante|agua sanitaria|"
                            r"alvejante|rodos?(?![a-z])|vassoura|saco (plastico )?(para )?lixo|pano de (chao|prato)|esponja|"
                            r"limpa|multiuso|cera |inseticida|lixeira|carrinho .*limpeza|alcool|flanela|"
                            r"luva (de )?(latex|borracha)|balde|pa de lixo|amaciante|lustra", "material de limpeza"),
    "Higiene": (r"papel higien|sabonete|creme dental|escova dental|fralda|absorvente|shampoo|desodorante|"
                r"papel toalha|lenco", "higiene"),
    "Gêneros alimentícios": (r"arroz|feijao|acucar|cafe|leite|oleo de soja|carne|frango|biscoito|bolacha|macarrao|"
                             r"farinha|fuba|margarina|manteiga|sal refinado|tempero|extrato de tomate|sardinha|"
                             r"achocolatado|suco|polpa|fruta|verdura|legume|ovos?(?![a-z])|queijo|presunto|pao |cuscuz|"
                             r"flocos de milho|milho|aveia|mel |vinagre|alho|cebola|batata|tomate|banana", "generos alimenticios"),
    "Alimentação escolar": (r"merenda|alimentacao escolar|pnae", "alimentacao escolar"),
    "Copa e cozinha / descartáveis": (r"copo descart|prato descart|talher descart|guardanapo|garrafa termica|"
                                      r"filtro de papel|coador|bandeja|marmitex|papel aluminio|filme pvc|"
                                      r"panela|caneca|jarra|descartav", "copa e cozinha"),
    "Material de expediente": (r"papel a4|papel sulfite|resma|caneta|lapis|borracha escolar|grampeador|grampo|clipe|"
                               r"pasta (suspensa|catalogo|az|aba)|envelope|cola |fita adesiva|marca texto|"
                               r"pincel atomico|corretivo|tesoura|perfurador|caderno|agenda|post-it|bloco (de )?(anotac|rascunho|nota|recado)", "material de expediente"),
    "Material escolar": (r"lapis de cor|giz de cera|massa de modelar|caderno|mochila|apontador|kit escolar", "material escolar"),
    "Informática e suprimentos": (r"toner|cartucho|computador|notebook|impressora|mouse|teclado|monitor(es)?(?![a-z])|pen drive|"
                                  r"hd externo|cabo (hdmi|usb|de rede)|roteador|nobreak|estabilizador", "informatica"),
    "Material hospitalar": (r"seringa|agulha|gaze|atadura|luva de procedimento|esparadrapo|algodao hidrofilo|"
                            r"mascara cirurgica|cateter|sonda|equipos? (macro|micro|para|de soro)|termometro|estetoscopio", "material hospitalar"),
    "Medicamentos": (r"comprimido|capsula|solucao injetavel|xarope|ampola|\bmg\b.*(cp|comp)", "medicamentos"),
    "Material de construção": (r"cimento|tijolo|bloco ceramico|areia|brita|vergalhao|tinta (acrilica|latex|esmalte)|"
                               r"telha|tubo pvc|conexoes? (pvc|soldave|roscave)|registro de (gaveta|pressao|esfera)|torneira|fio (eletrico|flexivel)|disjuntor|lampada", "material de construcao"),
    "Água e gás": (r"agua mineral|galao|gas de cozinha|glp|botijao", "agua mineral"),
    "EPI e uniformes": (r"botas?(?![a-z])|capacete|colete|uniforme|camiseta|jaleco|avental|protetor auricular|oculos de protecao", "uniformes"),
    "Combustíveis": (r"gasolina|diesel|etanol|lubrificante|oleo de motor", "combustivel"),
    "Pneus e autopeças": (r"pneu|camara de ar|bateria automotiva|pastilha de freio|filtro de oleo", "pneus"),
}
PALAVRAS_VAZIAS = set("""de da do das dos e em para com sem por a o as os um uma no na nos nas ao aos tipo cor
material produto item unidade unidades pacote caixa fardo kg g ml l litro litros cm mm m x und un pct cx
minimo maximo aproximadamente medindo contendo embalagem marca referencia similar""".split())
GENERICOS = set("""conjunto kit jogo par aquisicao fornecimento servico servicos registro lote item itens outros diversos
produtos materiais""".split())


# ------------------------------------------------------------------ arquivo
def ler():
    try:
        d = json.loads(ARQ.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    d.setdefault("cfg", {})
    d["cfg"].setdefault("extra", "")
    d["cfg"].setdefault("excluir", "")
    d["cfg"].setdefault("ufs", [])
    d["cfg"].setdefault("desligados", [])
    d["cfg"].setdefault("alertar", False)
    d.setdefault("itens", {})
    d.setdefault("municipios", {})
    return d


def gravar(d):
    tmp = ARQ.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ARQ)


def salvar_cfg(extra, excluir, ufs, alertar, desligados):
    with TRAVA:
        d = ler()
        d["cfg"].update(extra=extra.strip()[:1000], excluir=excluir.strip()[:1000],
                        ufs=[u for u in ufs if u in buscas.UFS], alertar=bool(alertar),
                        desligados=sorted({norm(x) for x in desligados if x.strip()})[:300])
        gravar(d)


def salvar_bases(bases):
    """{nome_da_empresa: 'Cidade/UF'} – cidade-sede usada no raio do mapa (vazio = descobrir pelo CNPJ)."""
    with TRAVA:
        d = ler()
        d["cfg"]["bases"] = {k: v.strip()[:80] for k, v in bases.items() if k}
        gravar(d)


def descartar(oid, voltar=False):
    with TRAVA:
        d = ler()
        if oid in d["itens"]:
            d["itens"][oid]["descartado"] = not voltar
            gravar(d)


# ------------------------------------------------------------------ perfil
def cabeca(desc):
    """'PAPEL HIGIÊNICO, FOLHA DUPLA...' -> 'papel higienico' (nome do produto, sem medidas)."""
    t = norm(desc)
    t = re.split(r"[,;:(]| - | – |\.\s", t)[0]
    pal = []
    for w in t.split():
        if re.search(r"\d", w) or len(pal) >= 4:
            break
        pal.append(w)
    while pal and pal[-1] in PALAVRAS_VAZIAS:
        pal.pop()
    nome = " ".join(pal)
    if not nome or len(nome) < 4 or all(w in PALAVRAS_VAZIAS | GENERICOS for w in pal) or pal[0] in GENERICOS:
        return ""
    return nome


def municipio_do_orgao(promotor):
    t = norm(promotor)
    m = re.search(r"(?:municipio|prefeitura municipal|prefeitura|camara municipal|fundo municipal de \w+)\s+(?:de|do|da|dos|das)\s+(.+)", t)
    nome = (m.group(1) if m else "").split(" - ")[0].split("/")[0].strip(" .-")
    return nome if 3 <= len(nome) <= 40 and "..." not in promotor else ""


def perfis():
    """{nome_da_empresa: {"cats": Counter, "produtos": Counter, "orgaos": set, "municipios": set, "processos": n}}
    Empresas com o mesmo nome em plataformas diferentes (ex.: VC na BLL e na BNC) viram um perfil só."""
    out = defaultdict(lambda: {"cats": Counter(), "produtos": Counter(), "orgaos": set(), "municipios": set(),
                               "processos": set(), "ids": []})
    if not EMPRESAS.exists():
        return {}
    for env in sorted(EMPRESAS.glob("*.env")):
        nome = env.stem
        for linha in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if linha.startswith("EMPRESA_NOME="):
                nome = linha.split("=", 1)[1].strip().strip('"') or nome
        db = EMPRESAS / env.stem / "bll_monitor.db"
        if not db.exists():
            continue
        p = out[nome.upper()]
        p["ids"].append(env.stem)
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            tem_classif = con.execute("SELECT 1 FROM sqlite_master WHERE name='classificacao'").fetchone()
            if tem_classif:
                vistos = set()
                for proc, prom, desc in con.execute("SELECT processo, promotor, descricao FROM classificacao"):
                    chave_proc = f"{proc}|{prom}"
                    p["processos"].add(chave_proc)
                    d = norm(desc)
                    if not d:
                        continue
                    for cat, (rx, _) in CATEGORIAS.items():
                        if (cat, chave_proc) not in vistos and re.search(r"(?<![a-z])(?:" + rx + ")", d):
                            vistos.add((cat, chave_proc))
                            p["cats"][cat] += 1
                    c = cabeca(desc)
                    if c and (c, chave_proc) not in vistos:
                        vistos.add((c, chave_proc))
                        p["produtos"][c] += 1
            for proc, prom in con.execute("SELECT processo, promotor FROM processos"):
                p["processos"].add(f"{proc}|{prom}")
                if prom and "..." not in prom:
                    p["orgaos"].add(prom.strip())
                    mun = municipio_do_orgao(prom)
                    if mun:
                        p["municipios"].add(mun)
            con.close()
        except Exception as ex:  # noqa: BLE001
            print("oportunidades: não li", db, ex, flush=True)
    return {k: dict(v, processos=len(v["processos"])) for k, v in out.items() if v["processos"]}


def termos_do_perfil(pf, cfg):
    """Lista de (termo, peso, tipo) usada para pesquisar e pontuar."""
    desl = set(cfg.get("desligados") or [])
    out = []
    for cat, n in pf["cats"].most_common():
        termo = CATEGORIAS[cat][1]
        if termo not in desl and cat.lower() not in desl:
            out.append((termo, 4 + min(n, 6), "categoria", cat))
    for prod, n in pf["produtos"].most_common(25):
        if prod not in desl and (n >= 2 or len(out) < 12):
            out.append((prod, 2 + min(n, 5), "produto", prod))
    for t in buscas.termos(cfg.get("extra")):
        out.append((t, 8, "seu termo", t))
    return out


# ------------------------------------------------------------------ UFs dos órgãos
def descobrir_ufs(d, municipios, limite=12):
    """Descobre a UF de cada município (consulta o PNCP uma vez e guarda)."""
    faltam = [m for m in municipios if m not in d["municipios"]][:limite]
    for mun in faltam:
        uf = ""
        try:
            itens, _, _ = buscas.buscar({"q": mun}, 1, 20)
            c = Counter(it.get("uf") for it in itens if norm(it.get("municipio_nome")) == mun)
            uf = c.most_common(1)[0][0] if c else ""
        except Exception:  # noqa: BLE001
            pass
        d["municipios"][mun] = uf
        time.sleep(0.5)
    return sorted({d["municipios"].get(m) for m in municipios if d["municipios"].get(m)})


# ------------------------------------------------------------------ pontuação
def _tem(termo, texto):
    return re.search(r"(?<![a-z0-9])" + re.escape(termo).replace(r"\ ", r"\s+"), texto) is not None


def pontuar(it, pf, termos, ufs_perfil):
    texto = norm(" ".join(str(it.get(k) or "") for k in ("title", "description")))
    pontos, achou = 0, []
    for termo, peso, tipo, rot in termos:
        if _tem(termo, texto) or (tipo == "categoria" and re.search(r"(?<![a-z])(?:" + CATEGORIAS.get(rot, ("^$",))[0] + ")", texto)):
            pontos += peso
            achou.append(rot)
    if not achou:
        return 0, [], ""
    org = norm(it.get("orgao_nome"))
    mun = norm(it.get("municipio_nome"))
    motivo = ""
    if any(norm(o) == org for o in pf["orgaos"]) or (mun and mun in pf["municipios"]):
        pontos += 6
        motivo = "órgão/município onde a empresa já disputou"
    elif it.get("uf") in ufs_perfil:
        pontos += 2
    return pontos, achou, motivo


def nivel(p):
    return "alto" if p >= 12 else "medio" if p >= 6 else "baixo"


# ------------------------------------------------------------------ atualização
def atualizar(enviar=None):
    """Pesquisa no PNCP as licitações abertas para cada perfil e guarda as que combinam."""
    if RODANDO.is_set():
        return "já está atualizando"
    RODANDO.set()
    try:
        with TRAVA:
            d = ler()
        cfg = d["cfg"]
        pfs = perfis()
        if not pfs:
            d["erro"] = "Ainda não há processos lidos pelo monitor para montar o perfil."
            with TRAVA:
                gravar(d)
            return d["erro"]
        todos_mun = sorted({m for pf in pfs.values() for m in pf["municipios"]})
        ufs_auto = descobrir_ufs(d, todos_mun)
        ufs = cfg["ufs"] or ufs_auto
        consultas = {}  # termo -> peso máximo (as consultas são compartilhadas entre empresas)
        tabela = {}
        for nome, pf in pfs.items():
            tabela[nome] = termos_do_perfil(pf, cfg)
            for termo, peso, tipo, _ in tabela[nome]:
                consultas[termo] = max(consultas.get(termo, 0), peso + (10 if tipo == "seu termo" else 0))
        lista = [t for t, _ in sorted(consultas.items(), key=lambda x: -x[1])][:18]
        achados, erros = {}, 0

        def pesquisa(termo):
            res = []
            for pag in range(1, 11):   # até 500 por termo
                its, total, erro = buscas.buscar({"q": termo, "status": "recebendo_proposta", "ufs": ufs,
                                                  "excluir": cfg.get("excluir", "")}, pag, 50)
                res += its
                if erro or total <= pag * 50:
                    return res, bool(erro)
            return res, False

        with ThreadPoolExecutor(4) as ex:
            for res, erro in ex.map(pesquisa, lista):
                erros += erro
                for it in res:
                    achados[str(it.get("id"))] = it
        agora = datetime.now()
        novos_alto = []
        ufs_perfil = set(ufs)
        for oid, it in achados.items():
            melhor = (0, [], "", "")
            empresas = []
            for nome, pf in pfs.items():
                pts, quais, motivo = pontuar(it, pf, tabela[nome], ufs_perfil)
                if pts:
                    empresas.append(nome)
                    if pts > melhor[0]:
                        melhor = (pts, quais, motivo, nome)
            if not melhor[0]:
                continue
            ant = d["itens"].get(oid, {})
            cnpj, ano, seq = buscas.ref(it)
            reg = {"id": oid, "titulo": it.get("title") or "", "objeto": (it.get("description") or "")[:900],
                   "orgao": it.get("orgao_nome") or "", "municipio": it.get("municipio_nome") or "", "uf": it.get("uf") or "",
                   "modalidade": it.get("modalidade_licitacao_nome") or "", "valor": it.get("valor_total_estimado"),
                   "fim": it.get("data_fim_vigencia") or "", "publicada": it.get("data_publicacao_pncp") or "",
                   "cnpj": cnpj, "ano": ano, "seq": seq, "link": buscas.link_pncp(it),
                   "origem": it.get("link_sistema_origem") or "",
                   "pontos": melhor[0], "nivel": nivel(melhor[0]), "termos": melhor[1][:8], "motivo": melhor[2],
                   "empresas": empresas, "achada_em": ant.get("achada_em") or agora.strftime("%Y-%m-%d %H:%M"),
                   "descartado": ant.get("descartado", False)}
            if not ant and d.get("ultima") and reg["nivel"] == "alto":
                novos_alto.append(reg)
            d["itens"][oid] = reg
        # tira as encerradas e as muito antigas
        limite = (agora - timedelta(days=45)).strftime("%Y-%m-%d")
        for oid in list(d["itens"]):
            r = d["itens"][oid]
            fim = (r.get("fim") or "")[:10]
            if (fim and fim < agora.strftime("%Y-%m-%d")) or r.get("achada_em", "")[:10] < limite:
                del d["itens"][oid]
        d["perfis"] = {n: {"cats": dict(pf["cats"].most_common(12)), "produtos": dict(pf["produtos"].most_common(20)),
                           "orgaos": len(pf["orgaos"]), "municipios": sorted(pf["municipios"])[:40],
                           "processos": pf["processos"], "termos": [t[3] for t in tabela[n]][:30]}
                       for n, pf in pfs.items()}
        d["ufs_auto"] = ufs_auto
        d["consultas"] = lista
        d["ultima"] = agora.strftime("%d/%m/%Y %H:%M")
        d["erro"] = "O PNCP não respondeu a algumas pesquisas; tento de novo na próxima." if erros else ""
        with TRAVA:
            atual = ler()
            for oid, r in d["itens"].items():  # preserva o que o usuário descartou enquanto rodava
                if oid in atual["itens"]:
                    r["descartado"] = atual["itens"][oid].get("descartado", r["descartado"])
            d["cfg"] = atual["cfg"]
            gravar(d)
        if enviar and d["cfg"].get("alertar") and novos_alto:
            for r in novos_alto[:10]:
                try:   # vai para o dono das empresas que combinam
                    enviar(formatar(r), empresas=r.get("empresas") or [])
                except TypeError:
                    enviar(formatar(r))
        return f"{sum(1 for r in d['itens'].values() if not r.get('descartado'))} oportunidade(s)"
    finally:
        RODANDO.clear()


def formatar(r):
    e = html.escape
    linhas = [f"🎯 <b>Oportunidade ({r['nivel']}) – {e(', '.join(r['empresas'][:2]))}</b>",
              f"<b>{e(r['orgao'])}</b> – {e(r['municipio'])}/{e(r['uf'])}",
              f"{e(r['modalidade'])} · {e(r['titulo'])}", e(r["objeto"][:350])]
    if r.get("valor"):
        linhas.append(f"Valor estimado: {buscas.moeda(r['valor'])}")
    if r.get("fim"):
        linhas.append(f"Propostas até: {buscas.data_br(r['fim'])}")
    linhas.append("Combina com: " + e(", ".join(r["termos"][:5])))
    linhas.append(r["link"])
    return "\n".join(linhas)


def ciclo(enviar, horas=3):
    """Atualiza em segundo plano a cada 3 horas (chamado pelo painel)."""
    time.sleep(90)
    while True:
        try:
            h = datetime.now().hour
            if 6 <= h <= 22:
                print("Oportunidades:", atualizar(enviar), flush=True)
        except Exception as ex:  # noqa: BLE001
            print("Erro nas oportunidades:", ex, flush=True)
        time.sleep(horas * 3600)
