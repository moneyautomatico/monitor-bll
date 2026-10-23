"""Busca de licitações no PNCP (Portal Nacional de Contratações Públicas) + buscas salvas com alerta.

O PNCP recebe, por lei (14.133/2021), as licitações de todos os órgãos e de todas as plataformas
(Compras.gov.br, BLL, BNC, Licitanet, Portal de Compras Públicas...). Só leitura, API pública.
"""
import html
import json
import subprocess
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ_SALVAS = PASTA / "buscas_salvas.json"
ARQ_VISTOS = PASTA / "buscas_vistos.json"
API = "https://pncp.gov.br/api/search/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36", "Accept": "application/json, text/plain, */*",
      "Accept-Language": "pt-BR,pt;q=0.9", "Referer": "https://pncp.gov.br/app/editais"}
TRAVA = threading.Lock()

UFS = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI",
       "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]
MODALIDADES = [("6", "Pregão eletrônico"), ("8", "Dispensa"), ("4", "Concorrência eletrônica"),
               ("7", "Pregão presencial"), ("5", "Concorrência presencial"), ("9", "Inexigibilidade"),
               ("12", "Credenciamento"), ("1", "Leilão eletrônico"), ("13", "Leilão presencial"),
               ("2", "Diálogo competitivo"), ("3", "Concurso"), ("11", "Pré-qualificação"),
               ("10", "Manifestação de interesse")]
SITUACOES = [("recebendo_proposta", "Abertas (recebendo propostas)"), ("em_julgamento", "Em julgamento"),
             ("encerradas", "Encerradas"), ("", "Todas")]


def norm(txt):
    s = unicodedata.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(s.split())


def termos(txt):
    """'papel a4, "caneta azul"' -> ['papel a4', 'caneta azul'] (vírgula ou ; separa alternativas)."""
    return [norm(t.strip().strip('"')) for t in str(txt or "").replace(";", ",").split(",") if t.strip()]


def _get(url, timeout=25):
    """Tenta urllib (2x) e, se o PNCP fechar a conexão, usa o curl do sistema."""
    ultimo = None
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8") or "null")
        except Exception as ex:  # noqa: BLE001
            ultimo = ex
            time.sleep(1)
    try:
        r = subprocess.run(["curl", "-s", "--compressed", "-m", str(timeout), "-A", UA["User-Agent"],
                            "-H", "Accept: application/json", url], capture_output=True, text=True, timeout=timeout + 5)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
        ultimo = RuntimeError(f"curl código {r.returncode}")
    except Exception as ex:  # noqa: BLE001
        ultimo = ex
    raise ultimo


# ------------------------------------------------------------------ busca
def buscar(f, pagina=1, tam=50):
    """f = {q, excluir, ufs:[..], municipio, modalidade, status, vmin, vmax, itens}. Retorna (itens, total, erro)."""
    params = {"tipos_documento": "edital", "ordenacao": "-data", "pagina": pagina, "tam_pagina": tam}
    alternativas = termos(f.get("q"))
    if f.get("status"):
        params["status"] = f["status"]
    if f.get("ufs"):
        params["ufs"] = "|".join(f["ufs"])
    if f.get("modalidade"):
        params["modalidades"] = f["modalidade"]
    consultas = alternativas or [""]
    vistos, itens, total, erro = set(), [], 0, ""
    for termo in consultas[:6]:  # cada alternativa vira uma consulta (OU)
        p = dict(params, q=termo) if termo else params
        try:
            dados = _get(API + "?" + urllib.parse.urlencode(p))
        except Exception as ex:  # noqa: BLE001
            erro = f"PNCP não respondeu ({ex}). Tente de novo em instantes."
            continue
        total += int(dados.get("total") or 0)
        for it in dados.get("items", []):
            if it.get("id") not in vistos:
                vistos.add(it.get("id"))
                itens.append(it)
    itens = [it for it in itens if passa(it, f)]
    itens.sort(key=lambda it: it.get("data_publicacao_pncp") or "", reverse=True)
    if f.get("itens") and alternativas:
        conferir_itens(itens, alternativas)
    return itens, total, erro


_CACHE = {}


