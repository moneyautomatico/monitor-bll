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
  function infoCompra(id) {   // UASG(6) + modalidade(2) + número(5) + ano(4)
    if (!/^\d{17}$/.test(id)) return {processo: id ? "Compra " + id : "", promotor: ""};
    return {processo: `${id.slice(8, 13)}/${id.slice(13)}`, promotor: "UASG " + id.slice(0, 6)};
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

  window.addEventListener("message", (ev) => {
    if (ev.source !== window || !ev.data || !ev.data.__expert) return;
    const {url, data: d} = ev.data;
    const k = caminho(url);
    if (k) { const e = endpoints.get(k) || {n: 0, campos: null}; e.n++; e.campos = e.campos || esqueleto(d, 0); endpoints.set(k, e); if (endpoints.size > 60) endpoints.delete(endpoints.keys().next().value); }
    const msgs = acharMensagens(d);
    if (!msgs.length) return;
    detectadas += msgs.length;
    const compra = compraDe(url) || compraDe(location.href);
    const inf = infoCompra(compra);
    const porItem = {};
    msgs.forEach((m) => { (porItem[m.item || ""] = porItem[m.item || ""] || []).push(m); });
    Object.entries(porItem).forEach(([item, lista]) => {
      const escopo = item ? "Item " + item : "Chat";
      const novas = lista.filter((m) => !enviados.has((m.id || m.cab + "|" + m.msg) + "|" + escopo));
      if (!novas.length) return;
      envia({tipo: "mensagens", sala: compra || "sem-compra", processo: inf.processo, promotor: inf.promotor, escopo, cabecalho: document.title,
             mensagens: novas.map(({item: _i, ...m}) => m)})
        .then((r) => { if (r.ok) novas.forEach((m) => enviados.add((m.id || m.cab + "|" + m.msg) + "|" + escopo)); });
    });
  });

  // diagnóstico: estrutura da tela e das respostas do site (endereços e NOMES de campos, sem valores)
  function estrutura() {
    const t = (s) => limpa(s).slice(0, 80);
    return {
      caminho: location.pathname, titulo: document.title, compra: compraDe(location.href), mensagens_detectadas: detectadas,
      chat_lido: detectadas, processos_lidos: 0,
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
