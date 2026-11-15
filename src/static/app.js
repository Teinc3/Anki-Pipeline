const CARD_COLUMNS = [
  "Target_Word", "Declined_Form", "Word_Meaning", "Word_Meaning_ZH",
  "Word_Formation", "CEFR_Level", "Grammar_Metadata", "Sentence_RU",
  "Sentence_EN", "Extra_Example_RU", "Extra_Example_EN"
];

const state = {
  imageFile: null,
  selectedWords: [],
  skippedWords: [],
  existingWords: [],
  cefrDist: {},
  cards: [],
  lastTsv: ""
};

const $ = (id) => document.getElementById(id);

async function readPayload(res) {
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text || `HTTP ${res.status}` };
  }
}

function showBanner(msg, kind = "ok") {
  const el = $("status-banner");
  el.textContent = msg;
  el.className = `banner ${kind}`;
  el.classList.remove("hidden");
}

function hideBanner() {
  $("status-banner").classList.add("hidden");
}

function setAuthView(authenticated) {
  $("login-view").classList.toggle("hidden", authenticated);
  $("app-view").classList.toggle("hidden", !authenticated);
}

async function checkAuth() {
  const res = await fetch("/api/auth-status");
  const data = await res.json();
  setAuthView(Boolean(data.authenticated));
}

function setImageFile(file) {
  state.imageFile = file;
  if (!file) {
    $("image-preview-wrap").classList.add("hidden");
    $("drop-label").classList.remove("hidden");
    $("image-file").value = "";
    return;
  }
  const url = URL.createObjectURL(file);
  $("image-preview").src = url;
  $("image-preview-wrap").classList.remove("hidden");
  $("drop-label").classList.add("hidden");
}

function renderStage1() {
  $("stage1-panel").classList.remove("hidden");
  $("stage1-panel").open = true;
  $("selected-count").textContent = state.selectedWords.length;
  $("skipped-count").textContent = state.skippedWords.length;
  $("existing-count").textContent = state.existingWords.length;

  const statsEl = $("cefr-stats");
  statsEl.innerHTML = "";
  const entries = Object.entries(state.cefrDist);
  if (entries.length === 0) {
    const empty = document.createElement("span");
    empty.className = "badge";
    empty.textContent = "60d CEFR: empty";
    statsEl.appendChild(empty);
  } else {
    entries.forEach(([lvl, count]) => {
      const b = document.createElement("span");
      b.className = "badge";
      b.textContent = `60d ${lvl}: ${count}`;
      statsEl.appendChild(b);
    });
  }

  renderWordColumn($("selected-list"), state.selectedWords, true);
  renderWordColumn($("skipped-list"), state.skippedWords, false);

  const existingEl = $("existing-list");
  existingEl.innerHTML = "";
  state.existingWords.forEach((word) => {
    const chip = document.createElement("span");
    chip.className = "badge";
    chip.textContent = word;
    existingEl.appendChild(chip);
  });
}

function renderWordColumn(container, items, isSelectedList) {
  container.innerHTML = "";
  items.forEach((item, idx) => {
    const pill = document.createElement("div");
    pill.className = "word-pill";
    pill.addEventListener("click", () => toggleWord(isSelectedList, idx));

    const head = document.createElement("div");
    head.className = "word-pill-head";

    const title = document.createElement("span");
    const formNote = item.declined_form && item.declined_form !== item.target_word
      ? ` (${item.declined_form})`
      : "";
    title.textContent = `${item.target_word}${formNote}`;
    head.appendChild(title);

    const badges = document.createElement("span");
    const cefr = document.createElement("span");
    cefr.className = "badge";
    cefr.textContent = item.cefr_level;
    badges.appendChild(cefr);

    if (item.user_bolded) {
      const boldBadge = document.createElement("span");
      boldBadge.className = "badge bold-flag";
      boldBadge.textContent = "bolded";
      badges.appendChild(boldBadge);
    }
    head.appendChild(badges);
    pill.appendChild(head);

    if (item.brief_meaning) {
      const meta = document.createElement("div");
      meta.className = "word-pill-meta";
      meta.textContent = item.brief_meaning;
      pill.appendChild(meta);
    }

    const sent = document.createElement("div");
    sent.className = "word-pill-sent";
    sent.textContent = item.sentence_ru;
    pill.appendChild(sent);

    container.appendChild(pill);
  });
}

function toggleWord(fromSelected, idx) {
  if (fromSelected) {
    const [moved] = state.selectedWords.splice(idx, 1);
    moved.selected = false;
    state.skippedWords.push(moved);
  } else {
    const [moved] = state.skippedWords.splice(idx, 1);
    moved.selected = true;
    state.selectedWords.push(moved);
  }
  renderStage1();
}

function renderStage2() {
  $("stage2-panel").classList.remove("hidden");
  $("stage2-panel").open = true;
  const tbody = $("cards-tbody");
  tbody.innerHTML = "";
  state.cards.forEach((cardState, rowIdx) => {
    const tr = document.createElement("tr");
    if (!cardState.approved) tr.classList.add("declined");

    const actionTd = document.createElement("td");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `btn small ${cardState.approved ? "approve" : "decline"}`;
    btn.textContent = cardState.approved ? "Approved" : "Declined";
    btn.addEventListener("click", () => {
      cardState.approved = !cardState.approved;
      renderStage2();
    });
    actionTd.appendChild(btn);
    tr.appendChild(actionTd);

    CARD_COLUMNS.forEach((col) => {
      const td = document.createElement("td");
      td.contentEditable = "true";
      td.textContent = cardState.data[col] || "";
      td.addEventListener("input", () => {
        state.cards[rowIdx].data[col] = td.textContent;
      });
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  updateCommitButtonLabel();
}

function updateCommitButtonLabel() {
  const count = state.cards.filter((c) => c.approved).length;
  $("commit-btn").textContent = `Commit ${count} Approved Cards to Anki`;
  $("commit-btn").disabled = count === 0;
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").classList.add("hidden");
  const password = $("login-password").value;
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password })
  });
  if (!res.ok) {
    $("login-error").textContent = "Incorrect password.";
    $("login-error").classList.remove("hidden");
    return;
  }
  $("login-password").value = "";
  setAuthView(true);
});

