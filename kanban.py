"""Minhas licitações (kanban + siga-me): cada licitação é um cartão que anda pelas etapas, do interesse ao contrato.
A etapa e o progresso são calculados sozinhos a partir do que o sistema já sabe (edital analisado, dados da empresa,
certidões, proposta, pacote, monitor, classificação, contrato) – e o licitante marca o que é manual (ex.: "enviei")."""
import hashlib
import json
import re
import secrets
import shutil
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "kanban.json"
DIR_ANEXOS = PASTA / "kanban_anexos"
TRAVA = threading.Lock()

ETAPAS = [("interesse", "📌 Interesse"), ("edital", "📖 Edital lido"), ("documentos", "📑 Documentos"),
          ("proposta", "💰 Proposta pronta"), ("enviada", "🚀 Enviada / em disputa"), ("resultado", "🏁 Resultado"),
          ("contrato", "💼 Contrato")]
IDX = {k: i for i, (k, _) in enumerate(ETAPAS)}


def _ler():
    try:
        return json.loads(ARQ.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _gravar(d):
    tmp = ARQ.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(ARQ)


def chave(empresa, processo, orgao):
    return hashlib.sha1(f"{empresa}|{processo}|{orgao}".upper().encode()).hexdigest()[:12]


def listar():
    return [{**v, "id": k} for k, v in _ler().items()]


def ler(cid):
    d = _ler().get(cid)
    return {**d, "id": cid} if d else None


def gravar(cid, dados):
    with TRAVA:
        d = _ler()
        d[cid] = {k: v for k, v in dados.items() if k != "id"}
        _gravar(d)


def excluir(cid):
    with TRAVA:
        d = _ler()
        d.pop(cid, None)
        _gravar(d)
    pasta = DIR_ANEXOS / cid
    if re.fullmatch(r"[0-9a-f]{12}", cid) and pasta.is_dir():
        shutil.rmtree(pasta, ignore_errors=True)


def atualizar(cid, mudar):
    """Lê, aplica mudar(cartao) e grava – tudo dentro da trava (comentários/tarefas de duas pessoas ao mesmo tempo)."""
    with TRAVA:
        d = _ler()
        if cid not in d:
            return None
        it = {**d[cid], "id": cid}
        mudar(it)
        d[cid] = {k: v for k, v in it.items() if k != "id"}
        _gravar(d)
        return it


def novo_id():
    return secrets.token_hex(4)


# ------------------------------------------------------------------ anexos do cartão
def salvar_anexo(cid, nome, dados, por):
    aid = novo_id()
    nome = re.sub(r"[^\w.\- ]", "_", nome or "arquivo")[:100] or "arquivo"
    pasta = DIR_ANEXOS / cid
    pasta.mkdir(parents=True, exist_ok=True)
    DIR_ANEXOS.chmod(0o700)
    (pasta / f"{aid}_{nome}").write_bytes(dados)
    atualizar(cid, lambda c: c.setdefault("anexos", []).append(
        {"id": aid, "nome": nome, "tam": len(dados), "por": por, "quando": datetime.now().strftime("%Y-%m-%dT%H:%M")}))
    return aid


def arquivo_anexo(cid, aid):
    c = ler(cid) or {}
    a = next((x for x in c.get("anexos") or [] if x.get("id") == aid), None)
    p = DIR_ANEXOS / cid / f"{aid}_{(a or {}).get('nome', '')}"
    return (p, a["nome"]) if a and p.is_file() else (None, "")


def remover_anexo(cid, aid):
    p, _ = arquivo_anexo(cid, aid)
    if p:
        p.unlink()
    atualizar(cid, lambda c: c.__setitem__("anexos", [x for x in c.get("anexos") or [] if x.get("id") != aid]))


# ------------------------------------------------------------------ prazos legais (Lei 14.133/2021 e LC 123/2006)
def _data(txt):
    try:
        return date.fromisoformat((txt or "")[:10])
    except ValueError:
        return None


def prazos_legais(card, abertura, resultado, uteis_antes, uteis_depois):
    """[(tipo, titulo, quando: datetime, explicacao, na_agenda: bool)] desta licitação.
    uteis_antes/uteis_depois = funções de dias úteis da agenda (feriados nacionais)."""
    out = []
    avisar = card.get("avisar_legais", True)
    if abertura:
        lim = uteis_antes(abertura, 3)
        out.append(("impugnacao", "Último dia para impugnar o edital ou pedir esclarecimento", lim,
                    "Lei 14.133, art. 164: até 3 dias úteis antes da abertura. Confira no edital o horário e o canal (e-mail/sistema).",
                    avisar and card.get("origem") != "monitor"))
        out.append(("abertura", "Abertura da sessão / disputa", abertura,
                    "Data informada no cartão ou lida pelo monitor.", avisar and card.get("origem") != "monitor"))
    r = _data(card.get("resultado_em"))
    if r:
        out.append(("recurso", "Prazo final das razões de recurso", uteis_depois(r, 3),
                    "Lei 14.133, art. 165: a intenção de recorrer é manifestada NA SESSÃO, logo após o resultado; "
                    "as razões vão em 3 dias úteis contados da ata/intimação (data de referência abaixo).",
                    bool(card.get("recorrer"))))
        if card.get("me_restricao") and resultado == "ganhou":
            out.append(("regularizacao", "Regularização fiscal ME/EPP", uteis_depois(r, 5),
                        "LC 123/2006, art. 43 §1º: 5 dias úteis (prorrogáveis por mais 5) a partir da declaração de vencedor "
                        "para regularizar certidão com restrição.", True))
    rec = _data(card.get("recurso_em"))
    if rec:
        out.append(("contrarrazoes", "Prazo final das contrarrazões", uteis_depois(rec, 3),
                    "Lei 14.133, art. 165 §4º: mesmo prazo do recurso (3 dias úteis), contado da intimação/divulgação do recurso.",
                    True))
    return out


def garantir(empresa, processo, orgao, **extra):
    """Cria o cartão se ainda não existir (não mexe no que já existe). Devolve o id."""
    cid = chave(empresa, processo, orgao)
    with TRAVA:
        d = _ler()
        if cid not in d:
            d[cid] = {"empresa": empresa, "processo": processo, "orgao": orgao, "checks": {}, "etapa_manual": "",
                      "criado": datetime.now().strftime("%Y-%m-%dT%H:%M"), **extra}
            _gravar(d)
    return cid


def passos(info):
    """Siga-me: lista de passos [(chave, titulo, feito, aplicavel, dica)] a partir do que o sistema sabe (info)."""
    c = info.get("checks") or {}
    ganhou = info.get("resultado") == "ganhou"
    acabou = info.get("resultado") in ("ganhou", "perdeu", "encerrado")
    p = [("edital", "Ler o edital (análise com IA ou leitura própria)", bool(info.get("edital") or c.get("edital")), True,
          "Analise o edital em Editais (IA) – ou marque como lido."),
         ("dados", "Dados da empresa completos (CNPJ, endereço, representante)", bool(info.get("dados_ok")), True,
          "Documentos → Declarações → Dados da empresa."),
         ("certidoes", "Certidões válidas", bool(info.get("certidoes_ok")), True,
          "Cadastre e mantenha as certidões em dia em Documentos → Certidões."),
         ("declaracoes", "Declarações geradas", bool(c.get("declaracoes") or info.get("pacote")), True,
          "Gere as declarações (ou o pacote de habilitação)."),
         ("proposta", "Proposta comercial com todos os preços", bool(info.get("proposta_ok")), True,
          "Monte a proposta com os itens do edital e os preços."),
         ("conferencia", "Conferência sem erros", bool(info.get("conferencia_ok")), True,
          "Abra a proposta e corrija o que a conferência apontar."),
         ("pacote", "Pacote de habilitação gerado (PDF único)", bool(info.get("pacote") or c.get("pacote")), True,
          "Documentos → Pacote de habilitação."),
         ("enviado", "Assinado e enviado na plataforma", bool(c.get("enviado") or info.get("no_monitor")), True,
          "Assine (gov.br/ICP-Brasil) e envie na plataforma – depois marque aqui."),
         ("disputa", "Disputa acompanhada pelo monitor", bool(info.get("no_monitor")), True,
          "Cadastre a empresa/plataforma em Empresas para o monitor acompanhar o chat e a classificação."),
         ("resultado", "Resultado da licitação", acabou, True, "O monitor lê a classificação e a situação do processo."),
         ("contrato", "Contrato e pagamentos acompanhados", bool(info.get("contrato")), ganhou,
          "Venceu? Abra o pós-venda em Contratos para controlar entregas e pagamentos.")]
    return p


def etapa_auto(info):
    if info.get("contrato"):
        return "contrato"
    if info.get("resultado") in ("ganhou", "perdeu", "encerrado"):
        return "resultado"
    c = info.get("checks") or {}
    if info.get("no_monitor") or c.get("enviado"):
        return "enviada"
    if info.get("proposta_ok") and info.get("conferencia_ok"):
        return "proposta"
    if info.get("dados_ok") and info.get("certidoes_ok") and (c.get("declaracoes") or info.get("pacote")):
        return "documentos"
    if info.get("edital") or c.get("edital"):
        return "edital"
    return "interesse"


def progresso(info):
    ps = [x for x in passos(info) if x[3]]
    return int(100 * sum(1 for x in ps if x[2]) / len(ps)) if ps else 0
