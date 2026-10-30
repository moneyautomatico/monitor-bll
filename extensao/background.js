// Expert Licitante – Monitor: recebe o que os scripts das abas leram e manda para o painel,
// para a empresa certa (uma ou várias empresas; nunca mistura).
importScripts("config.js");

const SUFIXOS = new Set(["LTDA","ME","EPP","EIRELI","SA","S","A","DE","DA","DO","DAS","DOS","E","EM","LIMITADA","MEI","CIA"]);
const GENERICAS = new Set(["COMERCIAL","COMERCIO","ALIMENTOS","ALIMENTICIOS","SERVICOS","SERVICO","INDUSTRIA","INDUSTRIAL","DISTRIBUIDORA",
  "DISTRIBUICAO","ATACADO","ATACADISTA","VAREJO","EMPRESA","GRUPO","BRASIL","PRODUTOS","MATERIAIS","EQUIPAMENTOS","SOLUCOES",
  "TECNOLOGIA","CONSTRUCOES","ENGENHARIA","IMPORTACAO","EXPORTACAO","REPRESENTACOES","GERAL","NACIONAL"]);
function palavras(n) {
  return new Set((n || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toUpperCase().split(/[^A-Z0-9]+/).filter((w) => w && !SUFIXOS.has(w)));
}
function nomeBate(tela, cadastrado) {   // mesma regra do painel
  const a = palavras(tela), b = palavras(cadastrado);
  if (!a.size || !b.size) return false;
  const [menor, maior] = a.size <= b.size ? [a, b] : [b, a];
  const comum = [...menor].filter((w) => maior.has(w));
  if (comum.length < menor.size && comum.length < Math.max(2, Math.round(0.8 * menor.size))) return false;
  return comum.some((w) => !GENERICAS.has(w)) || (comum.length === menor.size && menor.size === maior.size);
}

async function config() {
  const c = await chrome.storage.local.get(["contas", "codigo", "servidor"]);
  let contas = c.contas || [];
  if (c.codigo && !contas.some((x) => x.codigo === c.codigo)) {   // versão 1.0: um código só
    contas.push({codigo: c.codigo, empresa: ""});
    await chrome.storage.local.set({contas});
    await chrome.storage.local.remove("codigo");
  }
  let s = (c.servidor || SERVIDOR_PADRAO || "").trim().replace(/\/+$/, "");
  if (s.startsWith("__")) s = "https://expertlicitante.com.br";
  return {contas, servidor: s};
}
async function sessao() { return (await chrome.storage.session.get(["abaFixa", "abaConta", "abaInfo"])); }
async function gravarSessao(k, v) { await chrome.storage.session.set({[k]: v}); }
async function situacao(campos) {
  const atual = (await chrome.storage.local.get("situacao")).situacao || {};
  await chrome.storage.local.set({situacao: Object.assign(atual, campos)});
}

async function post(servidor, codigo, evento) {
  try {
    const r = await fetch(servidor + "/api/extensao", {method: "POST",
      headers: {"Content-Type": "application/json", "Authorization": "Bearer " + codigo}, body: JSON.stringify(evento)});
    const j = await r.json().catch(() => ({}));
    return {status: r.status, j};
  } catch (e) { return {status: 0, j: {erro: "Sem conexão com o painel (" + servidor + ")."}}; }
}

const PORTAIS = {licitanet: ["https://portal.licitanet.com.br/*"],
                 comprasgov: ["https://cnetmobile.estaleiro.serpro.gov.br/*", "https://www.comprasnet.gov.br/*"]};
const RE_PORTAL = /^https:\/\/(portal\.licitanet\.com\.br|cnetmobile\.estaleiro\.serpro\.gov\.br|www\.comprasnet\.gov\.br)\//;
const plataformaDaUrl = (u) => /licitanet/.test(u || "") ? "licitanet" : "comprasgov";

async function completarPlataformas(contas, servidor) {   // contas salvas antes da 1.3 não sabiam a plataforma
  let mudou = false;
  for (const c of contas) {
    if (c.plataforma) continue;
    const {status, j} = await post(servidor, c.codigo, {tipo: "ping", url: "", versao: chrome.runtime.getManifest().version});
    if (status === 200 && j.plataforma) { c.plataforma = j.plataforma; c.empresa = j.empresa || c.empresa; mudou = true; }
  }
  if (mudou) await chrome.storage.local.set({contas});
  return contas;
}

async function escolherConta(contas, evento, tabId) {
  const s = await sessao();
  const fixa = (s.abaFixa || {})[tabId];
  if (fixa) return contas.find((c) => c.codigo === fixa) || null;
  contas = contas.filter((c) => !c.plataforma || !evento.plataforma || c.plataforma === evento.plataforma);   // código da plataforma certa
  if (contas.length === 1) return contas[0];
  if (evento.empresa_tela) {
    const m = contas.filter((c) => c.empresa && nomeBate(evento.empresa_tela, c.empresa));
    if (m.length === 1) return m[0];
  }
  return null;
}

async function enviar(evento, tabId, codigoForcado) {
  const {contas, servidor} = await config();
  let conta = codigoForcado ? {codigo: codigoForcado, empresa: ""} : null;
  if (!conta) {
    if (!contas.length) { await situacao({erro: "Adicione o código de conexão de uma empresa (painel → Empresas → 🧩 Extensão)."}); return {ok: false}; }
    conta = await escolherConta(contas.some((c) => !c.plataforma) ? await completarPlataformas(contas, servidor) : contas, evento, tabId);
  }
  if (!conta) {   // várias empresas e não deu para saber de qual é esta aba: não envia nada
    const s = await sessao(); const info = s.abaInfo || {};
    if (tabId != null) { info[tabId] = {erro: "empresa não identificada", tela: evento.empresa_tela || ""}; await gravarSessao("abaInfo", info); }
    await situacao({erro: "Uma aba da Licitanet não teve a empresa identificada – abra a extensão nessa aba e escolha a empresa."});
    return {ok: false, erro: "empresa não identificada"};
  }
  const {status, j} = await post(servidor, conta.codigo, evento);
  const s = await sessao();
  if (tabId != null) {
    const ac = s.abaConta || {}; ac[tabId] = conta.codigo; await gravarSessao("abaConta", ac);
    const info = s.abaInfo || {}; info[tabId] = status === 200 ? {empresa: j.empresa, tela: evento.empresa_tela || ""} : {erro: j.erro || ("erro " + status), tela: evento.empresa_tela || ""};
    await gravarSessao("abaInfo", info);
  }
  if (status === 200 && j.ok) {
    if (j.empresa && !codigoForcado) {
      const lista = (await config()).contas;
      const k = lista.find((c) => c.codigo === conta.codigo);
      if (k && (k.empresa !== j.empresa || k.plataforma !== j.plataforma)) { k.empresa = j.empresa; k.plataforma = j.plataforma || k.plataforma; await chrome.storage.local.set({contas: lista}); }
    }
    await situacao({erro: "", ultimo_envio: Date.now(), ultimo_tipo: evento.tipo});
    return {ok: true, empresa: j.empresa, plataforma: j.plataforma};
  }
  await situacao({erro: j.erro || ("O painel respondeu " + status), quando: Date.now()});
  return {ok: false, erro: j.erro, status};
}

// ----------------------------------------------------------------- abas vigia: UMA aba fixa por plataforma (Licitanet e Compras.gov.br),
// cada uma passa sozinha pelos processos em andamento da sua plataforma, só lendo. Nenhuma outra aba é aberta.
const PORTAL = "https://portal.licitanet.com.br";
const HOME_PADRAO = PORTAL + "/home-avulso";
const IGNORAR = /HOMOLOG|CANCEL|REVOG|ANULAD|FRACASS|DESERT/i;
const EM_DISPUTA = /DISPUTA|LANCE|SESS[AÃ]O|ABERT/i;
const PAUSA_VOLTA = 60000;
const NOME_PLAT = {licitanet: "Licitanet", comprasgov: "Compras.gov.br"};
const salaUrl = (s) => `${PORTAL}/sala-disputa/${s}`;
const entre = (a, b) => a + Math.round(Math.random() * (b - a));
async function vigias() { return (await chrome.storage.session.get("vigias")).vigias || {}; }
async function gravarVigias(vs) { await chrome.storage.session.set({vigias: vs}); }
async function vigiaDaAba(tabId) {
  const vs = await vigias();
  const p = Object.keys(vs).find((k) => vs[k] && vs[k].tabId === tabId);
  return p ? {p, vs, v: vs[p]} : null;
}
async function marcarBadge() {
  const n = Object.keys(await vigias()).length;
  await chrome.action.setBadgeText({text: n ? "V" + (n > 1 ? n : "") : ""});
  await chrome.action.setBadgeBackgroundColor({color: "#1f8f57"});
}
async function homes() {
  const l = await chrome.storage.local.get(["vigiaHome", "vigiaHomeCG"]);
  return {licitanet: l.vigiaHome || HOME_PADRAO, comprasgov: l.vigiaHomeCG || ""};
}
async function plataformasComEmpresa() {
  const {contas} = await config();
  return ["licitanet", "comprasgov"].filter((p) => contas.some((c) => (c.plataforma || "licitanet") === p));
}

async function ligarVigia(p, home) {
  if (home) await chrome.storage.local.set(p === "licitanet" ? {vigiaHome: home} : {vigiaHomeCG: home});
  const vs = await vigias();
  if (vs[p] && vs[p].tabId) { try { await chrome.tabs.get(vs[p].tabId); return vs[p]; } catch (e) { /* aba fechada */ } }
  const h = await homes();
  if (!h[p]) return null;   // Compras.gov.br sem a lista aprendida ainda
  // reaproveita a aba fixada desta plataforma que o Chrome reabriu (em vez de criar outra)
  const usadas = new Set(Object.values(vs).map((v) => v.tabId));
  const fixadas = (await chrome.tabs.query({pinned: true, url: PORTAIS[p]})).filter((t) => !usadas.has(t.id));
  let aba = fixadas[0];
  if (aba) await chrome.tabs.update(aba.id, {url: h[p]});
  else aba = await chrome.tabs.create({url: h[p], pinned: true, active: false});
  vs[p] = {tabId: aba.id, fila: [], pos: 0, cgPos: 0, cgTotal: 0, voltas: 0, inicio: Date.now(), desde: Date.now(), ultimaVolta: 0, n: 0};
  await gravarVigias(vs);
  const lig = (await chrome.storage.local.get("vigiaLigadas")).vigiaLigadas || [];
  if (!lig.includes(p)) await chrome.storage.local.set({vigiaLigadas: [...lig, p]});
  await marcarBadge();
  return vs[p];
}
async function ligarTodas(home) {
  const ps = await plataformasComEmpresa();
  const r = {};
  for (const p of ps) r[p] = !!(await ligarVigia(p, home && ((p === "licitanet") === /licitanet/.test(home)) ? home : ""));
  return r;
}
async function desligarVigias() {
  const vs = await vigias();
  await gravarVigias({});
  await chrome.storage.local.set({vigiaLigadas: []});
  await marcarBadge();
  for (const v of Object.values(vs)) { try { await chrome.tabs.remove(v.tabId); } catch (e) { /* já fechada */ } }
}
chrome.runtime.onStartup.addListener(async () => {
  for (const p of ((await chrome.storage.local.get("vigiaLigadas")).vigiaLigadas || [])) await ligarVigia(p);
});

// uma aba vigia nunca abre outras abas: se o site abrir uma aba nova logo depois de a vigia clicar (o Chrome nem sempre marca a
// vigia como "origem"), o endereço vem para a própria aba vigia e a aba nova é fechada. Abas abertas por você não são tocadas.
const filhas = {};
const daPlataforma = (url, p) => (PORTAIS[p] || []).some((pad) => url.startsWith(pad.replace("*", "")));
async function vigiaQueClicou(t) {
  const vs = await vigias();
  const agora = Date.now();
  return Object.entries(vs).find(([, v]) => v.tabId === t.openerTabId) ||
         Object.entries(vs).find(([, v]) => (v.cliqueAte || 0) > agora && v.tabId !== t.id) || null;
}
chrome.tabs.onCreated.addListener(async (t) => {
  const a = await vigiaQueClicou(t);
  if (!a) return;
  const [p, v] = a;
  const url = t.pendingUrl || t.url || "";
  if (/^https:/.test(url)) { if (daPlataforma(url, p)) { chrome.tabs.update(v.tabId, {url}); chrome.tabs.remove(t.id); } }
  else filhas[t.id] = {p, tabId: v.tabId};   // ainda sem endereço: espera o primeiro
});
chrome.tabs.onUpdated.addListener((id, info) => {
  const f = filhas[id];
  if (!f || !info.url || !/^https:/.test(info.url)) return;
  delete filhas[id];
  if (!daPlataforma(info.url, f.p)) return;   // não é da plataforma da vigia: deixa a aba em paz
  chrome.tabs.update(f.tabId, {url: info.url});
  chrome.tabs.remove(id);
});

async function tratarVigia(m, tabId) {
  if (m.cgHome) {   // lista "Minhas participações" do Compras.gov.br (de qualquer aba): a vigia aprende o endereço
    await chrome.storage.local.set({vigiaHomeCG: m.cgHome});
    if (!m.cgProximo) return {ok: true};
  }
  const a = await vigiaDaAba(tabId);
  if (!a) return {vigia: false};
  const {p, vs, v} = a;
  v.desde = Date.now();
  const salvar = async () => { vs[p] = v; await gravarVigias(vs); };
  if (m.vigiaTarefa) {
    if (m.plataforma && m.plataforma !== p) { const h = await homes(); await salvar(); return {vigia: true, ir: h[p], espera: 1500}; }
    await salvar();
    return {vigia: true, plataforma: p};
  }
  if (m.vigiaLista) {   // Licitanet
    const ativos = m.vigiaLista.filter((x) => x.sala && !IGNORAR.test(x.situacao || ""));
    if (m.vigiaLista.length) {
      await chrome.storage.local.set({vigiaHome: (m.url || "").split("#")[0] || HOME_PADRAO});
      const disputa = ativos.filter((x) => EM_DISPUTA.test(x.situacao || "")).map((x) => x.sala);
      const outros = ativos.filter((x) => !EM_DISPUTA.test(x.situacao || "")).map((x) => x.sala);
      const meio = Math.floor(outros.length / 2);   // em disputa: no começo e de novo no meio da volta
      v.fila = [...disputa, ...outros.slice(0, meio), ...(outros.length > 3 ? disputa : []), ...outros.slice(meio)];
      v.n = ativos.length;
      v.sem_link = m.vigiaLista.filter((x) => !x.sala && !IGNORAR.test(x.situacao || "")).length;
    }
    v.pos = 0;
    await salvar();
    if (v.fila.length) return {ir: salaUrl(v.fila[0]), espera: entre(3000, 6000), texto: `${v.fila.length} leitura(s) – abrindo a 1ª`};
    return fimDaVolta(p, v, salvar, "nenhum processo em andamento com sala");
  }
  if (m.vigiaPronto) {   // Licitanet: próxima sala, ou recomeça a volta
    v.pos = (v.pos || 0) + 1;
    if (v.pos < v.fila.length) { await salvar(); return {ir: salaUrl(v.fila[v.pos]), espera: entre(3000, 6000), texto: `${v.pos + 1} de ${v.fila.length}`}; }
    return fimDaVolta(p, v, salvar);
  }
  if (m.cgProximo) {   // Compras.gov.br: qual pregão da lista abrir agora
    v.cgTotal = m.total; v.n = m.total;
    if ((v.cgPos || 0) < m.total) { await salvar(); return {abrir: v.cgPos, texto: `${v.cgPos + 1} de ${m.total}`}; }
    return fimDaVolta(p, v, salvar, m.total ? "" : "nenhum pregão em andamento na lista");
  }
  if (m.cgLido) { v.cgPos = (v.cgPos || 0) + 1; await salvar(); return {ok: true}; }
  if (m.vigiaClique) { v.cliqueAte = Date.now() + 12000; await salvar(); return {ok: true}; }
  await salvar();
  return {vigia: true};
}
async function fimDaVolta(p, v, salvar, motivo) {
  v.voltas = (v.voltas || 0) + 1; v.ultimaVolta = Date.now(); v.pos = 0; v.cgPos = 0;
  await salvar();
  const h = await homes();
  return {ir: h[p], espera: PAUSA_VOLTA, texto: (motivo ? motivo + " – " : "volta completa – ") + "recomeça em 1 min"};
}

// travou fora da plataforma (ex.: tela de login) por mais de 4 min: avisa (Telegram) e tenta de novo pela lista
async function conferirVigias() {
  const vs = await vigias();
  const {contas, servidor} = await config();
  const h = await homes();
  for (const [p, v] of Object.entries(vs)) {
    let aba;
    try { aba = await chrome.tabs.get(v.tabId); } catch (e) { delete vs[p]; continue; }
    if (RE_PORTAL.test(aba.url || "") || Date.now() - (v.desde || 0) < 240000) continue;
    for (const c of contas.filter((c) => (c.plataforma || "licitanet") === p))
      post(servidor, c.codigo, {tipo: "estado", estado: "deslogado", plataforma: p, versao: chrome.runtime.getManifest().version, url: ""});
    v.desde = Date.now();
    if (!/acesso\.gov\.br|login/i.test(aba.url || "")) chrome.tabs.update(v.tabId, {url: h[p]});   // na tela de login, espera você entrar
  }
  await gravarVigias(vs);
  await marcarBadge();
}

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const a = await vigiaDaAba(tabId);
  if (!a) return;
  delete a.vs[a.p];
  await gravarVigias(a.vs);
  const lig = ((await chrome.storage.local.get("vigiaLigadas")).vigiaLigadas || []).filter((x) => x !== a.p);
  await chrome.storage.local.set({vigiaLigadas: lig});
  await marcarBadge();
});

