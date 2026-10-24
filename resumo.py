"""Resumo diário no Telegram: prazos, certidões, o que mudou nos processos nas últimas 24 h e oportunidades novas."""
import html
import json
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import agenda
import kanban
import oportunidades
import urgencia

PASTA = Path(__file__).resolve().parent
EMPRESAS = PASTA / "empresas"
ARQ = PASTA / "resumo_estado.json"
e = html.escape


def _dt(br):
    try:
        return datetime.strptime((br or "")[:19], "%d/%m/%Y %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime((br or "")[:16], "%d/%m/%Y %H:%M")
        except ValueError:
            return None


def _nome(env):
    for linha in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        if linha.startswith("EMPRESA_NOME="):
            return linha.split("=", 1)[1].strip() or env.stem
    return env.stem


def montar(agora=None, permitidos=None, login=""):
    """permitidos = NOMES das empresas do usuário (None = todas, resumo geral)."""
    agora = agora or datetime.now()

    def ok(nomes, criador=""):
        lista = [x.strip().upper() for x in (nomes or "").split(",") if x.strip()]
        if permitidos is None:
            return True
        return any(x in permitidos for x in lista) if lista else criador == login
    desde = agora - timedelta(hours=24)
    partes = [f"☀️ <b>Resumo do dia – {agora:%d/%m/%Y}</b>"]

    # ---- prazos
    abertos = [(datetime.fromisoformat(it["quando"]), it) for it in agenda.prazos()["itens"].values() if it.get("status") == "aberto"
               and ok(it.get("empresa"), it.get("criado_por", ""))]
    atras = sorted([x for x in abertos if desde - timedelta(days=2) < x[0] < agora], key=lambda x: x[0])
    hoje = sorted([x for x in abertos if x[0] >= agora and x[0].date() == agora.date()], key=lambda x: x[0])
    amanha = sorted([x for x in abertos if x[0].date() == agora.date() + timedelta(days=1)], key=lambda x: x[0])
    if atras or hoje or amanha:
        linhas = ["\n📅 <b>Prazos</b>"]
        for rot, grupo in (("❗ Atrasado", atras), ("🔴 Hoje", hoje), ("🟠 Amanhã", amanha)):
            for q, it in grupo[:8]:
                hora = "" if q.hour == 23 and q.minute == 59 else q.strftime(" %H:%M")
                linhas.append(f"{rot}{hora} – {e(it['titulo'])} <i>({e(it.get('empresa', ''))})</i>")
        partes.append("\n".join(linhas))
    else:
        partes.append("\n📅 Nenhum prazo para hoje ou amanhã.")

    # ---- licitações (kanban): disputas de hoje/amanhã e tarefas da equipe, por responsável
    try:
        grupos = {}
        dia_h, dia_a = agora.date().isoformat(), (agora.date() + timedelta(days=1)).isoformat()
        for c in kanban.listar():
            if c.get("arquivado") or not ok(c.get("empresa"), c.get("usuario", "")):
                continue
            proc = e(c.get("processo") or "Licitação")
            resp = (c.get("responsavel") or "").strip() or "Sem responsável"
            ab = c.get("abertura") or ""
            if ab[:10] in (dia_h, dia_a) and c.get("resultado", "") == "":
                falta = c.get("progresso", 0)
                prox = c.get("proximo", "")
                grupos.setdefault(resp, []).append(
                    f"🏁 {'Hoje' if ab[:10] == dia_h else 'Amanhã'} {ab[11:16]} – disputa {proc} <i>({e(c.get('empresa', ''))})</i> · {falta}% pronto"
                    + (f" – falta: {e(prox)}" if falta < 100 and prox else ""))
            for t in c.get("tarefas") or []:
                if t.get("feito") or not t.get("prazo") or t["prazo"] > dia_h:
                    continue
                quem = (t.get("resp") or "").strip() or resp
                grupos.setdefault(quem, []).append(
                    f"{'❗ Atrasada' if t['prazo'] < dia_h else '🔴 Hoje'} – {e(t.get('texto', ''))} <i>({proc})</i>")
        if grupos:
            linhas = ["\n🗂️ <b>Licitações – o que vence hoje</b>"]
            for quem in sorted(grupos, key=lambda x: (x == "Sem responsável", x.lower())):
                linhas.append(f"👤 <b>{e(quem)}</b>")
                linhas += [f"   {x}" for x in grupos[quem][:10]]
            partes.append("\n".join(linhas))
    except Exception as ex:  # noqa: BLE001
        print("resumo kanban:", ex, flush=True)

    # ---- certidões
    cert = []
    for c in agenda.certidoes()["itens"].values():
        if not ok(c.get("empresa"), "-"):
            continue
        st, dias = agenda.status_cert(c)
        if dias is not None and dias <= 7:
            nome = agenda.NOME_CERT.get(c.get("tipo"), "Certidão")
            cert.append((dias, f"{'❌ vencida' if dias < 0 else ('vence hoje' if dias == 0 else f'vence em {dias} dia(s)')} – "
                               f"{e(nome)} <i>({e(c.get('empresa', ''))})</i>"))
    if cert:
        partes.append("\n📑 <b>Certidões</b>\n" + "\n".join(t for _, t in sorted(cert)[:10]))

    # ---- processos: o que mudou nas últimas 24 h
    blocos = []
    for env in sorted(EMPRESAS.glob("*.env")) if EMPRESAS.exists() else []:
        if permitidos is not None and _nome(env).upper().strip() not in permitidos:
            continue
        db = EMPRESAS / env.stem / "bll_monitor.db"
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            msgs = con.execute("SELECT processo, escopo, autor, texto, visto_em FROM mensagens ORDER BY rowid DESC LIMIT 3000").fetchall()
            con.close()
        except Exception:  # noqa: BLE001
            continue
        recentes = [m for m in msgs if (_dt(m[4]) or datetime.min) >= desde]
        if not recentes:
            continue
        chat = sum(1 for m in recentes if urgencia.eh_chat(m[1], m[2]))
        dilig = [m for m in recentes if "dilig" in urgencia.norm(m[1])]
        situ = [m for m in recentes if "mudanca de situacao" in urgencia.norm(m[1])]
        classif = [m for m in recentes if "classifica" in urgencia.norm(m[1])]
        procs = sorted({m[0] for m in recentes})
        linhas = [f"🏢 <b>{e(_nome(env))}</b>: {len(recentes)} novidade(s) em {len(procs)} processo(s)"
                  + (f" · {chat} mensagem(ns) no chat" if chat else "")]
        for m in situ[:4]:
            linhas.append(f"   🔄 {e(m[0])}: {e(m[3][:90])}")
        for m in classif[:4]:
            linhas.append(f"   📊 {e(m[0])}: {e(m[3].splitlines()[0][:110])}")
        for m in dilig[:3]:
            linhas.append(f"   🔎 {e(m[0])}: diligência – {e(m[3].splitlines()[-1][:90])}")
        blocos.append("\n".join(linhas))
    partes.append("\n📨 <b>Processos (últimas 24 h)</b>\n" + ("\n".join(blocos) if blocos else "Nenhuma novidade."))

    # ---- oportunidades novas de encaixe alto
    try:
        itens = oportunidades.ler().get("itens", {}).values()
    except Exception:  # noqa: BLE001
        itens = []
    novas = sorted([r for r in itens if not r.get("descartado") and r.get("nivel") == "alto"
                    and (r.get("achada_em") or "") >= desde.strftime("%Y-%m-%d %H:%M")], key=lambda r: -r.get("pontos", 0))
    if novas:
        linhas = [f"\n🎯 <b>{len(novas)} oportunidade(s) nova(s) com encaixe alto</b>"]
        for r in novas[:5]:
            fim = f" – até {r['fim'][8:10]}/{r['fim'][5:7]}" if r.get("fim") else ""
            linhas.append(f"• {e(r.get('municipio', ''))}/{e(r.get('uf', ''))}: {e((r.get('objeto') or r.get('titulo') or '')[:90])}{fim}")
        partes.append("\n".join(linhas))
    return "\n".join(partes)


def enviar_partes(enviar, texto, limite=3900):
    """O Telegram aceita até 4096 caracteres por mensagem: divide nas quebras de linha."""
    atual = ""
    for linha in texto.split("\n"):
        if len(atual) + len(linha) + 1 > limite:
            enviar(atual)
            atual = ""
        atual += ("\n" if atual else "") + linha
    if atual.strip():
        enviar(atual)


def ciclo(enviar, cfg_func):
    """Manda o resumo 1x por dia na hora configurada (padrão 7h)."""
    time.sleep(120)
    while True:
        try:
            cfg = cfg_func()
            if cfg.get("RESUMO_ATIVO", "1") != "0":
                try:
                    hora = int(cfg.get("RESUMO_HORA", "7") or 7)
                except ValueError:
                    hora = 7
                try:
                    est = json.loads(ARQ.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    est = {}
                agora = datetime.now()
                if agora.hour == hora and est.get("ultimo") != date.today().isoformat():
                    enviar_partes(enviar, montar(agora))
                    ARQ.write_text(json.dumps({"ultimo": date.today().isoformat()}), encoding="utf-8")
        except Exception as ex:  # noqa: BLE001
            print("Erro no resumo diário:", ex, flush=True)
        time.sleep(300)
