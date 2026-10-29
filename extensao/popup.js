const $ = (id) => document.getElementById(id);
const padrao = SERVIDOR_PADRAO.startsWith("__") ? "https://expertlicitante.com.br" : SERVIDOR_PADRAO;
const esc = (s) => (s || "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const quando = (ms) => { if (!ms) return "nunca"; const s = Math.round((Date.now() - ms) / 1000);
  return s < 60 ? "agora" : s < 3600 ? `há ${Math.round(s / 60)} min` : `há ${Math.round(s / 3600)} h`; };

async function contas() { return (await chrome.storage.local.get("contas")).contas || []; }
async function abaAtual() { const [t] = await chrome.tabs.query({active: true, currentWindow: true}); return t; }
const ehLicitanet = (t) => t && /^https:\/\/portal\.licitanet\.com\.br\//.test(t.url || "");

async function desenhar(extra) {
  const cs = await contas();
  $("lista").innerHTML = cs.length ? cs.map((c, i) => `<div class="emp"><span>🏢 <b>${esc(c.empresa || "(testando…)")}</b></span>` +
    `<button class="x" data-i="${i}" title="Remover">✕</button></div>`).join("") : "<small>nenhuma ainda</small>";
  document.querySelectorAll("#lista .x").forEach((b) => b.onclick = async () => {
    if (!confirm("Remover esta empresa da extensão?")) return;
    const l = await contas(); l.splice(+b.dataset.i, 1); await chrome.storage.local.set({contas: l}); desenhar();
  });
  const t = await abaAtual();
  if (!ehLicitanet(t)) { $("aba").innerHTML = "<small>Abra uma aba da Licitanet para ver de qual empresa ela é.</small>"; }
  else {
    const s = await chrome.storage.session.get(["abaFixa", "abaInfo"]);
    const fixa = (s.abaFixa || {})[t.id] || "";
    const info = (s.abaInfo || {})[t.id] || {};
    let tela = info.tela || "";
    try { const r = await chrome.tabs.sendMessage(t.id, {qualEmpresa: true}); tela = (r && r.empresa) || tela; } catch (e) { /* aba sem o script ainda */ }
    $("aba").innerHTML = `Empresa logada nesta aba: <b>${esc(tela || "não identificada")}</b><br>` +
      (info.empresa ? `<span class="ok">✅ Enviando para: <b>${esc(info.empresa)}</b></span>` : info.erro ? `<span class="erro">⚠️ ${esc(info.erro)}</span>` : "<small>ainda sem envio</small>") +
      (cs.length > 1 ? `<label for="fixa">Empresa desta aba</label><select id="fixa"><option value="">Automático (pelo nome no topo da Licitanet)</option>` +
        cs.map((c) => `<option value="${esc(c.codigo)}" ${c.codigo === fixa ? "selected" : ""}>${esc(c.empresa || c.codigo.slice(0, 8))}</option>`).join("") + "</select>" : "");
    if ($("fixa")) $("fixa").onchange = async (ev) => {
      const s2 = (await chrome.storage.session.get("abaFixa")).abaFixa || {};
      if (ev.target.value) s2[t.id] = ev.target.value; else delete s2[t.id];
      await chrome.storage.session.set({abaFixa: s2}); desenhar("Empresa da aba salva.");
    };
  }
  const {situacao = {}} = await chrome.storage.local.get("situacao");
  const abas = await chrome.tabs.query({url: "https://portal.licitanet.com.br/*"});
  $("st").innerHTML = (extra ? esc(extra) + "<br>" : "") + (situacao.erro ? `<span class="erro">⚠️ ${esc(situacao.erro)}</span><br>` : "") +
    `Abas da Licitanet abertas: <b>${abas.length}</b> · último envio: ${quando(situacao.ultimo_envio)}`;
}

(async () => { $("servidor").value = (await chrome.storage.local.get("servidor")).servidor || padrao; desenhar(); })();

$("add").onclick = async () => {
  const codigo = $("codigo").value.trim();
  if (!/^EL-/.test(codigo)) { desenhar("O código começa com EL-. Copie de novo no painel."); return; }
  const l = await contas();
  if (l.some((c) => c.codigo === codigo)) { desenhar("Este código já está na lista."); return; }
  const r = await chrome.runtime.sendMessage({evento: {tipo: "ping", plataforma: "licitanet", url: "", versao: chrome.runtime.getManifest().version}, codigo});
  if (!r || !r.ok) { desenhar("⚠️ O painel não aceitou o código: " + ((r && r.erro) || "confira o código e o endereço")); return; }
  l.push({codigo, empresa: r.empresa || ""});
  await chrome.storage.local.set({contas: l});
  $("codigo").value = "";
  desenhar(`✅ ${r.empresa} adicionada.`);
};

$("salvarSrv").onclick = async () => {
  await chrome.storage.local.set({servidor: $("servidor").value.trim().replace(/\/+$/, "")}); desenhar("Endereço salvo.");
};

$("diag").onclick = async () => {
  const t = await abaAtual();
  if (!ehLicitanet(t)) { desenhar("Abra uma aba da Licitanet e clique de novo."); return; }
  chrome.tabs.sendMessage(t.id, {diag: true}, async (est) => {
    if (chrome.runtime.lastError || !est) { desenhar("Recarregue a aba da Licitanet (F5) e tente de novo."); return; }
    const r = await chrome.runtime.sendMessage({evento: {tipo: "diagnostico", plataforma: "licitanet", url: t.url.split("?")[0],
      versao: chrome.runtime.getManifest().version, empresa_tela: est.empresa_tela || "", estrutura: est}, tabId: t.id});
    desenhar(r && r.ok ? `🧭 Diagnóstico enviado para ${r.empresa} (processos lidos: ${est.processos_lidos}, mensagens lidas: ${est.chat_lido}).`
      : "⚠️ Não enviei: " + ((r && r.erro) || "escolha a empresa desta aba acima."));
  });
};
