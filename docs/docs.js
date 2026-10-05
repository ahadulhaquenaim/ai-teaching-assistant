// Turns each ASCII flow trace (<pre class="trace">) into a stacked block flowchart.
// The ASCII stays the source of truth (update-flow-docs edits it); this only renders it.
// Step colour = who does the job: you (Streamlit), LLM, MongoDB/Pinecone, other tool, plain code.
(() => {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const KEEP_CAPS = new Set(["LLM", "UI", "API", "PDF", "DOCX", "MCQ", "POST", "GET", "ID"]);
  const CATS = {
    user: "You (Streamlit)", llm: "LLM call", store: "MongoDB / Pinecone",
    tool: "Other tool or service", code: "Plain code",
  };

  // "CREATE RECORD" -> "Create record", "SHOW in the UI" -> "Show in the UI"
  const sentence = (s) => {
    const t = s.trim().split(/\s+/)
      .map((w) => (/^[A-Z]{2,}\??$/.test(w) && !KEEP_CAPS.has(w.replace("?", "")) ? w.toLowerCase() : w))
      .join(" ");
    return t.charAt(0).toUpperCase() + t.slice(1);
  };

  const category = (name, tag) => {
    if (/^USER\b/.test(name)) return "user";
    if (!tag || /^only if/i.test(tag)) return /WEB SEARCH/.test(name) ? "tool" : "code";
    if (/\bNO LLM\b/.test(tag)) return "code";
    if (/LLM/.test(tag)) return "llm";
    if (/MongoDB|Pinecone/.test(tag)) return "store";
    return "tool";
  };

  // Light inline markup for body text: HTTP routes and quoted values as code.
  const inline = (s) => esc(s)
    .replace(/\b(GET|POST|DELETE|PUT) (\/\S+)/g, "<code>$1 $2</code>")
    .replace(/"([^"]+)"/g, '<code>"$1"</code>');

  const STEP = /^[┌├└]─ (\d+)\.( [^(←]+)(?:\(([^)]*)\))?(.*)$/;

  const parse = (text) => {
    const steps = [];
    let tail = null;
    for (const raw of text.split("\n")) {
      const m = raw.match(STEP);
      if (m) {
        let [, num, name, tag, rest] = m;
        name = name.trim();
        let note = null, lead = null;
        if (rest.includes("←")) { note = rest.replace(/^\s*←\s*/, "").trim(); rest = ""; }
        let title = name + rest;
        if (title.includes(" → ")) { [title, lead] = title.split(/ → (.+)/); }
        if (tag && /^only if/i.test(tag)) { note = tag; }
        steps.push({
          num, title: sentence(title), cat: category(name, tag),
          who: tag && !/^only if/i.test(tag) ? tag : null,
          note, lines: lead ? [{ indent: 0, text: "→ " + lead }] : [], gate: null,
        });
        continue;
      }
      const cur = steps[steps.length - 1];
      if (!cur) continue;
      const body = raw.replace(/^│/, " ");
      const content = body.trim();
      if (!content) continue;
      const sep = content.match(/^──\s*(.*?)\s*──$/);
      if (sep) { cur.gate = sep[1]; continue; }
      const down = content.match(/^↓\s*(.*?)\s*↓$/);
      if (down) { tail = down[1]; continue; }
      cur.lines.push({ indent: body.length - body.trimStart().length, text: content });
    }
    for (const s of steps) {
      const base = Math.min(...s.lines.map((l) => l.indent).filter((n) => n > 0), 99);
      s.lines.forEach((l) => { l.indent = Math.max(0, Math.round((l.indent - base) / 2)); });
      const loop = s.lines.map((l) => l.text.match(/(?:back to|repeat) steps? (\d+(?:-\d+)?)/)).find(Boolean);
      s.loop = loop ? loop[1] : null;
      s.decision = /\?$/.test(s.title);
    }
    return { steps, tail };
  };

  const renderBody = (lines) => {
    if (!lines.length) return "";
    let html = "", inList = false;
    for (const l of lines) {
      const bullet = l.text.match(/^-\s+(.*)$/);
      if (bullet) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += `<li style="--lvl:${l.indent}">${inline(bullet[1])}</li>`;
        continue;
      }
      if (inList) { html += "</ul>"; inList = false; }
      html += `<p style="--lvl:${l.indent}">${inline(l.text)}</p>`;
    }
    if (inList) html += "</ul>";
    return `<div class="fc-body">${html}</div>`;
  };

  const renderStep = (s) => {
    const cls = ["fc-step", `cat-${s.cat}`, s.note ? "is-cond" : "", s.decision ? "is-decision" : ""].join(" ").trim();
    const flags = [
      s.note ? `<span class="fc-flag">${esc(s.note)}</span>` : "",
      s.loop ? `<span class="fc-flag fc-loop">↺ back to step${s.loop.includes("-") ? "s" : ""} ${esc(s.loop)}</span>` : "",
    ].join("");
    const body = renderBody(s.lines);
    const head = `<span class="fc-num" aria-label="Step ${s.num}"><span>${s.num}</span></span>`
      + `<span class="fc-title">${esc(s.title)}</span>${flags}`
      + (s.who ? `<span class="fc-who">${esc(s.who)}</span>` : "")
      + (body ? `<span class="fc-chev" aria-hidden="true"></span>` : "");
    return body
      ? `<details class="${cls}" open><summary class="fc-head">${head}</summary>${body}</details>`
      : `<div class="${cls}"><div class="fc-head">${head}</div></div>`;
  };

  const link = (label) => label
    ? `<div class="fc-link has-label"><span>${esc(label)}</span></div>`
    : `<div class="fc-link" aria-hidden="true"></div>`;

  document.querySelectorAll("pre.trace").forEach((pre) => {
    const { steps, tail } = parse(pre.textContent);
    if (!steps.length) return;
    let html = "";
    steps.forEach((s, i) => {
      html += renderStep(s);
      if (i < steps.length - 1) html += link(s.gate);
    });
    if (tail) html += `<div class="fc-link fc-tail has-label"><span>${esc(tail)}</span></div>`;

    const chart = document.createElement("div");
    chart.className = "flowchart";
    chart.innerHTML = html;

    const bar = document.createElement("div");
    bar.className = "fc-bar";
    const used = [...new Set(steps.map((s) => s.cat))];
    bar.innerHTML = `<div class="fc-key">${Object.keys(CATS).filter((c) => used.includes(c))
      .map((c) => `<span><i class="cat-${c}"></i>${CATS[c]}</span>`).join("")}</div>`;
    if (chart.querySelector("details")) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "fc-toggle";
      btn.textContent = "Collapse steps";
      btn.setAttribute("aria-pressed", "false");
      btn.addEventListener("click", () => {
        const collapse = btn.getAttribute("aria-pressed") === "false";
        chart.querySelectorAll("details").forEach((d) => { d.open = !collapse; });
        chart.classList.toggle("is-compact", collapse);
        btn.setAttribute("aria-pressed", String(collapse));
        btn.textContent = collapse ? "Expand steps" : "Collapse steps";
      });
      bar.appendChild(btn);
    }

    const key = pre.previousElementSibling;
    if (key && key.classList.contains("trace-key")) key.remove();
    pre.replaceWith(bar, chart);
  });
})();

