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
CAMPOS = ["cnpj", "razao", "endereco", "cidade", "porte", "rep_nome", "rep_cpf", "rep_cargo",
          "ie", "im", "telefone", "email", "rep_rg", "banco", "agencia", "conta", "pix", "timbrado"]
DIR_LOGOS = PASTA / "logos"


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
    atual = tudo.get(nome, {})
    # campo que não veio no formulário mantém o valor que já estava
    tudo[nome] = {k: str(valores[k] if k in valores else atual.get(k, "")).strip()[:300] for k in CAMPOS}
    ARQ.write_text(json.dumps(tudo, ensure_ascii=False, indent=1), encoding="utf-8")
    ARQ.chmod(0o600)


def _chave_logo(nome):
    import hashlib
    return hashlib.sha256((nome or "").upper().encode()).hexdigest()[:16]


def tamanho_img(dados):
    """(largura, altura) de PNG ou JPEG sem bibliotecas."""
    if dados[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(dados[16:20], "big"), int.from_bytes(dados[20:24], "big")
    if dados[:2] == b"\xff\xd8":
        i = 2
        while i < len(dados) - 9:
            if dados[i] != 0xFF:
                i += 1
                continue
            marca = dados[i + 1]
            if marca in (0xC0, 0xC1, 0xC2):
                return int.from_bytes(dados[i + 7:i + 9], "big"), int.from_bytes(dados[i + 5:i + 7], "big")
            i += 2 + int.from_bytes(dados[i + 2:i + 4], "big")
    return None


def salvar_logo(nome, dados):
    tam = tamanho_img(dados or b"")
    if not tam or not all(tam):
        raise ValueError("Envie a logo em PNG ou JPG.")
    if len(dados) > 2 * 1024 * 1024:
        raise ValueError("A logo pode ter no máximo 2 MB.")
    DIR_LOGOS.mkdir(exist_ok=True)
    ext = "png" if dados[:4] == b"\x89PNG" else "jpg"
    for a in DIR_LOGOS.glob(_chave_logo(nome) + ".*"):
        a.unlink()
    (DIR_LOGOS / f"{_chave_logo(nome)}.{ext}").write_bytes(dados)


def logo(nome):
    """(bytes, extensão) da logo da empresa, ou (None, '')."""
    for a in DIR_LOGOS.glob(_chave_logo(nome) + ".*") if DIR_LOGOS.exists() else []:
        return a.read_bytes(), a.suffix[1:]
    return None, ""


def remover_logo(nome):
    for a in DIR_LOGOS.glob(_chave_logo(nome) + ".*") if DIR_LOGOS.exists() else []:
        a.unlink()


def linha_rodape(ctx):
    return " · ".join(x for x in [ctx.get("razao"), f"CNPJ {fmt_cnpj(ctx.get('cnpj'))}" if ctx.get("cnpj") else "",
                                  ctx.get("endereco"), ctx.get("telefone"), ctx.get("email")] if x)


def timbrado(ctx):
    return ctx.get("timbrado", "1") != "0" and bool(ctx.get("razao"))


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
    tel = re.sub(r"\D", "", j.get("ddd_telefone_1") or "")
    tel = f"({tel[:2]}) {tel[2:-4]}-{tel[-4:]}" if len(tel) >= 10 else ""
    return {"telefone": tel, "email": (j.get("email") or "").lower(),
            "cnpj": fmt_cnpj(n), "razao": (j.get("razao_social") or "").upper(), "endereco": end,
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


def preencher_modelo(texto, ctx):
    """Modelo vindo do edital (lido pela IA) com {RAZAO}, {CNPJ}... trocados pelos dados."""
    trocas = {"RAZAO": f"**{ctx.get('razao', '')}**", "CNPJ": f"**{fmt_cnpj(ctx.get('cnpj'))}**",
              "ENDERECO": ctx.get("endereco", ""), "REP_NOME": f"**{ctx.get('rep_nome', '')}**",
              "REP_CPF": f"**{fmt_cpf(ctx.get('rep_cpf'))}**", "ORGAO": ctx.get("orgao", "") or "________",
              "PROCESSO": ctx.get("processo", "") or "____/____"}
    return re.sub(r"\{(RAZAO|CNPJ|ENDERECO|REP_NOME|REP_CPF|ORGAO|PROCESSO)\}", lambda m: trocas[m.group(1)], texto)


def montar(ids, ctx, unificada=False, extras=()):
    """Lista de documentos: cada um é uma lista de blocos (tipo, texto). extras = [(titulo, texto do modelo do edital)]"""
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
    for titulo, txt in extras:   # modelos próprios do edital
        corpo = [("p", par.strip()) for par in preencher_modelo(txt, ctx).split("\n") if par.strip()]
        docs.append((titulo, ref + [("titulo", (titulo or "DECLARAÇÃO").upper())] + corpo + fim))
    return docs


# ------------------------------------------------------------------ saída HTML (visualizar / imprimir em PDF)
def _neg_html(t):
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escape(t)).replace("\n", "<br>")
    return re.sub(r"(\[(?:CONFERIR|inserir)[^\]]*\])", r'<mark style="background:#fff59d">\1</mark>', t)


def _assinatura_linhas(ctx):
    return ["_____________________________________________", ctx.get("rep_nome") or "",
            f"{ctx.get('rep_cargo') or 'Representante legal'} – CPF {fmt_cpf(ctx.get('rep_cpf'))}",
            ctx.get("razao") or "", f"CNPJ {fmt_cnpj(ctx.get('cnpj'))}"]


def _tabela_html(t):
    al = t.get("alinhar", "")
    linhas = []
    for i, ln in enumerate(t["linhas"]):
        if ln and ln[0] == "§":   # linha de grupo (ex.: "LOTE 1")
            linhas.append(f'<tr class="grupo"><td colspan="{len(t["linhas"][0])}"><b>{escape(ln[1])}</b></td></tr>')
            continue
        tag = "th" if i == 0 else "td"
        cels = "".join(f'<{tag} style="text-align:{ {"r": "right", "c": "center"}.get(al[j] if j < len(al) else "l", "left") }">'
                       f'{_neg_html(str(c))}</{tag}>' for j, c in enumerate(ln))
        linhas.append(f"<tr>{cels}</tr>")
    return '<table class="tab-doc">' + "".join(linhas) + "</table>"


def html(docs, ctx, logo_url=""):
    folhas = []
    timbre = ""
    if timbrado(ctx):
        timbre = ('<div class="timbre">' + (f'<img src="{escape(logo_url)}" alt="">' if logo_url else "") +
                  f'<div><b>{escape(ctx.get("razao") or "")}</b><br><small>CNPJ {escape(fmt_cnpj(ctx.get("cnpj")))}'
                  + (f' · IE {escape(ctx["ie"])}' if ctx.get("ie") else "") + "</small></div></div>")
    rodape = f'<div class="rodape-doc">{escape(linha_rodape(ctx))}</div>' if timbrado(ctx) else ""
    for _, blocos in docs:
        partes = [timbre] if timbre else []
        for tipo, txt in blocos:
            if tipo == "titulo":
                partes.append(f'<h2>{escape(txt)}</h2>')
            elif tipo == "dir":
                partes.append(f'<p class="ref">{_neg_html(txt)}</p>')
            elif tipo == "secao":
                partes.append(f'<h3>{_neg_html(txt)}</h3>')
            elif tipo == "data":
                partes.append(f'<p class="data">{escape(txt)}</p>')
            elif tipo == "assinatura":
                partes.append('<div class="ass">' + "<br>".join(escape(x) for x in _assinatura_linhas(ctx)) + "</div>")
            elif tipo == "vazio":
                partes.append("<br>")
            elif tipo == "tabela":   # txt = {"linhas": [[...]], "larguras": [...], "alinhar": "lrc..."}
                partes.append(_tabela_html(txt))
            else:
                cls = "item" if tipo == "item" else "corpo"
                partes.append(f'<p class="{cls}">{_neg_html(txt)}</p>')
        folhas.append('<div class="folha" contenteditable="true" spellcheck="false">' + "".join(partes) + rodape + "</div>")
    return "".join(folhas)


def css_impressao(ctx):
    """Rodapé com dados da empresa e 'Página X de Y' em cada página impressa (Chrome/Edge)."""
    txt = linha_rodape(ctx).replace("\\", "").replace('"', "'") if timbrado(ctx) else ""
    return ("@page{@bottom-left{content:\"" + txt + "\";font:8pt Arial;color:#555}"
            "@bottom-right{content:\"Página \" counter(page) \" de \" counter(pages);font:8pt Arial;color:#555;width:32mm;white-space:nowrap}}")


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


def _cel(txt, largura, jc="left", negrito=False, span=1, fundo=""):
    sh = f'<w:shd w:val="clear" w:color="auto" w:fill="{fundo}"/>' if fundo else ""
    gs = f'<w:gridSpan w:val="{span}"/>' if span > 1 else ""
    runs = _runs(str(txt), negrito).replace("<w:rPr>", '<w:rPr><w:sz w:val="18"/>')
    p = f'<w:p><w:pPr><w:spacing w:before="20" w:after="20"/><w:jc w:val="{jc}"/></w:pPr>{runs}</w:p>'
    return f'<w:tc><w:tcPr><w:tcW w:w="{largura}" w:type="dxa"/>{gs}{sh}</w:tcPr>{p}</w:tc>'


def _tabela_docx(t):
    larg = t.get("larguras") or [1000] * len(t["linhas"][0])
    al = t.get("alinhar", "")
    borda = '<w:{0} w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
    bordas = "".join(borda.format(x) for x in ("top", "left", "bottom", "right", "insideH", "insideV"))
    out = [f'<w:tbl><w:tblPr><w:tblW w:w="{sum(larg)}" w:type="dxa"/><w:tblBorders>{bordas}</w:tblBorders>'
           '<w:tblLayout w:type="fixed"/><w:tblCellMar><w:left w:w="60" w:type="dxa"/><w:right w:w="60" w:type="dxa"/></w:tblCellMar>'
           '</w:tblPr><w:tblGrid>' + "".join(f'<w:gridCol w:w="{w}"/>' for w in larg) + "</w:tblGrid>"]
    for i, ln in enumerate(t["linhas"]):
        if ln and ln[0] == "§":
            out.append("<w:tr>" + _cel(ln[1], sum(larg), "left", True, len(larg), "EDEDED") + "</w:tr>")
            continue
        def jc(j):
            return {"r": "right", "c": "center"}.get(al[j] if j < len(al) else "l", "left")
        out.append("<w:tr>" + ("<w:trPr><w:tblHeader/></w:trPr>" if i == 0 else "") +
                   "".join(_cel(c, larg[j], "center" if i == 0 else jc(j), i == 0, 1, "D9E2DC" if i == 0 else "")
                           for j, c in enumerate(ln)) + "</w:tr>")
    return "".join(out) + "</w:tbl>"


def _cabecalho_docx(ctx, img):
    """header1.xml: logo à esquerda + razão social/CNPJ à direita, com linha embaixo."""
    w_ns = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')
    figura = ""
    if img:
        w, h = img[2]
        alt = 540000   # 1,5 cm
        larg = min(int(alt * w / h), 2160000)   # no máximo 6 cm
        alt = int(larg * h / w)
        figura = (f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{larg}" cy="{alt}"/>'
                  '<wp:docPr id="1" name="Logo"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                  f'<pic:pic><pic:nvPicPr><pic:cNvPr id="0" name="logo.{img[1]}"/><pic:cNvPicPr/></pic:nvPicPr>'
                  '<pic:blipFill><a:blip r:embed="rIdLogo"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                  f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{larg}" cy="{alt}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
                  '</pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')
    texto = (_runs(f"**{ctx.get('razao') or ''}**").replace("<w:rPr>", '<w:rPr><w:sz w:val="22"/>') +
             '<w:r><w:br/></w:r>' + _runs(f"CNPJ {fmt_cnpj(ctx.get('cnpj'))}" + (f" · IE {ctx['ie']}" if ctx.get("ie") else ""))
             .replace("<w:rPr>", '<w:rPr><w:sz w:val="16"/><w:color w:val="555555"/>'))
    sem = '<w:tblBorders>' + "".join(f'<w:{x} w:val="nil"/>' for x in ("top", "left", "right", "insideH", "insideV")) + \
          '<w:bottom w:val="single" w:sz="8" w:space="0" w:color="1B3A2A"/></w:tblBorders>'
    tabela = (f'<w:tbl><w:tblPr><w:tblW w:w="9071" w:type="dxa"/>{sem}<w:tblLayout w:type="fixed"/></w:tblPr>'
              '<w:tblGrid><w:gridCol w:w="3400"/><w:gridCol w:w="5671"/></w:tblGrid><w:tr>'
              f'<w:tc><w:tcPr><w:tcW w:w="3400" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:spacing w:after="60"/></w:pPr>{figura}</w:p></w:tc>'
              f'<w:tc><w:tcPr><w:tcW w:w="5671" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr><w:p><w:pPr><w:spacing w:after="60"/><w:jc w:val="right"/></w:pPr>{texto}</w:p></w:tc>'
              '</w:tr></w:tbl><w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p>')
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr {w_ns}>{tabela}</w:hdr>'


def _rodape_docx(ctx):
    pq = '<w:rPr><w:sz w:val="15"/><w:color w:val="555555"/></w:rPr>'
    linha = escape(linha_rodape(ctx), quote=False)
    pag = (f'<w:r>{pq}<w:t xml:space="preserve">Página </w:t></w:r><w:fldSimple w:instr="PAGE"><w:r>{pq}<w:t>1</w:t></w:r></w:fldSimple>'
           f'<w:r>{pq}<w:t xml:space="preserve"> de </w:t></w:r><w:fldSimple w:instr="NUMPAGES"><w:r>{pq}<w:t>1</w:t></w:r></w:fldSimple>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:p><w:pPr><w:pBdr><w:top w:val="single" w:sz="4" w:space="4" w:color="999999"/></w:pBdr><w:spacing w:after="0"/>'
            f'<w:jc w:val="center"/></w:pPr><w:r>{pq}<w:t xml:space="preserve">{linha}</w:t></w:r></w:p>'
            f'<w:p><w:pPr><w:spacing w:after="0"/><w:jc w:val="right"/></w:pPr>{pag}</w:p></w:ftr>')


def docx(docs, ctx, logo_img=None):
    """logo_img = (bytes, 'png'|'jpg') opcional."""
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
            elif tipo == "secao":
                corpo.append(_par(txt, "left", True, 160, quebra=q))
            elif tipo == "data":
                corpo.append(_par(txt, "right", quebra=q))
            elif tipo == "assinatura":
                for i, linha in enumerate(_assinatura_linhas(ctx)):
                    corpo.append(_par(linha, "center", i in (1, 3), 0))
            elif tipo == "vazio":
                corpo.append(_par("", quebra=q))
            elif tipo == "tabela":
                corpo.append(_tabela_docx(txt))
                corpo.append(_par("", depois=80))
            else:
                corpo.append(_par(txt, "both", recuo=(tipo == "p"), quebra=q))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
           'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>'
           + "".join(corpo) +
           ('<w:sectPr><w:headerReference w:type="default" r:id="rIdH"/><w:footerReference w:type="default" r:id="rIdF"/>'
              if timbrado(ctx) else '<w:sectPr>') + '<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1701" w:right="1134" w:bottom="1134" '
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
             '<Default Extension="png" ContentType="image/png"/><Default Extension="jpg" ContentType="image/jpeg"/>'
             '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
             + ('<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
                '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
                if timbrado(ctx) else "") + '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                + ('<Relationship Id="rIdH" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
                   '<Relationship Id="rIdF" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
                   if timbrado(ctx) else "") + '</Relationships>')
    img = None
    if logo_img and logo_img[0] and tamanho_img(logo_img[0]):
        img = (logo_img[0], logo_img[1], tamanho_img(logo_img[0]))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", tipos)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", estilos)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        if timbrado(ctx):
            z.writestr("word/header1.xml", _cabecalho_docx(ctx, img))
            z.writestr("word/footer1.xml", _rodape_docx(ctx))
            if img:
                z.writestr(f"word/media/logo.{img[1]}", img[0])
                z.writestr("word/_rels/header1.xml.rels",
                           '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                           '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                           '<Relationship Id="rIdLogo" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                           f'Target="media/logo.{img[1]}"/></Relationships>')
    return buf.getvalue()
