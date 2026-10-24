"""Biblioteca de atestados de capacidade técnica: cadastra uma vez (PDF + o que foi fornecido, quanto e para quem)
e a IA diz quais atestados atendem a qualificação técnica de cada edital – para já entrarem no pacote de habilitação."""
import io
import json
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "atestados.json"
ARQ_MATCH = PASTA / "atestados_match.json"
DIR = PASTA / "atestados"
TRAVA = threading.Lock()
CAMPOS = ("empresa", "contratante", "cnpj_contratante", "objeto", "itens", "periodo_ini", "periodo_fim", "valor", "emissao", "obs")


def _ler(arq):
    try:
        return json.loads(arq.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _gravar(arq, d):
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(arq)


def listar(empresa=None):
    out = [{**v, "id": k} for k, v in _ler(ARQ).items() if empresa is None or v.get("empresa") == empresa]
    return sorted(out, key=lambda a: (a.get("empresa", ""), a.get("contratante", "").lower()))


def ler(aid):
    d = _ler(ARQ).get(aid)
    return {**d, "id": aid} if d else None


def atualizar(aid, campos):
    with TRAVA:
        d = _ler(ARQ)
        if aid in d:
            d[aid].update(campos)
            _gravar(ARQ, d)


def salvar(aid, dados, arquivo=None):
    """aid vazio = novo. arquivo = (nome, bytes). Guarda também o texto do PDF (para a IA comparar com o edital)."""
    with TRAVA:
        d = _ler(ARQ)
        aid = aid if aid in d else secrets.token_hex(6)
        atual = d.get(aid, {"criado": datetime.now().strftime("%Y-%m-%dT%H:%M")})
        atual.update({k: str(dados.get(k, ""))[:4000] for k in CAMPOS if k in dados})
        if arquivo and arquivo[1]:
            pasta = DIR / aid
            pasta.mkdir(parents=True, exist_ok=True)
            DIR.chmod(0o700)
            for velho in pasta.glob("*"):
                velho.unlink()
            nome = re.sub(r"[^\w.\- ]", "_", arquivo[0])[:120] or "atestado.pdf"
            (pasta / nome).write_bytes(arquivo[1])
            atual["arquivo"] = nome
            atual["texto"] = texto_pdf(arquivo[1])[:20000] if arquivo[1][:4] == b"%PDF" else ""
        d[aid] = atual
        _gravar(ARQ, d)
        return aid


def excluir(aid):
    with TRAVA:
        d = _ler(ARQ)
        d.pop(aid, None)
        _gravar(ARQ, d)
    pasta = DIR / aid
    if re.fullmatch(r"[0-9a-f]{12}", aid) and pasta.is_dir():
        for f in pasta.glob("*"):
            f.unlink()
        pasta.rmdir()


def arquivo(aid):
    a = ler(aid) or {}
    p = DIR / aid / (a.get("arquivo") or "")
    return p if a.get("arquivo") and p.is_file() else None


def texto_pdf(dados):
    try:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(dados)).pages[:15])
    except Exception:  # noqa: BLE001
        return ""


def titulo(a):
    return f"Atestado – {a.get('contratante') or 'capacidade técnica'}"[:90]


# ------------------------------------------------------------------ IA
PROMPT_LER = """Você recebe um ATESTADO DE CAPACIDADE TÉCNICA (documento emitido por um cliente, público ou privado, dizendo o que a empresa
forneceu/executou). Extraia os dados e responda SOMENTE com um JSON neste formato:
{"contratante": "nome de quem emitiu o atestado", "cnpj_contratante": "", "objeto": "resumo do que foi fornecido/executado em 1 ou 2 frases",
 "itens": [{"descricao": "item/serviço", "quantidade": "número", "unidade": "un/kg/m²/meses..."}],
 "periodo_ini": "AAAA-MM-DD ou vazio", "periodo_fim": "AAAA-MM-DD ou vazio", "valor": "valor total em reais ou vazio",
 "emissao": "AAAA-MM-DD ou vazio"}
Não invente: o que não estiver no documento fica vazio. Quantidades exatamente como no atestado."""

PROMPT_MATCH = """Você é um especialista em licitações (Lei 14.133/2021, art. 67 – qualificação técnica).
Recebe o EDITAL e a lista de ATESTADOS DE CAPACIDADE TÉCNICA que a empresa já tem (com id, contratante, objeto, itens e quantidades).
1) Liste as exigências de qualificação técnica do edital ligadas a atestados (parcelas de maior relevância, quantidades mínimas,
   percentuais – ex.: "50% do quantitativo" –, prazo/período, somatório permitido ou não).
2) Para cada exigência diga se os atestados atendem ("sim", "parcial" ou "nao"), quais ids servem e o porquê (some quantidades
   quando o edital permitir somatório; diga a conta feita).
3) Recomende o MENOR conjunto de atestados que atende tudo.
Responda SOMENTE com JSON:
{"exigencias": [{"texto": "exigência como no edital (com o item/página se houver)", "minimo": "quantidade/percentual exigido ou vazio",
  "atendida": "sim|parcial|nao", "atestados": ["id"], "comentario": "explicação curta com a conta"}],
 "recomendados": ["id"], "faltando": "o que nenhum atestado cobre (vazio se nada)",
 "observacoes": "alertas: exige firma reconhecida, registro no conselho (CREA/CRA/CRN...), CAT, atestado em nome da empresa etc."}
Se o edital não exigir atestado, devolva exigencias vazia e explique em observacoes. Não invente exigências."""


def texto_para_ia(lista):
    partes = []
    for a in lista:
        itens = a.get("itens") or ""
        partes.append(f"[id {a['id']}] Contratante: {a.get('contratante', '')} | Objeto: {a.get('objeto', '')} | Itens/quantidades: {itens} | "
                      f"Período: {a.get('periodo_ini', '')} a {a.get('periodo_fim', '')} | Valor: {a.get('valor', '')}\n"
                      f"Texto do atestado (trecho): {(a.get('texto') or '')[:2500]}")
    return "\n\n".join(partes)


def itens_txt(lista):
    """[{"descricao","quantidade","unidade"}] -> texto de uma linha por item."""
    if isinstance(lista, str):
        return lista
    return "\n".join(f"{i.get('descricao', '')} – {i.get('quantidade', '')} {i.get('unidade', '')}".strip(" –") for i in lista or [] if i)


# ------------------------------------------------------------------ resultados da comparação com o edital
def match(edital, empresa):
    return _ler(ARQ_MATCH).get(f"{edital}|{empresa}")


def gravar_match(edital, empresa, dados):
    with TRAVA:
        d = _ler(ARQ_MATCH)
        d[f"{edital}|{empresa}"] = dados
        _gravar(ARQ_MATCH, d)


def recomendados(edital, empresa):
    m = match(edital, empresa) or {}
    return list((m.get("resultado") or {}).get("recomendados") or []) if m.get("status") == "pronto" else []
