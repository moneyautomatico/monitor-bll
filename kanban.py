"""Minhas licitações (kanban + siga-me): cada licitação é um cartão que anda pelas etapas, do interesse ao contrato.
A etapa e o progresso são calculados sozinhos a partir do que o sistema já sabe (edital analisado, dados da empresa,
certidões, proposta, pacote, monitor, classificação, contrato) – e o licitante marca o que é manual (ex.: "enviei")."""
import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "kanban.json"
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
