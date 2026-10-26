"""Vigia do edital no PNCP: para as licitações que o licitante está seguindo, percebe documento novo (errata, adendo,
esclarecimento, resposta a impugnação), mudança da data de abertura/encerramento e mudança de situação (suspensa, revogada…).
Só leitura da API pública do PNCP."""
import html
import re

import buscas

API = "https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}"


def ref_do_link(link):
    m = re.search(r"(\d{14})/(\d{4})/(\d+)", link or "")
    return m.groups() if m else None


def _data(txt):
    t = (txt or "")[:16]
    return t if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", t) else (t[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", t) else "")


def consultar(cnpj, ano, seq):
    """{"arquivos": {seq: "tipo: título (data)"}, "encerramento", "abertura", "situacao"} – campos ausentes ficam vazios."""
    base = API.format(cnpj=cnpj, ano=ano, seq=seq)
    out = {"arquivos": {}, "encerramento": "", "abertura": "", "situacao": ""}
    try:
        c = buscas._get(base, timeout=20) or {}
        if isinstance(c, dict):
            out["encerramento"] = _data(c.get("dataEncerramentoProposta"))
            out["abertura"] = _data(c.get("dataAberturaProposta"))
            out["situacao"] = str(c.get("situacaoCompraNome") or "")[:80]
    except Exception:  # noqa: BLE001
        pass
    arqs = buscas._get(base + "/arquivos", timeout=20) or []   # erro aqui sobe: não dá para comparar
    if isinstance(arqs, dict):
        arqs = arqs.get("documentos") or arqs.get("data") or []
    for a in arqs:
        if a.get("statusAtivo") is False:
            continue
        sq = str(a.get("sequencialDocumento") or a.get("url") or "")
        if sq:
            dt = (a.get("dataPublicacaoPncp") or "")[:10]
            out["arquivos"][sq] = (f"{a.get('tipoDocumentoNome') or 'Documento'}: {a.get('titulo') or ''}".strip(": ")
                                   + (f" ({dt[8:10]}/{dt[5:7]}/{dt[:4]})" if dt else ""))[:200]
    return out


def _br(iso):
    if not iso:
        return "—"
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" + (f" {iso[11:16]}" if len(iso) >= 16 else "")


def diferencas(antes, agora):
    """Lista de textos com o que mudou (vazia = nada)."""
    if not antes:
        return []
    out = []
    novos = [t for k, t in agora["arquivos"].items() if k not in (antes.get("arquivos") or {})]
    for t in novos[:6]:
        n = buscas.norm(t).replace("_", " ")
        if re.search(r"estudos? tecnicos? preliminar|\betp\b", n):
            out.append(f"📘 ETP publicado: {html.escape(t)} – veja como o órgão chegou ao preço (Editais IA → 📘 Explicar o ETP)")
        else:
            out.append(f"📄 Documento novo: {html.escape(t)}")
    removidos = [t for k, t in (antes.get("arquivos") or {}).items() if k not in agora["arquivos"]]
    for t in removidos[:3]:
        out.append(f"🗑 Documento retirado: {html.escape(t)}")
    if agora["encerramento"] and antes.get("encerramento") and agora["encerramento"] != antes["encerramento"]:
        out.append(f"📅 Data de encerramento das propostas/abertura mudou: {_br(antes['encerramento'])} → <b>{_br(agora['encerramento'])}</b>")
    if agora["abertura"] and antes.get("abertura") and agora["abertura"] != antes["abertura"]:
        out.append(f"📅 Início do recebimento de propostas mudou: {_br(antes['abertura'])} → {_br(agora['abertura'])}")
    if agora["situacao"] and antes.get("situacao") and agora["situacao"] != antes["situacao"]:
        out.append(f"⚠️ Situação mudou: {html.escape(antes['situacao'])} → <b>{html.escape(agora['situacao'])}</b>")
    return out