// project-flow.html layer flows (.flow / .step / .arrow): arrows become connectors
// ("→ HTTP →" keeps "HTTP" as a label), steps in a .flow.col are linked in order,
// and neighbouring steps with no arrow between them sit side by side.
(() => {
  const connector = (text) => {
    const label = (text || "").replace(/→/g, "").trim();
    const el = document.createElement("div");
    el.className = label ? "fc-link has-label" : "fc-link";
    if (label) {
      const span = document.createElement("span");
      span.textContent = label;
      el.appendChild(span);
    } else {
      el.setAttribute("aria-hidden", "true");
    }
    return el;
  };

  document.querySelectorAll(".flow").forEach((flow) => {
    // A .flow right after another .flow continues it.
    if (flow.previousElementSibling?.classList.contains("flow")) flow.prepend(connector(""));
    if (flow.classList.contains("col")) {
      [...flow.querySelectorAll(":scope > .step")].slice(1).forEach((s) => s.before(connector("")));
      return;
    }
    let run = [];
    const flush = () => {
      if (run.length > 1) {
        const row = document.createElement("div");
        row.className = "fc-row";
        run[0].before(row);
        row.append(...run);
      }
      run = [];
    };
    for (const el of [...flow.children]) {
      if (el.classList.contains("step")) { run.push(el); continue; }
      flush();
      if (el.classList.contains("arrow")) el.replaceWith(connector(el.textContent));
    }
    flush();
  });

  // Legend chips: inline "background:var(--fe)" -> coloured key square like the flowcharts.
  document.querySelectorAll(".legend span").forEach((span) => {
    const layer = (span.getAttribute("style") || "").match(/var\(--(fe|api|svc|graph|db|ext)\)/);
    if (!layer) return;
    span.removeAttribute("style");
    span.insertAdjacentHTML("afterbegin", `<i class="lay-${layer[1]}"></i>`);
  });
})();