chrome.runtime.onMessage.addListener((m, remetente, responder) => {
  if (m && m.dormir) { setTimeout(() => responder({}), Math.min(+m.dormir || 0, 180000)); return true; }
  if (m && (m.vigiaTarefa || m.vigiaLista || m.vigiaPronto || m.cgProximo || m.cgLido || m.cgHome || m.vigiaClique)) {
    tratarVigia(m, remetente.tab ? remetente.tab.id : null).then(responder);
    return true;
  }
  if (m && m.ligarVigia) { ligarTodas(m.home).then((r) => responder({ok: true, ligadas: r})); return true; }
  if (m && m.desligarVigia) { desligarVigias().then(() => responder({ok: true})); return true; }
  return false;
});


chrome.runtime.onMessage.addListener((m, remetente, responder) => {
  if (m && m.evento) {
    const daLicitanet = remetente.tab && RE_PORTAL.test(remetente.tab.url || "");
    const tabId = m.tabId != null ? m.tabId : (daLicitanet ? remetente.tab.id : null);
    enviar(m.evento, tabId, m.codigo).then(responder);
    return true;
  }
  return false;
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const s = await sessao();
  for (const k of ["abaFixa", "abaConta", "abaInfo"]) { const o = s[k] || {}; delete o[tabId]; await gravarSessao(k, o); }
});

// sinal de vida a cada minuto, por empresa: só conta como "viva" a empresa que tem aba aberta
chrome.alarms.create("pulso", {periodInMinutes: 1});
chrome.alarms.onAlarm.addListener(async (a) => {
  if (a.name !== "pulso") return;
  conferirVigias();
  const {contas, servidor} = await config();
  const abas = await chrome.tabs.query({url: [...PORTAIS.licitanet, ...PORTAIS.comprasgov]});
  const ac = (await sessao()).abaConta || {};
  const ativas = new Set(abas.map((t) => ac[t.id]).filter(Boolean));
  const versao = chrome.runtime.getManifest().version;
  for (const c of contas) {
    const plataforma = c.plataforma || "licitanet";
    if (ativas.has(c.codigo)) post(servidor, c.codigo, {tipo: "ping", plataforma, versao, url: ""});
    else post(servidor, c.codigo, {tipo: "estado", estado: "sem_aba", plataforma, versao, url: ""});
  }
});
