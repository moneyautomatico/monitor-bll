// Expert Licitante – Monitor (Licitanet). SÓ LÊ a página: não digita e não envia nada na Licitanet.
// Na "aba vigia" ela também troca de página e de lote para LER todos os processos em andamento, um de cada vez.
(() => {
  const VERSAO = chrome.runtime.getManifest().version;
  const enviados = new Set();
  let ultimaLista = "";
  let enviando = false;
  let modoVigia = false;
  const limpa = (s) => (s || "").replace(/\s+/g, " ").trim();
  const msg = (m) => new Promise((ok) => {
    try { chrome.runtime.sendMessage(m, (r) => { void chrome.runtime.lastError; ok(r || {}); }); } catch (e) { ok({}); }
  });
  // espera feita pela extensão (o Chrome desacelera os relógios de abas em segundo plano; os da extensão não)
  const dormir = (ms) => msg({dormir: ms});

  // nome da empresa logada (aparece no topo do portal) – serve para mandar os dados à empresa certa no painel
  let empCache = {t: 0, v: ""};
  function empresaTela() {
    if (Date.now() - empCache.t < 30000) return empCache.v;
    const re = /\b(LTDA|EIRELI|S\.?\/?A\.?|ME|EPP|MEI)\b|COMERCI|INDUSTRI|SERVI[CÇ]|DISTRIBUI|ALIMENT/i;
    let melhor = "";
    const els = document.body ? document.body.querySelectorAll("*") : [];
    for (let i = 0; i < els.length && i < 2500; i++) {
      const el = els[i];
      if (el.children.length > 2 || el.id === "expert-vigia") continue;
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
    return msg({evento: ev});
  }

  function naTelaDeLogin() {
    return !!document.querySelector("input[type=password]") || /login/i.test(location.href);
  }

  function tabelasProcessos() {
    return [...document.querySelectorAll("table")].filter((t) => {
      const cab = [...((t.querySelector("thead tr") || t.querySelector("tr") || {children: []}).children)].map((x) => x.innerText.trim().toUpperCase());
      return cab.includes("PROCESSO") && cab.includes("STATUS");
    });
  }

  function lerProcessos() {
    const out = [];
    for (const t of tabelasProcessos()) {
      const cab = [...((t.querySelector("thead tr") || t.querySelector("tr") || {children: []}).children)]
        .map((x) => x.innerText.trim().toUpperCase());
      t.querySelectorAll("tbody tr").forEach((tr, idx) => {
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
        out.push({processo, sala: sala || "", situacao: cel["STATUS"] || "", disputa: ini ? ini[1] : "", _tr: tr,
                  promotor: cel["ORGAO COMPRADOR"] || cel["ÓRGÃO COMPRADOR"] || cel["ORGÃO COMPRADOR"] || ""});
      });
    }
    return out;
  }
  const semTr = (l) => l.map(({_tr, ...p}) => p);

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
  const salaAtual = () => (location.pathname.match(/sala-disputa\/(\d+)/) || [])[1];
  const tituloChat = () => limpa((document.querySelector("#title-chat") || {}).innerText || "");

  async function enviarChat(sala, escopo, mensagens) {
    const chave = (m) => (m.id || m.cab + "|" + m.msg) + "|" + escopo;
    const novas = mensagens.filter((m) => !enviados.has(chave(m)));
    if (!novas.length) return true;
    const r = await envia({tipo: "mensagens", sala, escopo, cabecalho: cabecalho(), mensagens: novas});
    if (r.ok) novas.forEach((m) => enviados.add(chave(m)));   // só marca depois que o painel confirmou
    return !!r.ok;
  }

  // ----------------------------------------------------------------- leitura normal (abas que você abriu)
  function ciclo() {
    if (modoVigia) return;
    if (naTelaDeLogin()) { envia({tipo: "estado", estado: "deslogado"}); return; }
    const lista = lerProcessos();
    if (lista.length) {
      const k = JSON.stringify(semTr(lista));
      if (k !== ultimaLista) envia({tipo: "processos", lista: semTr(lista)}).then((r) => { if (r.ok) ultimaLista = k; });
    }
    const sala = salaAtual();
    if (sala && !enviando) {
      enviando = true;
      enviarChat(sala, tituloChat(), lerChat()).finally(() => { enviando = false; });
    }
  }

  // ----------------------------------------------------------------- aba vigia
  function faixaVigia(txt) {
    let d = document.getElementById("expert-vigia");
    if (!d) {
      d = document.createElement("div");
      d.id = "expert-vigia";
      d.style.cssText = "position:fixed;z-index:2147483647;left:50%;top:8px;transform:translateX(-50%);background:#0b2a1d;color:#f4efe3;" +
        "font:600 13px system-ui,sans-serif;padding:8px 14px;border-radius:10px;border:1px solid #c9a24a;box-shadow:0 6px 20px rgba(0,0,0,.35);pointer-events:none";
      (document.body || document.documentElement).appendChild(d);
    }
    d.textContent = "🛡️ Aba vigia do Expert Licitante – " + txt + " (não use esta aba)";
  }

  async function esperar(cond, max) {
    for (let i = 0; i < max; i++) { if (cond()) return true; await dormir(1000); }
    return cond();
  }

  async function descobrirLinks(lista) {
    // linhas sem link direto: abre o menu de ações (⋮) só para LER o endereço da sala e fecha (Esc)
    let n = 0;
    for (const p of lista) {
      if (p.sala || n >= 25) continue;
      n++;
      const b = [...p._tr.querySelectorAll("button, a, i, span")].filter((x) => x.getClientRects().length > 0).pop();
      if (!b) continue;
      await msg({vigiaClique: true});
      b.click();
      await dormir(700);
      const a = [...document.querySelectorAll("a[href]")].find((x) => x.getClientRects().length > 0 && /sala de disputa/i.test(x.innerText || "") && /sala-disputa\/\d+/.test(x.href));
      if (a) p.sala = (a.href.match(/sala-disputa\/(\d+)/) || [])[1] || "";
      document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
      document.body.click();
      await dormir(300);
    }
  }

  async function rodarVigia() {
    const t = await msg({vigiaTarefa: true, plataforma: "licitanet"});
    if (!t.vigia) return;
    modoVigia = true;
    if (t.ir) { faixaVigia("indo para a plataforma da vez"); await dormir(t.espera || 1500); location.href = t.ir; return; }
    if (naTelaDeLogin()) {
      faixaVigia("a Licitanet pediu login: entre na sua conta nesta aba");
      await envia({tipo: "estado", estado: "deslogado"});
      return;   // depois do login a página recarrega e a vigia continua sozinha
    }
    const sala = salaAtual();
    if (!sala) {   // página da lista de processos
      faixaVigia("lendo a lista de processos");
      const ok = await esperar(() => tabelasProcessos().length && lerProcessos().length, 25);
      const lista = ok ? lerProcessos() : [];
      if (lista.length) {
        await descobrirLinks(lista);
        await envia({tipo: "processos", lista: semTr(lista)});
      }
      const r = await msg({vigiaLista: semTr(lista), url: location.href});
      faixaVigia(r.texto || "próximo processo…");
      await dormir(r.espera || 5000);
      location.href = r.ir;
      return;
    }
    faixaVigia("lendo o chat da sala " + sala);
    await esperar(() => document.querySelector(".chat-list, #title-chat, [id^=notificationMensagem]"), 25);
    await dormir(1500);
    const lotes = [...document.querySelectorAll('input[name="item_lote"]')].slice(0, 80);
    if (lotes.length) {
      for (let i = 0; i < lotes.length; i++) {
        faixaVigia(`sala ${sala}: lote ${i + 1} de ${lotes.length}`);
        const r = lotes[i];
        (r.closest("label") || r).click();
        await dormir(1500);
        await enviarChat(sala, tituloChat() || ("Lote " + r.value), lerChat());
      }
    } else {
      await enviarChat(sala, tituloChat() || "Chat", lerChat());
    }
    const geral = document.querySelector("#checkBoxChatGeral");   // caixa "Mensagem geral": só para mostrar as mensagens gerais
    if (geral) {
      if (!geral.checked) geral.click();
      await dormir(1500);
      await enviarChat(sala, "Mensagem geral", lerChat());
    }
    const r = await msg({vigiaPronto: true});
    faixaVigia(r.texto || "próximo processo…");
    await dormir(r.espera || 5000);
    location.href = r.ir;
  }

  // diagnóstico (botão no ícone da extensão): só a ESTRUTURA da página, sem o texto das mensagens
  function estrutura() {
    const t = (s) => limpa(s).slice(0, 80);
    const procs = lerProcessos();
    return {
      caminho: location.pathname, titulo: document.title, login: naTelaDeLogin(), vigia: modoVigia,
      tabelas: [...document.querySelectorAll("table")].slice(0, 8).map((tb) => ({
        cab: [...tb.querySelectorAll("th")].slice(0, 12).map((th) => t(th.innerText)), linhas: tb.querySelectorAll("tbody tr").length})),
      processos_lidos: procs.length, processos_sem_link: procs.filter((p) => !p.sala).length,
      chat_lido: lerChat().length,
      chat_titulo: t(tituloChat()),
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
  setTimeout(() => { rodarVigia().finally(() => { if (!modoVigia) ciclo(); }); }, 2500);
})();
