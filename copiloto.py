"""Copiloto da disputa ao vivo (BLL/BNC) + precificação por piso.

Durante a fase de lances, o monitor relê a tabela de lotes da sala a cada ~25 s (só leitura – NENHUM lance é enviado)
e avisa no Telegram/ntfy quando a empresa é superada, quando volta ao 1º lugar e se ainda dá para cobrir sem passar
do PISO (custo + margem mínima) definido no painel. Os lances continuam sendo dados por uma pessoa."""
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ_PISOS = PASTA / "pisos.json"
INTERVALO = 25          # segundos entre leituras de cada sala ao vivo
JANELA_MAX = 600        # no máximo 10 min seguidos de leitura rápida por ciclo (depois o ciclo normal roda)
RESUMO_A_CADA = 90      # lance novo de concorrente com a empresa fora do 1º: no máximo 1 aviso a cada 90 s por lote


# ------------------------------------------------------------------ pisos (definidos no painel)
def chave_piso(empresa, processo, promotor, lote):
    return f"{(empresa or '').upper().strip()}|{processo}|{promotor}|{lote}"


def pisos():
    try:
        return json.loads(ARQ_PISOS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def salvar_piso(chave, custo, margem, piso):
    d = pisos()
    if piso is None and custo is None:
        d.pop(chave, None)
    else:
        if piso is None and custo is not None:
            piso = round(custo * (1 + (margem or 0) / 100), 2)
        d[chave] = {"custo": custo, "margem": margem, "piso": piso, "atualizado": datetime.now().strftime("%d/%m/%Y %H:%M")}
    tmp = ARQ_PISOS.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ARQ_PISOS)
    ARQ_PISOS.chmod(0o600)


def analise_piso(melhor, piso, somos_primeiro):
    """Texto curto: dá para cobrir? quanto de folga?"""
    if not piso:
        return "sem piso definido (defina no painel → Disputa)", None
    if not melhor:
        return f"piso R$ {_br(piso)}", None
    folga = (melhor - piso) / piso * 100
    if somos_primeiro:
        if folga < 0:
            return f"⚠️ você está em 1º, mas ABAIXO do seu piso (R$ {_br(piso)}) – confira a margem", folga
        return f"você está em 1º · piso R$ {_br(piso)} (folga {_pct(folga)})", folga
    if melhor > piso:
        return f"✅ dá para cobrir: o lance tem que ficar abaixo de R$ {_br(melhor)} e o seu piso é R$ {_br(piso)} (folga {_pct(folga)})", folga
    return f"⛔ o melhor lance (R$ {_br(melhor)}) já está ABAIXO do seu piso (R$ {_br(piso)}) – cobrir dá prejuízo", folga


def _sem_rs(v):
    """'R$ 1.234,56' -> '1.234,56' (as mensagens já põem o R$)."""
    return re.sub(r"^\s*R\$\s*", "", str(v or ""))


def _br(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v):
    return f"{v:.1f}%".replace(".", ",")


