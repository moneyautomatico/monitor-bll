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

// ----------------------------------------------------------------- aba vigia: uma aba que passa por todos os processos em andamento
const PORTAL = "https://portal.licitanet.com.br";
const HOME_PADRAO = PORTAL + "/home-avulso";
const IGNORAR = /HOMOLOG|CANCEL|REVOG|ANULAD|FRACASS|DESERT/i;
const EM_DISPUTA = /DISPUTA|LANCE|SESS[AÃ]O|ABERT/i;
const PAUSA_VOLTA = 60000;
const salaUrl = (s) => `${PORTAL}/sala-disputa/${s}`;
const entre = (a, b) => a + Math.round(Math.random() * (b - a));
async function vigia() { return (await chrome.storage.session.get("vigia")).vigia || null; }
async function gravarVigia(v) { await chrome.storage.session.set({vigia: v}); }
async function marcarBadge(ligada) {
  await chrome.action.setBadgeText({text: ligada ? "V" : ""});
  await chrome.action.setBadgeBackgroundColor({color: "#1f8f57"});
}
async function ligarVigia(home) {
  const v = await vigia();
  if (v && v.tabId) { try { await chrome.tabs.get(v.tabId); return v; } catch (e) { /* aba fechada */ } }
  const casa = home || (await chrome.storage.local.get("vigiaHome")).vigiaHome || HOME_PADRAO;
  const aba = await chrome.tabs.create({url: casa, pinned: true, active: false});
  const nova = {tabId: aba.id, home: casa, fila: [], pos: 0, voltas: 0, inicio: Date.now(), ultimaVolta: 0, n: 0};
  await gravarVigia(nova);
  await chrome.storage.local.set({vigiaLigada: true, vigiaHome: casa});
  await marcarBadge(true);
  return nova;
}
async function desligarVigia() {
  const v = await vigia();
  await gravarVigia(null);
  await chrome.storage.local.set({vigiaLigada: false});
  await marcarBadge(false);
  if (v && v.tabId) { try { await chrome.tabs.remove(v.tabId); } catch (e) { /* já fechada */ } }
}
chrome.runtime.onStartup.addListener(async () => {
  if ((await chrome.storage.local.get("vigiaLigada")).vigiaLigada) ligarVigia();
});

async function tratarVigia(m, tabId) {
  const v = await vigia();
  if (!v || v.tabId !== tabId) return {vigia: false};
  if (m.vigiaTarefa) return {vigia: true};
  if (m.vigiaLista) {
    const ativos = m.vigiaLista.filter((p) => p.sala && !IGNORAR.test(p.situacao || ""));
    if (m.vigiaLista.length) {   // lista lida: esta é a página "casa" da vigia
      v.home = (m.url || v.home).split("#")[0];
      await chrome.storage.local.set({vigiaHome: v.home});
      const disputa = ativos.filter((p) => EM_DISPUTA.test(p.situacao || "")).map((p) => p.sala);
      const outros = ativos.filter((p) => !EM_DISPUTA.test(p.situacao || "")).map((p) => p.sala);
      // os que estão em disputa entram no começo e de novo no meio da volta
      const meio = Math.floor(outros.length / 2);
      v.fila = [...disputa, ...outros.slice(0, meio), ...(outros.length > 3 ? disputa : []), ...outros.slice(meio)];
      v.n = ativos.length;
      v.sem_link = m.vigiaLista.filter((p) => !p.sala && !IGNORAR.test(p.situacao || "")).length;
    }
    v.pos = 0;
    await gravarVigia(v);
    if (!v.fila.length) return {ir: v.home, espera: 120000, texto: "nenhum processo em andamento com sala – nova olhada em 2 min"};
    return {ir: salaUrl(v.fila[0]), espera: entre(3000, 6000), texto: `volta com ${v.fila.length} leitura(s) – abrindo a 1ª`};
  }
  if (m.vigiaPronto) {
    v.pos = (v.pos || 0) + 1;
    if (v.pos < v.fila.length) {
      await gravarVigia(v);
      return {ir: salaUrl(v.fila[v.pos]), espera: entre(3000, 6000), texto: `próxima: ${v.pos + 1} de ${v.fila.length}`};
    }
    v.voltas = (v.voltas || 0) + 1;
    v.ultimaVolta = Date.now();
    await gravarVigia(v);
    return {ir: v.home, espera: PAUSA_VOLTA, texto: "volta completa – recomeça em 1 min"};
  }
  return {vigia: true};
}

chrome.runtime.onMessage.addListener((m, remetente, responder) => {
  if (m && m.dormir) { setTimeout(() => responder({}), Math.min(+m.dormir || 0, 180000)); return true; }
  if (m && (m.vigiaTarefa || m.vigiaLista || m.vigiaPronto)) {
    tratarVigia(m, remetente.tab ? remetente.tab.id : null).then(responder);
    return true;
  }
  if (m && m.ligarVigia) { ligarVigia(m.home).then(() => responder({ok: true})); return true; }
  if (m && m.desligarVigia) { desligarVigia().then(() => responder({ok: true})); return true; }
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
  const v = await vigia();
  if (v && v.tabId === tabId) { await gravarVigia(null); await chrome.storage.local.set({vigiaLigada: false}); await marcarBadge(false); }
  const s = await sessao();
  for (const k of ["abaFixa", "abaConta", "abaInfo"]) { const o = s[k] || {}; delete o[tabId]; await gravarSessao(k, o); }
});

// sinal de vida a cada minuto, por empresa: só conta como "viva" a empresa que tem aba aberta
chrome.alarms.create("pulso", {periodInMinutes: 1});
chrome.alarms.onAlarm.addListener(async (a) => {
  if (a.name !== "pulso") return;
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
