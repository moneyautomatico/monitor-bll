const $ = (id) => document.getElementById(id);
const padrao = SERVIDOR_PADRAO.startsWith("__") ? "https://expertlicitante.com.br" : SERVIDOR_PADRAO;

function quando(ms) {
  if (!ms) return "nunca";
  const s = Math.round((Date.now() - ms) / 1000);
  return s < 60 ? "agora" : s < 3600 ? `há ${Math.round(s / 60)} min` : `há ${Math.round(s / 3600)} h`;
}

async function mostrar(extra) {
  const {situacao = {}} = await chrome.storage.local.get("situacao");
  const abas = await chrome.tabs.query({url: "https://portal.licitanet.com.br/*"});
  $("st").innerHTML = (extra ? extra + "<br>" : "") +
    (situacao.erro ? `<span class="erro">⚠️ ${situacao.erro}</span><br>` : "") +
    (situacao.empresa ? `<span class="ok">✅ Conectada: <b>${situacao.empresa}</b></span><br>` : "") +
    `Abas da Licitanet abertas: <b>${abas.length}</b><br>Último envio: ${quando(situacao.ultimo_envio)}`;
}

(async () => {
  const c = await chrome.storage.local.get(["codigo", "servidor"]);
  $("codigo").value = c.codigo || "";
  $("servidor").value = c.servidor || padrao;
  mostrar();
})();

$("salvar").onclick = async () => {
  await chrome.storage.local.set({codigo: $("codigo").value.trim(), servidor: $("servidor").value.trim().replace(/\/+$/, "")});
  const r = await chrome.runtime.sendMessage({evento: {tipo: "ping", plataforma: "licitanet", url: "", versao: chrome.runtime.getManifest().version}});
  mostrar(r && r.ok ? "✅ Teste ok." : "⚠️ O teste não passou.");
};

$("diag").onclick = async () => {
  const [aba] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!aba || !/^https:\/\/portal\.licitanet\.com\.br\//.test(aba.url || "")) { mostrar("Abra uma aba da Licitanet e clique de novo."); return; }
  chrome.tabs.sendMessage(aba.id, {diag: true}, async (est) => {
    if (chrome.runtime.lastError || !est) { mostrar("Recarregue a aba da Licitanet (F5) e tente de novo."); return; }
    const r = await chrome.runtime.sendMessage({evento: {tipo: "diagnostico", plataforma: "licitanet", url: aba.url.split("?")[0],
      versao: chrome.runtime.getManifest().version, estrutura: est}});
    mostrar(r && r.ok ? `🧭 Diagnóstico enviado (processos lidos: ${est.processos_lidos}, mensagens lidas: ${est.chat_lido}).` : "⚠️ Não consegui enviar o diagnóstico.");
  });
};