def buscar_tudo(f, limite=3000):
    """Todas as páginas da busca (até 'limite' resultados, por segurança), em paralelo. Guarda 10 min em memória."""
    if f.get("itens"):
        limite = min(limite, 300)  # conferir dentro dos itens é lento: no máximo 300
    chave = json.dumps(f, sort_keys=True, ensure_ascii=False)
    agora = time.time()
    c = _CACHE.get(chave)
    if c and agora - c[0] < 600:
        return c[1]
    itens, total, erro = buscar(f, 1, 50)
    vistos = {it.get("id") for it in itens}
    paginas = min((total + 49) // 50, (limite + 49) // 50)
    if paginas > 1 and not (erro and not itens):
        with ThreadPoolExecutor(6) as ex:
            for its, _, err in ex.map(lambda pg: buscar(f, pg, 50), range(2, paginas + 1)):
                erro = erro or err
                for it in its:
                    if it.get("id") not in vistos:
                        vistos.add(it.get("id"))
                        itens.append(it)
    itens.sort(key=lambda it: it.get("data_publicacao_pncp") or "", reverse=True)
    res = (itens[:limite], total, erro)
    if not erro:
        _CACHE[chave] = (agora, res)
        for k in [k for k, v in _CACHE.items() if agora - v[0] > 600]:
            _CACHE.pop(k, None)
    return res


def passa(it, f):
    """Filtros que o PNCP não faz (ou que reforçamos aqui)."""
    if f.get("ufs") and it.get("uf") not in f["ufs"]:
        return False
    if f.get("modalidade") and str(it.get("modalidade_licitacao_id")) != str(f["modalidade"]):
        return False
    mun = termos(f.get("municipio"))
    if mun and not any(m in norm(it.get("municipio_nome")) for m in mun):
        return False
    texto = norm(" ".join(str(it.get(k) or "") for k in ("title", "description", "orgao_nome", "unidade_nome")))
    if any(x in texto for x in termos(f.get("excluir"))):
        return False
    if it.get("cancelado"):
        return False
    v = it.get("valor_total_estimado") or 0
    try:
        if f.get("vmin") and v and v < float(f["vmin"]):
            return False
        if f.get("vmax") and v and v > float(f["vmax"]):
            return False
    except ValueError:
        pass
    return True


def ref(it):
    """(cnpj, ano, sequencial) da contratação."""
    return it.get("orgao_cnpj"), it.get("ano"), it.get("numero_sequencial")


def listar_itens(cnpj, ano, seq, maximo=500):
    out, pag = [], 1
    while len(out) < maximo:
        try:
            dados = _get(f"https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens"
                         f"?pagina={pag}&tamanhoPagina=100")
        except Exception:  # noqa: BLE001
            break
        if not dados:
            break
        out += dados
        if len(dados) < 100:
            break
        pag += 1
    return out


def conferir_itens(lista, alternativas):
    """Marca em cada licitação os itens cuja descrição contém alguma das palavras-chave."""
    def um(it):
        try:
            its = listar_itens(*ref(it), maximo=300)
        except Exception:  # noqa: BLE001
            its = []
        it["_itens_achados"] = [x for x in its if any(a in norm(x.get("descricao")) for a in alternativas)][:8]
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(um, lista[:60]))


def link_pncp(it):
    cnpj, ano, seq = ref(it)
    return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"


def data_br(txt):
    if not txt:
        return ""
    try:
        return datetime.fromisoformat(str(txt)[:19]).strftime("%d/%m/%Y %H:%M").replace(" 00:00", "")
    except ValueError:
        return str(txt)


