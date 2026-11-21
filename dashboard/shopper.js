/* Tripwire Shopper Page (/r/{token}) — live timeline, Approve/Block, Raise budget, strictly DOM-safe */
(function () {
  "use strict";

  const pathMatch = window.location.pathname.match(/^(.*)\/r\/([^/]+)\/?$/);
  const ROOT_PREFIX = pathMatch ? pathMatch[1] : "";
  const TOKEN = pathMatch ? pathMatch[2] : "";
  const REQ_BASE = ROOT_PREFIX + "/r/" + TOKEN;
  const WEB_BASE = ROOT_PREFIX + "/w/" + TOKEN;

  const shellEl = document.getElementById("shopper-shell");
  const timelineEl = document.getElementById("timeline");
  const liveIndicatorEl = document.getElementById("live-indicator");
  const themeToggleEl = document.getElementById("theme-toggle");
  const statusBannerEl = document.getElementById("status-banner");
  const statusHeadlineEl = document.getElementById("status-headline");
  const statusElapsedEl = document.getElementById("status-elapsed");
  const statusSubEl = document.getElementById("status-sub");
  const progressFillEl = document.getElementById("progress-fill");
  const stepperEl = document.getElementById("stepper");
  const footerMetaEl = document.getElementById("footer-meta");
  const newReqLinkEl = document.getElementById("new-request-link");
  const stickyBarEl = document.getElementById("sticky-bar");

  if (newReqLinkEl) {
    newReqLinkEl.setAttribute("href", ROOT_PREFIX + "/buy");
  }

  const STEPS = ["Understand", "Shopping", "Delivery", "Your OK", "Shop"];
  const ACTIVE_STATUSES = new Set(["received", "compiling_contract", "contract_ready", "shopping"]);
  const TERMINAL_STOP_STATUSES = new Set(["block", "no_checkout", "agent_failed", "contract_failed", "expired"]);

  let pollTimer = null;
  let elapsedTimer = null;
  let startedClientMs = null;
  let reqStartTs = null;
  let frozenElapsedSec = null;
  let isCurrentlyWorking = true;

  let actionBusy = null; // "approving" | "blocking" | "raising" | null
  let openingShop = false;
  let decideError = null;
  let raiseError = null;
  let dismissedOfferCartId = null;
  let lastStickySignature = null;
  let latestData = null;

  // ---------------------------------------------------------------------------
  // Theme toggle (light + dark, respects prefers-color-scheme)
  // ---------------------------------------------------------------------------
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

  // ---------------------------------------------------------------------------
  // Strictly DOM-safe helpers — NEVER uses innerHTML
  // ---------------------------------------------------------------------------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === "className") node.className = v;
        else if (k === "onclick") node.addEventListener("click", v);
        else if (k === "disabled") node.disabled = Boolean(v);
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
    return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function makeBubble(side, sender, ts, bodyNodes, extraClass) {
    const meta = el("div", { className: "bubble-meta" }, sender, ts ? " · " + fmtTime(ts) : "");
    const bubble = el("div", { className: "bubble" + (extraClass ? " " + extraClass : "") }, bodyNodes);
    return el("div", { className: "bubble-wrap " + side }, meta, bubble);
  }

  function isBuyerInputMessage(m) {
    const code = String((m && m.code) || "");
    return code.endsWith("_required") || (m && m.severity === "requires_buyer_input");
  }

  function classifyDeliveryCheck(ev) {
    if (ev.ok) {
      return {
        cls: "check-ok",
        icon: "✓",
        text: ev.summary ? ("Delivers within budget · " + ev.summary) : "Delivers within your budget",
      };
    }
    const reason = String(ev.reason || "Store couldn't fulfill delivery");
    const low = reason.toLowerCase();
    const isOverBudget = low.includes("over the") || low.includes("budget") || low.includes("tried too many");
    return {
      cls: isOverBudget ? "check-warn" : "check-bad",
      icon: isOverBudget ? "!" : "✕",
      text: reason,
    };
  }

  // ---------------------------------------------------------------------------
  // Elapsed timer
  // ---------------------------------------------------------------------------
  function computeElapsedSeconds(events) {
    if (reqStartTs) {
      const nowSec = Date.now() / 1000;
      if (nowSec >= reqStartTs) {
        return Math.max(0, Math.round(nowSec - reqStartTs));
      }
    }
    if (startedClientMs !== null) {
      return Math.max(0, Math.round((Date.now() - startedClientMs) / 1000));
    }
    if (events && events.length >= 2) {
      const first = events[0].ts;
      const last = events[events.length - 1].ts;
      if (first && last && last >= first) return Math.round(last - first);
    }
    return 0;
  }

  function updateElapsedDisplay() {
    if (!statusElapsedEl) return;
    if (isCurrentlyWorking) {
      const sec = computeElapsedSeconds(latestData && latestData.ev ? latestData.ev.events : null);
      statusElapsedEl.textContent = sec + "s elapsed";
    } else if (frozenElapsedSec !== null) {
      statusElapsedEl.textContent = frozenElapsedSec + "s";
    }
  }

  function ensureElapsedTicker() {
    if (isCurrentlyWorking && !elapsedTimer) {
      elapsedTimer = setInterval(updateElapsedDisplay, 1000);
    } else if (!isCurrentlyWorking && elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  }

  // ---------------------------------------------------------------------------
  // Status Header + 5-step stepper
  // ---------------------------------------------------------------------------
  function computeStage(w, ev) {
    const req = (ev && ev.request) || {};
    const status = w.status || req.status || "received";
    const events = (ev && ev.events) || [];
    const checkout = w.checkout || (ev && ev.checkout) || null;
    const pending = w.pending || null;

    if (openingShop) {
      return {
        headline: "Opening Shop…",
        sub: "Redirecting you to the merchant's real Shop Pay checkout.",
        tone: "ok",
        stepIdx: 4,
        stepState: "active",
        working: true,
        progressPct: 95,
      };
    }

    if (checkout && checkout.status === "completed") {
      return {
        headline: "Order confirmed",
        sub: "Your purchase is complete on Shop.",
        tone: "ok",
        stepIdx: 5,
        stepState: "done",
        working: false,
        progressPct: 100,
      };
    }

    if (pending) {
      return {
        headline: "Needs your OK",
        sub: "Check the delivered total and tap Approve to pay on Shop.",
        tone: "accent",
        stepIdx: 3,
        stepState: "active",
        working: false,
        progressPct: 80,
      };
    }

    if (status === "approve" && w.pay_ready) {
      return {
        headline: "Approved — ready for Shop",
        sub: "Tripwire verified every clause. Tap below to open the merchant's Shop checkout.",
        tone: "ok",
        stepIdx: 4,
        stepState: "active",
        working: false,
        progressPct: 90,
      };
    }

    if (status === "approve") {
      return {
        headline: "Opening Shop",
        sub: "Approved. Complete payment on the merchant's Shop page.",
        tone: "ok",
        stepIdx: 4,
        stepState: "done",
        working: false,
        progressPct: 100,
      };
    }

    if (status === "over_budget") {
      return {
        headline: "Over your budget",
        sub: "Every store that delivers here is above your budget. Only you can raise it.",
        tone: "warn",
        stepIdx: 2,
        stepState: "warn",
        working: false,
        progressPct: 60,
      };
    }

    if (status === "block") {
      return {
        headline: "Blocked by Tripwire",
        sub: "Tripwire stopped this checkout to protect your Intent Contract.",
        tone: "bad",
        stepIdx: 3,
        stepState: "bad",
        working: false,
        progressPct: 100,
      };
    }

    if (status === "expired") {
      return {
        headline: "Session expired",
        sub: "The server restarted, so this session can no longer be approved.",
        tone: "info",
        stepIdx: -1,
        stepState: "done",
        working: false,
        progressPct: 100,
      };
    }

    if (status === "contract_failed") {
      return {
        headline: "Couldn't understand request",
        sub: "Tripwire couldn't build an Intent Contract from this request.",
        tone: "bad",
        stepIdx: 0,
        stepState: "bad",
        working: false,
        progressPct: 100,
      };
    }

    if (status === "agent_failed") {
      return {
        headline: "Shopping agent failed",
        sub: "The shopping agent ran into an error.",
        tone: "bad",
        stepIdx: 1,
        stepState: "bad",
        working: false,
        progressPct: 100,
      };
    }

    if (status === "no_checkout") {
      return {
        headline: "Couldn't buy it",
        sub: w.problem || "The agent finished without picking a deliverable item.",
        tone: "warn",
        stepIdx: 2,
        stepState: "warn",
        working: false,
        progressPct: 100,
      };
    }

    if (status === "shopping") {
      // Detect whether agent is actively checking delivery/quotes right now.
      const shopEvents = events.filter((e) =>
        e.kind === "search" || e.kind === "cart" || e.kind === "delivery_check"
      );
      const lastEv = shopEvents[shopEvents.length - 1];
      if (lastEv && (lastEv.kind === "cart" || lastEv.kind === "delivery_check")) {
        return {
          headline: "Checking delivery…",
          sub: "Quoting real shipping, tax, and deliverability with the store…",
          tone: "info",
          stepIdx: 2,
          stepState: "active",
          working: true,
          progressPct: null,
        };
      }
      return {
        headline: "Shopping…",
        sub: "Searching live Shopify stores to match your Intent Contract…",
        tone: "info",
        stepIdx: 1,
        stepState: "active",
        working: true,
        progressPct: null,
      };
    }

    if (status === "contract_ready") {
      return {
        headline: "Shopping…",
        sub: "Intent Contract signed. Launching the shopping agent…",
        tone: "info",
        stepIdx: 1,
        stepState: "active",
        working: true,
        progressPct: null,
      };
    }

    return {
      headline: "Understanding…",
      sub: "Turning your request into a signed Intent Contract with Gemini 4 Neon…",
      tone: "info",
      stepIdx: 0,
      stepState: "active",
      working: true,
      progressPct: null,
    };
  }

  function renderStatusBanner(w, ev) {
    const req = (ev && ev.request) || {};
    const events = (ev && ev.events) || [];
    if (req.ts && reqStartTs === null) {
      reqStartTs = req.ts;
    }
    if (startedClientMs === null) {
      startedClientMs = Date.now();
    }

    const stage = computeStage(w, ev);
    isCurrentlyWorking = stage.working;

    if (!isCurrentlyWorking && frozenElapsedSec === null) {
      const doneEv = events.find((e) => e.kind === "agent_done");
      const lastTs = events.length ? events[events.length - 1].ts : null;
      if (reqStartTs && lastTs && lastTs >= reqStartTs) {
        frozenElapsedSec = Math.max(1, Math.round(lastTs - reqStartTs));
      } else if (doneEv && typeof doneEv.seconds === "number") {
        frozenElapsedSec = Math.round(doneEv.seconds);
      } else {
        frozenElapsedSec = computeElapsedSeconds(events);
      }
    } else if (isCurrentlyWorking) {
      frozenElapsedSec = null;
    }

    statusBannerEl.className = "status-banner tone-" + stage.tone;
    statusHeadlineEl.textContent = stage.headline;
    statusSubEl.textContent = stage.sub;

    if (stage.working) {
      progressFillEl.className = "progress-fill indeterminate tone-" + stage.tone;
      progressFillEl.style.width = "";
    } else {
      progressFillEl.className = "progress-fill tone-" + stage.tone;
      progressFillEl.style.width = (stage.progressPct ?? 100) + "%";
    }

    const stepNodes = STEPS.map((label, idx) => {
      let cls = "step-item";
      if (stage.stepIdx >= 0) {
        if (idx < stage.stepIdx) cls += " step-done";
        else if (idx === stage.stepIdx) {
          cls += stage.stepState === "warn" ? " step-warn"
            : stage.stepState === "bad" ? " step-bad"
            : stage.stepState === "done" ? " step-done"
            : " step-active";
        }
      }
      return el("div", { className: cls },
        el("div", { className: "step-bar" }),
        el("span", null, label));
    });
    stepperEl.replaceChildren(...stepNodes);

    // Topbar live indicator
    liveIndicatorEl.replaceChildren();
    if (stage.working) {
      liveIndicatorEl.append(el("span", { className: "pulse-dot" }), " Live");
    } else if (w.pending) {
      liveIndicatorEl.append("Waiting for OK");
    } else if (w.pay_ready) {
      liveIndicatorEl.append("Ready to pay");
    } else if (w.offer && w.status === "over_budget") {
      liveIndicatorEl.append("Action needed");
    } else if (w.status === "expired") {
      liveIndicatorEl.append("Expired");
    } else if (w.status === "block") {
      liveIndicatorEl.append("Blocked");
    } else {
      liveIndicatorEl.append("Done");
    }

    updateElapsedDisplay();
    ensureElapsedTicker();
  }

  // ---------------------------------------------------------------------------
  // Product Card (original Shop image_url, exact product_url)
  // ---------------------------------------------------------------------------
  function renderProductCard(p) {
    const imgUrl = safeUrl(p.image_url);
    const prodUrl = safeUrl(p.product_url);

    let imgNode = null;
    if (imgUrl) {
      imgNode = el("img", {
        className: "product-thumb",
        src: imgUrl,
        alt: p.title || "Product photo",
        loading: "lazy",
      });
      imgNode.addEventListener("error", () => {
        imgNode.hidden = true;
      });
    }

    const titleNode = prodUrl
      ? el("a", { href: prodUrl, target: "_blank", rel: "noopener noreferrer", style: "font-weight:750;" },
          p.title || p.summary || "Product")
      : el("div", { style: "font-weight:750;" }, p.title || p.summary || "Product");

    const details = [titleNode];
    const metaParts = [];
    if (typeof p.total_cents === "number") metaParts.push(money(p.total_cents, p.currency));
    if (p.seller) metaParts.push(p.seller);
    if (p.quantity && p.quantity > 1) metaParts.push("Qty " + p.quantity);
    if (metaParts.length) {
      details.push(el("div", { className: "small muted num", style: "margin-top:3px;" }, metaParts.join(" · ")));
    }
    if (prodUrl) {
      details.push(el("div", { style: "margin-top:4px;font-size:0.84rem;" },
        el("a", { href: prodUrl, target: "_blank", rel: "noopener noreferrer" }, "View product ↗")));
    }

    return el("div", { className: "product-card" },
      imgNode,
      el("div", { style: "min-width:0;flex:1;" }, details));
  }

  // ---------------------------------------------------------------------------
  // Timeline Rendering
  // ---------------------------------------------------------------------------
  function renderTimeline(w, ev) {
    const req = (ev && ev.request) || {};
    const status = w.status || req.status || "received";
    const events = (ev && ev.events) || [];
    const contract = (ev.contract || w.contract)
      ? Object.assign({}, ev.contract || {}, w.contract || {})
      : null;
    const verdict = (ev.verdict || w.verdict)
      ? Object.assign({}, ev.verdict || {}, w.verdict || {})
      : null;
    const checkout = (ev.checkout || w.checkout)
      ? Object.assign({}, ev.checkout || {}, w.checkout || {})
      : null;
    const picked = ev.picked || null;
    const currency = (checkout && checkout.currency) || (contract && contract.currency) || "USD";

    // Index cart events by cart_id so offer card & sticky bar can display image/seller/product link.
    const cartById = {};
    for (const e of events) {
      if (e.kind === "cart" && e.cart_id) {
        cartById[e.cart_id] = {
          cart_id: e.cart_id,
          title: e.title || e.summary,
          total_cents: e.price_cents,
          currency: e.currency || currency,
          seller: e.seller,
          quantity: e.quantity,
          image_url: e.image_url,
          product_url: e.product_url,
          summary: e.summary,
        };
      }
    }

    const frag = document.createDocumentFragment();

    // 1. Shopper's original request + photo
    const userNodes = [];
    if (req.has_photo) {
      const photoHref = REQ_BASE + "/photo";
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

    // 2. What Tripwire understood (Intent Contract)
    if (contract) {
      const cNodes = [
        el("div", { style: "font-weight:750;margin-bottom:6px;" }, contract.summary || "Intent Contract ready"),
      ];

      const metaPills = [];
      if (typeof contract.max_total_cents === "number") {
        const budText = "Budget ≤ " + money(contract.max_total_cents, contract.currency) + " delivered";
        metaPills.push(el("span", { className: "pill pill-neutral num" }, budText));
      }
      if (contract.budget_stated === false) {
        metaPills.push(el("span", { className: "pill pill-warn" }, "Tripwire's guess"));
      } else if (contract.amended_by === "shopper") {
        metaPills.push(el("span", { className: "pill pill-ok" }, "Raised by you"));
      }
      if (contract.returns) {
        metaPills.push(el("span", { className: "pill " + (contract.returns === "required" ? "pill-info" : "pill-neutral") },
          "Returns: " + contract.returns));
      }
      if (contract.quantity && contract.quantity > 1) {
        metaPills.push(el("span", { className: "pill pill-neutral" }, "Qty " + contract.quantity));
      }
      if (contract.compiled_by) {
        metaPills.push(el("span", { className: "pill pill-neutral mono" }, contract.compiled_by));
      }
      if (metaPills.length) {
        cNodes.push(el("div", { style: "display:flex;flex-wrap:wrap;gap:6px;margin:6px 0;" }, metaPills));
      }

      if (contract.photo_observations && contract.photo_observations.length) {
        cNodes.push(el("div", { className: "small muted", style: "margin-top:8px;font-weight:650;" }, "From your photo:"));
        const obsList = el("ul", { className: "clause-list" });
        for (const obs of contract.photo_observations) {
          obsList.appendChild(el("li", { className: "clause-item small" }, "• ", obs));
        }
        cNodes.push(obsList);
      }

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
    } else if (status === "compiling_contract" || status === "received") {
      frag.appendChild(makeBubble("system", "Tripwire", null, [
        el("div", { className: "muted" }, "Understanding your request and building a signed Intent Contract…"),
      ]));
    } else if (status === "contract_failed") {
      const failEv = events.find((e) => e.kind === "contract_failed");
      frag.appendChild(makeBubble("system", "Tripwire", failEv ? failEv.ts : null, [
        el("div", { style: "font-weight:750;" }, "Couldn't understand this request."),
        failEv && failEv.error ? el("div", { className: "small muted", style: "margin-top:4px;" }, failEv.error) : null,
      ], "verdict-block"));
    }

    // 3. Agent timeline: searches, tried products, delivery checks, budget raises
    let showedCart = false;
    const totalCartEvents = events.filter((e) => e.kind === "cart").length;
    let cartIdx = 0;

    for (let i = 0; i < events.length; i++) {
      const e = events[i];
      if (e.kind === "search") {
        const countText = " (" + (e.results ?? 0) + " live results" +
          (typeof e.delivers === "number" ? (" · " + e.delivers + " deliver") : "") + ")";
        const sNodes = [
          el("div", null,
            el("span", { className: "muted" }, "Searched Shopify for "),
            el("strong", null, "“" + (e.query || "") + "”"),
            el("span", { className: "muted num" }, countText)),
        ];
        if (e.top_titles && e.top_titles.length) {
          const list = el("ul", { className: "clause-list", style: "margin-top:6px;" });
          for (const t of e.top_titles) {
            list.appendChild(el("li", { className: "small muted" }, "• ", t));
          }
          sNodes.push(list);
        }
        frag.appendChild(makeBubble("system", "Shopping agent", e.ts, sNodes));
      } else if (e.kind === "cart") {
        showedCart = true;
        cartIdx += 1;
        const label = totalCartEvents > 1
          ? ("Agent tried option " + cartIdx)
          : "Agent picked a product";
        frag.appendChild(makeBubble("system", label, e.ts, [renderProductCard({
          title: e.title || e.summary,
          total_cents: e.price_cents,
          currency: e.currency || currency,
          seller: e.seller,
          quantity: e.quantity,
          image_url: e.image_url,
          product_url: e.product_url,
          summary: e.summary,
        })]));
      } else if (e.kind === "delivery_check") {
        const dc = classifyDeliveryCheck(e);
        frag.appendChild(el("div", { className: "check-row " + dc.cls },
          el("span", { className: "check-icon", "aria-hidden": "true" }, dc.icon),
          el("div", { className: "check-body" }, dc.text),
          e.ts ? el("span", { className: "check-ts num" }, fmtTime(e.ts)) : null));
      } else if (e.kind === "verdict" && e.verdict === "block") {
        // Show intermediate blocked attempt if there is another cart later.
        const hasLaterCart = events.slice(i + 1).some((later) => later.kind === "cart");
        if (hasLaterCart) {
          frag.appendChild(el("div", { className: "check-row check-bad" },
            el("span", { className: "check-icon", "aria-hidden": "true" }, "✕"),
            el("div", { className: "check-body" },
              "Tripwire blocked this option" + (e.reason ? ": " + e.reason : "")),
            e.ts ? el("span", { className: "check-ts num" }, fmtTime(e.ts)) : null));
        }
      } else if (e.kind === "budget_raised") {
        const oldStr = money(e.old_budget_cents, currency);
        const newStr = money(e.new_budget_cents, currency);
        frag.appendChild(el("div", { className: "check-row check-info" },
          el("span", { className: "check-icon", "aria-hidden": "true" }, "↑"),
          el("div", { className: "check-body" },
            "You raised your budget" + (oldStr && newStr ? (" from " + oldStr + " to " + newStr) : "") +
            ". Contract re-signed and re-checked by Tripwire."),
          e.ts ? el("span", { className: "check-ts num" }, fmtTime(e.ts)) : null));
      }
    }

    if (!showedCart && picked) {
      frag.appendChild(makeBubble("system", "Agent picked a product", null, [renderProductCard(picked)]));
    }

    if (status === "shopping") {
      frag.appendChild(makeBubble("system", "Shopping agent", null, [
        el("div", { className: "muted" }, "Checking live Shopify stores and real delivery quotes…"),
      ]));
    } else if (status === "agent_failed") {
      const failEv = events.find((e) => e.kind === "agent_failed");
      frag.appendChild(makeBubble("system", "Shopping agent", failEv ? failEv.ts : null, [
        el("div", { style: "font-weight:750;" }, "The shopping agent ran into an error."),
        failEv && failEv.error ? el("div", { className: "small muted", style: "margin-top:4px;" }, failEv.error) : null,
      ], "verdict-block"));
    }

    // 4. Over-budget offer card (when GET /w/{token} has offer)
    const offer = w.offer;
    if (offer && offer.cart_id && dismissedOfferCartId !== offer.cart_id) {
      const offerCard = renderOfferCard(offer, cartById[offer.cart_id], currency);
      frag.appendChild(offerCard);
    }

    // 5. Verdict card (with full X-ray when Tripwire blocks)
    if (verdict && verdict.verdict) {
      const vEv = [...events].reverse().find((e) => e.kind === "verdict");
      const vMap = {
        approve: ["Approved by Tripwire", "pill-ok", "verdict-approve"],
        ask_human: ["Needs your OK", "pill-info", "verdict-ask_human"],
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

      if (verdict.verdict === "block") {
        // Full 4-part verdict X-ray: asked for / found in listing / agent tried / reasons
        const hiddenText = String(verdict.hidden_in_listing || "").trim();
        const reasonsNode = reasons.length
          ? el("ul", { className: "clause-list", style: "margin:0;" },
              reasons.map((r) => el("li", { className: "small" }, "• ", r)))
          : el("div", { className: "small muted" }, "No reason recorded");

        vNodes.push(el("div", { className: "shopper-xray" },
          el("div", { className: "xray-box" },
            el("div", { className: "xray-label" }, "Asked for"),
            el("div", { className: "small" }, verdict.asked_for || (contract && contract.summary) || "—")),
          el("div", { className: "xray-box" + (hiddenText ? " alert" : "") },
            el("div", { className: "xray-label" }, "Found in listing"),
            el("div", { className: "small" + (hiddenText ? "" : " muted") },
              hiddenText || "No hidden instructions detected")),
          el("div", { className: "xray-box" },
            el("div", { className: "xray-label" }, "Agent tried"),
            el("div", { className: "small" }, verdict.agent_tried || (picked && picked.summary) || "—")),
          el("div", { className: "xray-box" },
            el("div", { className: "xray-label" }, "Reasons"),
            reasonsNode)));
      } else {
        for (const r of reasons) {
          vNodes.push(el("div", { className: "small", style: "margin-top:4px;" }, r));
        }
      }

      frag.appendChild(makeBubble("system", "Tripwire guard", vEv ? vEv.ts : null, vNodes, bCls));
    }

    // 6. Human decision bubble
    const decEv = [...events].reverse().find((e) => e.kind === "human_decision");
    if (decEv) {
      const approved = decEv.decision === "approve";
      frag.appendChild(makeBubble("user", "Your decision", decEv.ts, [
        el("span", { className: "pill " + (approved ? "pill-ok" : "pill-bad") },
          approved ? "You approved this purchase" : "You blocked this purchase"),
      ]));
    }

    // 7. Shop checkout details (totals, friendly to_fill_on_shop, warnings, links — NEVER continue_url or checkout id)
    if (checkout) {
      frag.appendChild(renderCheckoutBubble(checkout, events));
    }

    // 8. Problem / Expired / No checkout / Agent note
    if (status === "expired") {
      frag.appendChild(makeBubble("system", "Tripwire", null, [
        el("div", { style: "font-weight:750;" }, "This session expired after a server restart."),
        el("div", { className: "small muted", style: "margin-top:4px;" },
          "Live shopping sessions aren't kept across restarts. Start a fresh request to shop again."),
        el("div", { className: "action-row" },
          el("a", { className: "btn btn-primary btn-lg", href: ROOT_PREFIX + "/buy" }, "Start a new request")),
      ]));
    } else if (w.problem && !offer) {
      frag.appendChild(makeBubble("system", "Couldn't buy it", null, [
        el("div", null, w.problem),
      ], "verdict-warn"));
    } else if (status === "no_checkout" && !offer) {
      frag.appendChild(makeBubble("system", "Shopping agent", null, [
        el("div", null, "The agent finished without requesting a checkout."),
        (w.agent_summary || ev.agent_summary)
          ? el("div", { className: "small muted", style: "margin-top:6px;" }, w.agent_summary || ev.agent_summary)
          : null,
      ]));
    }

    const agentSummary = w.agent_summary || ev.agent_summary;
    if (!w.pending && agentSummary && status !== "shopping" && status !== "no_checkout") {
      const doneEv = events.find((e) => e.kind === "agent_done");
      frag.appendChild(makeBubble("system", "Agent note", doneEv ? doneEv.ts : null, [
        el("div", { className: "small muted" }, agentSummary),
      ]));
    }

    timelineEl.replaceChildren(frag);
  }

  function renderOfferCard(offer, cartInfo, currency) {
    const budgetStr = money(offer.budget_cents, currency);
    const totalStr = money(offer.total_cents, currency);
    const isRaising = actionBusy === "raising";

    const raiseBtn = el("button", {
      className: "btn-ok btn-lg action-primary",
      type: "button",
      disabled: isRaising,
      onclick: () => handleRaiseBudget(offer.cart_id),
    }, isRaising ? "Raising budget…" : ("Raise to " + totalStr));

    const declineBtn = el("button", {
      className: "btn-lg",
      type: "button",
      disabled: isRaising,
      onclick: () => {
        dismissedOfferCartId = offer.cart_id;
        if (latestData) renderAll(latestData.w, latestData.ev);
      },
    }, "No thanks");

    const budgetLine = "Your budget " + budgetStr +
      (offer.budget_stated === false ? " (a guess — you didn't state one)" : "") +
      ". Only you can raise it, the agent can't.";

    return el("div", { className: "offer-card" },
      el("span", { className: "pill pill-warn" }, "OVER YOUR BUDGET"),
      el("h2", { className: "offer-title" }, "Everything is over your budget"),
      cartInfo ? renderProductCard(cartInfo) : null,
      el("p", { style: "margin:10px 0 6px;" },
        el("strong", null, "Cheapest that delivers: "),
        offer.summary || ""),
      el("p", { className: "small muted", style: "margin:0;" }, budgetLine),
      raiseError ? el("div", { className: "callout callout-bad small" }, raiseError) : null,
      el("div", { className: "action-row" }, declineBtn, raiseBtn));
  }

  function renderCheckoutBubble(checkout, events) {
    const cEv = [...events].reverse().find((e) => e.kind === "checkout_created");
    const isDone = checkout.status === "completed";
    const statusLabel = isDone
      ? "Order confirmed"
      : (checkout.status === "incomplete" ? "Ready for Shop" : (checkout.status || "Ready"));

    const chNodes = [
      el("div", { style: "display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;" },
        el("strong", null, checkout.shop_name ? ("Shop checkout · " + checkout.shop_name) : "Shop checkout"),
        el("span", { className: "pill " + (isDone ? "pill-ok" : "pill-info") }, statusLabel)),
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

    // Friendly non-error line for fields entered on Shop (NEVER shown in red)
    const toFill = checkout.to_fill_on_shop || [];
    if (toFill.length && !isDone) {
      chNodes.push(el("div", { className: "shop-next-note" },
        el("strong", null, "You'll enter on Shop: "),
        toFill.join(", ") + "."));
    }

    // Warnings (strictly exclude buyer-input fields)
    const msgs = (checkout.messages || []).filter((m) => !isBuyerInputMessage(m));
    for (const m of msgs) {
      const isWarn = m.type === "warning" || m.severity !== "error";
      chNodes.push(el("div", { className: "callout " + (isWarn ? "callout-warn" : "callout-bad") },
        isWarn ? "⚠ " : "",
        m.content || m.code || ""));
    }

    // Exact merchant policy links (refund policy, shipping policy, terms, privacy)
    if (checkout.links && checkout.links.length) {
      const lRow = el("div", { className: "link-row" });
      for (const link of checkout.links) {
        const href = safeUrl(link.url);
        if (!href) continue;
        const label = String(link.type || "link").replace(/_/g, " ") + " ↗";
        lRow.appendChild(el("a", { href, target: "_blank", rel: "noopener noreferrer" }, label));
      }
      if (lRow.childNodes.length) chNodes.push(lRow);
    }

    if (isDone && checkout.order) {
      const ordLabel = checkout.order.name || checkout.order.order_number || "Confirmed";
      chNodes.push(el("div", { className: "callout callout-ok" },
        el("strong", null, "Order placed: "),
        String(ordLabel)));
    }

    return makeBubble("system", "What's next on Shop", cEv ? cEv.ts : null, chNodes, isDone ? "verdict-approve" : "");
  }

  // ---------------------------------------------------------------------------
  // Sticky Bottom Approve / Block / Pay Bar (independent of timeline redraws)
  // ---------------------------------------------------------------------------
  function renderStickyBar(w, ev) {
    const pending = w.pending;
    const payReady = !pending && Boolean(w.pay_ready);
    const checkoutDone = (w.checkout && w.checkout.status === "completed")
      || (ev && ev.checkout && ev.checkout.status === "completed");

    if ((!pending || !pending.cart_id) && (!payReady || checkoutDone)) {
      stickyBarEl.hidden = true;
      shellEl.classList.remove("has-sticky-bar");
      lastStickySignature = null;
      return;
    }

    const events = (ev && ev.events) || [];
    const checkout = (ev && ev.checkout) || w.checkout
      ? Object.assign({}, (ev && ev.checkout) || {}, w.checkout || {})
      : null;
    const picked = ev && ev.picked;
    const targetCartId = (pending && pending.cart_id) || (picked && picked.cart_id) || (checkout && checkout.cart_id) || "";
    const matchCartEv = [...events].reverse().find((e) => e.kind === "cart" && (!targetCartId || e.cart_id === targetCartId));

    const itemTitle = (picked && (!targetCartId || picked.cart_id === targetCartId) && picked.title)
      || (matchCartEv && matchCartEv.title)
      || (pending && pending.summary)
      || (checkout && checkout.lines && checkout.lines[0] && checkout.lines[0].title)
      || "Item ready for checkout";

    const seller = (picked && (!targetCartId || picked.cart_id === targetCartId) && picked.seller)
      || (matchCartEv && matchCartEv.seller)
      || (checkout && checkout.shop_name)
      || "";

    const currency = (checkout && checkout.currency)
      || (picked && picked.currency)
      || (matchCartEv && matchCartEv.currency)
      || "USD";

    let totalCents = null;
    if (checkout && checkout.totals) {
      const grand = checkout.totals.find((t) => t.type === "total");
      if (grand && typeof grand.amount === "number") totalCents = grand.amount;
    }
    if (totalCents === null && picked && typeof picked.total_cents === "number") {
      totalCents = picked.total_cents;
    } else if (totalCents === null && matchCartEv && typeof matchCartEv.price_cents === "number") {
      totalCents = matchCartEv.price_cents;
    }
    const totalStr = totalCents !== null ? money(totalCents, currency) : "";

    const reasons = (w.verdict && w.verdict.reasons && w.verdict.reasons.length)
      ? w.verdict.reasons
      : ((ev && ev.verdict && ev.verdict.reasons) || []);
    const reasonStr = reasons.join("; ");

    const mode = pending ? "pending" : "pay_ready";
    const sig = JSON.stringify([mode, targetCartId, itemTitle, seller, totalStr, reasonStr, actionBusy, decideError]);
    if (sig === lastStickySignature && !stickyBarEl.hidden) {
      return;
    }
    lastStickySignature = sig;

    const busy = actionBusy === "approving" || actionBusy === "blocking";
    let actionRow;
    if (pending) {
      const blockBtn = el("button", {
        className: "btn-bad btn-lg",
        type: "button",
        disabled: busy,
        onclick: () => handleDecide(pending.cart_id, "block"),
      }, actionBusy === "blocking" ? "Blocking…" : "Block");

      const approveBtn = el("button", {
        className: "btn-ok btn-lg action-primary",
        type: "button",
        disabled: busy,
        onclick: () => handleDecide(pending.cart_id, "approve"),
      }, actionBusy === "approving" ? "Opening Shop…" : "Approve & pay on Shop");

      actionRow = el("div", { className: "action-row" }, blockBtn, approveBtn);
    } else {
      const payBtn = el("button", {
        className: "btn-ok btn-lg btn-block",
        type: "button",
        disabled: busy,
        onclick: () => handlePayReady(),
      }, actionBusy === "approving" ? "Opening Shop…" : "Pay on Shop");

      actionRow = el("div", { className: "action-row" }, payBtn);
    }

    const inner = el("div", { className: "sticky-action-inner" },
      el("div", { className: "sticky-summary-row" },
        el("div", { style: "min-width:0;flex:1;" },
          el("div", { className: "sticky-item-title" }, itemTitle),
          seller ? el("div", { className: "sticky-seller" }, seller) : null),
        totalStr
          ? el("div", { className: "sticky-price-col" },
              el("div", { className: "sticky-price num" }, totalStr),
              el("div", { className: "sticky-price-sub" }, "delivered"))
          : null),
      reasonStr ? el("div", { className: "sticky-reason" }, "Tripwire: " + reasonStr) : null,
      decideError ? el("div", { className: "callout callout-bad small" }, decideError) : null,
      actionRow);

    stickyBarEl.replaceChildren(inner);
    stickyBarEl.hidden = false;
    shellEl.classList.add("has-sticky-bar");
  }

  // ---------------------------------------------------------------------------
  // Actions: Approve / Block, Pay (auto-approved), & Raise Budget
  // ---------------------------------------------------------------------------
  async function handlePayReady() {
    if (actionBusy) return;
    decideError = null;
    actionBusy = "approving";
    openingShop = true;
    clearTimeout(pollTimer);
    pollTimer = null;
    if (latestData) renderAll(latestData.w, latestData.ev);

    try {
      const resp = await fetch(WEB_BASE + "/pay", { method: "POST" });
      const res = await resp.json();
      if (res.url) {
        const target = safeUrl(res.url);
        if (target) {
          // Keep the merchant's real Shop checkout URL verbatim (all query params intact).
          window.location.href = target;
          return;
        }
      }
      actionBusy = null;
      openingShop = false;
      decideError = res.speak || "Could not open Shop checkout. Please try again.";
      await poll();
    } catch (_) {
      actionBusy = null;
      openingShop = false;
      decideError = "Network error while opening Shop checkout. Please try again.";
      if (latestData) renderAll(latestData.w, latestData.ev);
    }
  }
  async function handleDecide(cartId, decision) {
    if (actionBusy) return;
    decideError = null;
    actionBusy = decision === "approve" ? "approving" : "blocking";
    if (decision === "approve") {
      openingShop = true;
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    if (latestData) renderAll(latestData.w, latestData.ev);

    try {
      const resp = await fetch(WEB_BASE + "/decide", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ cart_id: cartId, decision }),
      });
      const res = await resp.json();
      if (decision === "approve" && res.url) {
        const target = safeUrl(res.url);
        if (target) {
          // Keep the merchant's real Shop checkout URL verbatim (all query params intact).
          window.location.href = target;
          return;
        }
      }
      actionBusy = null;
      openingShop = false;
      if (!res.ok) {
        decideError = res.speak || "Could not complete that action. Please try again.";
      }
      await poll();
    } catch (err) {
      actionBusy = null;
      openingShop = false;
      decideError = "Network error while sending your decision. Please try again.";
      if (latestData) renderAll(latestData.w, latestData.ev);
    }
  }

  async function handleRaiseBudget(cartId) {
    if (actionBusy) return;
    raiseError = null;
    actionBusy = "raising";
    clearTimeout(pollTimer);
    pollTimer = null;
    if (latestData) renderAll(latestData.w, latestData.ev);

    try {
      const resp = await fetch(WEB_BASE + "/raise_budget", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ cart_id: cartId }),
      });
      const res = await resp.json();
      actionBusy = null;
      if (!res.ok) {
        raiseError = res.speak || "Could not raise the budget.";
      }
      await poll();
    } catch (err) {
      actionBusy = null;
      raiseError = "Network error while raising budget. Please try again.";
      if (latestData) renderAll(latestData.w, latestData.ev);
    }
  }

  // ---------------------------------------------------------------------------
  // Render Coordinator & Polling
  // ---------------------------------------------------------------------------
  function renderFooter(w, ev) {
    const ver = (w && w.server_version) || "";
    const client = (w && w.client) || (ev && ev.request && ev.request.client) || "unknown";
    footerMetaEl.textContent = "Tripwire server " + ver + " · sent from " + client;
  }

  function renderAll(w, ev) {
    renderStatusBanner(w, ev);
    renderTimeline(w, ev);
    renderStickyBar(w, ev);
    renderFooter(w, ev);
  }

  async function poll() {
    clearTimeout(pollTimer);
    pollTimer = null;
    if (openingShop) return;

    try {
      const [wResp, evResp] = await Promise.all([
        fetch(WEB_BASE, { cache: "no-store" }),
        fetch(REQ_BASE + "/events", { cache: "no-store" }),
      ]);
      if (wResp.status === 404 || evResp.status === 404) {
        isCurrentlyWorking = false;
        ensureElapsedTicker();
        statusBannerEl.className = "status-banner tone-bad";
        statusHeadlineEl.textContent = "Request not found";
        statusSubEl.textContent = "This Tripwire link is invalid or no longer exists.";
        liveIndicatorEl.textContent = "Not found";
        stickyBarEl.hidden = true;
        timelineEl.replaceChildren(el("div", { className: "empty-state" }, "This Tripwire request was not found."));
        return;
      }
      if (!wResp.ok || !evResp.ok) {
        throw new Error("HTTP " + wResp.status + "/" + evResp.status);
      }
      const [w, ev] = await Promise.all([wResp.json(), evResp.json()]);
      latestData = { w, ev };
      renderAll(w, ev);

      const status = w.status || (ev.request && ev.request.status) || "";
      const checkoutDone = (w.checkout && w.checkout.status === "completed")
        || (ev.checkout && ev.checkout.status === "completed");

      if (openingShop || checkoutDone) {
        return;
      }
      if (ACTIVE_STATUSES.has(status)) {
        pollTimer = setTimeout(poll, 2000);
      } else if (w.pending) {
        pollTimer = setTimeout(poll, 3000);
      } else if (status === "approve") {
        pollTimer = setTimeout(poll, 4000);
      } else if (status === "over_budget" || TERMINAL_STOP_STATUSES.has(status)) {
        // Stable waiting / terminal state — stop polling until shopper acts.
        return;
      } else {
        pollTimer = setTimeout(poll, 3000);
      }
    } catch (err) {
      liveIndicatorEl.textContent = "Reconnecting…";
      pollTimer = setTimeout(poll, 3000);
    }
  }

  initTheme();
  poll();
})();
