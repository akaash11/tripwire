/* Tripwire Shopper Chat Page (/r/{token}) — strictly DOM-safe, mobile-first */
(function () {
  "use strict";

  const BASE = window.location.pathname.replace(/\/+$/, "");
  const timelineEl = document.getElementById("timeline");
  const statusPillEl = document.getElementById("status-pill");
  const liveIndicatorEl = document.getElementById("live-indicator");
  const themeToggleEl = document.getElementById("theme-toggle");

  // Theme toggle (respects system default, persists explicit choice)
  function initTheme() {
    const saved = localStorage.getItem("tripwire-theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
    updateThemeLabel();
    themeToggleEl.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme");
      const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
      const isDark = current ? current === "dark" : prefersDark;
      const next = isDark ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("tripwire-theme", next);
      updateThemeLabel();
    });
  }

  function updateThemeLabel() {
    const current = document.documentElement.getAttribute("data-theme");
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const isDark = current ? current === "dark" : prefersDark;
    themeToggleEl.textContent = isDark ? "Light" : "Dark";
  }

  // Safe DOM builder — NEVER uses innerHTML
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === "className") node.className = v;
        else if (k === "style") node.setAttribute("style", v);
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
    if (typeof cents !== "number") return "";
    const sym = !currency || currency === "USD" ? "$" : currency + " ";
    return sym + (cents / 100).toFixed(2);
  }

  function fmtTime(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function makeBubble(side, sender, ts, bodyNodes, extraClass) {
    const meta = el("div", { className: "bubble-meta" }, sender, ts ? " · " + fmtTime(ts) : "");
    const bubble = el("div", { className: "bubble" + (extraClass ? " " + extraClass : "") }, bodyNodes);
    return el("div", { className: "bubble-wrap " + side }, meta, bubble);
  }

  const STATUS_META = {
    received: ["Received", "pill-info"],
    compiling_contract: ["Understanding request…", "pill-info"],
    contract_ready: ["Contract signed", "pill-info"],
    shopping: ["Shopping Shopify…", "pill-info"],
    ask_human: ["Needs your approval", "pill-warn"],
    approve: ["Approved", "pill-ok"],
    block: ["Blocked", "pill-bad"],
    no_checkout: ["No item picked", "pill-neutral"],
    contract_failed: ["Contract failed", "pill-bad"],
    agent_failed: ["Agent failed", "pill-bad"],
  };

  function setStatusPill(status, checkoutStatus) {
    if (checkoutStatus === "completed") {
      statusPillEl.className = "pill pill-ok";
      statusPillEl.textContent = "Order completed";
      return;
    }
    const [label, cls] = STATUS_META[status] || [status || "Working", "pill-neutral"];
    statusPillEl.className = "pill " + cls;
    statusPillEl.textContent = label;
  }

  function render(data) {
    const req = data.request || {};
    const contract = data.contract;
    const events = data.events || [];
    const verdict = data.verdict;
    const checkout = data.checkout;
    const picked = data.picked;

    setStatusPill(req.status, checkout && checkout.status);

    const frag = document.createDocumentFragment();

    // 1. Shopper's original request bubble
    const userNodes = [];
    if (req.has_photo) {
      const photoHref = BASE + "/photo";
      const img = el("img", { className: "shopper-photo", src: photoHref, alt: "Uploaded request photo" });
      userNodes.push(el("a", { href: photoHref, target: "_blank", rel: "noopener" }, img));
    }
    const spoken = (contract && contract.user_request) || req.text;
    if (spoken) {
      userNodes.push(el("div", null, spoken));
    } else if (req.has_audio) {
      userNodes.push(el("div", { className: "muted" }, "Voice recording sent (transcribing…)"));
    } else if (req.has_photo) {
      userNodes.push(el("div", { className: "muted" }, "Photo sent"));
    }
    if (req.has_audio) {
      userNodes.push(el("div", { className: "small muted", style: "margin-top:4px;" }, "Voice note attached"));
    }
    frag.appendChild(makeBubble("user", "You", req.ts, userNodes));

    // 2. Contract / What Tripwire understood
    if (contract) {
      const cNodes = [
        el("div", { style: "font-weight:700;margin-bottom:6px;" }, contract.summary || "Intent Contract ready"),
      ];

      // Budget + returns + qty pills
      const metaPills = [];
      if (typeof contract.max_total_cents === "number") {
        metaPills.push(el("span", { className: "pill pill-neutral num" }, "Budget ≤ " + money(contract.max_total_cents, contract.currency)));
      }
      if (contract.returns) {
        metaPills.push(el("span", { className: "pill " + (contract.returns === "required" ? "pill-info" : "pill-neutral") }, "Returns: " + contract.returns));
      }
      if (contract.quantity && contract.quantity > 1) {
        metaPills.push(el("span", { className: "pill pill-neutral" }, "Qty " + contract.quantity));
      }
      if (contract.compiled_by) {
        metaPills.push(el("span", { className: "pill pill-neutral mono" }, "By " + contract.compiled_by));
      }
      if (metaPills.length) {
        cNodes.push(el("div", { style: "display:flex;flex-wrap:wrap;gap:6px;margin:6px 0;" }, metaPills));
      }

      // Photo observations
      if (contract.photo_observations && contract.photo_observations.length) {
        cNodes.push(el("div", { className: "small muted", style: "margin-top:8px;font-weight:650;" }, "From your photo:"));
        const obsList = el("ul", { className: "clause-list" });
        for (const obs of contract.photo_observations) {
          obsList.appendChild(el("li", { className: "clause-item small" }, "• ", obs));
        }
        cNodes.push(obsList);
      }

      // Must / Must not clauses
      const clauses = el("ul", { className: "clause-list" });
      for (const m of contract.must || []) {
        clauses.appendChild(el("li", { className: "clause-item" },
          el("span", { className: "clause-tag clause-must" }, "MUST"),
          el("span", null, m.clause || m)));
      }
      for (const n of contract.must_not || []) {
        clauses.appendChild(el("li", { className: "clause-item" },
          el("span", { className: "clause-tag clause-must-not" }, "NOT"),
          el("span", null, n.clause || n)));
      }
      if (clauses.childNodes.length) {
        cNodes.push(clauses);
      }

      const readyEv = events.find((e) => e.kind === "contract_ready");
      frag.appendChild(makeBubble("system", "Tripwire understood", readyEv ? readyEv.ts : null, cNodes));
    } else if (req.status === "compiling_contract" || req.status === "received") {
      frag.appendChild(makeBubble("system", "Tripwire", null, [
        el("div", { className: "muted" }, "Understanding your request and building a signed Intent Contract…"),
      ]));
    } else if (req.status === "contract_failed") {
      const failEv = events.find((e) => e.kind === "contract_failed");
      frag.appendChild(makeBubble("system", "Tripwire", failEv ? failEv.ts : null, [
        el("div", { style: "font-weight:700;" }, "Could not understand this request."),
        failEv && failEv.error ? el("div", { className: "small muted", style: "margin-top:4px;" }, failEv.error) : null,
      ], "verdict-block"));
    }

    // 3. Agent steps: searches and picked product(s)
    let showedPicked = false;
    for (const ev of events) {
      if (ev.kind === "search") {
        const sNodes = [
          el("div", null,
            el("span", { className: "muted" }, "Searched Shopify for "),
            el("strong", null, "“" + (ev.query || "") + "”"),
            el("span", { className: "muted num" }, " (" + (ev.results ?? 0) + " live results)")),
        ];
        if (ev.top_titles && ev.top_titles.length) {
          const list = el("ul", { className: "clause-list", style: "margin-top:6px;" });
          for (const t of ev.top_titles) {
            list.appendChild(el("li", { className: "small muted" }, "• ", t));
          }
          sNodes.push(list);
        }
        frag.appendChild(makeBubble("system", "Shopping agent", ev.ts, sNodes));
      } else if (ev.kind === "cart") {
        showedPicked = true;
        frag.appendChild(makeBubble("system", "Agent picked a product", ev.ts, [renderProductCard({
          title: ev.title || ev.summary,
          total_cents: ev.price_cents,
          currency: ev.currency,
          seller: ev.seller,
          quantity: ev.quantity,
          image_url: ev.image_url,
          product_url: ev.product_url,
          summary: ev.summary,
        })]));
      }
    }
    if (!showedPicked && picked) {
      frag.appendChild(makeBubble("system", "Agent picked a product", null, [renderProductCard(picked)]));
    }

    if (req.status === "shopping") {
      frag.appendChild(makeBubble("system", "Shopping agent", null, [
        el("div", { className: "muted" }, "Browsing live Shopify listings to match your contract…"),
      ]));
    } else if (req.status === "agent_failed") {
      const failEv = events.find((e) => e.kind === "agent_failed");
      frag.appendChild(makeBubble("system", "Shopping agent", failEv ? failEv.ts : null, [
        el("div", { style: "font-weight:700;" }, "The shopping agent ran into an error."),
        failEv && failEv.error ? el("div", { className: "small muted", style: "margin-top:4px;" }, failEv.error) : null,
      ], "verdict-block"));
    } else if (req.status === "no_checkout") {
      frag.appendChild(makeBubble("system", "Shopping agent", null, [
        el("div", null, "The agent finished without requesting a checkout."),
        data.agent_summary ? el("div", { className: "small muted", style: "margin-top:6px;" }, data.agent_summary) : null,
      ]));
    }

    // 4. Verdict bubble
    if (verdict) {
      const vEv = events.find((e) => e.kind === "verdict");
      const vMap = {
        approve: ["Approved by Tripwire", "pill-ok", "verdict-approve"],
        ask_human: ["Needs your approval", "pill-info", "verdict-ask_human"],
        block: ["Blocked by Tripwire", "pill-bad", "verdict-block"],
      };
      const [label, pCls, bCls] = vMap[verdict.verdict] || [verdict.verdict, "pill-neutral", ""];
      const vNodes = [
        el("div", { style: "display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:6px;" },
          el("span", { className: "pill " + pCls }, label),
          verdict.enforced === false ? el("span", { className: "pill pill-warn" }, "Guard OFF (demo mode)") : null),
      ];
      const reasons = (verdict.reasons && verdict.reasons.length)
        ? verdict.reasons
        : (verdict.reason ? [verdict.reason] : []);
      if (reasons.length) {
        for (const r of reasons) {
          vNodes.push(el("div", { style: "margin-top:4px;" }, r));
        }
      }
      if (data.pending_approval) {
        vNodes.push(el("div", { className: "callout callout-info" },
          "Waiting for your decision. Run your ",
          el("strong", null, "Tripwire Approve"),
          " shortcut on iPhone to approve or block this purchase."));
      }
      frag.appendChild(makeBubble("system", "Tripwire guard", vEv ? vEv.ts : null, vNodes, bCls));
    }

    // Human decision event
    const decEv = events.find((e) => e.kind === "human_decision");
    if (decEv) {
      const approved = decEv.decision === "approve";
      frag.appendChild(makeBubble("user", "Your decision", decEv.ts, [
        el("span", { className: "pill " + (approved ? "pill-ok" : "pill-bad") },
          approved ? "You approved this purchase" : "You blocked this purchase"),
      ]));
    }

    // 5. Checkout status bubble (NEVER shows continue_url or checkout id)
    if (checkout) {
      const cEv = events.find((e) => e.kind === "checkout_created");
      const isDone = checkout.status === "completed";
      const chNodes = [
        el("div", { style: "display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;" },
          el("strong", null, checkout.shop_name ? "Shop checkout · " + checkout.shop_name : "Shop checkout"),
          el("span", { className: "pill " + (isDone ? "pill-ok" : "pill-info") }, checkout.status || "created")),
      ];

      if (checkout.lines && checkout.lines.length) {
        const ul = el("ul", { className: "clause-list" });
        for (const line of checkout.lines) {
          ul.appendChild(el("li", { className: "small" },
            (line.quantity || 1) + "× " + (line.title || "Item")));
        }
        chNodes.push(ul);
      }

      if (checkout.totals && checkout.totals.length) {
        const totalsBox = el("div", { className: "totals-list" });
        for (const t of checkout.totals) {
          const isTotal = t.type === "total";
          totalsBox.appendChild(el("div", { className: "total-line num" + (isTotal ? " grand" : "") },
            el("span", null, t.label || t.type || "Amount"),
            el("span", null, money(t.amount, checkout.currency))));
        }
        chNodes.push(totalsBox);
      }

      if (checkout.messages && checkout.messages.length) {
        for (const m of checkout.messages) {
          const sev = (m.type === "error" || m.severity === "error") ? "callout-bad" : "callout-warn";
          chNodes.push(el("div", { className: "callout " + sev },
            el("strong", null, (m.type || "Notice").toUpperCase() + ": "),
            m.content || m.code || ""));
        }
      }

      if (checkout.links && checkout.links.length) {
        const lRow = el("div", { className: "link-row" });
        for (const link of checkout.links) {
          const href = safeUrl(link.url);
          if (!href) continue;
          const label = String(link.type || "link").replace(/_/g, " ");
          lRow.appendChild(el("a", { href, target: "_blank", rel: "noopener noreferrer" }, label));
        }
        if (lRow.childNodes.length) chNodes.push(lRow);
      }

      if (isDone && checkout.order) {
        const ordLabel = checkout.order.name || checkout.order.order_number || "Confirmed";
        chNodes.push(el("div", { className: "callout callout-ok" },
          el("strong", null, "Order placed: "),
          String(ordLabel)));
      } else if (req.pay_ready) {
        chNodes.push(el("div", { className: "callout callout-info small" },
          "Your private Shop Pay checkout link is delivered only through the Tripwire Approve shortcut on your phone."));
      }

      frag.appendChild(makeBubble("system", "Checkout", cEv ? cEv.ts : null, chNodes, isDone ? "verdict-approve" : ""));
    }

    // Agent summary at the end if finished
    if (data.agent_summary && req.status !== "shopping" && req.status !== "no_checkout") {
      const doneEv = events.find((e) => e.kind === "agent_done");
      frag.appendChild(makeBubble("system", "Agent note", doneEv ? doneEv.ts : null, [
        el("div", { className: "small muted" }, data.agent_summary),
      ]));
    }

    timelineEl.replaceChildren(frag);
  }

  function renderProductCard(p) {
    const imgUrl = safeUrl(p.image_url);
    const prodUrl = safeUrl(p.product_url);
    const titleNode = prodUrl
      ? el("a", { href: prodUrl, target: "_blank", rel: "noopener noreferrer", style: "font-weight:700;" }, p.title || p.summary || "Product")
      : el("div", { style: "font-weight:700;" }, p.title || p.summary || "Product");

    const details = [titleNode];
    const metaParts = [];
    if (typeof p.total_cents === "number") metaParts.push(money(p.total_cents, p.currency));
    if (p.seller) metaParts.push(p.seller);
    if (p.quantity && p.quantity > 1) metaParts.push("Qty " + p.quantity);
    if (metaParts.length) {
      details.push(el("div", { className: "small muted num", style: "margin-top:3px;" }, metaParts.join(" · ")));
    }
    return el("div", { className: "product-card" },
      imgUrl ? el("img", { className: "product-thumb", src: imgUrl, alt: p.title || "Product image" }) : null,
      el("div", { style: "min-width:0;" }, details));
  }

  let pollTimer = null;
  async function poll() {
    try {
      const resp = await fetch(BASE + "/events", { cache: "no-store" });
      if (resp.status === 404) {
        statusPillEl.className = "pill pill-bad";
        statusPillEl.textContent = "Not found";
        liveIndicatorEl.textContent = "Stopped";
        timelineEl.replaceChildren(el("div", { className: "empty-state" }, "This Tripwire request was not found."));
        return;
      }
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();
      render(data);
      const done = data.checkout && data.checkout.status === "completed";
      if (!done) {
        pollTimer = setTimeout(poll, 2000);
      } else {
        liveIndicatorEl.textContent = "Completed";
      }
    } catch (err) {
      liveIndicatorEl.textContent = "Reconnecting…";
      pollTimer = setTimeout(poll, 3000);
    }
  }

  initTheme();
  poll();
})();
