/* Tripwire Admin Control Panel (/admin, local-only) — strictly DOM-safe */
(function () {
  "use strict";

  const contentEl = document.getElementById("tab-content");
  const enforceBtn = document.getElementById("enforce-toggle");
  const enforceLabel = document.getElementById("enforce-label");
  const refreshBtn = document.getElementById("refresh-btn");
  const themeToggleBtn = document.getElementById("theme-toggle");
  const toastEl = document.getElementById("toast");
  const tabBtns = Array.from(document.querySelectorAll(".tab-btn"));

  let activeTab = (window.location.hash || "#live").slice(1);
  if (!["live", "orders", "usage", "models", "keys", "redteam"].includes(activeTab)) {
    activeTab = "live";
  }
  let currentEnforced = true;
  let liveTimer = null;
  let toastTimer = null;

  // ---------------------------------------------------------------------------
  // Utilities (strictly DOM-safe: never uses innerHTML)
  // ---------------------------------------------------------------------------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === "className") node.className = v;
        else if (k === "onclick") node.addEventListener("click", v);
        else if (k === "onsubmit") node.addEventListener("submit", v);
        else if (k === "value") node.value = String(v);
        else if (k === "checked") node.checked = Boolean(v);
        else if (k === "disabled") node.disabled = Boolean(v);
        else node.setAttribute(k, String(v));
      }
    }
    for (const c of children) {
      if (c === null || c === undefined || c === false) continue;
      if (Array.isArray(c)) {
        for (const item of c) {
          if (item !== null && item !== undefined && item !== false) {
            node.appendChild(item instanceof Node ? item : document.createTextNode(String(item)));
          }
        }
      } else {
        node.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
      }
    }
    return node;
  }

  function safeUrl(u) {
    return typeof u === "string" && (u.startsWith("https://") || u.startsWith("http://")) ? u : null;
  }

  function money(cents, currency) {
    if (typeof cents !== "number") return "—";
    const sym = !currency || currency === "USD" ? "$" : currency + " ";
    return sym + (cents / 100).toFixed(2);
  }

  function fmtCost(usd) {
    if (usd === null || usd === undefined) return "unknown";
    if (usd === 0) return "$0.00";
    if (usd < 0.01) return "$" + usd.toFixed(5);
    return "$" + usd.toFixed(4);
  }

  function fmtInt(n) {
    if (n === null || n === undefined) return "—";
    return Number(n).toLocaleString();
  }

  function fmtMs(ms) {
    if (ms === null || ms === undefined) return "—";
    if (ms >= 1000) return (ms / 1000).toFixed(2) + "s";
    return Math.round(ms) + "ms";
  }

  function fmtPct(rate) {
    if (rate === null || rate === undefined) return "—";
    return (Number(rate) * 100).toFixed(1) + "%";
  }

  function fmtTime(ts) {
    if (!ts) return "—";
    return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function fmtDateTime(ts) {
    if (!ts) return "Never";
    return new Date(ts * 1000).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  }

  function showToast(msg) {
    toastEl.textContent = msg;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 3200);
  }

  async function api(url, opts) {
    const resp = await fetch(url, { cache: "no-store", ...opts });
    let data = {};
    try { data = await resp.json(); } catch (_) {}
    if (!resp.ok) {
      throw new Error(data.error || data.speak || ("HTTP " + resp.status));
    }
    return data;
  }

  // ---------------------------------------------------------------------------
  // Theme & Enforcement Toggle
  // ---------------------------------------------------------------------------
  function initTheme() {
    const saved = localStorage.getItem("tripwire-theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
    updateThemeBtn();
    themeToggleBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme");
      const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
      const isDark = current ? current === "dark" : prefersDark;
      const next = isDark ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("tripwire-theme", next);
      updateThemeBtn();
    });
  }

  function updateThemeBtn() {
    const current = document.documentElement.getAttribute("data-theme");
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const isDark = current ? current === "dark" : prefersDark;
    themeToggleBtn.textContent = isDark ? "Light mode" : "Dark mode";
  }

  function updateEnforceUI(enforced) {
    currentEnforced = Boolean(enforced);
    enforceBtn.className = "enforce-toggle " + (currentEnforced ? "on" : "off");
    enforceBtn.setAttribute("aria-pressed", String(currentEnforced));
    enforceLabel.textContent = currentEnforced ? "Tripwire ON" : "Tripwire OFF (Demo)";
  }

  enforceBtn.addEventListener("click", async () => {
    enforceBtn.disabled = true;
    try {
      const res = await api("/api/admin/enforcement", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ enforced: !currentEnforced }),
      });
      updateEnforceUI(res.enforced);
      showToast(res.enforced ? "Tripwire enforcement ON" : "Tripwire enforcement OFF (verdicts logged only)");
    } catch (e) {
      showToast("Failed: " + e.message);
    } finally {
      enforceBtn.disabled = false;
    }
  });

  // ---------------------------------------------------------------------------
  // Tab Navigation
  // ---------------------------------------------------------------------------
  function setTab(tab) {
    activeTab = tab;
    window.location.hash = tab;
    for (const b of tabBtns) {
      b.classList.toggle("active", b.dataset.tab === tab);
    }
    clearInterval(liveTimer);
    liveTimer = null;
    loadActiveTab();
    if (tab === "live") {
      liveTimer = setInterval(() => {
        if (activeTab === "live") loadLive(true);
      }, 2500);
    }
  }

  for (const b of tabBtns) {
    b.addEventListener("click", () => setTab(b.dataset.tab));
  }
  refreshBtn.addEventListener("click", () => loadActiveTab());

  function loadActiveTab() {
    if (activeTab === "live") loadLive(false);
    else if (activeTab === "orders") loadOrders();
    else if (activeTab === "usage") loadUsage();
    else if (activeTab === "models") loadModels();
    else if (activeTab === "keys") loadKeys();
    else if (activeTab === "redteam") loadRedteam();
  }

  // ---------------------------------------------------------------------------
  // TAB 1: LIVE
  // ---------------------------------------------------------------------------
  let quickInputVal = "";

  async function loadLive(silent) {
    try {
      const data = await api("/api/admin/live");
      updateEnforceUI(data.enforced);
      renderLive(data);
    } catch (e) {
      if (!silent) {
        contentEl.replaceChildren(el("div", { className: "callout callout-bad" }, "Failed to load Live data: " + e.message));
      }
    }
  }

  function renderLive(data) {
    // Preserve input focus/value if user is typing in the quick request box
    const existingInput = document.getElementById("quick-req-input");
    const wasFocused = existingInput && document.activeElement === existingInput;
    if (existingInput) quickInputVal = existingInput.value;

    const frag = document.createDocumentFragment();

    // Quick typed request launcher for demos
    const reqInput = el("input", {
      id: "quick-req-input",
      type: "text",
      placeholder: "Send a test request (e.g. a 100W USB-C cable under 20 dollars, returnable)",
      value: quickInputVal,
    });
    const sendBtn = el("button", { className: "btn-primary", type: "submit" }, "Run request");
    const quickForm = el("form", {
      className: "quick-request-bar",
      onsubmit: async (ev) => {
        ev.preventDefault();
        const text = reqInput.value.trim();
        if (!text) return;
        sendBtn.disabled = true;
        sendBtn.textContent = "Starting…";
        try {
          const r = await api("/request_text", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ text }),
          });
          quickInputVal = "";
          reqInput.value = "";
          showToast("Started " + r.contract_id);
          loadLive(false);
        } catch (e) {
          showToast("Error: " + e.message);
        } finally {
          sendBtn.disabled = false;
          sendBtn.textContent = "Run request";
        }
      },
    }, reqInput, sendBtn);
    frag.appendChild(quickForm);

    // Pending approval banner (laptop fallback for phone)
    if (data.pending && data.pending.cart_id) {
      const p = data.pending;
      const approveBtn = el("button", {
        className: "btn-ok",
        type: "button",
        onclick: () => decidePending(p.cart_id, "approve", approveBtn, blockBtn),
      }, "Approve & create Shop checkout");
      const blockBtn = el("button", {
        className: "btn-bad",
        type: "button",
        onclick: () => decidePending(p.cart_id, "block", approveBtn, blockBtn),
      }, "Block");

      frag.appendChild(el("div", { className: "pending-banner" },
        el("div", { style: "min-width:0;flex:1;" },
          el("div", { style: "display:flex;align-items:center;gap:8px;margin-bottom:4px;" },
            el("span", { className: "pill pill-info" }, "PENDING HUMAN APPROVAL"),
            el("span", { className: "mono small muted" }, p.cart_id)),
          el("div", { style: "font-weight:700;" }, p.summary || "Cart awaiting decision"),
          p.reason ? el("div", { className: "small muted", style: "margin-top:3px;" }, p.reason) : null),
        el("div", { style: "display:flex;gap:8px;flex-wrap:wrap;" }, blockBtn, approveBtn)));
    }

    const reqs = data.requests || [];
    if (!reqs.length) {
      frag.appendChild(el("div", { className: "empty-state" },
        "No requests yet. Speak a request via Tripwire Buy or type one above."));
    } else {
      for (const req of reqs) {
        frag.appendChild(renderRequestCard(req));
      }
    }

    contentEl.replaceChildren(frag);
    if (wasFocused) {
      const restored = document.getElementById("quick-req-input");
      if (restored) {
        restored.focus();
        restored.setSelectionRange(restored.value.length, restored.value.length);
      }
    }
  }

  async function decidePending(cartId, decision, btnA, btnB) {
    btnA.disabled = true;
    btnB.disabled = true;
    try {
      const res = await api("/api/admin/approve", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ cart_id: cartId, decision }),
      });
      showToast(res.speak || (decision === "approve" ? "Approved" : "Blocked"));
      loadLive(false);
    } catch (e) {
      showToast("Decision failed: " + e.message);
      btnA.disabled = false;
      btnB.disabled = false;
    }
  }

  function statusPill(status) {
    const map = {
      approve: "pill-ok",
      completed: "pill-ok",
      ask_human: "pill-warn",
      block: "pill-bad",
      contract_failed: "pill-bad",
      agent_failed: "pill-bad",
      shopping: "pill-info",
      compiling_contract: "pill-info",
      contract_ready: "pill-info",
    };
    return el("span", { className: "pill " + (map[status] || "pill-neutral") }, status || "unknown");
  }

  function renderRequestCard(req) {
    const headerLeft = el("div", { style: "display:flex;flex-wrap:wrap;align-items:center;gap:8px;" },
      el("span", { className: "mono", style: "font-weight:750;" }, req.id),
      statusPill(req.status),
      req.contract && req.contract.compiled_by
        ? el("span", { className: "pill pill-neutral mono" }, req.contract.compiled_by)
        : null,
      req.pay_ready ? el("span", { className: "pill pill-ok" }, "Checkout ready") : null);

    const headerRight = el("div", { style: "display:flex;align-items:center;gap:10px;" },
      req.view_url
        ? el("a", { href: req.view_url, target: "_blank", rel: "noopener", className: "small" }, "Shopper view ↗")
        : null,
      el("span", { className: "small muted num" }, fmtTime(req.ts)));

    // Left column: Contract + Photo
    const contractCol = renderContractSection(req);

    // Right column: Verdict X-ray + Decisions clause bars
    const verdictCol = renderVerdictSection(req);

    // Bottom: Agent tool calls timeline
    const toolsSection = renderToolCalls(req);

    // Per-request pending fallback buttons (if pending_cart exists on this request)
    let inlinePending = null;
    if (req.pending_cart) {
      const pc = req.pending_cart;
      const bOk = el("button", {
        className: "btn-ok btn-sm",
        type: "button",
        onclick: () => decidePending(pc.cart_id, "approve", bOk, bNo),
      }, "Approve");
      const bNo = el("button", {
        className: "btn-bad btn-sm",
        type: "button",
        onclick: () => decidePending(pc.cart_id, "block", bOk, bNo),
      }, "Block");
      inlinePending = el("div", {
        className: "callout callout-info",
        style: "display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;margin-top:12px;",
      },
      el("div", null, el("strong", null, "Awaiting decision: "), pc.summary || pc.cart_id),
      el("div", { style: "display:flex;gap:8px;" }, bNo, bOk));
    }

    return el("article", { className: "card" },
      el("div", { className: "card-title" }, headerLeft, headerRight),
      el("div", { className: "grid-2" }, contractCol, verdictCol),
      inlinePending,
      toolsSection);
  }

  function renderContractSection(req) {
    const c = req.contract;
    const nodes = [el("div", { className: "xray-label" }, "Intent Contract")];

    if (req.has_photo && req.photo_url) {
      const img = el("img", { className: "shopper-photo", src: req.photo_url, alt: "Shopper photo" });
      nodes.push(el("a", { href: req.photo_url, target: "_blank", rel: "noopener" }, img));
    }

    if (!c) {
      nodes.push(el("div", { className: "muted" }, req.text ? "Request: " + req.text : "Compiling contract…"));
      return el("div", null, nodes);
    }

    nodes.push(el("div", { style: "font-weight:700;margin-bottom:6px;" }, c.summary || c.item || ""));
    if (c.user_request) {
      nodes.push(el("div", { className: "small muted", style: "margin-bottom:6px;" }, "Heard: “" + c.user_request + "”"));
    }

    nodes.push(el("div", { style: "display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;" },
      el("span", { className: "pill pill-neutral num" }, "Cap: " + money(c.max_total_cents, c.currency)),
      c.returns ? el("span", { className: "pill pill-info" }, "Returns: " + c.returns) : null,
      c.signed ? el("span", { className: "pill pill-ok" }, "Ed25519 signed") : null));

    if (c.photo_observations && c.photo_observations.length) {
      nodes.push(el("div", { className: "small muted", style: "font-weight:650;margin-top:6px;" }, "Photo observations:"));
      const ul = el("ul", { className: "clause-list" });
      for (const obs of c.photo_observations) ul.appendChild(el("li", { className: "small" }, "• ", obs));
      nodes.push(ul);
    }

    const cl = el("ul", { className: "clause-list" });
    for (const m of c.must || []) {
      cl.appendChild(el("li", { className: "clause-item" },
        el("span", { className: "clause-tag clause-must" }, m.id || "must"),
        el("span", null, m.clause || m)));
    }
    for (const n of c.must_not || []) {
      cl.appendChild(el("li", { className: "clause-item" },
        el("span", { className: "clause-tag clause-must-not" }, n.id || "not"),
        el("span", null, n.clause || n)));
    }
    if (cl.childNodes.length) nodes.push(cl);

    if (c.compile_attempts && c.compile_attempts.length) {
      const att = c.compile_attempts.map((a) =>
        a.model + " (" + (a.ok ? fmtMs(a.latency_ms) : ("failed: " + (a.error || "err"))) + ")"
      ).join(" → ");
      nodes.push(el("div", { className: "small muted mono", style: "margin-top:6px;" }, "Compile: " + att));
    }

    return el("div", null, nodes);
  }

  function renderVerdictSection(req) {
    const v = req.verdict;
    const nodes = [el("div", { className: "xray-label" }, "Guard Verdict X-Ray & Decisions Scores")];

    if (!v) {
      nodes.push(el("div", { className: "muted small" }, "Waiting for shopping agent to call request_checkout…"));
      if (req.picked) {
        nodes.push(el("div", { className: "small", style: "margin-top:8px;" },
          "Picked so far: ", el("strong", null, req.picked.summary || req.picked.title)));
      }
      return el("div", null, nodes);
    }

    const stages = v.stages || {};
    const xray = v.xray || {};

    nodes.push(el("div", { style: "display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px;" },
      statusPill(v.verdict),
      v.enforced === false ? el("span", { className: "pill pill-warn" }, "ENFORCEMENT OFF") : null,
      typeof v.latency_ms === "number" ? el("span", { className: "pill pill-neutral num" }, fmtMs(v.latency_ms)) : null,
      stages.policy ? el("span", { className: "pill " + (stages.policy.pass ? "pill-ok" : "pill-bad") },
        "Policy: " + (stages.policy.pass ? "pass" : "fail")) : null,
      stages.canary ? el("span", { className: "pill " + (stages.canary.tripped ? "pill-bad" : "pill-ok") },
        "Canary: " + (stages.canary.tripped ? "TRIPPED" : "clean")) : null,
      stages.quarantine ? el("span", { className: "pill " + (stages.quarantine.clean ? "pill-ok" : "pill-warn") },
        "Quarantine: " + (stages.quarantine.clean ? "clean" : ((stages.quarantine.stripped_count || 0) + " stripped"))) : null,
      stages.contract && stages.contract.model
        ? el("span", { className: "pill pill-neutral mono" }, "Checker: " + stages.contract.model)
        : null));

    // X-ray 4 boxes
    const hasHidden = Boolean((xray.hidden_in_listing || "").trim());
    const reasonsList = (xray.reasons && xray.reasons.length)
      ? el("ul", { className: "clause-list", style: "margin:0;" },
          ...xray.reasons.map((r) => el("li", { className: "small" }, "• ", r)))
      : el("div", { className: "small muted" }, "None");

    nodes.push(el("div", { className: "xray-grid" },
      el("div", { className: "xray-box" },
        el("div", { className: "xray-label" }, "Asked for"),
        el("div", { className: "small" }, xray.asked_for || "—")),
      el("div", { className: "xray-box" },
        el("div", { className: "xray-label" }, "Agent tried"),
        el("div", { className: "small" }, xray.agent_tried || "—")),
      el("div", { className: "xray-box" + (hasHidden ? " alert" : "") },
        el("div", { className: "xray-label" }, "Hidden in listing"),
        el("div", { className: "small" }, hasHidden ? xray.hidden_in_listing : "Nothing suspicious detected")),
      el("div", { className: "xray-box" },
        el("div", { className: "xray-label" }, "Reasons"),
        reasonsList)));

    // Decisions clause score bars
    const dec = stages.decisions || {};
    const clauses = dec.clauses || {};
    const clauseEntries = Object.entries(clauses);
    nodes.push(el("div", { className: "xray-label", style: "margin-top:12px;" },
      "Decisions API (" + (dec.model || "gemini-decision-1") + ")" + (dec.escalated ? " · ESCALATED" : "")));

    if (!clauseEntries.length) {
      nodes.push(el("div", { className: "small muted" }, "No clause scores recorded for this verdict yet."));
    } else {
      // Build lookup map from contract clause ids -> text
      const clauseText = {};
      const mustIds = new Set();
      const notIds = new Set(["injection"]);
      if (req.contract) {
        for (const m of req.contract.must || []) {
          clauseText[m.id] = m.clause;
          mustIds.add(m.id);
        }
        for (const n of req.contract.must_not || []) {
          clauseText[n.id] = n.clause;
          notIds.add(n.id);
        }
      }

      for (const [key, val] of clauseEntries) {
        nodes.push(renderDecisionBar(key, val, clauseText[key], mustIds.has(key), notIds.has(key)));
      }
    }

    return el("div", null, nodes);
  }

  function renderDecisionBar(key, val, description, isMust, isNegative) {
    if (!val || typeof val !== "object") {
      return el("div", { className: "score-row small" }, key + ": " + String(val));
    }

    // Choice-type question (e.g. returns)
    if (val.choice !== undefined) {
      const probs = val.probabilities || {};
      const topP = typeof probs[val.choice] === "number" ? probs[val.choice] : val.confidence;
      const pct = typeof topP === "number" ? Math.round(topP * 100) : 0;
      const barCls = val.choice === "final_sale" ? "bar-bad" : (val.choice === "returnable" ? "bar-ok" : "bar-info");
      return el("div", { className: "score-row" },
        el("div", { className: "score-head" },
          el("span", null, el("strong", { className: "mono" }, key), " → ", el("strong", null, String(val.choice))),
          el("span", { className: "small muted num" },
            (typeof topP === "number" ? pct + "%" : "") +
            (typeof val.confidence === "number" ? " (conf " + Math.round(val.confidence * 100) + "%)" : ""))),
        el("div", { className: "bar-track" },
          el("div", { className: "bar-fill " + barCls, style: "width:" + Math.max(2, Math.min(100, pct)) + "%;" })));
    }

    // Noul / p_yes / score numeric bar
    const rawP = val.p_yes !== undefined ? val.p_yes : (val.noul !== undefined ? val.noul : val.score);
    const p = typeof rawP === "number" ? rawP : 0;
    const pct = Math.round(Math.max(0, Math.min(1, p)) * 100);
    let barCls = "bar-info";
    if (isNegative) {
      barCls = p <= 0.15 ? "bar-ok" : (p <= 0.5 ? "bar-warn" : "bar-bad");
    } else if (isMust || key.startsWith("m")) {
      barCls = p >= 0.85 ? "bar-ok" : (p >= 0.5 ? "bar-warn" : "bar-bad");
    }

    const labelNodes = [el("strong", { className: "mono" }, key)];
    if (description) labelNodes.push(" — ", description);

    return el("div", { className: "score-row" },
      el("div", { className: "score-head" },
        el("span", { style: "min-width:0;" }, labelNodes),
        el("span", { className: "small muted num" },
          "p=" + p.toFixed(2) +
          (typeof val.confidence === "number" ? " · conf " + val.confidence.toFixed(2) : ""))),
      el("div", { className: "bar-track" },
        el("div", { className: "bar-fill " + barCls, style: "width:" + Math.max(2, pct) + "%;" })));
  }

  function renderToolCalls(req) {
    const events = (req.events || []).filter((e) =>
      ["tool_call", "search", "cart", "verdict", "checkout_created", "checkout_refreshed",
       "human_decision", "agent_turn", "agent_done", "agent_failed", "contract_failed"].includes(e.kind)
    );
    const box = el("div", { style: "margin-top:14px;border-top:1px solid var(--border);padding-top:12px;" },
      el("div", { className: "xray-label" }, "Agent Tool Calls & Pipeline Events (" + events.length + ")"));

    if (!events.length) {
      box.appendChild(el("div", { className: "small muted" }, "No tool calls yet."));
      return box;
    }

    const log = el("div", { className: "tool-log" });
    for (const ev of events) {
      let summary = "";
      if (ev.kind === "tool_call") {
        summary = (ev.name || "tool") + "(" + JSON.stringify(ev.args || {}) + ")";
      } else if (ev.kind === "search") {
        summary = "query=“" + (ev.query || "") + "” → " + (ev.results ?? 0) + " live results";
      } else if (ev.kind === "cart") {
        summary = ev.summary || ev.title || ev.cart_id || "";
      } else if (ev.kind === "verdict") {
        summary = (ev.verdict || "") + (ev.enforced === false ? " [OFF]" : "") + (ev.reason ? " — " + ev.reason : "");
      } else if (ev.kind === "agent_turn") {
        summary = "turn " + ev.turn + " · " + ev.status + " · " + fmtMs(ev.latency_ms);
      } else if (ev.kind === "agent_done") {
        summary = "finished in " + ev.seconds + "s" + (ev.summary ? " — " + ev.summary : "");
      } else if (ev.kind === "human_decision") {
        summary = "Human decided: " + ev.decision;
      } else if (ev.kind === "checkout_created" || ev.kind === "checkout_refreshed") {
        summary = "status=" + ev.status + (ev.has_order ? " (order confirmed)" : "");
      } else if (ev.error) {
        summary = ev.error;
      }

      log.appendChild(el("div", { className: "tool-entry" },
        el("div", { className: "tool-entry-head" },
          el("span", { className: "mono", style: "font-weight:700;" }, ev.kind),
          el("span", { className: "small muted num" }, fmtTime(ev.ts))),
        summary ? el("div", { className: "small mono", style: "word-break:break-word;" }, summary) : null));
    }
    box.appendChild(log);
    return box;
  }

  // ---------------------------------------------------------------------------
  // TAB 2: ORDERS
  // ---------------------------------------------------------------------------
  async function loadOrders() {
    try {
      const data = await api("/api/admin/orders");
      renderOrders(data.orders || []);
    } catch (e) {
      contentEl.replaceChildren(el("div", { className: "callout callout-bad" }, "Failed to load orders: " + e.message));
    }
  }

  function renderOrders(orders) {
    if (!orders.length) {
      contentEl.replaceChildren(el("div", { className: "empty-state" },
        "No Shop checkouts created yet. Approve a pending cart to create a real Shop checkout."));
      return;
    }

    const frag = document.createDocumentFragment();
    for (const o of orders) {
      const ch = o.checkout || {};
      const refreshOrderBtn = el("button", {
        className: "btn-sm",
        type: "button",
        disabled: !o.can_refresh,
        onclick: async () => {
          refreshOrderBtn.disabled = true;
          refreshOrderBtn.textContent = "Checking Shop MCP…";
          try {
            const res = await api("/api/admin/orders/" + encodeURIComponent(o.cart_id) + "/refresh", { method: "POST" });
            showToast("Checkout status: " + (res.order && res.order.status ? res.order.status : "updated"));
            loadOrders();
          } catch (e) {
            showToast("Refresh failed: " + e.message);
            refreshOrderBtn.disabled = false;
            refreshOrderBtn.textContent = "Refresh status";
          }
        },
      }, "Refresh status");

      const headerLeft = el("div", { style: "display:flex;flex-wrap:wrap;align-items:center;gap:8px;" },
        el("span", { className: "mono", style: "font-weight:750;" }, o.cart_id || "checkout"),
        statusPill(o.status),
        ch.shop_name ? el("span", { className: "pill pill-neutral" }, ch.shop_name) : null,
        o.request_id ? el("span", { className: "small muted mono" }, "req " + o.request_id) : null);

      const headerRight = el("div", { style: "display:flex;align-items:center;gap:10px;" },
        el("span", { className: "small muted num" }, fmtDateTime(o.ts)),
        refreshOrderBtn);

      const itemsList = el("ul", { className: "clause-list" });
      for (const line of ch.lines || []) {
        itemsList.appendChild(el("li", null, (line.quantity || 1) + "× " + (line.title || "Item")));
      }

      const totalsBox = el("div", { className: "totals-list" });
      for (const t of ch.totals || []) {
        const isTotal = t.type === "total";
        totalsBox.appendChild(el("div", { className: "total-line num" + (isTotal ? " grand" : "") },
          el("span", null, t.label || t.type || "Amount"),
          el("span", null, money(t.amount, ch.currency))));
      }

      const msgs = [];
      for (const m of ch.messages || []) {
        const cls = (m.type === "error" || m.severity === "error") ? "callout-bad" : "callout-warn";
        msgs.push(el("div", { className: "callout " + cls + " small" },
          el("strong", null, (m.type || "notice").toUpperCase() + ": "),
          m.content || m.code || ""));
      }

      const linksRow = el("div", { className: "link-row" });
      for (const link of ch.links || []) {
        const href = safeUrl(link.url);
        if (!href) continue;
        linksRow.appendChild(el("a", { href, target: "_blank", rel: "noopener noreferrer" },
          String(link.type || "link").replace(/_/g, " ") + " ↗"));
      }

      let orderBox = null;
      if (ch.status === "completed" && ch.order) {
        const parts = [];
        if (ch.order.name) parts.push("Order " + ch.order.name);
        if (ch.order.order_number) parts.push("#" + ch.order.order_number);
        if (ch.order.created_at) parts.push(ch.order.created_at);
        const orderLink = safeUrl(ch.order.status_url || ch.order.permalink_url);
        orderBox = el("div", { className: "callout callout-ok" },
          el("strong", null, "Paid & Completed: "),
          parts.join(" · ") || "Order confirmed",
          orderLink ? el("span", null, " · ", el("a", { href: orderLink, target: "_blank", rel: "noopener noreferrer" }, "Order page ↗")) : null);
      }

      frag.appendChild(el("article", { className: "card" },
        el("div", { className: "card-title" }, headerLeft, headerRight),
        o.request_summary ? el("div", { className: "small muted", style: "margin-bottom:8px;" }, "Request: " + o.request_summary) : null,
        el("div", { className: "grid-2" },
          el("div", null,
            el("div", { className: "xray-label" }, "Items"),
            itemsList,
            o.agent_summary
              ? el("div", { style: "margin-top:10px;" },
                  el("div", { className: "xray-label" }, "Agent summary"),
                  el("div", { className: "small muted" }, o.agent_summary))
              : null),
          el("div", null,
            el("div", { className: "xray-label" }, "Totals (exact Shop labels)"),
            totalsBox,
            linksRow.childNodes.length
              ? el("div", { style: "margin-top:10px;" },
                  el("div", { className: "xray-label" }, "Merchant links (exact as returned)"),
                  linksRow)
              : null)),
        msgs,
        orderBox));
    }
    contentEl.replaceChildren(frag);
  }

  // ---------------------------------------------------------------------------
  // TAB 3: USAGE
  // ---------------------------------------------------------------------------
  async function loadUsage() {
    try {
      const data = await api("/api/admin/usage");
      renderUsage(data);
    } catch (e) {
      contentEl.replaceChildren(el("div", { className: "callout callout-bad" }, "Failed to load usage: " + e.message));
    }
  }

  function renderUsage(data) {
    const t = data.totals || {};
    const s = data.settings || {};
    const frag = document.createDocumentFragment();

    // KPI row
    const budget = s.daily_token_budget || 0;
    const todayTok = t.today_tokens || 0;
    const budgetLabel = budget > 0 ? (fmtInt(todayTok) + " / " + fmtInt(budget)) : (fmtInt(todayTok) + " (unlimited)");

    frag.appendChild(el("div", { className: "kpi-grid" },
      kpiCard("Total calls", fmtInt(t.calls), fmtInt(t.errors) + " errors"),
      kpiCard("Latency p50 / p95", fmtMs(t.p50_ms), "p95: " + fmtMs(t.p95_ms)),
      kpiCard("Total tokens", fmtInt((t.input_tokens || 0) + (t.output_tokens || 0)),
        "in " + fmtInt(t.input_tokens) + " · out " + fmtInt(t.output_tokens)),
      kpiCard("Est. cost", fmtCost(t.est_cost_usd),
        (t.unknown_cost_models && t.unknown_cost_models.length) ? ("excl. " + t.unknown_cost_models.join(", ")) : "all priced models"),
      kpiCard("Today's tokens", budgetLabel, fmtInt(t.today_calls) + " calls today")));

    // Budget bar if set
    if (budget > 0) {
      const pct = Math.min(100, Math.round((todayTok / budget) * 100));
      const barCls = pct >= 100 ? "bar-bad" : (pct >= 80 ? "bar-warn" : "bar-ok");
      frag.appendChild(el("div", { className: "card", style: "margin-bottom:14px;" },
        el("div", { className: "score-head" },
          el("strong", null, "Daily token budget"),
          el("span", { className: "num small muted" }, pct + "% (" + fmtInt(todayTok) + " / " + fmtInt(budget) + ")")),
        el("div", { className: "bar-track" },
          el("div", { className: "bar-fill " + barCls, style: "width:" + pct + "%;" }))));
    }

    // Per-model table
    frag.appendChild(el("div", { className: "card" },
      el("div", { className: "card-title" }, "Usage per model", el("span", { className: "small muted" }, "logs/usage.jsonl")),
      usageTable(data.by_model || [], true)));

    // Per-job table
    frag.appendChild(el("div", { className: "card" },
      el("div", { className: "card-title" }, "Usage per job"),
      usageTable(data.by_job || [], false)));

    // Rate limit & Daily token budget settings
    const rpmInput = el("input", { type: "number", min: "1", max: "1000", value: s.rate_limit_per_min || 10 });
    const budgetInput = el("input", { type: "number", min: "0", step: "1000", value: s.daily_token_budget || 0 });
    const saveBtn = el("button", { className: "btn-primary", type: "submit" }, "Save limits");

    const settingsForm = el("form", {
      className: "card",
      onsubmit: async (ev) => {
        ev.preventDefault();
        saveBtn.disabled = true;
        try {
          await api("/api/admin/settings", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              rate_limit_per_min: Number(rpmInput.value),
              daily_token_budget: Number(budgetInput.value),
            }),
          });
          showToast("Limits saved to .env and applied");
          loadUsage();
        } catch (e) {
          showToast("Save failed: " + e.message);
        } finally {
          saveBtn.disabled = false;
        }
      },
    },
    el("div", { className: "card-title" }, "Rate limit & daily token budget"),
    el("div", { className: "form-row" },
      el("label", null, "Requests per minute (RATE_LIMIT_PER_MIN)"),
      rpmInput),
    el("div", { className: "form-row" },
      el("label", null, "Daily token budget (0 = unlimited; /request replies 'budget reached')"),
      budgetInput),
    el("div", { style: "display:flex;justify-content:flex-end;" }, saveBtn));

    frag.appendChild(settingsForm);
    contentEl.replaceChildren(frag);
  }

  function kpiCard(label, value, sub) {
    return el("div", { className: "kpi-card" },
      el("div", { className: "kpi-label" }, label),
      el("div", { className: "kpi-value num" }, value),
      sub ? el("div", { className: "kpi-sub num" }, sub) : null);
  }

  function usageTable(rows, showRates) {
    if (!rows.length) {
      return el("div", { className: "small muted" }, "No logged calls yet.");
    }
    const headers = ["Name", "Calls", "Errors", "p50", "p95", "Input tok", "Output tok"];
    if (showRates) headers.push("Rate ($/1M)");
    headers.push("Est. cost");

    const thead = el("thead", null, el("tr", null,
      ...headers.map((h, i) => el("th", { className: i > 0 ? "num" : "" }, h))));

    const tbody = el("tbody", null, ...rows.map((r) => {
      const cells = [
        el("td", { className: "mono" }, r.name),
        el("td", { className: "num" }, fmtInt(r.calls)),
        el("td", { className: "num" }, r.errors ? el("span", { className: "pill pill-bad" }, fmtInt(r.errors)) : "0"),
        el("td", { className: "num" }, fmtMs(r.p50_ms)),
        el("td", { className: "num" }, fmtMs(r.p95_ms)),
        el("td", { className: "num" }, fmtInt(r.input_tokens)),
        el("td", { className: "num" }, fmtInt(r.output_tokens)),
      ];
      if (showRates) {
        const rateStr = r.rate_per_1m
          ? ("$" + r.rate_per_1m.input + " / $" + r.rate_per_1m.output)
          : "unknown";
        cells.push(el("td", { className: "num small muted" }, rateStr));
      }
      cells.push(el("td", { className: "num" }, fmtCost(r.est_cost_usd)));
      return el("tr", null, cells);
    }));

    return el("div", { className: "table-wrap" }, el("table", null, thead, tbody));
  }

  // ---------------------------------------------------------------------------
  // TAB 4: MODELS
  // ---------------------------------------------------------------------------
  async function loadModels() {
    try {
      const data = await api("/api/admin/models");
      renderModels(data);
    } catch (e) {
      contentEl.replaceChildren(el("div", { className: "callout callout-bad" }, "Failed to load models: " + e.message));
    }
  }

  function renderModels(data) {
    const models = data.models || {};
    const allowed = data.allowed_models || [];
    const thinkingLevels = data.allowed_thinking || ["low", "medium", "high", "xhigh"];
    const recent = data.recent_calls || [];

    const fields = [
      ["CONTRACT_MODEL", "Contract compiler (primary)", allowed],
      ["CONTRACT_FALLBACK_MODEL", "Contract compiler (fallback)", allowed],
      ["CONTRACT_THINKING", "Contract thinking level (Gemini 4)", thinkingLevels],
      ["AGENT_MODEL", "Shopping agent", allowed],
      ["CHECKER_MODEL", "Contract checker on escalations (Tanu)", allowed],
      ["ATTACK_MODEL", "Red-team attack generator (Tanu)", allowed],
    ];

    const selects = {};
    const rows = fields.map(([key, label, options]) => {
      const sel = el("select", { name: key },
        ...options.map((opt) => {
          const o = el("option", { value: opt }, opt);
          if (models[key] === opt) o.selected = true;
          return o;
        }));
      selects[key] = sel;
      return el("div", { className: "form-row" },
        el("label", null, label, el("div", { className: "small muted mono" }, key)),
        sel);
    });

    const saveBtn = el("button", { className: "btn-primary", type: "submit" }, "Save model selection");
    const form = el("form", {
      className: "card",
      onsubmit: async (ev) => {
        ev.preventDefault();
        saveBtn.disabled = true;
        const payload = {};
        for (const [k, sel] of Object.entries(selects)) payload[k] = sel.value;
        try {
          await api("/api/admin/models", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(payload),
          });
          showToast("Model selection saved to .env and applied");
          loadModels();
        } catch (e) {
          showToast("Save failed: " + e.message);
        } finally {
          saveBtn.disabled = false;
        }
      },
    },
    el("div", { className: "card-title" },
      "Model selection per job",
      el("span", { className: "pill pill-info mono" }, "Per-clause gate: gemini-decision-1 (pinned)")),
    ...rows,
    el("div", { style: "display:flex;justify-content:flex-end;" }, saveBtn));

    // Recent calls showing which model actually ran
    const recentTable = recent.length
      ? el("div", { className: "table-wrap" },
          el("table", null,
            el("thead", null, el("tr", null,
              el("th", null, "Time"),
              el("th", null, "Job"),
              el("th", null, "Model actually ran"),
              el("th", { className: "num" }, "Latency"),
              el("th", { className: "num" }, "Tokens (in / out)"),
              el("th", null, "Result"))),
            el("tbody", null, ...recent.map((c) => el("tr", null,
              el("td", { className: "num small muted" }, fmtTime(c.ts)),
              el("td", { className: "mono" }, c.job || "—"),
              el("td", { className: "mono", style: "font-weight:700;" }, c.model || "—"),
              el("td", { className: "num" }, fmtMs(c.latency_ms)),
              el("td", { className: "num small" },
                (c.input_tokens !== null && c.input_tokens !== undefined)
                  ? (fmtInt(c.input_tokens) + " / " + fmtInt(c.output_tokens || 0))
                  : (c.n_questions ? (c.n_questions + " q's") : "—")),
              el("td", null, c.error
                ? el("span", { className: "pill pill-bad" }, c.error)
                : el("span", { className: "pill pill-ok" }, "OK")))))))
      : el("div", { className: "small muted" }, "No model calls logged yet.");

    const recentCard = el("div", { className: "card" },
      el("div", { className: "card-title" }, "Recent calls — actual model used"),
      recentTable);

    contentEl.replaceChildren(form, recentCard);
  }

  // ---------------------------------------------------------------------------
  // TAB 5: API KEYS (never receives or displays secret values)
  // ---------------------------------------------------------------------------
  async function loadKeys() {
    try {
      const data = await api("/api/admin/keys");
      renderKeys(data.keys || {});
    } catch (e) {
      contentEl.replaceChildren(el("div", { className: "callout callout-bad" }, "Failed to load key status: " + e.message));
    }
  }

  function renderKeys(keys) {
    const meta = [
      ["GEMINI_API_KEY", "Required for Contract compiler, Shopping agent, Decisions API, and Red-team. Test runs 1 cheap Decisions call."],
      ["ANTHROPIC_API_KEY", "Optional (Claude Code adapter). Test verifies auth against /v1/models."],
      ["XAI_API_KEY", "Optional (Grok adapter). Test verifies auth against /v1/models."],
    ];

    const frag = document.createDocumentFragment();
    frag.appendChild(el("div", { className: "callout callout-info small", style: "margin-bottom:14px;" },
      "Keys are written server-side to .env (chmod 600) and never returned to the browser or logged."));

    for (const [name, desc] of meta) {
      const info = keys[name] || { is_set: false, updated_at: null };
      const input = el("input", {
        type: "password",
        autocomplete: "off",
        placeholder: info.is_set ? "Enter new key to replace…" : "Paste API key…",
      });
      const testOut = el("div", { className: "small muted" });

      const saveBtn = el("button", {
        className: "btn-primary btn-sm",
        type: "button",
        onclick: async () => {
          const val = input.value.trim();
          if (!val) { showToast("Enter a key value first"); return; }
          saveBtn.disabled = true;
          try {
            await api("/api/admin/keys", {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ name, value: val }),
            });
            input.value = "";
            showToast(name + " saved (.env chmod 600)");
            loadKeys();
          } catch (e) {
            showToast("Failed: " + e.message);
          } finally {
            saveBtn.disabled = false;
          }
        },
      }, info.is_set ? "Update" : "Save");

      const removeBtn = el("button", {
        className: "btn-bad btn-sm",
        type: "button",
        disabled: !info.is_set,
        onclick: async () => {
          removeBtn.disabled = true;
          try {
            await api("/api/admin/keys/" + encodeURIComponent(name), { method: "DELETE" });
            showToast(name + " removed");
            loadKeys();
          } catch (e) {
            showToast("Failed: " + e.message);
            removeBtn.disabled = false;
          }
        },
      }, "Remove");

      const testBtn = el("button", {
        className: "btn-sm",
        type: "button",
        disabled: !info.is_set,
        onclick: async () => {
          testBtn.disabled = true;
          testOut.textContent = "Testing…";
          try {
            const res = await api("/api/admin/keys/" + encodeURIComponent(name) + "/test", { method: "POST" });
            if (res.ok) {
              testOut.replaceChildren(
                el("span", { className: "pill pill-ok" }, "OK · " + fmtMs(res.latency_ms)),
                " ",
                res.detail || "");
            } else {
              testOut.replaceChildren(
                el("span", { className: "pill pill-bad" }, "FAIL · " + fmtMs(res.latency_ms)),
                " ",
                res.error || "Check key");
            }
          } catch (e) {
            testOut.replaceChildren(el("span", { className: "pill pill-bad" }, "ERROR"), " ", e.message);
          } finally {
            testBtn.disabled = false;
          }
        },
      }, "Test");

      frag.appendChild(el("div", { className: "card key-card" },
        el("div", { className: "card-title" },
          el("div", { style: "display:flex;align-items:center;gap:10px;" },
            el("span", { className: "mono" }, name),
            el("span", { className: "pill " + (info.is_set ? "pill-ok" : "pill-neutral") },
              info.is_set ? "Set" : "Not set")),
          el("span", { className: "small muted" },
            "Last updated: " + (info.updated_at ? fmtDateTime(info.updated_at) : "Never"))),
        el("div", { className: "small muted" }, desc),
        el("div", { className: "key-actions" }, input, saveBtn, testBtn, removeBtn),
        testOut));
    }

    contentEl.replaceChildren(frag);
  }

  // ---------------------------------------------------------------------------
  // TAB 6: RED-TEAM
  // ---------------------------------------------------------------------------
  async function loadRedteam() {
    try {
      const data = await api("/api/admin/redteam");
      renderRedteam(data);
    } catch (e) {
      contentEl.replaceChildren(el("div", { className: "callout callout-bad" }, "Failed to load red-team results: " + e.message));
    }
  }

  function renderRedteam(data) {
    const frag = document.createDocumentFragment();
    const runStatus = el("span", { className: "small muted" },
      data.runner_available ? "Runner ready" : "Runner not available yet");

    const runBtn = el("button", {
      className: "btn-primary btn-sm",
      type: "button",
      onclick: async () => {
        runBtn.disabled = true;
        try {
          const res = await api("/api/admin/redteam/run", { method: "POST" });
          runStatus.textContent = res.message || (res.available ? "Started" : "Not available yet");
          showToast(res.message || "Not available yet");
        } catch (e) {
          showToast("Error: " + e.message);
        } finally {
          runBtn.disabled = false;
        }
      },
    }, data.runner_available ? "Run red-team" : "Run (not available yet)");

    frag.appendChild(el("div", { className: "card" },
      el("div", { className: "card-title" },
        "5 Sigma Red-Team Evaluation",
        el("div", { style: "display:flex;align-items:center;gap:10px;" }, runStatus, runBtn)),
      el("div", { className: "small muted" },
        "Reads evals/out/results.json — compares No guard vs Rules-only baseline vs Tripwire, including false positives and misses.")));

    if (!data.available || !data.results) {
      frag.appendChild(el("div", { className: "empty-state", style: "margin-top:14px;" },
        data.error || "No results found yet at evals/out/results.json. Run Tanu's red-team harness to populate this tab."));
      contentEl.replaceChildren(frag);
      return;
    }

    const r = data.results;
    const arms = r.arms || {};
    const armDefs = [
      ["no_guard", "No guard (baseline)", "bar-bad"],
      ["rules_only", "Rules-only baseline", "bar-warn"],
      ["tripwire", "Tripwire (Decisions + Neon)", "bar-ok"],
    ];

    // 3-arm comparison cards
    const armCards = armDefs.map(([key, title, barCls]) => {
      const a = arms[key];
      if (!a) {
        return el("div", { className: "kpi-card" },
          el("div", { className: "kpi-label" }, title),
          el("div", { className: "kpi-value muted" }, "—"),
          el("div", { className: "kpi-sub" }, "No data in results.json"));
      }
      const pct = Math.round(Math.max(0, Math.min(1, a.attack_rate || 0)) * 100);
      return el("div", { className: "kpi-card" },
        el("div", { className: "kpi-label" }, title),
        el("div", { className: "kpi-value num" }, fmtPct(a.attack_rate)),
        el("div", { className: "kpi-sub num" },
          "Attack success: " + fmtInt(a.attack_success) + " / " + fmtInt(r.n_attacks) +
          (a.false_positives !== null && a.false_positives !== undefined
            ? (" · False positives: " + fmtInt(a.false_positives) + " (" + fmtPct(a.fp_rate) + ")")
            : "")),
        el("div", { className: "bar-track", style: "margin-top:8px;" },
          el("div", { className: "bar-fill " + barCls, style: "width:" + pct + "%;" })));
    });

    frag.appendChild(el("div", { style: "margin-top:14px;" },
      el("div", { className: "small muted", style: "margin-bottom:8px;" },
        "Run: " + (r.run_id || "latest") +
        " · Generator: " + (r.generator_model || "gemini-4-carbon") +
        " · Attacks: " + fmtInt(r.n_attacks) +
        " · Legit cases: " + fmtInt(r.n_legit) +
        (data.updated_at ? (" · Updated: " + fmtDateTime(data.updated_at)) : "")),
      el("div", { className: "grid-3" }, ...armCards)));

    // Failures section (5 Sigma: show where it still fails)
    const failures = r.failures || [];
    const failCard = el("div", { className: "card", style: "margin-top:14px;" },
      el("div", { className: "card-title" },
        "Where Tripwire still fails (" + failures.length + ")",
        el("span", { className: "pill " + (failures.length ? "pill-warn" : "pill-ok") },
          failures.length ? (failures.length + " misses") : "0 misses")));

    if (!failures.length) {
      failCard.appendChild(el("div", { className: "small muted" }, "No failures recorded in this run."));
    } else {
      failCard.appendChild(el("div", { className: "table-wrap" },
        el("table", null,
          el("thead", null, el("tr", null,
            el("th", null, "Case"),
            el("th", null, "Arm"),
            el("th", null, "What happened"),
            el("th", null, "Why"))),
          el("tbody", null, ...failures.map((f) => el("tr", null,
            el("td", { className: "mono" }, f.id || "—"),
            el("td", null, el("span", { className: "pill pill-bad" }, f.arm || "tripwire")),
            el("td", null, f.what_happened || "—"),
            el("td", { className: "small muted" }, f.why || "—")))))));
    }
    frag.appendChild(failCard);

    // All cases table
    const cases = r.cases || [];
    if (cases.length) {
      frag.appendChild(el("div", { className: "card" },
        el("div", { className: "card-title" }, "All eval cases (" + cases.length + ")"),
        el("div", { className: "table-wrap" },
          el("table", null,
            el("thead", null, el("tr", null,
              el("th", null, "ID"),
              el("th", null, "Kind"),
              el("th", null, "Attack type"),
              el("th", null, "Source"),
              el("th", null, "No guard"),
              el("th", null, "Rules-only"),
              el("th", null, "Tripwire"),
              el("th", { className: "num" }, "Latency"))),
            el("tbody", null, ...cases.map((c) => el("tr", null,
              el("td", { className: "mono" }, c.id || "—"),
              el("td", null, c.kind || "—"),
              el("td", null, c.attack_type || "—"),
              el("td", { className: "mono small" }, c.source || "—"),
              el("td", null, c.no_guard || c.off_result || "—"),
              el("td", null, c.rules_only || "—"),
              el("td", { style: "font-weight:700;" }, c.tripwire || c.on_result || "—"),
              el("td", { className: "num" }, fmtMs(c.latency_ms)))))))));
    }

    contentEl.replaceChildren(frag);
  }

  // Boot
  initTheme();
  setTab(activeTab);
})();
