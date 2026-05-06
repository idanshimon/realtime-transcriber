// content.js — Teams RTT Transcript Capture
// Monitors Teams web client caption DOM and extracts speaker + text in real time

(function () {
  "use strict";

  // ── State ────────────────────────────────────────────────────────────────
  let isCapturing = false;
  let observer = null;
  let lines = []; // { ts, speaker, text }
  // FIX: full-session dedup Set instead of lastEntry (last-1 only).
  // This catches scroll-reinjected old DOM nodes (virtual list) and prevents
  // re-emitting captions that were already captured.
  let seenLines = new Set(); // "speaker||text"
  // FIX: WeakSet to guard against double-fire (parent + child both mutating).
  let processedNodes = new WeakSet();
  let sessionStart = null;
  let meetingTitle = "";

  // ── Teams Caption DOM selectors (tested Apr 2026) ─────────────────────
  // Teams web continuously updates a live-region / caption container.
  // Multiple Teams web versions use different class names — we try each.
  const CAPTION_CONTAINER_SELECTORS = [
    // New Teams (post-2024)
    '[data-tid="closed-captions-renderer"]',
    '.ts-caption-container',
    '[class*="captionContainer"]',
    '[class*="caption-container"]',
    // Fallback: ARIA live region
    '[aria-live="polite"][role="log"]',
    '[aria-live="assertive"]',
  ];

  const CAPTION_ITEM_SELECTORS = [
    '[data-tid="caption-item"]',
    '[class*="captionItem"]',
    '[class*="caption-item"]',
    '[class*="captionLine"]',
  ];

  const SPEAKER_SELECTORS = [
    '[data-tid="caption-speaker-name"]',
    '[class*="speakerName"]',
    '[class*="speaker-name"]',
    '[class*="captionAuthor"]',
    '.ts-caption-speaker',
  ];

  const TEXT_SELECTORS = [
    '[data-tid="caption-text"]',
    '[class*="captionText"]',
    '[class*="caption-text"]',
    '.ts-caption-text',
    'span:not([class*="speaker"])',
  ];

  // ── Utilities ──────────────────────────────────────────────────────────
  function getTimestamp() {
    const now = new Date();
    return [
      String(now.getHours()).padStart(2, "0"),
      String(now.getMinutes()).padStart(2, "0"),
      String(now.getSeconds()).padStart(2, "0"),
    ].join(":");
  }

  function getSessionTimestamp() {
    const now = new Date();
    return (
      now.getFullYear().toString() +
      String(now.getMonth() + 1).padStart(2, "0") +
      String(now.getDate()).padStart(2, "0") +
      "-" +
      String(now.getHours()).padStart(2, "0") +
      String(now.getMinutes()).padStart(2, "0") +
      String(now.getSeconds()).padStart(2, "0")
    );
  }

  function detectMeetingTitle() {
    const selectors = [
      '[data-tid="meeting-title"]',
      '.meeting-title',
      '[class*="meetingTitle"]',
      'title',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && el.textContent.trim()) return el.textContent.trim();
    }
    return document.title || "teams-meeting";
  }

  function findCaptionContainer() {
    for (const sel of CAPTION_CONTAINER_SELECTORS) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  // ── Caption Parser ─────────────────────────────────────────────────────
  // Tries to extract (speaker, text) from a caption DOM node.
  // Falls back to heuristic text parsing if selectors don't match.
  function parseCaptionNode(node) {
    let speaker = "";
    let text = "";

    // Try structured selectors first
    for (const sel of SPEAKER_SELECTORS) {
      const el = node.querySelector(sel);
      if (el) { speaker = el.textContent.trim(); break; }
    }
    for (const sel of TEXT_SELECTORS) {
      const el = node.querySelector(sel);
      if (el) { text = el.textContent.trim(); break; }
    }

    // Fallback: parse raw text content "Speaker Name: caption text"
    if (!text) {
      const raw = node.textContent.trim();
      const colonIdx = raw.indexOf(":");
      if (colonIdx > 0 && colonIdx < 60) {
        speaker = raw.slice(0, colonIdx).trim();
        text = raw.slice(colonIdx + 1).trim();
      } else {
        text = raw;
      }
    }

    return { speaker: speaker || "Unknown", text };
  }

  // ── Dedup + Emit ──────────────────────────────────────────────────────
  function emitLine(speaker, text) {
    if (!text || text.length < 2) return;

    const key = `${speaker}||${text}`;

    // FIX: Full-session dedup. Catches:
    //   1. Same speaker+text within session (exact repeat)
    //   2. Scroll-reinjected old nodes (virtual list re-adds them as new DOM nodes)
    //   3. Double-fire from parent+child mutation (both call emitLine with same content)
    if (seenLines.has(key)) return;

    // Check if last line from same speaker can be updated in-place
    // (Teams appends words to the current caption line before finalizing it)
    if (lines.length > 0) {
      const last = lines[lines.length - 1];
      if (
        last.speaker === speaker &&
        text.startsWith(last.text.slice(0, Math.min(last.text.length, 15))) &&
        text.length > last.text.length
      ) {
        // Remove old partial key, update line in-place
        seenLines.delete(`${last.speaker}||${last.text}`);
        lines[lines.length - 1] = { ts: last.ts, speaker, text };
        seenLines.add(key);
        syncToBackground();
        return;
      }
    }

    const ts = getTimestamp();
    const entry = { ts, speaker, text };
    lines.push(entry);
    seenLines.add(key);
    syncToBackground();
    console.debug(`[RTT] ${ts} ${speaker}: ${text}`);
  }

  // ── Background Sync ──────────────────────────────────────────────────
  function syncToBackground() {
    chrome.runtime.sendMessage({
      type: "RTT_LINE",
      line: lines[lines.length - 1],
      sessionTs: sessionStart,
      meetingTitle,
    });
  }

  // ── DOM Observer ─────────────────────────────────────────────────────
  function startObserver(container) {
    if (observer) observer.disconnect();

    observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === "childList") {
          for (const node of mutation.addedNodes) {
            if (node.nodeType !== Node.ELEMENT_NODE) continue;
            processNode(node);
          }
        }
        // characterData handler removed — see observe() options above
      }
    });

    observer.observe(container, {
      childList: true,
      subtree: true,
      // FIX: Do NOT observe characterData.
      // characterData fires on every text node change (e.g. Teams re-rendering
      // during scroll). This was the primary cause of infinite spike on scroll-up.
      // Teams removes+re-adds nodes when a caption line finalizes — childList is enough.
      characterData: false,
    });

    console.log("[RTT] Observer attached to caption container:", container);
  }

  function processNode(node) {
    if (!node) return;

    // FIX: Guard against double-fire.
    // With subtree:true, MutationObserver fires for BOTH a parent node and each
    // of its children as separate mutations. Without this guard:
    //   1. Parent added → processNode(parent) → querySelectorAll finds 3 children → emit 3×
    //   2. Child1 added  → processNode(child1) → emit again (duplicate)
    //   3. Child2 added  → processNode(child2) → emit again (duplicate)
    // WeakSet marks a node as processed so child mutations skip re-processing.
    if (processedNodes.has(node)) return;
    processedNodes.add(node);

    // Check if the node itself is a caption item
    for (const sel of CAPTION_ITEM_SELECTORS) {
      if (node.matches && node.matches(sel)) {
        const { speaker, text } = parseCaptionNode(node);
        emitLine(speaker, text);
        return;
      }
    }
    // Otherwise scan children (parent container was added, find caption items inside)
    for (const sel of CAPTION_ITEM_SELECTORS) {
      const items = node.querySelectorAll(sel);
      if (items.length > 0) {
        items.forEach((item) => {
          processedNodes.add(item); // mark children so their individual mutations skip
          const { speaker, text } = parseCaptionNode(item);
          emitLine(speaker, text);
        });
        return; // matched first working selector, stop
      }
    }
  }

  // ── Scan existing captions on start ──────────────────────────────────
  function scanExisting(container) {
    for (const sel of CAPTION_ITEM_SELECTORS) {
      const items = container.querySelectorAll(sel);
      if (items.length > 0) {
        items.forEach((item) => {
          const { speaker, text } = parseCaptionNode(item);
          emitLine(speaker, text);
        });
        return;
      }
    }
  }

  // ── Wait for caption container (Teams lazy-renders it) ───────────────
  function waitForCaptionsAndStart() {
    let attempts = 0;
    const MAX = 60; // wait up to 60 seconds

    const poll = setInterval(() => {
      attempts++;
      const container = findCaptionContainer();
      if (container) {
        clearInterval(poll);
        scanExisting(container);
        startObserver(container);
        chrome.runtime.sendMessage({ type: "RTT_CAPTIONS_FOUND" });
      } else if (attempts >= MAX) {
        clearInterval(poll);
        chrome.runtime.sendMessage({ type: "RTT_CAPTIONS_NOT_FOUND" });
        console.warn("[RTT] Caption container not found after 60s. Captions may not be enabled.");
      }
    }, 1000);
  }

  // ── Start / Stop ─────────────────────────────────────────────────────
  function startCapture() {
    if (isCapturing) return;
    isCapturing = true;
    lines = [];
    seenLines = new Set();
    processedNodes = new WeakSet();
    sessionStart = getSessionTimestamp();
    meetingTitle = detectMeetingTitle();

    chrome.runtime.sendMessage({
      type: "RTT_STARTED",
      sessionTs: sessionStart,
      meetingTitle,
    });

    waitForCaptionsAndStart();
    console.log(`[RTT] Started — session ${sessionStart}, meeting: ${meetingTitle}`);
  }

  function stopCapture() {
    if (!isCapturing) return;
    isCapturing = false;
    if (observer) { observer.disconnect(); observer = null; }

    chrome.runtime.sendMessage({
      type: "RTT_STOPPED",
      lines,
      sessionTs: sessionStart,
      meetingTitle,
    });

    console.log(`[RTT] Stopped — ${lines.length} lines captured`);
  }

  // ── Message Handler (from popup/background) ──────────────────────────
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === "RTT_START") startCapture();
    if (msg.type === "RTT_STOP") stopCapture();
    if (msg.type === "RTT_STATUS") {
      chrome.runtime.sendMessage({
        type: "RTT_STATUS_REPLY",
        isCapturing,
        lineCount: lines.length,
        meetingTitle,
        sessionTs: sessionStart,
      });
    }
  });

  // ── Auto-start if capturing was active (e.g. page reload) ────────────
  chrome.storage.local.get("rttAutoStart", (data) => {
    if (data.rttAutoStart) startCapture();
  });

})();
