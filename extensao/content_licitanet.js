// Expert Licitante – Monitor (Licitanet). SÓ LÊ a página: não clica, não digita, não envia nada na Licitanet.
(() => {
  const VERSAO = chrome.runtime.getManifest().version;
  const enviados = new Set();
  let ultimaLista = "";
  const limpa = (s) => (s || "").replace(/\s+/g, " ").trim();

  function envia(ev) {
    ev.plataforma = "licitanet";
    ev.url = location.origin + location.pathname;
    ev.versao = VERSAO;
    try { chrome.runtime.sendMessage({evento: ev}); } catch (e) { /* extensão recarregada */ }
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
      if (k !== ultimaLista) { ultimaLista = k; envia({tipo: "processos", lista}); }
    }
    const sala = (location.pathname.match(/sala-disputa\/(\d+)/) || [])[1];
    if (sala) {
      const escopo = limpa((document.querySelector("#title-chat") || {}).innerText || "");
      const novas = lerChat().filter((m) => {
        const k = (m.id || m.cab + "|" + m.msg) + "|" + escopo;
        if (enviados.has(k)) return false;
        enviados.add(k);
        return true;
      });
      if (novas.length) envia({tipo: "mensagens", sala, escopo, cabecalho: cabecalho(), mensagens: novas});
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
    if (m && m.diag) { responder(estrutura()); return false; }
    return false;
  });

  let espera = null;
  new MutationObserver(() => { clearTimeout(espera); espera = setTimeout(ciclo, 1500); })
    .observe(document.documentElement, {childList: true, subtree: true, characterData: true});
  setInterval(ciclo, 20000);
  setTimeout(ciclo, 3000);
})();