$("logout-btn").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  setAuthView(false);
});

$("drop-zone").addEventListener("click", (e) => {
  if (e.target.id !== "clear-image-btn") $("image-file").click();
});

$("image-file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (file) setImageFile(file);
});

$("clear-image-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  setImageFile(null);
});

$("drop-zone").addEventListener("dragover", (e) => {
  e.preventDefault();
  $("drop-zone").classList.add("dragover");
});

$("drop-zone").addEventListener("dragleave", () => {
  $("drop-zone").classList.remove("dragover");
});

$("drop-zone").addEventListener("drop", (e) => {
  e.preventDefault();
  $("drop-zone").classList.remove("dragover");
  const file = e.dataTransfer.files[0];
  if (file && file.type.startsWith("image/")) setImageFile(file);
});

document.addEventListener("paste", (e) => {
  const items = e.clipboardData ? Array.from(e.clipboardData.items) : [];
  const imgItem = items.find((it) => it.type.startsWith("image/"));
  if (imgItem) {
    const file = imgItem.getAsFile();
    if (file) setImageFile(file);
  }
});

$("extract-btn").addEventListener("click", async () => {
  hideBanner();
  $("stage1-panel").classList.add("hidden");
  $("stage2-panel").classList.add("hidden");
  state.selectedWords = [];
  state.skippedWords = [];
  state.existingWords = [];
  state.cefrDist = {};
  state.cards = [];
  const btn = $("extract-btn");
  btn.disabled = true;
  btn.textContent = "Analyzing...";
  try {
    const form = new FormData();
    form.append("text", $("input-text").value);
    if (state.imageFile) form.append("image", state.imageFile);

    const res = await fetch("/api/extract", { method: "POST", body: form });
    const data = await readPayload(res);
    if (res.status === 401) {
      setAuthView(false);
      return;
    }
    if (!res.ok) {
      showBanner(data.detail || "Extraction failed.", "err");
      return;
    }

    $("extracted-text").textContent = data.extracted_text;
    state.selectedWords = data.selected_words;
    state.skippedWords = data.skipped_words;
    state.existingWords = data.existing_words;
    state.cefrDist = data.cefr_distribution;
    $("stage0-panel").open = false;
    $("stage2-panel").classList.add("hidden");
    renderStage1();
  } catch (err) {
    showBanner(`Network error: ${err.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyze & Extract Words";
  }
});

$("generate-btn").addEventListener("click", async () => {
  hideBanner();
  if (state.selectedWords.length === 0) {
    showBanner("Select at least one word to generate cards.", "err");
    return;
  }
  const btn = $("generate-btn");
  btn.disabled = true;
  btn.textContent = "Generating cards...";
  try {
    const payload = {
      words: state.selectedWords.map((w) => ({
        target_word: w.target_word,
        declined_form: w.declined_form,
        sentence_ru: w.sentence_ru,
        cefr_level: w.cefr_level
      })),
      skipped_words: state.skippedWords.map((w) => w.target_word)
    };
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await readPayload(res);
    if (!res.ok) {
      showBanner(data.detail || "Generation failed.", "err");
      return;
    }
    state.cards = data.cards.map((card) => ({ approved: true, data: card }));
    $("stage0-panel").open = false;
    $("stage1-panel").open = false;
    $("tsv-output").classList.add("hidden");
    $("copy-tsv-btn").classList.add("hidden");
    $("download-tsv-btn").classList.add("hidden");
    renderStage2();
  } catch (err) {
    showBanner(`Network error: ${err.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate Flashcard Table";
  }
});

$("commit-btn").addEventListener("click", async () => {
  hideBanner();
  const approvedCards = state.cards.filter((c) => c.approved).map((c) => c.data);
  const declinedWords = state.cards
    .filter((c) => !c.approved)
    .map((c) => c.data.Target_Word);
  const btn = $("commit-btn");
  btn.disabled = true;
  btn.textContent = "Syncing to Anki...";
  try {
    const res = await fetch("/api/commit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        cards: approvedCards,
        declined_words: declinedWords
      })
    });
    const data = await readPayload(res);
    if (!res.ok) {
      showBanner(data.detail || "Commit failed.", "err");
      return;
    }
    state.lastTsv = data.tsv;
    $("tsv-output").textContent = data.tsv;
    $("tsv-output").classList.remove("hidden");
    $("copy-tsv-btn").classList.remove("hidden");
    $("download-tsv-btn").classList.remove("hidden");
    showBanner(`Added ${data.added_count} cards and synced to Anki.`, "ok");
  } catch (err) {
    showBanner(`Network error: ${err.message}`, "err");
  } finally {
    updateCommitButtonLabel();
  }
});

$("copy-tsv-btn").addEventListener("click", async () => {
  if (!state.lastTsv) return;
  await navigator.clipboard.writeText(state.lastTsv);
  showBanner("TSV copied to clipboard.", "ok");
});

$("download-tsv-btn").addEventListener("click", () => {
  if (!state.lastTsv) return;
  const blob = new Blob([state.lastTsv], { type: "text/tab-separated-values" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "rulang_cards.tsv";
  a.click();
});

checkAuth();
