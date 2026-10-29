// Expert Licitante – Monitor (Licitanet). SÓ LÊ a página: não clica, não digita, não envia nada na Licitanet.
(() => {
  const VERSAO = chrome.runtime.getManifest().version;
  const enviados = new Set();
  let ultimaLista = "";
  let enviando = false;
  const limpa = (s) => (s || "").replace(/\s+/g, " ").trim();

  // nome da empresa logada (aparece no topo do portal) – serve para mandar os dados à empresa certa no painel
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
      if (r.top > 140 || r.height === 0 || r.height > 70) continue;
      const tx = limpa(el.innerText || "");
      if (tx.length < 5 || tx.length > 120 || !re.test(tx)) continue;
      const letras = tx.replace(/[^A-Za-zÀ-ú]/g, "");
      if (!letras || letras.replace(/[^A-ZÀ-Ú]/g, "").length / letras.length < 0.7) continue;   // nome em maiúsculas
      if (tx.length > melhor.length) melhor = tx;
    }
    empCache = {t: Date.now(), v: melhor};
    return melhor;
  }

  function envia(ev) {
    ev.empresa_tela = empresaTela();
    ev.plataforma = "licitanet";
    ev.url = location.origin + location.pathname;
    ev.versao = VERSAO;
    return new Promise((ok) => {
      try { chrome.runtime.sendMessage({evento: ev}, (r) => { void chrome.runtime.lastError; ok(r || {}); }); }
      catch (e) { ok({}); }   // extensão recarregada
    });
  }

  function naTelaDeLogin() {
    return !!document.querySelector("input[type=password]") || /login/i.test(location.href);
  }

  function lerProcessos() {
    const out = [];
    for (const t of document.querySelectorAll("table")) {
      const cab = [...((t.querySelector("thead tr") || t.querySelector("tr") || {children: []}).children)]
        .map((x) => x.innerText.trim().toUpperCase());
      if (!cab.includes("PROCESSO") || !cab.includes("STATUS")) continue;
      t.querySelectorAll("tbody tr").forEach((tr) => {
        const tds = [...tr.children];
        if (tds.length < 3) return;
        const cel = {};
        cab.forEach((n, i) => { if (n && tds[i]) cel[n] = limpa(tds[i].innerText); });
        const a = [...tr.querySelectorAll("a[href]")].find((x) => /sala-disputa\/\d+/.test(x.href));
        const sala = a ? (a.href.match(/sala-disputa\/(\d+)/) || [])[1] : "";
        const info = cel["INFORMAÇÕES"] || cel["INFORMACOES"] || "";
        const ini = info.match(/Início:\s*([\d/]+\s*-\s*[\dh:]+)/);
        const processo = (cel["PROCESSO"] || "").replace(/\s*-\s*Modo.*$/, "").trim();
        if (!processo) return;
        out.push({processo, sala: sala || "", situacao: cel["STATUS"] || "", disputa: ini ? ini[1] : "",
                  promotor: cel["ORGAO COMPRADOR"] || cel["ÓRGÃO COMPRADOR"] || cel["ORGÃO COMPRADOR"] || ""});
      });
    }
    return out;
  }

  function lerChat() {
    const vistos = new Set();
    return [...document.querySelectorAll('.chat-list .chat-content, [id^="notificationMensagem"]')]
      .filter((el) => (vistos.has(el) ? false : vistos.add(el)))
      .map((el) => {
        const cab = limpa((el.querySelector("h5, h6, .text-muted") || {innerText: ""}).innerText);
        const msg = ((el.querySelector(".message") || el).innerText || "").replace(/\s+\n/g, "\n").trim();
        return {id: (el.id || "").replace("notificationMensagem", ""), cab, msg};
      })
      .filter((m) => m.msg);
  }

  function cabecalho() {
    return [...document.querySelectorAll("h1, h2, h3, h4, .card-header, .breadcrumb")]
      .slice(0, 8).map((x) => limpa(x.innerText)).filter(Boolean).join(" | ").slice(0, 600);
  }

  function ciclo() {
    if (naTelaDeLogin()) { envia({tipo: "estado", estado: "deslogado"}); return; }
    const lista = lerProcessos();
    if (lista.length) {
      const k = JSON.stringify(lista);
      if (k !== ultimaLista) envia({tipo: "processos", lista}).then((r) => { if (r.ok) ultimaLista = k; });
    }
    const sala = (location.pathname.match(/sala-disputa\/(\d+)/) || [])[1];
    if (sala) {
      const escopo = limpa((document.querySelector("#title-chat") || {}).innerText || "");
      const chave = (m) => (m.id || m.cab + "|" + m.msg) + "|" + escopo;
      const novas = lerChat().filter((m) => !enviados.has(chave(m)));
      if (novas.length && !enviando) {   // só marca como enviada depois que o painel confirmou (senão tenta de novo)
        enviando = true;
        envia({tipo: "mensagens", sala, escopo, cabecalho: cabecalho(), mensagens: novas})
          .then((r) => { if (r.ok) novas.forEach((m) => enviados.add(chave(m))); })
          .finally(() => { enviando = false; });
      }
    }
  }

  // diagnóstico (botão no ícone da extensão): só a ESTRUTURA da página, sem o texto das mensagens
  function estrutura() {
    const t = (s) => limpa(s).slice(0, 80);
    return {
      caminho: location.pathname, titulo: document.title, login: naTelaDeLogin(),
      tabelas: [...document.querySelectorAll("table")].slice(0, 8).map((tb) => ({
        cab: [...tb.querySelectorAll("th")].slice(0, 12).map((th) => t(th.innerText)), linhas: tb.querySelectorAll("tbody tr").length})),
      processos_lidos: lerProcessos().length,
      chat_lido: lerChat().length,
      chat_titulo: t((document.querySelector("#title-chat") || {}).innerText || ""),
      lotes: document.querySelectorAll('input[name="item_lote"]').length,
      chat_geral: !!document.querySelector("#checkBoxChatGeral"),
      titulos: [...document.querySelectorAll("h1,h2,h3,h4,h5,legend,.card-title")].slice(0, 25).map((h) => t(h.innerText)).filter(Boolean),
      classes: [...new Set([...document.querySelectorAll('[class*=chat],[class*=mensag],[class*=message],[class*=lote]')]
        .slice(0, 300).map((e) => e.className.toString().split(" ").slice(0, 3).join(".")))].slice(0, 40)
    };
  }
  chrome.runtime.onMessage.addListener((m, r, responder) => {
    if (m && m.qualEmpresa) { empCache.t = 0; responder({empresa: empresaTela()}); return false; }
    if (m && m.diag) { const x = estrutura(); x.empresa_tela = empresaTela(); responder(x); return false; }
    return false;
  });

  let espera = null;
  new MutationObserver(() => { clearTimeout(espera); espera = setTimeout(ciclo, 1500); })
    .observe(document.documentElement, {childList: true, subtree: true, characterData: true});
  setInterval(ciclo, 20000);
  setTimeout(ciclo, 3000);
})();
