// Colours the ASCII flow traces (<pre class="trace">): step numbers and names,
// and the "(who does it)" tag on each step line: LLM yellow, stores mint, other tools blue.
document.querySelectorAll("pre.trace").forEach((pre) => {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const tagClass = (t) =>
    /LLM/.test(t) ? "t-llm" : /MongoDB|Pinecone/.test(t) ? "t-store" : "t-tool";
  pre.innerHTML = pre.textContent
    .split("\n")
    .map((line) => {
      let h = esc(line);
      const step = h.match(/^([┌├└]─ )(\d+\.)( [^(←]+)(\(([^)]*)\))?(.*)$/);
      if (step) {
        const [, lead, num, name, paren, tag, rest] = step;
        h = `<span class="t-dim">${lead}</span><span class="t-num">${num}</span><span class="t-step">${name}</span>`;
        if (paren) h += `(<span class="${tagClass(tag)}">${tag}</span>)`;
        if (rest) h += `<span class="${rest.includes("←") ? "t-dim" : "t-step"}">${rest}</span>`;
        return h;
      }
      if (/── .* ──/.test(h)) return h.replace(/(── .* ──)/, '<span class="t-sep">$1</span>');
      return h.replace(/^([│ ]+)/, '<span class="t-dim">$1</span>');
    })
    .join("\n");
});
