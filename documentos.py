"""Declarações padrão de licitação (Lei 14.133/2021), preenchidas com os dados da empresa e do processo.
Gera visualização para imprimir/salvar em PDF e arquivo Word (.docx) – sem bibliotecas extras.
Os textos são MODELOS GENÉRICOS: quando o edital trouxer modelo próprio nos anexos, use o do edital."""
import io
import json
import re
import urllib.request
import zipfile
from datetime import date
from html import escape
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "dados_empresas.json"
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]
PORTES = {"ME": "MICROEMPRESA (ME)", "EPP": "EMPRESA DE PEQUENO PORTE (EPP)", "": ""}
CAMPOS = ["cnpj", "razao", "endereco", "cidade", "porte", "rep_nome", "rep_cpf", "rep_cargo"]


# ------------------------------------------------------------------ dados da empresa
def _ler():
    try:
        return json.loads(ARQ.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def dados(nome):
    d = _ler().get(nome, {})
    return {k: d.get(k, "") for k in CAMPOS}


def salvar_dados(nome, valores):
    tudo = _ler()
    tudo[nome] = {k: str(valores.get(k, "")).strip()[:300] for k in CAMPOS}
    ARQ.write_text(json.dumps(tudo, ensure_ascii=False, indent=1), encoding="utf-8")
    ARQ.chmod(0o600)


def fmt_cnpj(c):
    n = re.sub(r"\D", "", c or "")
    return f"{n[:2]}.{n[2:5]}.{n[5:8]}/{n[8:12]}-{n[12:]}" if len(n) == 14 else (c or "")


def fmt_cpf(c):
    n = re.sub(r"\D", "", c or "")
    return f"{n[:3]}.{n[3:6]}.{n[6:9]}-{n[9:]}" if len(n) == 11 else (c or "")


def consultar_cnpj(cnpj):
    """Dados públicos da Receita (BrasilAPI): razão social, endereço, porte e sócios administradores."""
    n = re.sub(r"\D", "", cnpj or "")
    if len(n) != 14:
        raise ValueError("CNPJ precisa ter 14 números.")
    req = urllib.request.Request(f"https://brasilapi.com.br/api/cnpj/v1/{n}", headers={"User-Agent": "Licitatudo/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        j = json.load(r)
    end = ", ".join(x for x in [
        " ".join(x for x in [j.get("descricao_tipo_de_logradouro") or "", j.get("logradouro") or ""] if x).strip(),
        f"nº {j['numero']}" if j.get("numero") else "", j.get("complemento") or "", j.get("bairro") or "",
        f"{(j.get('municipio') or '').title()}/{j.get('uf') or ''}", f"CEP {j['cep']}" if j.get("cep") else ""] if x.strip())
    porte_txt = (j.get("porte") or j.get("descricao_porte") or "").upper()
    porte = "ME" if "MICRO" in porte_txt else "EPP" if "PEQUENO" in porte_txt else ""
    adm = next((s for s in j.get("qsa") or [] if re.search(r"administrador|titular|presidente|diretor",
                                                            s.get("qualificacao_socio", ""), re.I)), None)
    adm = adm or (j.get("qsa") or [None])[0]
    return {"cnpj": fmt_cnpj(n), "razao": (j.get("razao_social") or "").upper(), "endereco": end,
            "cidade": f"{(j.get('municipio') or '').title()}/{j.get('uf') or ''}", "porte": porte,
            "rep_nome": (adm or {}).get("nome_socio", "").upper(),
            "rep_cargo": "Sócio-Administrador" if adm and "socio" in (adm.get("qualificacao_socio", "").lower()
                                                                      .replace("ó", "o")) else ("Administrador" if adm else "")}


# ------------------------------------------------------------------ textos
def data_extenso(d=None):
    d = d or date.today()
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


DECLARACOES = [
    ("menor", "Declaração de não emprego de menor",
     "para fins do disposto no inciso VI do art. 68 da Lei nº 14.133/2021, que não emprega menor de 18 (dezoito) anos "
     "em trabalho noturno, perigoso ou insalubre e não emprega menor de 16 (dezesseis) anos, salvo a partir de 14 "
     "(quatorze) anos, na condição de aprendiz, nos termos do inciso XXXIII do art. 7º da Constituição Federal."),
    ("impeditivo", "Declaração de inexistência de fato impeditivo",
     "que, até a presente data, inexistem fatos impeditivos para a sua habilitação no presente processo licitatório, "
     "ciente da obrigatoriedade de declarar ocorrências posteriores."),
    ("habilitacao", "Declaração de cumprimento dos requisitos de habilitação",
     "que atende aos requisitos de habilitação previstos no edital e que responde pela veracidade das informações "
     "prestadas, na forma da lei, nos termos do art. 63, inciso I, da Lei nº 14.133/2021."),
    ("edital", "Declaração de ciência e concordância com o edital",
     "que tem pleno conhecimento e aceita todas as condições estabelecidas no edital e em seus anexos, e que a sua "
     "proposta está em conformidade com as exigências do instrumento convocatório."),
    ("custos", "Declaração de que a proposta compreende todos os custos",
     "que a sua proposta econômica compreende a integralidade dos custos para atendimento dos direitos trabalhistas "
     "assegurados na Constituição Federal, nas leis trabalhistas, nas normas infralegais, nas convenções coletivas de "
     "trabalho e nos termos de ajustamento de conduta vigentes na data de entrega das propostas, nos termos do "
     "art. 63, § 1º, da Lei nº 14.133/2021."),
    ("pcd", "Declaração de reserva de cargos (PcD e reabilitados)",
     "que cumpre as exigências de reserva de cargos para pessoa com deficiência e para reabilitado da Previdência "
     "Social, previstas em lei e em outras normas específicas, nos termos do art. 63, inciso IV, da Lei nº "
     "14.133/2021 e do art. 93 da Lei nº 8.213/1991."),
    ("independente", "Declaração de elaboração independente de proposta",
     "que a proposta apresentada foi elaborada de maneira independente e que o seu conteúdo não foi, no todo ou em "
     "parte, direta ou indiretamente, informado, discutido ou recebido de qualquer outro participante potencial ou "
     "de fato do presente certame, por qualquer meio ou por qualquer pessoa."),
    ("idoneidade", "Declaração de idoneidade",
     "que não foi declarada inidônea para licitar ou contratar com a Administração Pública, nem se encontra impedida "
     "de licitar e contratar ou suspensa, nos termos do art. 156 da Lei nº 14.133/2021, não incorrendo em nenhuma "
     "das vedações do art. 14 da referida Lei."),
    ("vinculo", "Declaração de inexistência de vínculo com agente público",
     "que não possui, em seu quadro societário ou de empregados, agente público do órgão ou entidade licitante ou "
     "contratante, nem mantém com ele vínculo de natureza técnica, comercial, econômica, financeira, trabalhista ou "
     "civil, ou relação de parentesco, nos termos do art. 14, inciso IV, da Lei nº 14.133/2021."),
    ("degradante", "Declaração de não utilização de trabalho degradante ou forçado",
     "que não possui, em sua cadeia produtiva, empregados executando trabalho degradante ou forçado, observando o "
     "disposto nos incisos III e IV do art. 1º e no inciso III do art. 5º da Constituição Federal."),
    ("meepp", "Declaração de enquadramento como ME/EPP",
     "que se enquadra como {PORTE}, nos termos do art. 3º da Lei Complementar nº 123/2006, cumprindo os requisitos "
     "legais para essa qualificação, e que não se enquadra em nenhuma das vedações do § 4º do mesmo artigo, estando "
     "apta a usufruir do tratamento favorecido estabelecido nos arts. 42 a 49 da referida Lei Complementar, "
     "observados os limites do art. 4º da Lei nº 14.133/2021."),
]
PADRAO = ["menor", "impeditivo", "habilitacao", "edital", "custos", "pcd", "independente", "idoneidade", "vinculo",
          "degradante", "meepp"]


def faltando(ctx):
    """Campos obrigatórios que ainda não foram preenchidos."""
    nomes = {"razao": "razão social", "cnpj": "CNPJ", "endereco": "endereço", "rep_nome": "nome do representante",
             "rep_cpf": "CPF do representante"}
    return [v for k, v in nomes.items() if not ctx.get(k)]


def _intro(ctx):
    return (f"**{ctx['razao']}**, inscrita no CNPJ sob o nº **{fmt_cnpj(ctx['cnpj'])}**, com sede em {ctx['endereco']}, "
            f"por intermédio de seu representante legal, **{ctx['rep_nome']}**, portador(a) do CPF nº "
            f"**{fmt_cpf(ctx['rep_cpf'])}**, DECLARA, sob as penas da lei, ")


def montar(ids, ctx, unificada=False):
    """Lista de documentos: cada um é uma lista de blocos (tipo, texto)."""
    escolhidas = [d for d in DECLARACOES if d[0] in ids and (d[0] != "meepp" or ctx.get("porte") in ("ME", "EPP"))]
    porte = PORTES.get(ctx.get("porte", ""), "")
    ref = []
    if ctx.get("orgao"):
        ref = [("dir", f"À\n{ctx['orgao']}"),
               ("dir", f"Ref.: {ctx.get('modalidade') or 'Licitação'} nº {ctx.get('processo') or '____/____'}")]
    fim = [("vazio", ""), ("data", f"{ctx.get('cidade_data') or ctx.get('cidade') or '__________'}, {ctx.get('data') or data_extenso()}."),
           ("vazio", ""), ("vazio", ""), ("assinatura", "")]
    if unificada and escolhidas:
        blocos = ref + [("titulo", "DECLARAÇÃO UNIFICADA"), ("p", _intro(ctx).rstrip(", ") + ", para fins de participação no "
                        f"certame em referência:")]
        for i, (_, _, txt) in enumerate(escolhidas):
            blocos.append(("item", f"{chr(97 + i)}) " + txt.replace("{PORTE}", porte)[0].upper()
                           + txt.replace("{PORTE}", porte)[1:]))
        return [("Declaração unificada", blocos + fim)]
    docs = []
    for _, titulo, txt in escolhidas:
        docs.append((titulo, ref + [("titulo", titulo.upper()), ("p", _intro(ctx) + txt.replace("{PORTE}", porte))] + fim))
    return docs


# ------------------------------------------------------------------ saída HTML (visualizar / imprimir em PDF)
def _neg_html(t):
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escape(t)).replace("\n", "<br>")


def _assinatura_linhas(ctx):
    return ["_____________________________________________", ctx.get("rep_nome") or "",
            f"{ctx.get('rep_cargo') or 'Representante legal'} – CPF {fmt_cpf(ctx.get('rep_cpf'))}",
            ctx.get("razao") or "", f"CNPJ {fmt_cnpj(ctx.get('cnpj'))}"]


def html(docs, ctx):
    folhas = []
    for _, blocos in docs:
        partes = []
        for tipo, txt in blocos:
            if tipo == "titulo":
                partes.append(f'<h2>{escape(txt)}</h2>')
            elif tipo == "dir":
                partes.append(f'<p class="ref">{_neg_html(txt)}</p>')
            elif tipo == "data":
                partes.append(f'<p class="data">{escape(txt)}</p>')
            elif tipo == "assinatura":
                partes.append('<div class="ass">' + "<br>".join(escape(x) for x in _assinatura_linhas(ctx)) + "</div>")
            elif tipo == "vazio":
                partes.append("<br>")
            else:
                cls = "item" if tipo == "item" else "corpo"
                partes.append(f'<p class="{cls}">{_neg_html(txt)}</p>')
        folhas.append('<div class="folha" contenteditable="true" spellcheck="false">' + "".join(partes) + "</div>")
    return "".join(folhas)


# ------------------------------------------------------------------ saída Word (.docx) feita à mão
def _runs(txt, negrito=False):
    out = []
    for i, pedaco in enumerate(re.split(r"\*\*", txt)):
        if not pedaco:
            continue
        b = negrito or i % 2 == 1
        linhas = pedaco.split("\n")
        for j, linha in enumerate(linhas):
            br = "<w:br/>" if j < len(linhas) - 1 else ""
            out.append(f'<w:r><w:rPr>{"<w:b/>" if b else ""}</w:rPr><w:t xml:space="preserve">{escape(linha, quote=False)}</w:t>{br}</w:r>')
    return "".join(out)


def _par(txt, jc="both", negrito=False, depois=160, recuo=False, tamanho=None, quebra=False):
    quebra_x = "<w:pageBreakBefore/>" if quebra else ""
    recuo_x = '<w:ind w:firstLine="709"/>' if recuo else ""
    tam_x = f'<w:rPr><w:sz w:val="{tamanho}"/></w:rPr>' if tamanho else ""
    ppr = (f'<w:pPr>{quebra_x}<w:spacing w:after="{depois}" w:line="360" w:lineRule="auto"/>'
           f'{recuo_x}<w:jc w:val="{jc}"/>{tam_x}</w:pPr>')
    return f"<w:p>{ppr}{_runs(txt, negrito)}</w:p>"


def docx(docs, ctx):
    corpo = []
    for n, (_, blocos) in enumerate(docs):
        primeiro = True
        for tipo, txt in blocos:
            q = n > 0 and primeiro
            primeiro = False
            if tipo == "titulo":
                corpo.append(_par(txt, "center", True, 360, quebra=q))
            elif tipo == "dir":
                corpo.append(_par(txt, "left", False, 200, quebra=q))
            elif tipo == "data":
                corpo.append(_par(txt, "right", quebra=q))
            elif tipo == "assinatura":
                for i, linha in enumerate(_assinatura_linhas(ctx)):
                    corpo.append(_par(linha, "center", i in (1, 3), 0))
            elif tipo == "vazio":
                corpo.append(_par("", quebra=q))
            else:
                corpo.append(_par(txt, "both", recuo=(tipo == "p"), quebra=q))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + "".join(corpo) +
           '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1701" w:right="1134" w:bottom="1134" '
           'w:left="1701" w:header="709" w:footer="709" w:gutter="0"/></w:sectPr></w:body></w:document>')
    estilos = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:docDefaults>'
               '<w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/><w:sz w:val="22"/>'
               '<w:lang w:val="pt-BR"/></w:rPr></w:rPrDefault></w:docDefaults></w:styles>')
    tipos = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", tipos)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", estilos)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
    return buf.getvalue()
