// Expert Licitante – Monitor: recebe o que os scripts das abas leram e manda para o painel.
importScripts("config.js");

async function config() {
  const c = await chrome.storage.local.get(["codigo", "servidor"]);
  let s = (c.servidor || SERVIDOR_PADRAO || "").trim().replace(/\/+$/, "");
  if (s.startsWith("__")) s = "https://expertlicitante.com.br";
  return {codigo: (c.codigo || "").trim(), servidor: s};
}

async function situacao(campos) {
  const atual = (await chrome.storage.local.get("situacao")).situacao || {};
  await chrome.storage.local.set({situacao: Object.assign(atual, campos)});
}

async function enviar(evento) {
  const {codigo, servidor} = await config();
  if (!codigo) { await situacao({erro: "Cole o código de conexão (painel → Empresas → 🧩 Extensão)."}); return {ok: false}; }
  try {
    const r = await fetch(servidor + "/api/extensao", {
      method: "POST",
      headers: {"Content-Type": "application/json", "Authorization": "Bearer " + codigo},
      body: JSON.stringify(evento)
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) {
      await situacao({erro: j.erro || ("O painel respondeu " + r.status), quando: Date.now()});
      return {ok: false, erro: j.erro};
    }
    await situacao({erro: "", empresa: j.empresa || "", ultimo_envio: Date.now(), ultimo_tipo: evento.tipo});
    return {ok: true, empresa: j.empresa};
  } catch (e) {
    await situacao({erro: "Sem conexão com o painel (" + servidor + ").", quando: Date.now()});
    return {ok: false};
  }
}

chrome.runtime.onMessage.addListener((m, remetente, responder) => {
  if (m && m.evento) { enviar(m.evento).then(responder); return true; }
  return false;
});

// sinal de vida a cada minuto: quantas abas da Licitanet estão abertas
chrome.alarms.create("pulso", {periodInMinutes: 1});
chrome.alarms.onAlarm.addListener(async (a) => {
  if (a.name !== "pulso") return;
  const abas = await chrome.tabs.query({url: "https://portal.licitanet.com.br/*"});
  const versao = chrome.runtime.getManifest().version;
  if (abas.length) enviar({tipo: "ping", plataforma: "licitanet", versao, url: ""});
  else enviar({tipo: "estado", estado: "sem_aba", plataforma: "licitanet", versao, url: ""});
});
