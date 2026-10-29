// Expert Licitante – Monitor (Compras.gov.br). Roda na página e só OBSERVA as respostas que o próprio site já recebe
// (não faz nenhum pedido novo ao Compras.gov.br). Repassa as respostas em JSON para o script da extensão.
(() => {
  if (window.__expertCG) return;
  window.__expertCG = 1;
  const doGov = (u) => { try { return /(^|\.)(serpro\.gov\.br|compras\.gov\.br|comprasnet\.gov\.br)$/.test(new URL(u, location.href).hostname); } catch (e) { return false; } };
  const abs = (u) => { try { return new URL(u, location.href).href; } catch (e) { return String(u); } };
  const passa = (url, data) => { try { window.postMessage({__expert: 1, url: abs(url), data}, location.origin); } catch (e) { /* dado não copiável */ } };
  const f0 = window.fetch;
  if (f0) window.fetch = async function (...a) {
    const r = await f0.apply(this, a);
    try {
      const u = (a[0] && a[0].url) || a[0];
      if (doGov(u) && (r.headers.get("content-type") || "").includes("json")) r.clone().json().then((d) => passa(u, d)).catch(() => {});
    } catch (e) { /* ignora */ }
    return r;
  };
  const abrir = XMLHttpRequest.prototype.open, enviar = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__expertUrl = u; return abrir.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    this.addEventListener("load", () => {
      try {
        if (!doGov(this.__expertUrl) || !(this.getResponseHeader("content-type") || "").includes("json")) return;
        passa(this.__expertUrl, this.responseType === "json" ? this.response : JSON.parse(this.responseText));
      } catch (e) { /* resposta não é JSON */ }
    });
    return enviar.apply(this, arguments);
  };
})();