def moeda(v):
    if not v:
        return "sigiloso / não informado"
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ------------------------------------------------------------------ buscas salvas
def _ler(arq, padrao):
    try:
        return json.loads(arq.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return padrao


def _gravar(arq, dados):
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(arq)


def salvas():
    return _ler(ARQ_SALVAS, [])


def salvar_busca(f, usuario=""):
    with TRAVA:
        lista = salvas()
        bid = f"b{int(time.time())}"
        lista.append({"id": bid, "nome": f.get("nome") or f.get("q") or "Busca", "q": f.get("q", ""),
                      "excluir": f.get("excluir", ""), "ufs": f.get("ufs", []), "municipio": f.get("municipio", ""),
                      "modalidade": f.get("modalidade", ""), "vmin": f.get("vmin", ""), "vmax": f.get("vmax", ""),
                      "itens": bool(f.get("itens")), "alertar": True, "criada": datetime.now().strftime("%d/%m/%Y %H:%M"),
                      "ultima": "", "novas_total": 0, "usuario": usuario})
        _gravar(ARQ_SALVAS, lista)
    return bid


def alterar_busca(bid, acao):
    with TRAVA:
        lista = salvas()
        if acao == "remover":
            lista = [b for b in lista if b["id"] != bid]
            vistos = _ler(ARQ_VISTOS, {})
            vistos.pop(bid, None)
            _gravar(ARQ_VISTOS, vistos)
        else:
            for b in lista:
                if b["id"] == bid:
                    b["alertar"] = acao == "ligar"
        _gravar(ARQ_SALVAS, lista)


def verificar_salvas(enviar, so_id=None):
    """Roda as buscas salvas (só licitações abertas) e manda para o Telegram as que ainda não foram vistas.
    Na primeira vez de cada busca, só memoriza o que já existe e manda um resumo (evita enxurrada)."""
    with TRAVA:
        lista = salvas()
        vistos = _ler(ARQ_VISTOS, {})
    for b in lista:
        if so_id and b["id"] != so_id:
            continue
        if not so_id and not b.get("alertar"):
            continue
        f = dict(b, status="recebendo_proposta")
        achados, _, erro = buscar(f, 1, 50)
        if erro and not achados:
            continue
        ja = set(vistos.get(b["id"], []))
        primeira = b["id"] not in vistos
        novos = [it for it in achados if it.get("id") not in ja]
        _env = enviar

        def enviar(t, _b=b, _e=_env):   # vai para o Telegram de quem criou a busca
            try:
                _e(t, usuario=_b.get("usuario", ""))
            except TypeError:
                _e(t)
        if primeira:
            enviar(f"🔎 <b>Busca salva ativada: {html.escape(b['nome'])}</b>\n"
                   f"{len(achados)} licitação(ões) aberta(s) agora. A partir de agora aviso só as novas.")
        else:
            for it in novos[:15]:
                enviar(formatar_alerta(b, it))
            if len(novos) > 15:
                enviar(f"🔎 <b>{html.escape(b['nome'])}</b>: mais {len(novos) - 15} nova(s). Veja no painel → Buscar.")
        vistos[b["id"]] = list(ja | {it.get("id") for it in achados})[-3000:]
        b["ultima"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        if not primeira:
            b["novas_total"] = b.get("novas_total", 0) + len(novos)
    with TRAVA:
        atuais = {b["id"]: b for b in salvas()}
        for b in lista:
            if b["id"] in atuais:
                atuais[b["id"]].update(ultima=b["ultima"], novas_total=b.get("novas_total", 0))
        _gravar(ARQ_SALVAS, list(atuais.values()))
        _gravar(ARQ_VISTOS, vistos)


def formatar_alerta(b, it):
    e = html.escape
    linhas = [f"🔎 <b>Nova licitação – {e(b['nome'])}</b>",
              f"<b>{e(it.get('orgao_nome') or '')}</b> – {e(it.get('municipio_nome') or '')}/{e(it.get('uf') or '')}",
              f"{e(it.get('modalidade_licitacao_nome') or '')} · {e(it.get('title') or '')}",
              e((it.get("description") or "")[:400])]
    if it.get("valor_total_estimado"):
        linhas.append(f"Valor estimado: {moeda(it['valor_total_estimado'])}")
    if it.get("data_fim_vigencia"):
        linhas.append(f"Propostas até: {data_br(it['data_fim_vigencia'])}")
    for x in (it.get("_itens_achados") or [])[:3]:
        linhas.append(f"• Item {x.get('numeroItem')}: {e((x.get('descricao') or '')[:120])}")
    linhas.append(link_pncp(it))
    return "\n".join(linhas)


def ciclo(enviar, minutos):
    """Loop em segundo plano (chamado pelo painel)."""
    time.sleep(60)
    while True:
        try:
            verificar_salvas(enviar)
        except Exception as ex:  # noqa: BLE001
            print("Erro nas buscas salvas:", ex, flush=True)
        time.sleep(max(10, minutos()) * 60)
