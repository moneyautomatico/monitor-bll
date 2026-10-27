"""Robô de lances (BLL/BNC) – transparente, com regras e travas.

O licitante liga o robô lote a lote, define o PISO (nunca desce abaixo dele), o decremento e os limites.
Durante a fase de lances o monitor relê a sala e, para cada lote ligado, este módulo DECIDE se dá lance e de quanto.
Modo "ensaio" (padrão): só registra e avisa o lance que daria – nada é enviado.
Modo "real": envia o lance pela tela da plataforma, com a sessão do próprio licitante, e confere depois.
O envio real só funciona quando o administrador libera a plataforma (ROBO_REAL_BLL / ROBO_REAL_BNC = 1 em config.env),
depois de validado o ensaio na sala de verdade.

Regras (Lei 14.133 / IN SEGES 73/2022 e o edital): o lance precisa ser menor que o último lance DO PRÓPRIO licitante
pela diferença mínima do edital; o robô respeita um intervalo mínimo entre os próprios lances; nunca desce do piso;
nunca dá um salto maior que o limite de segurança; e para sozinho no "parar tudo"."""
import json
import math
import secrets
import threading
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "robo.json"
ARQ_LOG = PASTA / "robo_log.jsonl"
TRAVA = threading.Lock()

PADRAO = {"ativo": False, "modo": "ensaio", "decremento": "0,50", "dec_tipo": "valor", "dif_minima": "0,01",
          "intervalo": "20", "max_lances": "30", "salto_max": "5"}
LIMITES = {"intervalo": (3, 600), "max_lances": (1, 500), "salto_max": (0.1, 30)}


# ------------------------------------------------------------------ números
def num(v):
    if isinstance(v, (int, float)):
        return float(v)
    t = "".join(c for c in str(v or "") if c.isdigit() or c in ",.-")
    if not t:
        return 0.0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or (t.count(".") == 1 and len(t.split(".")[1]) == 3):
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return 0.0


