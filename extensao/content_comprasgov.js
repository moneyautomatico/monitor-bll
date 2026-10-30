// Expert Licitante – Monitor (Compras.gov.br). SÓ LÊ: as mensagens vêm das respostas que a própria página já recebeu.
(() => {
  const VERSAO = chrome.runtime.getManifest().version;
  const limpa = (s) => (s || "").replace(/\s+/g, " ").trim();
  const msg = (m) => new Promise((ok) => { try { chrome.runtime.sendMessage(m, (r) => { void chrome.runtime.lastError; ok(r || {}); }); } catch (e) { ok({}); } });
  const enviados = new Set();
  const endpoints = new Map();   // endereço (sem números/valores) -> {n, campos}
  let detectadas = 0;

  let empCache = {t: 0, v: ""};
  function empresaTela() {
    if (Date.now() - empCache.t < 30000) return empCache.v;
    const re = /\b(LTDA|EIRELI|S\.?\/?A\.?|ME|EPP|MEI)\b|COMERCI|INDUSTRI|SERVI[CÇ]|DISTRIBUI|ALIMENT/i;
    let melhor = "";
    const els = document.body ? document.body.querySelectorAll("*") : [];
    for (let i = 0; i < els.length && i < 2500; i++) {
      const el = els[i];
      if (el.children.length > 2) continue;
      const r = el.getBoundingClientRect();
      if (r.top > 160 || r.height === 0 || r.height > 70) continue;
      const tx = limpa(el.innerText || "");
      if (tx.length < 5 || tx.length > 120 || !re.test(tx)) continue;
      const letras = tx.replace(/[^A-Za-zÀ-ú]/g, "");
      if (!letras || letras.replace(/[^A-ZÀ-Ú]/g, "").length / letras.length < 0.7) continue;
      if (tx.length > melhor.length) melhor = tx;
    }
    empCache = {t: Date.now(), v: melhor};
    return melhor;
  }
  function envia(ev) {
    ev.empresa_tela = empresaTela(); ev.plataforma = "comprasgov"; ev.url = location.origin + location.pathname; ev.versao = VERSAO;
    return msg({evento: ev});
  }

  const caminho = (u) => { try { const x = new URL(u); return x.host + x.pathname.replace(/\/\d{3,}(?=\/|$)/g, "/{n}"); } catch (e) { return ""; } };
  function esqueleto(v, d) {
    if (d > 3) return "…";
    if (Array.isArray(v)) return v.length ? [esqueleto(v[0], d + 1), "×" + v.length] : [];
    if (v && typeof v === "object") { const o = {}; Object.keys(v).slice(0, 30).forEach((k) => { o[k] = esqueleto(v[k], d + 1); }); return o; }
    return typeof v;
  }
  const compraDe = (u) => ((u || "").match(/[?&]compra=(\d{10,20})/) || (u || "").match(/\/(\d{17})(?:[/?#]|$)/) || [])[1] || "";
  function infoCompra(id) {   // UASG(6) + modalidade(2) + número(5) + ano(4); a tela, quando mostra, vale mais
    const txt = document.body ? document.body.innerText : "";
    const num = (txt.match(/N[º°o]\s*(\d{1,6}\/\d{4})/) || [])[1];
    const org = (txt.match(/UASG\s*(\d{6})\s*-\s*([^\n]{3,120})/) || []);
    if (!/^\d{17}$/.test(id)) return {processo: num || (id ? "Compra " + id : ""), promotor: org[2] ? limpa(org[2]) : ""};
    const uasg = id.slice(0, 6), proprio = `${parseInt(id.slice(8, 13), 10)}/${id.slice(13)}`;
    return {processo: num && num.replace(/^0+/, "") === proprio ? num.replace(/^0+/, "") : proprio,
            promotor: org[1] === uasg && org[2] ? `${limpa(org[2])} (UASG ${uasg})` : "UASG " + uasg};
  }
  const CT = ["texto", "mensagem", "conteudo", "descricao", "msg", "txtmensagem", "textomensagem", "descricaomensagem"];
  const CD = ["datahora", "data", "datahoraenvio", "dataenvio", "dtmensagem", "datahoramensagem", "datainclusao", "datamensagem"];
  const CA = ["remetente", "autor", "origem", "tiporemetente", "usuario", "perfil", "nomeremetente", "emissor", "tipoautor"];
  const CI = ["id", "idmensagem", "codigo", "sequencial", "idchat"];
  const CITEM = ["numeroitem", "item", "identificadoritem", "numitem", "grupo", "numerogrupo"];
  const pega = (o, ks) => { for (const k of Object.keys(o)) if (ks.includes(k.toLowerCase()) && o[k] != null && typeof o[k] !== "object") return String(o[k]); return ""; };
  const data = (s) => { const m = (s || "").match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/); return m ? `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}:${m[6] || "00"}` : s; };

  function acharMensagens(d) {
    const out = [];
    const visita = (v, prof) => {
      if (prof > 5 || !v) return;
      if (Array.isArray(v)) {
        const a = v.find((x) => x && typeof x === "object" && !Array.isArray(x));
        if (a && pega(a, CT) && (pega(a, CD) || pega(a, CA))) {
          v.forEach((o) => { if (o && typeof o === "object") { const t = pega(o, CT); if (t) out.push({id: pega(o, CI), msg: limpa(t), item: pega(o, CITEM),
            cab: [pega(o, CA), data(pega(o, CD))].filter(Boolean).join(" - ")}); } });
          return;
        }
        v.slice(0, 300).forEach((x) => visita(x, prof + 1));
      } else if (typeof v === "object") Object.values(v).forEach((x) => visita(x, prof + 1));
    };
    visita(d, 0);
    return out;
  }

  function enviarLista(compra, msgs, origem) {
    const inf = infoCompra(compra);
    const porItem = {};
    msgs.forEach((m) => { (porItem[m.item || ""] = porItem[m.item || ""] || []).push(m); });
    Object.entries(porItem).forEach(([item, lista]) => {
      const escopo = item ? "Item " + item : "Chat";
      const chave = (m) => (m.id || m.cab + "|" + m.msg) + "|" + escopo;
      const novas = lista.filter((m) => !enviados.has(chave(m)));
      if (!novas.length) return;
      novas.forEach((m) => enviados.add(chave(m)));   // evita mandar duas vezes enquanto o painel responde
      envia({tipo: "mensagens", sala: compra || "sem-compra", processo: inf.processo, promotor: inf.promotor, escopo, cabecalho: document.title,
             mensagens: novas.map(({item: _i, ...m}) => m), origem})
        .then((r) => { if (!r.ok) novas.forEach((m) => enviados.delete(chave(m))); });
    });
  }

  // leitura pela TELA: os cartões do painel "Mensagens" (Mensagem do Pregoeiro · Item 28 · texto · 08/10/2026 às 10:42)
  let pelaApi = 0;
  const RE_CAB = /^Mensagem d[oae]s?\s+(.+)$/i, RE_DATA = /(\d{2}\/\d{2}\/\d{4})\s+às\s+(\d{2}:\d{2})(?::(\d{2}))?/;
  function lerPainelMensagens() {
    const cand = [...document.querySelectorAll("div, li, article, mat-card, p-card")].filter((el) => {
      if (el.children.length < 2 || !el.getClientRects().length) return false;
      const t = (el.innerText || "").trim();
      return t.length < 4000 && RE_CAB.test(t.split("\n")[0].trim()) && RE_DATA.test(t);
    });
    const cartoes = cand.filter((c) => !cand.some((o) => o !== c && c.contains(o)));   // só o cartão de dentro
    return cartoes.map((el) => {
      const linhas = el.innerText.split("\n").map(limpa).filter(Boolean);
      let autor = (linhas[0].match(RE_CAB) || [])[1] || "", item = "";
      const mi = autor.match(/^(.*?)\s+Item\s+(\d+)$/i);
      if (mi) { autor = mi[1]; item = mi[2]; }
      let ini = 1;
      if (!item && /^Item\s+\d+$/i.test(linhas[1] || "")) { item = linhas[1].replace(/\D/g, ""); ini = 2; }
      const dl = linhas.findIndex((l, i) => i >= ini && RE_DATA.test(l) && l.length < 40);
      const d = ((dl >= 0 ? linhas[dl] : el.innerText).match(RE_DATA)) || [];
      const corpo = linhas.slice(ini, dl >= 0 ? dl : undefined).join("\n");
      return {id: "", msg: corpo, item, cab: [autor, d[1] ? `${d[1]} ${d[2]}:${d[3] || "00"}` : ""].filter(Boolean).join(" - ")};
    }).filter((m) => m.msg);
  }
  function cicloTela() {
    if (pelaApi) return;   // as respostas do site já trouxeram as mensagens: não lê a tela de novo (evita duplicar)
    const msgs = lerPainelMensagens();
    if (!msgs.length) return;
    detectadasTela = msgs.length;
    enviarLista(compraDe(location.href), msgs, "tela");
  }
  let detectadasTela = 0, espera = null;
  new MutationObserver(() => { clearTimeout(espera); espera = setTimeout(cicloTela, 1500); })
    .observe(document.documentElement, {childList: true, subtree: true, characterData: true});
  setInterval(cicloTela, 20000);

  window.addEventListener("message", (ev) => {
    if (ev.source !== window || !ev.data || !ev.data.__expert) return;
    const {url, data: d} = ev.data;
    const k = caminho(url);
    if (k) { const e = endpoints.get(k) || {n: 0, campos: null}; e.n++; e.campos = e.campos || esqueleto(d, 0); endpoints.set(k, e); if (endpoints.size > 60) endpoints.delete(endpoints.keys().next().value); }
    const msgs = acharMensagens(d);
    if (!msgs.length) return;
    detectadas += msgs.length;
    pelaApi++;
    enviarLista(compraDe(url) || compraDe(location.href), msgs, "site");
  });

  // diagnóstico: estrutura da tela e das respostas do site (endereços e NOMES de campos, sem valores)
  function estrutura() {
    const t = (s) => limpa(s).slice(0, 80);
    return {
      caminho: location.pathname, titulo: document.title, compra: compraDe(location.href), mensagens_detectadas: detectadas,
      mensagens_na_tela: lerPainelMensagens().length, chat_lido: detectadas || lerPainelMensagens().length, processos_lidos: 0,
      leitura: pelaApi ? "respostas do site" : (detectadasTela ? "tela (painel Mensagens)" : "nenhuma ainda"),
      respostas: [...endpoints.entries()].map(([k, e]) => ({endereco: k, vezes: e.n, campos: e.campos})).slice(-25),
      titulos: [...document.querySelectorAll("h1,h2,h3,h4,h5,legend,.card-title,.p-panel-title")].slice(0, 25).map((h) => t(h.innerText)).filter(Boolean),
      botoes: [...document.querySelectorAll("button,[role=tab],.p-tabview-nav a")].filter((b) => b.getClientRects().length).slice(0, 40).map((b) => t(b.innerText)).filter(Boolean)
    };
  }
  chrome.runtime.onMessage.addListener((m, r, responder) => {
    if (m && m.qualEmpresa) { empCache.t = 0; responder({empresa: empresaTela()}); return false; }
    if (m && m.diag) { const x = estrutura(); x.empresa_tela = empresaTela(); responder(x); return false; }
    return false;
  });
})();