# ------------------------------------------------------------------ banco (tabelas do monitor)
def preparar(db):
    db.execute("""CREATE TABLE IF NOT EXISTS ao_vivo(chave TEXT PRIMARY KEY, processo TEXT, promotor TEXT, lote TEXT,
        descricao TEXT, fase TEXT, primeiro TEXT, melhor TEXT, nos_primeiro INTEGER, lido_em TEXT, aviso_em REAL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS ao_vivo_hist(chave TEXT, quando TEXT, primeiro TEXT, melhor REAL)""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_aovivo_hist ON ao_vivo_hist(chave, quando)")
    db.commit()


def _nosso_num(m, db, chave_classif):
    """Nº de participante da nossa empresa e mapa nº->nome no lote (da última classificação lida)."""
    r = db.execute("SELECT ranking FROM classificacao WHERE chave=?", (chave_classif,)).fetchone()
    nums, nosso = {}, ""
    try:
        for x in json.loads((r or ["[]"])[0] or "[]"):
            n = re.sub(r"\D", "", str(x.get("num") or ""))
            if n:
                nums[n] = x.get("nome", "")
                if m.eh_nossa(x.get("nome", "")):
                    nosso = n
    except ValueError:
        pass
    return nosso, nums


def _somos(m, primeiro, nosso_num):
    if m.eh_nossa(primeiro or ""):
        return True
    mo = m.RE_NUM_PART.search(primeiro or "")
    return bool(nosso_num and mo and mo.group(1) == nosso_num)


RE_HORA_DISPUTA = re.compile(r"(\d{2})/(\d{2})/(\d{4})\D{1,6}(\d{1,2})\s*[h:]\s*(\d{2})")


def hora_disputa(txt):
    """'27/10/2026 09:16', '27/10/2026 - 09h16'... -> datetime (ou None)."""
    mo = RE_HORA_DISPUTA.search(txt or "")
    if not mo:
        return None
    d, me, a, h, mi = (int(x) for x in mo.groups())
    try:
        return datetime(a, me, d, h, mi)
    except ValueError:
        return None


def _vivos(m, db):
    """Processos com algum lote em fase de disputa lido nos últimos 15 min – e os que têm sessão marcada agora
    (de 15 min antes até 4 h depois do horário), para o copiloto olhar a sala sem esperar o ciclo normal perceber."""
    lim = time.time() - 900
    rows = db.execute("SELECT DISTINCT c.processo, c.promotor, p.url FROM classificacao c JOIN processos p "
                      "ON p.processo=c.processo AND p.promotor=c.promotor WHERE c.lido_em > ? AND p.url != ''", (lim,)).fetchall()
    out, ja = [], set()
    for proc, prom, url in rows:
        fases = [f for (f,) in db.execute("SELECT fase FROM classificacao WHERE processo=? AND promotor=?", (proc, prom))]
        if any(any(v in (f or "").upper() for v in m.FASES_VIVAS) for f in fases):
            out.append({"processo": proc, "promotor": prom, "url": url, "motivo": "fase"})
            ja.add((proc, prom))
    agora = datetime.now()
    for proc, prom, disp, url, sit in db.execute("SELECT processo, promotor, disputa, url, situacao FROM processos WHERE url != ''"):
        h = hora_disputa(disp)
        if (proc, prom) in ja or not h or not (h - timedelta(minutes=15) <= agora <= h + timedelta(hours=4)):
            continue
        if any(x in (sit or "").upper() for x in ("HOMOLOG", "CANCELAD", "REVOGAD", "FRACASSAD", "DESERT", "SUSPENS")):
            continue
        if m.estado(db, f"copiloto_fim_{proc}|{prom}|{agora:%Y%m%d}"):
            continue
        out.append({"processo": proc, "promotor": prom, "url": url, "motivo": "horario"})
    return out


def _chave_conc(nome):
    import concorrentes
    return concorrentes.chave_nome(nome)


def _avisar(m, titulo, texto, urgente):
    m.telegram(f"{titulo}\n{texto}")
    try:
        m.alarme(re.sub(r"<[^>]+>", "", titulo), re.sub(r"<[^>]+>", "", texto), "5" if urgente else "4")
    except Exception:  # noqa: BLE001
        pass


def ler_sala(m, db, page, p, comportamento):
    """Lê a tabela de lotes de uma sala e dispara os avisos do copiloto."""
    m.ir(page, p["url"])
    page.wait_for_timeout(1200)
    if page.evaluate(m.JS_ABA_TODOS):
        page.wait_for_timeout(1500)
    lotes = m.avaliar(page, m.JS_LOTES) or []
    agora_txt = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    pos = ("HOMOLOG", "ADJUDIC", "FRACASS", "DESERT", "ENCERR", "CANCEL") + tuple(m.FASES_ATENCAO)
    if lotes and all(any(v in (lt.get("fase") or "").upper() for v in pos) for lt in lotes):
        p["pos_disputa"] = True   # a sala já passou dos lances: não precisa mais olhar a cada 25 s
        m.estado(db, f"copiloto_fim_{p['processo']}|{p['promotor']}|{datetime.now():%Y%m%d}", "1")
    if not any(any(v in (lt.get("fase") or "").upper() for v in m.FASES_VIVAS) for lt in lotes):
        k = f"copiloto_log_{p['processo']}|{p['promotor']}"
        ult = float(m.estado(db, k) or 0)
        if time.time() - ult > 600:   # no máximo 1 registro a cada 10 min por processo
            fases = sorted({(lt.get("fase") or "?") for lt in lotes})
            m.log(f"Copiloto: {p['processo']} – " + (f"nenhum lote em lances agora (fases na sala: {', '.join(fases)[:200]})" if lotes
                                                      else "não achei a tabela de lotes na sala"))
            m.estado(db, k, str(time.time()))
            kd = f"copiloto_diag_{p['processo']}|{p['promotor']}|{datetime.now():%Y%m%d}"
            if p.get("motivo") == "horario" and not m.estado(db, kd):   # 1 foto da sala por dia, para ajustar a leitura
                try:
                    m.salvar_diag(page, f"copiloto_{p['processo'].replace('/', '-')}_{datetime.now():%H%M}")
                except Exception:  # noqa: BLE001
                    pass
                m.estado(db, kd, "1")
    empresa = m.CFG.get("EMPRESA_NOME", "")
    tabela_pisos = pisos()
    algum_vivo = False
    import html as h
    for lt in lotes:
        viva = any(v in (lt.get("fase") or "").upper() for v in m.FASES_VIVAS)
        chave = m.hash_msg(p["processo"], p["promotor"], lt["lote"])
        if not viva:
            db.execute("DELETE FROM ao_vivo WHERE chave=?", (chave,))
            continue
        algum_vivo = True
        nosso_num, nomes = _nosso_num(m, db, chave)
        somos = _somos(m, lt.get("primeiro"), nosso_num)
        melhor = m.valor_br(lt.get("melhor") or "")
        ant = db.execute("SELECT primeiro, melhor, nos_primeiro, aviso_em FROM ao_vivo WHERE chave=?", (chave,)).fetchone()
        db.execute("INSERT INTO ao_vivo_hist VALUES(?,?,?,?)", (chave, agora_txt, lt.get("primeiro", ""), melhor))
        aviso_em = ant[3] if ant else 0
        piso = (tabela_pisos.get(chave_piso(empresa, p["processo"], p["promotor"], lt["lote"])) or {}).get("piso")
        txt_piso, _ = analise_piso(melhor, piso, somos)
        mo = m.RE_NUM_PART.search(lt.get("primeiro") or "")
        lider_nome = nomes.get(mo.group(1), "") if mo else (lt.get("primeiro") or "")
        lider = (lt.get("primeiro") or "?") + (f" ({lider_nome})" if lider_nome and lider_nome not in (lt.get("primeiro") or "") else "")
        hist = comportamento.get(_chave_conc(lider_nome)) if lider_nome else None
        hist_txt = (f"\n📈 Histórico: {h.escape(lider_nome[:40])} costuma ficar {_pct(abs(hist))} "
                    f"{'abaixo' if hist < 0 else 'acima'} do seu preço") if hist is not None else ""
        cab = f"{p['processo']} · L{int(lt['lote']) if str(lt['lote']).isdigit() else lt['lote']} – {h.escape((lt.get('descricao') or '')[:60])}"
        mudou = bool(ant) and (ant[0] != (lt.get("primeiro") or "") or (m.valor_br(ant[1] or "") or 0) != (melhor or 0))
        if not ant:
            _avisar(m, f"🎯 <b>Disputa ao vivo</b> – {h.escape(empresa)}", f"{cab}\n1º: {h.escape(lider)} – R$ {h.escape(_sem_rs(lt.get('melhor')))}"
                    f"\n{txt_piso}{hist_txt}", False)
            aviso_em = time.time()
        elif mudou and somos and not ant[2]:
            _avisar(m, f"🟢 <b>Você está em 1º</b> – {h.escape(empresa)}", f"{cab}\nMelhor lance: R$ {h.escape(_sem_rs(lt.get('melhor')))}\n{txt_piso}", False)
            aviso_em = time.time()
        elif mudou and not somos and ant[2]:
            _avisar(m, f"🥊 <b>SUPERADO</b> – {h.escape(empresa)}", f"{cab}\n1º agora: {h.escape(lider)} – R$ {h.escape(_sem_rs(lt.get('melhor')))}"
                    f"\n{txt_piso}{hist_txt}", True)
            aviso_em = time.time()
        elif mudou and not somos and piso and melhor and melhor <= piso < (m.valor_br(ant[1] or "") or 0):
            _avisar(m, f"⛔ <b>Passou do seu piso</b> – {h.escape(empresa)}", f"{cab}\n1º: {h.escape(lider)} – R$ {h.escape(_sem_rs(lt.get('melhor')))}"
                    f"\n{txt_piso}", True)
            aviso_em = time.time()
        elif mudou and not somos and time.time() - (aviso_em or 0) > RESUMO_A_CADA:
            _avisar(m, f"⚡ <b>Novo lance no L{lt['lote']}</b> – {h.escape(empresa)}", f"{cab}\n1º: {h.escape(lider)} – R$ {h.escape(_sem_rs(lt.get('melhor')))}"
                    f"\n{txt_piso}", False)
            aviso_em = time.time()
        try:
            robo_lote(m, db, page, p, lt, chave_piso(empresa, p["processo"], p["promotor"], lt["lote"]), melhor, somos, piso, h.escape(cab))
        except Exception as ex_r:  # noqa: BLE001 – o robô nunca derruba o copiloto
            m.log(f"Robô: erro no {p['processo']} L{lt['lote']}: {ex_r!r}")
        db.execute("INSERT OR REPLACE INTO ao_vivo VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (chave, p["processo"], p["promotor"], lt["lote"], lt.get("descricao", ""), lt.get("fase", ""),
                    lt.get("primeiro", ""), lt.get("melhor", ""), 1 if somos else 0, agora_txt, aviso_em))
    db.commit()
    return algum_vivo


def janela_rapida(m, db, page, segundos):
    """Fica lendo as salas com disputa ao vivo por até 'segundos'. Devolve quantos segundos usou."""
    if m.CFG.get("PLATAFORMA", "bll").lower() not in ("bll", "bnc") or m.CFG.get("COPILOTO", "1") == "0":
        return 0
    preparar(db)
    vivos = _vivos(m, db)
    if not vivos:
        return 0
    try:
        import concorrentes
        comp = {c["chave"]: c["dif_media"] for c in concorrentes.analisar(m.CFG.get("EMPRESA_NOME", "").upper())["concorrentes"]
                if c["dif_media"] is not None}
    except Exception:  # noqa: BLE001
        comp = {}
    corte = (datetime.now() - timedelta(days=2)).strftime("%Y%m%d")   # histórico de lances: guarda 2 dias
    db.execute("DELETE FROM ao_vivo_hist WHERE substr(quando,7,4)||substr(quando,4,2)||substr(quando,1,2) < ?", (corte,))
    import robo
    com_robo = bool(robo.ativos(m.CFG.get("EMPRESA_NOME", "")))
    intervalo = 5 if com_robo else INTERVALO
    inicio = time.time()
    fim = inicio + (2400 if com_robo else min(segundos, JANELA_MAX))   # com robô: até 40 min seguidos na sala
    m.log(f"Copiloto: {len(vivos)} processo(s) em disputa – leitura rápida a cada {intervalo} s{' (robô ligado)' if com_robo else ''}.")
    while time.time() < fim and vivos:
        rodada = time.time()
        ainda = []
        for p in vivos:
            try:
                if ler_sala(m, db, page, p, comp) or (p.get("motivo") == "horario" and not p.get("pos_disputa")):
                    ainda.append(p)
            except Exception as ex:  # noqa: BLE001
                m.log(f"Copiloto: erro lendo {p['processo']}: {ex}")
                ainda.append(p)
        vivos = ainda
        espera = intervalo - (time.time() - rodada)
        if espera > 0 and time.time() + espera < fim:
            time.sleep(espera)
    return int(time.time() - inicio)


# ------------------------------------------------------------------ robô de lances (decisão em robo.py)
def robo_lote(m, db, page, p, lt, chave, melhor, somos, piso, cab):
    import robo
    cfg = robo.cfg_lote(chave)
    if not cfg.get("ativo") or robo.ler().get("parar_tudo"):
        return
    est = robo.estado(chave)
    plat = m.CFG.get("PLATAFORMA", "bll").lower()
    real = cfg.get("modo") == "real" and robo.real_liberado(plat)
    modo = "real" if real else "ensaio"
    kd = f"robo_diag_{chave}|{datetime.now():%Y%m%d}"
    if not m.estado(db, kd):   # 1 foto da sala por dia com o robô ligado (para conferir a tela de lance)
        try:
            m.salvar_diag(page, f"robo_{p['processo'].replace('/', '-')}_L{lt['lote']}_{datetime.now():%H%M}")
        except Exception:  # noqa: BLE001
            pass
        m.estado(db, kd, "1")
    acao, valor, motivo = robo.decidir(cfg, piso, melhor, somos, None, True, time.time(), est)
    if acao == "nada":
        return
    if acao == "parar":
        if est.get("ultimo_motivo") != motivo:
            robo.log(chave, "parou", None, motivo, modo)
            robo.desligar_lote(chave)
            robo.registrar_estado(chave, ultimo_motivo=motivo)
            _avisar(m, "🤖 <b>Robô parou</b>", f"{cab}\n{motivo}", True)
        return
    # acao == "lance"
    if not real:
        if est.get("ensaio_melhor") != melhor:   # um registro por situação da sala
            robo.log(chave, "ensaio", valor, motivo, modo)
            robo.registrar_estado(chave, ensaio_melhor=melhor, ensaio_ts=time.time())
            aviso = "" if cfg.get("modo") != "real" else "\n⚠️ Modo real escolhido, mas a plataforma ainda não foi liberada pelo administrador – ficou em ensaio."
            _avisar(m, "🤖 <b>Robô (ensaio)</b> daria lance", f"{cab}\nR$ {robo.br(valor)} – {motivo}{aviso}", False)
        return
    try:   # UMA tentativa só (repetir poderia mandar o lance duas vezes)
        res = page.evaluate(robo.JS_ENVIAR, [str(lt["lote"]), robo.valor_tela(valor)]) or {}
    except Exception as ex_e:  # noqa: BLE001 – a página mudou no meio: confere na sala abaixo
        res = {"ok": True, "etapa": f"página recarregou durante o envio ({str(ex_e)[:60]})"}
    n = int(est.get("n") or 0) + 1
    robo.registrar_estado(chave, ultimo_ts=time.time(), ultimo_valor=valor, n=n, ultimo_motivo="")
    if not res.get("ok"):
        robo.log(chave, "falhou", valor, f"{motivo} · tela: {res.get('etapa', '?')}", modo, False)
        robo.desligar_lote(chave)
        _avisar(m, "🤖 <b>Robô NÃO conseguiu dar o lance</b> – desligado neste lote", f"{cab}\nR$ {robo.br(valor)} · {res.get('etapa', '?')}\nDê o lance manualmente.", True)
        return
    # confere na sala: o melhor lance tem que ser o nosso
    page.wait_for_timeout(2500)
    conferido = False
    for x in (m.avaliar(page, m.JS_LOTES) or []):
        if str(x.get("lote")) == str(lt["lote"]):
            conferido = abs((m.valor_br(x.get("melhor") or "") or 0) - valor) < 0.005
    robo.log(chave, "lance", valor, motivo, modo, conferido)
    if conferido:
        _avisar(m, "🤖 <b>Robô deu lance</b>", f"{cab}\nR$ {robo.br(valor)} – {motivo} ({n}º lance do robô)", False)
    else:
        robo.desligar_lote(chave)
        _avisar(m, "🤖 <b>Lance do robô não apareceu na sala</b> – desligado neste lote",
                f"{cab}\nTentou R$ {robo.br(valor)}. Confira a sala e dê o lance manualmente.", True)
