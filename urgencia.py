"""Decide se uma mensagem é URGENTE (pede ação rápida da empresa) – usado pelo monitor e pelo painel."""
import re
import unicodedata
from pathlib import Path

ARQ = Path(__file__).resolve().parent / "urgente_palavras.txt"   # editável pelo painel (Configurações)

# Trechos (sem acento, minúsculos). Cada linha: trecho | motivo mostrado no alerta
PADRAO = """
convoc | Convocação
anex | Pedido de anexo/documento
proposta ajustada | Proposta ajustada
proposta readequada | Proposta readequada
proposta atualizada | Proposta atualizada
readequ | Readequação
diligenc | Diligência
amostra | Amostra
contraproposta | Contraproposta/negociação
negocia | Negociação
presente | Pregoeiro perguntando se está presente
apresent | Pedido de apresentação
encaminh | Pedido de envio
enviar | Pedido de envio
prazo | Prazo
sob pena | Prazo com penalidade
horas | Prazo em horas
minutos | Prazo em minutos
recurso | Recurso
desclassific | Desclassificação
inabilit | Inabilitação
recusad | Recusa
reabert | Sessão reaberta
retomad | Sessão retomada
caiu de 1 | Perdeu o 1º lugar
"""


def norm(t):
    return " ".join(unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower().split())


def texto_atual():
    try:
        t = ARQ.read_text(encoding="utf-8").strip()
        return t or PADRAO.strip()
    except Exception:  # noqa: BLE001
        return PADRAO.strip()


def regras(texto_config=""):
    out = []
    for linha in (texto_config or texto_atual()).splitlines():
        if not linha.strip() or linha.strip().startswith("#"):
            continue
        trecho, _, motivo = linha.partition("|")
        trecho = norm(trecho)
        if trecho:
            out.append((trecho, motivo.strip() or trecho))
    return out


def classificar(texto, escopo="", autor="", texto_config=""):
    """Retorna o motivo (str) se a mensagem for urgente, senão ''."""
    t = norm(f"{escopo} {texto}")
    if norm(autor) in ("sistema",) and "mudanca de situacao" not in t and not re.search(r"convoc|recurso", t):
        return ""  # eventos automáticos comuns não tocam alarme
    for trecho, motivo in regras(texto_config):
        if re.search(r"(?<![a-z0-9])" + re.escape(trecho), t):  # início de palavra
            return motivo
    return ""