def br(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def centavos_abaixo(v):
    """Arredonda PARA BAIXO em centavos (nunca sobe o valor do lance)."""
    return math.floor(round(v * 100, 6)) / 100


# ------------------------------------------------------------------ configuração (painel grava, monitor lê)
def _ler():
    try:
        d = json.loads(ARQ.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    d.setdefault("lotes", {})
    d.setdefault("parar_tudo", False)
    d.setdefault("estado", {})
    return d


def _gravar(d):
    tmp = ARQ.with_suffix(".tmp." + secrets.token_hex(3))
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(ARQ)


def ler():
    return _ler()


def cfg_lote(chave):
    return {**PADRAO, **(_ler()["lotes"].get(chave) or {})}


def validar(valores):
    """Confere e normaliza a configuração vinda do painel. Devolve (cfg, erro)."""
    c = {**PADRAO}
    c["ativo"] = bool(valores.get("ativo"))
    c["modo"] = "real" if valores.get("modo") == "real" else "ensaio"
    c["dec_tipo"] = "pct" if valores.get("dec_tipo") == "pct" else "valor"
    dec, dif = num(valores.get("decremento")), num(valores.get("dif_minima"))
    if dec <= 0:
        return None, "Informe o decremento (quanto o robô desce em cada lance)."
    if c["dec_tipo"] == "pct" and dec > 10:
        return None, "Decremento em % muito alto (máximo 10% por lance)."
    if dif < 0:
        return None, "Diferença mínima inválida."
    c["decremento"], c["dif_minima"] = br(dec), br(dif)
    for k, (a, b) in LIMITES.items():
        v = num(valores.get(k, PADRAO[k]))
        if not a <= v <= b:
            return None, f"Valor fora do limite em '{k}' (de {a} a {b})."
        c[k] = str(int(v)) if k != "salto_max" else str(v).replace(".", ",")
    return c, ""


def salvar_lote(chave, cfg, quem=""):
    with TRAVA:
        d = _ler()
        d["lotes"][chave] = {**cfg, "atualizado": datetime.now().strftime("%d/%m/%Y %H:%M"), "por": quem}
        if cfg.get("ativo"):
            d["parar_tudo"] = False
            d["estado"][chave] = {}   # ligar de novo zera a contagem e o último motivo de parada
        _gravar(d)


def desligar_lote(chave):
    with TRAVA:
        d = _ler()
        if chave in d["lotes"]:
            d["lotes"][chave]["ativo"] = False
            _gravar(d)


def parar_tudo(sim=True):
    with TRAVA:
        d = _ler()
        d["parar_tudo"] = bool(sim)
        if sim:
            for c in d["lotes"].values():
                c["ativo"] = False
        _gravar(d)


def ativos(empresa):
    """Chaves dos lotes com robô ligado desta empresa (para o monitor saber se precisa ler a sala mais rápido)."""
    d = _ler()
    if d["parar_tudo"]:
        return []
    pre = (empresa or "").upper().strip() + "|"
    return [k for k, c in d["lotes"].items() if c.get("ativo") and k.startswith(pre)]


def registrar_estado(chave, **campos):
    with TRAVA:
        d = _ler()
        d["estado"].setdefault(chave, {}).update(campos)
        _gravar(d)


def estado(chave):
    return dict(_ler()["estado"].get(chave) or {})


def log(chave, acao, valor=None, motivo="", modo="ensaio", ok=None):
    reg = {"quando": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "chave": chave, "acao": acao,
           "valor": valor, "motivo": motivo[:300], "modo": modo, "ok": ok}
    with TRAVA:
        with ARQ_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
        ARQ_LOG.chmod(0o600)


def historico(prefixo="", n=60):
    try:
        linhas = ARQ_LOG.read_text(encoding="utf-8").splitlines()[-4000:]
    except FileNotFoundError:
        return []
    out = []
    for ln in reversed(linhas):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if prefixo and not r.get("chave", "").startswith(prefixo):
            continue
        out.append(r)
        if len(out) >= n:
            break
    return out


# ------------------------------------------------------------------ a decisão (pura – testada em tests_robo.py)
def decidir(cfg, piso, melhor, somos_primeiro, nosso_ultimo, viva, agora_ts, est):
    """Decide o próximo passo do robô para UM lote.
    cfg: configuração do lote; piso: menor valor aceitável (float ou None); melhor: melhor lance atual da sala;
    somos_primeiro: a empresa está em 1º; nosso_ultimo: último lance da empresa neste lote (None = não sabemos);
    viva: lote na fase de lances; agora_ts: time.time(); est: {"ultimo_ts", "ultimo_valor", "n"} do próprio robô.
    Devolve (acao, valor, motivo) com acao em "nada" | "lance" | "parar"."""
    if not cfg.get("ativo"):
        return "nada", None, "robô desligado neste lote"
    if not viva:
        return "nada", None, "lote fora da fase de lances"
    if not piso or piso <= 0:
        return "parar", None, "sem piso definido – o robô não dá lance sem piso"
    if not melhor or melhor <= 0:
        return "nada", None, "melhor lance da sala não lido"
    if somos_primeiro:
        return "nada", None, "você já está em 1º"
    if est.get("ultimo_valor") and abs(melhor - float(est["ultimo_valor"])) < 0.005:
        # o melhor lance da sala é o último lance do próprio robô: nunca cobrir a si mesmo
        return "nada", None, "o melhor lance da sala é o seu último lance"
    n = int(est.get("n") or 0)
    if n >= int(num(cfg.get("max_lances")) or 0):
        return "parar", None, f"limite de {n} lances do robô neste lote atingido"
    intervalo = num(cfg.get("intervalo")) or 20
    if est.get("ultimo_ts") and agora_ts - est["ultimo_ts"] < intervalo:
        return "nada", None, f"aguardando o intervalo mínimo de {int(intervalo)} s entre lances"
    dif_min = num(cfg.get("dif_minima"))
    dec = num(cfg.get("decremento"))
    passo = melhor * dec / 100 if cfg.get("dec_tipo") == "pct" else dec
    passo = max(passo, dif_min, 0.01)
    alvo = centavos_abaixo(melhor - passo)
    motivo = f"cobrir o 1º (R$ {br(melhor)}) descendo R$ {br(passo)}"
    if alvo < piso:
        if centavos_abaixo(piso) < melhor and melhor - piso >= max(dif_min, 0.01) - 1e-9:
            alvo = round(piso, 2)
            motivo = f"lance no seu piso (R$ {br(piso)}) – o decremento passaria do piso"
        else:
            return "parar", None, f"o 1º (R$ {br(melhor)}) já está no seu piso (R$ {br(piso)}) ou abaixo – o robô parou"
    referencia = nosso_ultimo if nosso_ultimo else est.get("ultimo_valor")
    if referencia and alvo > referencia - max(dif_min, 0.01) + 1e-9:
        # o lance precisa melhorar o PRÓPRIO último lance pela diferença mínima
        alvo = centavos_abaixo(referencia - max(dif_min, 0.01))
        if alvo < piso:
            return "parar", None, "melhorar o próprio lance passaria do piso – o robô parou"
        if alvo >= melhor:
            motivo = f"lance intermediário (melhora o seu último em R$ {br(max(dif_min, 0.01))})"
    salto = num(cfg.get("salto_max")) or 5
    if alvo < melhor * (1 - salto / 100) - 1e-9:
        return "parar", None, f"trava de segurança: o lance desceria mais de {br(salto)}% de uma vez – confira piso e decremento"
    if alvo <= 0:
        return "parar", None, "valor inválido"
    return "lance", round(alvo, 2), motivo


def real_liberado(plataforma):
    """O envio real só vale quando o administrador liberou a plataforma em config.env (ROBO_REAL_BLL=1 / ROBO_REAL_BNC=1)."""
    chave = "ROBO_REAL_" + (plataforma or "").upper()
    try:
        for linha in (PASTA / "config.env").read_text(encoding="utf-8").splitlines():
            k, _, v = linha.partition("=")
            if k.strip() == chave:
                return v.strip() == "1"
    except Exception:  # noqa: BLE001
        pass
    return False


# ------------------------------------------------------------------ envio pela tela (só no modo real liberado)
JS_ENVIAR = r"""
async ([lote, valor]) => {
  const norm = t => (t || '').replace(/\s+/g, ' ').trim().toUpperCase();
  const visivel = el => el && el.getClientRects().length > 0 && !el.disabled;
  const esperar = ms => new Promise(r => setTimeout(r, ms));
  const setar = (inp, v) => {
    const proto = Object.getPrototypeOf(inp);
    const d = Object.getOwnPropertyDescriptor(proto, 'value');
    inp.focus(); d.set.call(inp, ''); inp.dispatchEvent(new Event('input', {bubbles: true}));
    d.set.call(inp, v); inp.dispatchEvent(new Event('input', {bubbles: true})); inp.dispatchEvent(new Event('change', {bubbles: true}));
  };
  // 1) a linha do lote
  let linha = null;
  for (const t of document.querySelectorAll('table')) {
    if (!visivel(t)) continue;
    const cab = [...t.querySelectorAll('thead th, tr:first-child th')].map(x => norm(x.innerText));
    const iL = cab.findIndex(c => c.startsWith('LOTE'));
    if (iL < 0) continue;
    for (const tr of t.querySelectorAll('tbody tr')) {
      const td = tr.querySelectorAll('td');
      if (td[iL] && norm(td[iL].innerText) === norm(lote)) { linha = tr; break; }
    }
    if (linha) break;
  }
  if (!linha) return {ok: false, etapa: 'linha do lote não encontrada'};
  // 2) campo de valor na linha (ou numa janela aberta pelo botão de lance)
  const campos = el => [...el.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio])')].filter(visivel);
  let inp = campos(linha)[0], escopo = linha;
  if (!inp) {
    const b = [...linha.querySelectorAll('button, a')].find(x => visivel(x) && /lance|ofert/i.test((x.innerText || '') + ' ' + (x.title || '') + ' ' + (x.getAttribute('aria-label') || '')));
    if (!b) return {ok: false, etapa: 'campo/botão de lance não encontrado na linha'};
    b.click();
    for (let i = 0; i < 20 && !inp; i++) {
      await esperar(250);
      const m = [...document.querySelectorAll('.modal, [role=dialog]')].filter(visivel).pop();
      if (m) { escopo = m; inp = campos(m)[0]; }
    }
    if (!inp) return {ok: false, etapa: 'janela de lance sem campo de valor'};
  }
  setar(inp, valor);
  await esperar(300);
  // 3) botão de enviar
  const btn = [...escopo.querySelectorAll('button, a, input[type=submit], input[type=button]')].find(x => visivel(x) &&
     /enviar|ofertar|dar lance|confirmar|registrar/i.test((x.innerText || x.value || '') + ' ' + (x.title || '')));
  if (!btn) return {ok: false, etapa: 'botão de enviar não encontrado', campo: inp.outerHTML.slice(0, 300)};
  btn.click();
  // 4) confirmação ("Confirma o lance de R$ ...?")
  for (let i = 0; i < 8; i++) {
    await esperar(250);
    const m = [...document.querySelectorAll('.modal, [role=dialog], .swal2-popup')].filter(visivel).pop();
    if (!m) continue;
    const ok = [...m.querySelectorAll('button, a')].find(x => visivel(x) && /^(sim|confirmar|ok|enviar)$/i.test((x.innerText || '').trim()));
    if (ok) { ok.click(); break; }
  }
  return {ok: true, etapa: 'enviado'};
}
"""


def valor_tela(v):
    """Valor como se digita no campo da plataforma: sem separador de milhar, vírgula nos centavos (ex.: 1234,56)."""
    return f"{v:.2f}".replace(".", ",")
