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

async function escolherConta(contas, evento, tabId) {
  const s = await sessao();
  const fixa = (s.abaFixa || {})[tabId];
  if (fixa) return contas.find((c) => c.codigo === fixa) || null;
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
    conta = await escolherConta(contas, evento, tabId);
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
      if (k && k.empresa !== j.empresa) { k.empresa = j.empresa; await chrome.storage.local.set({contas: lista}); }
    }
    await situacao({erro: "", ultimo_envio: Date.now(), ultimo_tipo: evento.tipo});
    return {ok: true, empresa: j.empresa};
  }
  await situacao({erro: j.erro || ("O painel respondeu " + status), quando: Date.now()});
  return {ok: false, erro: j.erro, status};
}

chrome.runtime.onMessage.addListener((m, remetente, responder) => {
  if (m && m.evento) {
    const daLicitanet = remetente.tab && /^https:\/\/portal\.licitanet\.com\.br\//.test(remetente.tab.url || "");
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
  const {contas, servidor} = await config();
  const abas = await chrome.tabs.query({url: "https://portal.licitanet.com.br/*"});
  const ac = (await sessao()).abaConta || {};
  const ativas = new Set(abas.map((t) => ac[t.id]).filter(Boolean));
  const versao = chrome.runtime.getManifest().version;
  for (const c of contas) {
    if (ativas.has(c.codigo)) post(servidor, c.codigo, {tipo: "ping", plataforma: "licitanet", versao, url: ""});
    else post(servidor, c.codigo, {tipo: "estado", estado: "sem_aba", plataforma: "licitanet", versao, url: ""});
  }
});
