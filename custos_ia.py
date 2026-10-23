"""Custos da IA (Google Gemini): registra cada uso com os tokens que o próprio Google informa na resposta
e calcula o custo em US$ e R$ (preços e cotação editáveis em ⚙️). Serve para mostrar o custo de cada uso,
o consumo do mês e o comunicado aos licitantes."""
import json
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "ia_uso.jsonl"
TRAVA = threading.Lock()
_LOCAL = threading.local()

# preços padrão (US$ por 1 milhão de tokens) – conferir em https://ai.google.dev/pricing e ajustar em ⚙️
PADRAO = {"IA_PRECO_ENTRADA": "0.75", "IA_PRECO_SAIDA": "3.75", "IA_PRECO_LITE_ENTRADA": "0.30",
          "IA_PRECO_LITE_SAIDA": "2.50", "IA_COTACAO_USD": "5.50", "IA_ACRESCIMO_PCT": "0", "IA_COBRAR": "1"}
RECURSOS = {
    "analise": ("🤖 Análise de edital", 0.30),
    "habilitacao": ("✅ Posso participar? (documentos do edital)", 0.25),
    "declaracoes": ("📄 Declarações exigidas pelo edital", 0.25),
    "peca": ("⚖️ Peça com IA (recurso, contrarrazões…)", 0.50),
    "pergunta": ("💬 Pergunte ao Licitatudo (por pergunta)", 0.20),
}


def _cfg():
    c = dict(PADRAO)
    try:
        for linha in (PASTA / "config.env").read_text(encoding="utf-8").splitlines():
            if "=" in linha and linha.split("=", 1)[0].strip() in PADRAO:
                k, v = linha.split("=", 1)
                if v.strip():
                    c[k.strip()] = v.strip()
    except Exception:  # noqa: BLE001
        pass
    return c


def _f(v, padrao=0.0):
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return padrao


def precos():
    c = _cfg()
    return {"entrada": _f(c["IA_PRECO_ENTRADA"]), "saida": _f(c["IA_PRECO_SAIDA"]),
            "lite_entrada": _f(c["IA_PRECO_LITE_ENTRADA"]), "lite_saida": _f(c["IA_PRECO_LITE_SAIDA"]),
            "cotacao": _f(c["IA_COTACAO_USD"], 5.5), "acrescimo": _f(c["IA_ACRESCIMO_PCT"]), "cobrar": c["IA_COBRAR"] != "0"}


def calcular(modelo, entrada, saida):
    p = precos()
    lite = "lite" in (modelo or "")
    usd = entrada / 1e6 * (p["lite_entrada"] if lite else p["entrada"]) + saida / 1e6 * (p["lite_saida"] if lite else p["saida"])
    brl = usd * p["cotacao"]
    return round(usd, 5), round(brl, 4), round(brl * (1 + p["acrescimo"] / 100), 4)


def contexto(recurso, empresa="", referencia="", usuario=""):
    """Define o que a PRÓXIMA chamada à IA nesta thread está fazendo (para registrar o custo no lugar certo)."""
    _LOCAL.ctx = {"recurso": recurso, "empresa": empresa or "", "ref": referencia or "", "usuario": usuario or ""}


def registrar(modelo, uso_google):
    """Chamado pelo analise.gemini com o 'usageMetadata' da resposta. Devolve o registro (com o custo)."""
    ctx = getattr(_LOCAL, "ctx", None) or {"recurso": "outro", "empresa": "", "ref": "", "usuario": ""}
    _LOCAL.ctx = None
    u = uso_google or {}
    entrada = int(u.get("promptTokenCount") or 0)
    saida = int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0)
    usd, brl, cliente = calcular(modelo, entrada, saida)
    reg = {"quando": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "recurso": ctx["recurso"], "empresa": ctx["empresa"],
           "ref": ctx["ref"][:120], "usuario": ctx.get("usuario", ""), "modelo": modelo, "entrada": entrada, "saida": saida, "usd": usd, "brl": brl, "cliente": cliente}
    with TRAVA:
        with ARQ.open("a", encoding="utf-8") as f:
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
        ARQ.chmod(0o600)
    _LOCAL.ultimo = reg
    return reg


def ultimo():
    """Último uso registrado nesta thread (para mostrar 'custo deste uso')."""
    r = getattr(_LOCAL, "ultimo", None)
    _LOCAL.ultimo = None
    return r


def usos(mes=""):
    out = []
    try:
        for linha in ARQ.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(linha)
            except ValueError:
                continue
            if not mes or r.get("quando", "").startswith(mes):
                out.append(r)
    except FileNotFoundError:
        pass
    return out


def estimativa(recurso):
    """Custo típico em R$ (média dos últimos 20 usos; sem histórico, um valor de referência)."""
    ult = [r["brl"] for r in usos() if r.get("recurso") == recurso][-20:]
    base = sum(ult) / len(ult) if ult else RECURSOS.get(recurso, ("", 0.3))[1]
    p = precos()
    return base, base * (1 + p["acrescimo"] / 100), len(ult)


def resumo_mes(mes):
    rs = usos(mes)
    por_rec = defaultdict(lambda: {"n": 0, "brl": 0.0, "cliente": 0.0, "tokens": 0})
    por_emp = defaultdict(lambda: {"n": 0, "brl": 0.0, "cliente": 0.0})
    por_usu = defaultdict(lambda: {"n": 0, "brl": 0.0, "cliente": 0.0})
    for r in rs:
        a = por_rec[r.get("recurso", "outro")]
        a["n"] += 1
        a["brl"] += r.get("brl", 0)
        a["cliente"] += r.get("cliente", 0)
        a["tokens"] += r.get("entrada", 0) + r.get("saida", 0)
        b = por_emp[r.get("empresa") or "(sem empresa)"]
        b["n"] += 1
        b["brl"] += r.get("brl", 0)
        b["cliente"] += r.get("cliente", 0)
        c = por_usu[r.get("usuario") or "(antes dos usuários)"]
        c["n"] += 1
        c["brl"] += r.get("brl", 0)
        c["cliente"] += r.get("cliente", 0)
    return {"por_usuario": dict(por_usu), "usos": rs, "por_recurso": dict(por_rec), "por_empresa": dict(por_emp),
            "total_brl": sum(r.get("brl", 0) for r in rs), "total_cliente": sum(r.get("cliente", 0) for r in rs),
            "total_usd": sum(r.get("usd", 0) for r in rs)}


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def usos_usuario(login, mes="", dono_antigo=""):
    """Usos de um usuário. Os registros de antes dos usuários (sem login) contam para o usuário antigo."""
    return [r for r in usos(mes) if (r.get("usuario") or dono_antigo) == login]
