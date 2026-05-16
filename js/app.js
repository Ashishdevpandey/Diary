// js/app.js  –  Ink & Impressions Diary App

/* ── Constants ── */
const MOODS = { 5: "😄", 4: "🙂", 3: "😐", 2: "😕", 1: "😞" };
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const DAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/* ── Encryption Utilities ── */
let masterKey = null;

async function deriveKey(password, salt) {
  const encoder = new TextEncoder();
  const passwordKey = await crypto.subtle.importKey(
    "raw",
    encoder.encode(password),
    "PBKDF2",
    false,
    ["deriveKey"]
  );
  return await crypto.subtle.deriveKey(
    {
      name: "PBKDF2",
      salt: encoder.encode(salt.toLowerCase()),
      iterations: 100000,
      hash: "SHA-256"
    },
    passwordKey,
    { name: "AES-GCM", length: 256 },
    true, // extractable so we can save to sessionStorage
    ["encrypt", "decrypt"]
  );
}

async function encrypt(text, key) {
  if (!text || !key) return text;
  const encoder = new TextEncoder();
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: iv },
    key,
    encoder.encode(text)
  );
  const combined = new Uint8Array(iv.length + encrypted.byteLength);
  combined.set(iv);
  combined.set(new Uint8Array(encrypted), iv.length);
  return "enc:" + btoa(String.fromCharCode.apply(null, combined));
}

async function decrypt(encryptedBase64, key) {
  if (!encryptedBase64 || !encryptedBase64.startsWith("enc:") || !key) return encryptedBase64;
  try {
    const combined = new Uint8Array(atob(encryptedBase64.slice(4)).split("").map(c => c.charCodeAt(0)));
    const iv = combined.slice(0, 12);
    const data = combined.slice(12);
    const decrypted = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: iv },
      key,
      data
    );
    return new TextDecoder().decode(decrypted);
  } catch (e) {
    console.error("Decryption failed:", e);
    return "[Decryption Failed - Check Password]";
  }
}

async function exportKey(key) {
  const exported = await crypto.subtle.exportKey("raw", key);
  return btoa(String.fromCharCode.apply(null, new Uint8Array(exported)));
}

async function importKey(base64) {
  const raw = new Uint8Array(atob(base64).split("").map(c => c.charCodeAt(0)));
  return await crypto.subtle.importKey(
    "raw",
    raw,
    "AES-GCM",
    true,
    ["encrypt", "decrypt"]
  );
}

/* ── State ── */
let entries = [];
let selectedId = null;
let editingId = null;
let pickedMood = 3;
let sortAsc = localStorage.getItem('ii_sort') === 'asc';
let calYear, calMonth;
let currentUser = null;

/* ── Boot ── */
document.addEventListener("DOMContentLoaded", async () => {
  await checkAuth();
});

async function checkAuth() {
  try {
    const res = await fetch('/api/user_info');
    const data = await res.json();
    if (data.authenticated) {
      currentUser = data.user;
      
      // Try to recover key from session storage
      const savedKey = sessionStorage.getItem('ii_master_key');
      if (savedKey) {
        masterKey = await importKey(savedKey);
        initApp();
      } else {
        // Logged in but no key? Force re-login to derive key
        // Or show an "Unlock" UI. For now, let's just show auth.
        showAuth();
      }
    } else {
      showAuth();
    }
  } catch (e) {
    console.error("Auth check failed:", e);
    showAuth();
  }
}

async function initApp(welcomedBack = false) {
  document.getElementById("authOverlay").classList.remove("open");
  document.getElementById("logoutBtn").style.display = "flex";
  
  const now = new Date();
  calYear = now.getFullYear();
  calMonth = now.getMonth();

  if (currentUser.data_wipe_scheduled) {
    document.getElementById("restoreDataBanner").style.display = "block";
    const wipeDate = new Date(currentUser.data_wipe_date);
    document.getElementById("wipeCountdown").innerText = `Scheduled for permanent deletion on: ${wipeDate.toLocaleDateString()} at ${wipeDate.toLocaleTimeString()}`;
    showEmpty();
  } else {
    document.getElementById("restoreDataBanner").style.display = "none";
    await loadEntries();
    
    renderCalendar();
    renderMoodChart();
    renderStreak();
    renderTagCloud();
    renderMemories();

    if (entries.length > 0 && window.innerWidth > 900) {
      selectEntry(entries[0].id);
    } else if (entries.length > 0) {
      showWelcome();
    } else {
      showEmpty();
    }
  }

  if (welcomedBack) {
    setTimeout(() => {
      document.getElementById("welcomeBackModal").classList.add("open");
    }, 600);
  }
}

function showAuth() {
  document.getElementById("authOverlay").classList.add("open");
  document.getElementById("logoutBtn").style.display = "none";
}

async function loadEntries() {
  if (currentUser.data_wipe_scheduled) {
    entries = [];
    renderList();
    return;
  }
  try {
    const res = await fetch('/api/entries');
    if (res.status === 401) return showAuth();
    const rawEntries = await res.json();
    
    // Decrypt all entries
    entries = await Promise.all(rawEntries.map(async e => ({
      ...e,
      title: await decrypt(e.title, masterKey),
      body: await decrypt(e.body, masterKey),
      notes: await Promise.all((e.notes || []).map(n => decrypt(n, masterKey))),
      tags: await Promise.all((e.tags || []).map(t => decrypt(t, masterKey)))
    })));
    
    renderList();
    renderCalendar();
    renderMoodChart();
    renderStreak();
    renderTagCloud();
    renderMemories();
  } catch (e) {
    console.error("Cloud load failed:", e);
    entries = [];
  }
}

async function saveEntry(data) {
  data.id = Date.now();
  const res = await fetch('/api/entries', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  const saved = await res.json();
  entries.unshift(saved);

  renderList();
  renderCalendar();
  renderMoodChart();
  renderStreak();
  renderTagCloud();
  renderMemories();
  selectEntry(saved.id);
  closeModal();
}

async function updateEntry(data) {
  // Encrypt before sending
  const payload = {
    ...data,
    title: await encrypt(data.title, masterKey),
    body: await encrypt(data.body, masterKey),
    notes: await Promise.all((data.notes || []).map(n => encrypt(n, masterKey))),
    tags: await Promise.all((data.tags || []).map(t => encrypt(t, masterKey)))
  };

  const res = await fetch(`/api/entries/${data.id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  
  await loadEntries();
  selectEntry(data.id);
  closeModal();
}

async function deleteEntry(id) {
  await fetch(`/api/entries/${id}`, { method: 'DELETE' });
  entries = entries.filter(x => x.id !== id);
  renderList();
  renderCalendar();
  renderMoodChart();
  renderStreak();
  renderTagCloud();
  renderMemories();
  if (selectedId === id) showEmpty();
}

async function toggleStar(id) {
  const e = entries.find(x => x.id === id);
  if (e) {
    e.starred = !e.starred;
    await updateEntry(e);
    if (selectedId === id) selectEntry(id);
  }
}

async function clearAllData() {
  if (confirm("⚠️ CAUTION: This will permanently delete ALL your cloud diary entries! Are you sure?")) {
    for (const e of entries) {
      await fetch(`/api/entries/${e.id}`, { method: 'DELETE' });
    }
    entries = [];
    renderList();
    renderCalendar();
    renderMoodChart();
    renderStreak();
    renderTagCloud();
    renderMemories();
    showEmpty();
    closeModal();
  }
}

/* ── Full re-render ── */
function renderAll() {
  renderList();
  renderCalendar();
  renderMoodChart();
  renderStreak();
  renderTagCloud();
  renderMemories();
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ENTRIES LIST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function renderList() {
  const el = document.getElementById("entriesList");
  const sorted = [...entries].sort((a, b) =>
    sortAsc ? a.date.localeCompare(b.date) : b.date.localeCompare(a.date)
  );

  // Group by month
  const groups = {};
  sorted.forEach(e => {
    const d = new Date(e.date + "T12:00:00");
    const key = MONTHS[d.getMonth()].toUpperCase() + " " + d.getFullYear();
    (groups[key] = groups[key] || []).push(e);
  });

  el.innerHTML = "";
  for (const [month, items] of Object.entries(groups)) {
    el.insertAdjacentHTML("beforeend", `<div class="month-label">${month}</div>`);
    items.forEach(e => el.appendChild(makeCard(e)));
  }
}

function makeCard(e) {
  const d = new Date(e.date + "T12:00:00");
  const num = String(d.getDate()).padStart(2, "0");
  const dow = d.toLocaleString("default", { weekday: "short" }).toUpperCase();
  const div = document.createElement("div");
  div.className = "ecard" + (e.id === selectedId ? " sel" : "");
  div.onclick = () => selectEntry(e.id);
  div.innerHTML = `
    ${e.starred ? '<span class="ecard-star">⭐</span>' : ''}
    <div class="ecard-row">
      <div class="ecard-date">
        <div class="ecard-day">${num}</div>
        <div class="ecard-dow">${dow}</div>
      </div>
      <div class="ecard-body">
        <div class="ecard-title">${esc(e.title)}</div>
        <div class="ecard-preview">${esc(e.body)}</div>
      </div>
    </div>`;
  return div;
}

function toggleSort() { sortAsc = !sortAsc; renderList(); }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ENTRY VIEW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function selectEntry(id) {
  selectedId = id;
  const e = entries.find(x => x.id === id);
  if (!e) return;
  renderList();

  const d = new Date(e.date + "T12:00:00");
  const num = String(d.getDate()).padStart(2, "0");
  const monthYear = MONTHS[d.getMonth()] + " " + d.getFullYear();
  const dow = d.toLocaleString("default", { weekday: "long" });
  const time = e.time || "—";

  // Toolbar + date header
  document.getElementById("viewTop").innerHTML = `
    <div class="view-toolbar">
      <button class="view-back-btn" onclick="closeEntryView()" title="Back">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
      </button>
      <div style="flex:1"></div>
      <button class="vtool-btn" title="Edit"   onclick="openEdit(${e.id})">✏️</button>
      <button class="vtool-btn" title="Delete" onclick="openConfirm(${e.id})">🗑️</button>
      <button class="vtool-btn" title="${e.starred ? 'Unstar' : 'Star'}" onclick="toggleStar(${e.id})">${e.starred ? '⭐' : '☆'}</button>
      <button class="vtool-btn" title="Copy Text" onclick="copyEntryText(${e.id})">📋</button>
    </div>
    <div class="view-date-line">
      <span class="view-day-big">${num}</span>
      <span class="view-month-year">${monthYear}</span>
    </div>
    <div class="view-dow-time">${dow}, ${time}</div>
    <div class="view-mood-row">
      <span style="font-size:12.5px;color:var(--ink2);margin-right:2px;">Mood today</span>
      ${[5, 4, 3, 2, 1].map(m => `<div class="vmood ${e.mood === m ? 'active' : ''}">${MOODS[m]}</div>`).join("")}
    </div>`;

  // Notes block
  const notesHtml = e.notes && e.notes.length
    ? `<div class="view-notes">
         <div class="view-notes-title">📝 Things to remember</div>
         <ul>${e.notes.map(n => `<li>${esc(n.replace(/<br\s*\/?>/gi, "\n"))}</li>`).join("")}</ul>
       </div>` : "";

  // Tags
  const tagsHtml = e.tags && e.tags.length
    ? `<div class="view-tags">${e.tags.map(t => `<span class="view-tag">${esc(t)}</span>`).join("")}</div>`
    : "";

  // Body — split by double newline into paragraphs
  const paras = e.body.replace(/<br\s*\/?>/gi, "\n").split(/\n\n+/).map(p => `<p>${esc(p).replace(/\n/g, "<br>")}</p>`).join("");

  document.getElementById("viewContent").innerHTML = `
    <div class="view-title">${esc(e.title)}</div>
    <div class="view-divider">~ ~ ~</div>
    <div class="view-text">${paras}</div>
    ${notesHtml}
    ${tagsHtml}`;

  document.getElementById("entryView").classList.add("open");
  document.getElementById("entryView").scrollTo(0, 0);
}

function showWelcome() {
  document.getElementById("viewTop").innerHTML = `
    <div class="view-toolbar">
      <div style="flex:1"></div>
    </div>`;
    
  document.getElementById("viewContent").innerHTML = `
    <div class="empty-state">
      <span class="empty-state-big">✍️</span>
      Welcome back to your diary.<br>Select an entry to read or <strong>＋ New Entry</strong> to write.
    </div>`;
  
  if (window.innerWidth <= 900) {
    closeEntryView();
  }
}

function showEmpty() {
  // On mobile, if we're showing empty, we should still allow going back or creating a new one
  document.getElementById("viewTop").innerHTML = `
    <div class="view-toolbar">
      <button class="view-back-btn" onclick="closeEntryView()" title="Back">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
      </button>
      <div style="flex:1"></div>
    </div>`;
    
  document.getElementById("viewContent").innerHTML = `
    <div class="empty-state">
      <span class="empty-state-big">🪶</span>
      No entries yet.<br>Press <strong>＋ New Entry</strong> to begin.
      <div style="margin-top:25px;">
        <button class="btn-new" onclick="openNewEntry()" style="margin:0; background:var(--acc); color:white; padding: 12px 24px;">＋ New Entry</button>
      </div>
    </div>`;
  
  // If we're on mobile and everything is empty, might as well close the view
  if (window.innerWidth <= 900) {
    closeEntryView();
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   NEW / EDIT MODAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function openNewEntry() {
  editingId = null;
  pickedMood = 3;
  document.getElementById("modalTitle").textContent = "New Entry";
  document.getElementById("saveBtn").textContent = "Save Entry";
  document.getElementById("fTitle").value = "";
  document.getElementById("fBody").value = "";
  document.getElementById("fNotes").value = "";
  document.getElementById("fTags").value = "";
  syncMoodUI();
  document.getElementById("entryModal").classList.add("open");
  setTimeout(() => document.getElementById("fTitle").focus(), 80);
}

function openEdit(id) {
  editingId = id;
  const e = entries.find(x => x.id === id);
  if (!e) return;
  pickedMood = e.mood;
  document.getElementById("modalTitle").textContent = "Edit Entry";
  document.getElementById("saveBtn").textContent = "Update Entry";
  document.getElementById("fTitle").value = e.title;
  document.getElementById("fBody").value = e.body;
  document.getElementById("fNotes").value = (e.notes || []).join(", ");
  document.getElementById("fTags").value = (e.tags || []).join(", ");
  syncMoodUI();
  document.getElementById("entryModal").classList.add("open");
  setTimeout(() => document.getElementById("fTitle").focus(), 80);
}

function closeModal() { document.getElementById("entryModal").classList.remove("open"); }

function pickMood(v) { pickedMood = v; syncMoodUI(); }
function syncMoodUI() {
  document.querySelectorAll(".mopt").forEach(el => {
    el.classList.toggle("sel", +el.dataset.v === pickedMood);
  });
}

async function persistEntry() {
  const title = document.getElementById("fTitle").value.trim();
  const body = document.getElementById("fBody").value.trim().replace(/<br\s*\/?>/gi, "\n");
  if (!title && !body) { alert("Write something first!"); return; }

  const notesRaw = document.getElementById("fNotes").value;
  const tagsRaw = document.getElementById("fTags").value;
  const notes = notesRaw.split(",").map(s => s.trim().replace(/<br\s*\/?>/gi, "\n")).filter(Boolean);
  const tags = tagsRaw.split(",").map(s => s.trim()).filter(Boolean);

  const payload = { 
    title: await encrypt(title || "Untitled", masterKey), 
    body: await encrypt(body, masterKey), 
    mood: pickedMood, 
    notes: await Promise.all(notes.map(n => encrypt(n, masterKey))), 
    tags: await Promise.all(tags.map(t => encrypt(t, masterKey))) 
  };

  if (editingId) {
    payload.id = editingId;
    payload.starred = entries.find(e => e.id === editingId)?.starred || false;
    payload.date = entries.find(e => e.id === editingId)?.date;
    payload.time = entries.find(e => e.id === editingId)?.time;
    await fetch(`/api/entries/${editingId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
  } else {
    payload.id = Date.now();
    const now = new Date();
    const pad = n => String(n).padStart(2, "0");
    const h = now.getHours();
    payload.date = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    payload.time = `${pad(h % 12 || 12)}:${pad(now.getMinutes())} ${h >= 12 ? "PM" : "AM"}`;
    payload.starred = false;
    await fetch('/api/entries', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
  }

  closeModal();
  await loadEntries();
  if (editingId) selectEntry(editingId);
  else if (entries.length) selectEntry(entries[0].id);
  else showEmpty();
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   DELETE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function openConfirm(id) {
  deletePendingId = id;
  document.getElementById("confirmModal").classList.add("open");
}
function closeConfirm() { document.getElementById("confirmModal").classList.remove("open"); }
async function confirmDelete() {
  await fetch(`/api/entries/${deletePendingId}`, { method: 'DELETE' });
  closeConfirm();
  await loadEntries();
  selectedId = entries.length ? entries[0].id : null;
  renderAll();
  
  if (selectedId) {
    selectEntry(selectedId);
  } else {
    showEmpty();
    if (window.innerWidth <= 900) closeEntryView();
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   STAR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   MINI CALENDAR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function renderCalendar() {
  const titleStr = MONTHS[calMonth] + " " + calYear;
  if (document.getElementById("calTitle")) document.getElementById("calTitle").textContent = titleStr;
  if (document.getElementById("calTitleSidebar")) document.getElementById("calTitleSidebar").textContent = titleStr;

  const pad = n => String(n).padStart(2, "0");
  const today = new Date();
  const entryDates = new Set(entries.map(e => e.date));
  const firstDow = new Date(calYear, calMonth, 1).getDay(); // 0=Sun
  const blanks = (firstDow === 0) ? 6 : firstDow - 1;    // Mon-based
  const daysInM = new Date(calYear, calMonth + 1, 0).getDate();

  const grids = [document.getElementById("calGrid"), document.getElementById("calGridSidebar")].filter(Boolean);

  grids.forEach(grid => {
    grid.innerHTML = "";

    // Blank cells
    for (let i = 0; i < blanks; i++) {
      const c = document.createElement("div");
      c.className = "cday empty";
      c.textContent = "";
      grid.appendChild(c);
    }

    for (let d = 1; d <= daysInM; d++) {
      const ds = `${calYear}-${pad(calMonth + 1)}-${pad(d)}`;
      const isT = today.getDate() === d && today.getMonth() === calMonth && today.getFullYear() === calYear;
      const hasE = entryDates.has(ds);
      const c = document.createElement("div");
      c.className = "cday" + (isT ? " today" : "") + (hasE ? " has-entry" : "");
      c.textContent = d;
      c.onclick = () => {
        const e = entries.find(x => x.date === ds);
        if (e) {
          selectEntry(e.id);
          if (grid.id === 'calGridSidebar') closeSidebar();
        }
      };
      grid.appendChild(c);
    }
  });
}

function calPrev() { calMonth--; if (calMonth < 0) { calMonth = 11; calYear--; } renderCalendar(); }
function calNext() { calMonth++; if (calMonth > 11) { calMonth = 0; calYear++; } renderCalendar(); }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   MOOD CHART
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function renderMoodChart() {
  const canvases = [document.getElementById("moodCanvas"), document.getElementById("moodCanvasSidebar")].filter(Boolean);
  if (!canvases.length) return;

  const pad = n => String(n).padStart(2, "0");
  const now = new Date();
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(now); d.setDate(now.getDate() - 6 + i);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  });

  const moods = days.map(ds => {
    const e = entries.find(x => x.date === ds);
    return e ? e.mood : null;
  });

  // Update x labels
  const xlEls = [document.getElementById("moodXLabels"), document.getElementById("moodXLabelsSidebar")].filter(Boolean);
  xlEls.forEach(xlEl => {
    xlEl.innerHTML = "";
    days.forEach(ds => {
      const d = new Date(ds + "T12:00:00");
      const sp = document.createElement("span");
      sp.textContent = d.toLocaleString('en-US', { weekday: 'short' });
      xlEl.appendChild(sp);
    });
    xlEl.style.display = "flex";
    xlEl.style.justifyContent = "space-between";
    xlEl.style.padding = "0 2px";
  });

  canvases.forEach(canvas => {
    const wrap = canvas.parentElement;
    canvas.width = wrap.offsetWidth || 190;
    canvas.height = 68;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const W = canvas.width;
    const H = canvas.height - 10;
    const pts = moods.map((m, i) => ({
      x: 15 + (i / 6) * (W - 30),
      y: m !== null ? H - 4 - ((m - 1) / 4) * (H - 20) : null
    }));

    // 1. Draw horizontal grid lines
    ctx.strokeStyle = "rgba(139, 101, 53, 0.1)";
    ctx.lineWidth = 1;
    for (let i = 0; i < 5; i++) {
      const gy = H - 4 - (i / 4) * (H - 20);
      ctx.beginPath();
      ctx.moveTo(15, gy);
      ctx.lineTo(W - 15, gy);
      ctx.stroke();
    }

    const validPts = pts.filter(p => p.y !== null);
    if (validPts.length < 2) return;

    // 2. Area Gradient Fill
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, "rgba(215, 120, 40, 0.2)");
    grad.addColorStop(1, "rgba(215, 120, 40, 0)");

    ctx.beginPath();
    ctx.moveTo(validPts[0].x, H);
    validPts.forEach((p, i) => {
      if (i === 0) ctx.lineTo(p.x, p.y);
      else {
        const prev = validPts[i - 1];
        const cx = (prev.x + p.x) / 2;
        ctx.quadraticCurveTo(prev.x, prev.y, cx, (prev.y + p.y) / 2);
        ctx.lineTo(p.x, p.y);
      }
    });
    ctx.lineTo(validPts[validPts.length - 1].x, H);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // 3. Smooth Main Line
    ctx.shadowColor = "rgba(215, 120, 40, 0.2)";
    ctx.shadowBlur = 10;
    ctx.shadowOffsetY = 4;
    ctx.strokeStyle = "#d77828";
    ctx.lineWidth = 3.5;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    ctx.beginPath();
    validPts.forEach((p, i) => {
      if (i === 0) ctx.moveTo(p.x, p.y);
      else {
        const prev = validPts[i - 1];
        const cp1x = prev.x + (p.x - prev.x) / 2;
        ctx.bezierCurveTo(cp1x, prev.y, cp1x, p.y, p.x, p.y);
      }
    });
    ctx.stroke();
    ctx.shadowBlur = 0;
    ctx.shadowOffsetY = 0;

    // 4. Dots
    validPts.forEach(p => {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
      ctx.fillStyle = "var(--p0)";
      ctx.fill();
      ctx.strokeStyle = "#d77828";
      ctx.lineWidth = 2;
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = "#d77828";
      ctx.fill();
    });
  });
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   WRITING STREAK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function renderStreak() {
  let streak = 0;
  const now = new Date();
  const pad = n => String(n).padStart(2, "0");
  for (let i = 0; i < 365; i++) {
    const d = new Date(now); d.setDate(now.getDate() - i);
    const ds = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    if (entries.some(e => e.date === ds)) streak++;
    else break;
  }
  
  const streakNums = [document.getElementById("streakNum"), document.getElementById("streakNumSidebar")].filter(Boolean);
  const streakMsgs = [document.getElementById("streakMsg"), document.getElementById("streakMsgSidebar")].filter(Boolean);
  
  const msg = streak === 0 ? "Start writing!" :
              streak < 3 ? "Good start!" :
              streak < 7 ? "Keep going! 🌱" :
              streak < 14 ? "You're on fire! 🔥" :
              "Unstoppable! 💪";

  streakNums.forEach(el => el.textContent = streak);
  streakMsgs.forEach(el => el.textContent = msg);
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   TAGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function renderTagCloud() {
  const freq = {};
  entries.forEach(e => (e.tags || []).forEach(t => freq[t] = (freq[t] || 0) + 1));
  const top = Object.entries(freq).sort((a, b) => b[1] - a[1]).slice(0, 8).map(x => x[0]);
  document.getElementById("tagCloud").innerHTML =
    top.map(t => `<span class="tpill" onclick="filterTag('${esc(t)}')">${esc(t)}</span>`).join("");
}

function filterTag(tag) {
  const found = entries.find(e => (e.tags || []).includes(tag));
  if (found) selectEntry(found.id);
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   MEMORIES (starred)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function renderMemories() {
  const starred = entries.filter(e => e.starred).slice(0, 4);
  const el = document.getElementById("memoriesList");
  if (!starred.length) {
    el.innerHTML = `<div style="font-size:11.5px;color:var(--muted);font-style:italic;padding:4px 0;">Star entries to see them here.</div>`;
    return;
  }
  el.innerHTML = starred.map(e => {
    const d = new Date(e.date + "T12:00:00");
    const ds = d.toLocaleString("default", { day: "2-digit", month: "short", year: "numeric" });
    return `<div class="mem-item" onclick="selectEntry(${e.id})">
      <div class="mem-date">${ds}</div>
      <div class="mem-title">${esc(e.title)}</div>
    </div>`;
  }).join("");
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   SEARCH
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function openSearch() {
  document.getElementById("searchQ").value = "";
  document.getElementById("searchResults").innerHTML = "";
  document.getElementById("searchModal").classList.add("open");
  setTimeout(() => document.getElementById("searchQ").focus(), 80);
}
function closeSearch() { document.getElementById("searchModal").classList.remove("open"); }

function doSearch() {
  const q = document.getElementById("searchQ").value.toLowerCase().trim();
  const res = document.getElementById("searchResults");
  if (!q) { res.innerHTML = ""; return; }
  const hits = entries.filter(e =>
    e.title.toLowerCase().includes(q) ||
    e.body.toLowerCase().includes(q) ||
    (e.tags || []).some(t => t.toLowerCase().includes(q))
  );
  if (!hits.length) {
    res.innerHTML = `<div style="padding:10px;color:var(--muted);font-style:italic;font-size:13px;">No entries found.</div>`;
    return;
  }
  res.innerHTML = hits.map(e => {
    const d = new Date(e.date + "T12:00:00");
    const ds = d.toLocaleString("default", { day: "2-digit", month: "short", year: "numeric" });
    return `<div class="sres-item" onclick="selectEntry(${e.id});closeSearch()">
      <div class="sres-title">${esc(e.title)}</div>
      <div class="sres-date">${ds} · ${MOODS[e.mood]}</div>
    </div>`;
  }).join("");
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   NAV
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function setNav(el) {
  document.querySelectorAll(".nav-item").forEach(x => x.classList.remove("active"));
  el.classList.add("active");
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   UTILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
function esc(s) {
  return (s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function toggleSidebar() {
  const isOpen = document.getElementById("sidebar").classList.toggle("open");
  document.getElementById("sidebarOverlay").classList.toggle("open");
  if (isOpen) {
    renderMoodChart();
    renderStreak();
  }
}
function closeSidebar() {
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("sidebarOverlay").classList.remove("open");
}

function openSettings() { document.getElementById("settingsModal").classList.add("open"); }
function closeSettings() { document.getElementById("settingsModal").classList.remove("open"); }

function updateFontSize(val) {
  const base = parseInt(val, 10);
  document.documentElement.style.setProperty('--base-fs', `${base}px`);
  document.documentElement.style.setProperty('--title-fs', `${Math.round(base * 2.4)}px`);
  document.documentElement.style.setProperty('--preview-fs', `${Math.max(12, Math.round(base * 0.9))}px`);
  localStorage.setItem('ii_fontsize_val', base);
}

function setTheme(theme) {
  document.body.classList.remove('theme-vintage', 'theme-dark');
  if (theme === 'dark') document.body.classList.add('theme-dark');
  localStorage.setItem('ii_theme', theme);
  document.getElementById("theme-vintage").classList.toggle("active", theme !== 'dark');
  document.getElementById("theme-dark").classList.toggle("active", theme === 'dark');
}

function setSortOrder(asc) {
  sortAsc = asc;
  localStorage.setItem('ii_sort', asc ? 'asc' : 'desc');
  document.getElementById("sort-desc").classList.toggle("active", !asc);
  document.getElementById("sort-asc").classList.toggle("active", asc);
  renderList();
}

function exportData() {
  let html = `<html><head><title>My Diary</title>
  <link href="https://fonts.googleapis.com/css2?family=Caveat:wght@400..700&family=Great+Vibes&family=Lora:ital,wght@0,400..700;1,400..700&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Lora', serif; padding: 40px; max-width: 800px; margin: auto; color: #2c2116; background: #fff; }
    h1 { font-family: 'Great Vibes', cursive; text-align: center; font-size: 56px; border-bottom: 2px solid #8b6535; padding-bottom: 20px; margin-bottom: 40px; }
    .entry { margin-bottom: 50px; page-break-inside: avoid; }
    .title { font-family: 'Great Vibes', cursive; font-size: 38px; margin-bottom: 5px; color: #1a1510; }
    .meta { color: #856a45; font-size: 14px; margin-bottom: 15px; border-bottom: 1px dashed #d5c8a0; padding-bottom: 10px; }
    .body { font-family: 'Caveat', cursive; font-size: 22px; white-space: pre-wrap; line-height: 1.7; }
  </style></head><body>
  <h1>Ink & Impressions</h1>`;

  // Sort entries oldest to newest for the PDF book
  const sorted = [...entries].sort((a, b) => a.date.localeCompare(b.date));

  sorted.forEach(e => {
    html += `<div class="entry">
      <div class="title">${esc(e.title)}</div>
      <div class="meta">${e.date} • ${e.time || ''} • Mood: ${MOODS[e.mood] || ''}</div>
      <div class="body">${esc(e.body)}</div>
    </div>`;
  });

  html += `</body></html>`;

  const printWindow = window.open('', '_blank');
  printWindow.document.write(html);
  printWindow.document.close();
  printWindow.focus();
  setTimeout(() => {
    printWindow.print();
  }, 1000); // Give fonts a second to load before printing
}

function closeEntryView() {
  document.getElementById("entryView").classList.remove("open");
}



function renderDailyQuote() {
  // Use day of the year + a prime multiplier to get a random-looking but consistent daily quote
  const now = new Date();
  const start = new Date(now.getFullYear(), 0, 0);
  const diff = now - start;
  const dayOfYear = Math.floor(diff / (1000 * 60 * 60 * 24));

  // 137 is a prime that helps scatter the quotes so they don't look sequential
  const idx = (dayOfYear * 137) % ALL_QUOTES.length;
  const item = ALL_QUOTES[idx];

  // Sidebar Quote
  const qEl = document.querySelector(".sidebar-quote p");
  const aEl = document.querySelector(".sidebar-quote cite");
  if (qEl) qEl.textContent = `"${item.q}"`;
  if (aEl) aEl.textContent = `— ${item.a}`;

  // Calendar Tagline
  const tEl = document.querySelector(".cal-tagline");
  if (tEl) tEl.textContent = item.q;
}

// Restore settings on load
document.addEventListener("DOMContentLoaded", () => {
  const fsVal = localStorage.getItem('ii_fontsize_val') || 19;
  const slider = document.getElementById('fs-slider');
  if (slider) slider.value = fsVal;
  updateFontSize(fsVal);
  const theme = localStorage.getItem('ii_theme') || 'vintage';
  setTheme(theme);
  setSortOrder(sortAsc);
  renderDailyQuote();
});

function copyEntryText(id) {
  const e = entries.find(x => x.id === id);
  if (e) {
    const text = `${e.title}\n\n${e.body}`;
    navigator.clipboard.writeText(text).then(() => alert("Entry copied to clipboard!"));
  }
}

/* ─── AUTH ACTIONS ─── */
function showSignup() {
  document.getElementById("loginCard").style.display = "none";
  document.getElementById("resetCard").style.display = "none";
  document.getElementById("signupCard").style.display = "block";
  document.getElementById("signupStep1").style.display = "block";
  document.getElementById("signupStep2").style.display = "none";
  setupOtpListeners('signupOtpContainer');
}

function showLogin() {
  document.getElementById("signupCard").style.display = "none";
  document.getElementById("resetCard").style.display = "none";
  document.getElementById("loginCard").style.display = "block";
}

async function login() {
  const u = document.getElementById("loginUser").value.trim();
  const p = document.getElementById("loginPass").value;
  const err = document.getElementById("loginError");
  if (!u || !p) { err.textContent = "Enter both fields"; return; }

  try {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: u, password: p })
    });
    const data = await res.json();
    if (res.ok) {
      currentUser = data.user;
      
      // Derive and store master key
      masterKey = await deriveKey(p, currentUser.username);
      sessionStorage.setItem('ii_master_key', await exportKey(masterKey));
      
      initApp(data.welcomed_back === true);
    } else {
      err.textContent = data.error || "Login failed";
    }
  } catch (e) {
    err.textContent = "Server error";
  }
}

function showReset() {
  document.getElementById("loginCard").style.display = "none";
  document.getElementById("signupCard").style.display = "none";
  document.getElementById("resetCard").style.display = "block";
  document.getElementById("resetStep1").style.display = "block";
  document.getElementById("resetStep2").style.display = "none";
  setupOtpListeners('resetOtpContainer');
}

function setupOtpListeners(containerId) {
  const container = document.getElementById(containerId);
  if (!container || container.dataset.listenersAdded) return;
  
  const inputs = container.querySelectorAll('.otp-box');
  container.dataset.listenersAdded = "true";
  
  inputs.forEach((input, index) => {
    input.addEventListener('input', (e) => {
      // Allow only digits
      e.target.value = e.target.value.replace(/[^0-9]/g, '');
      if (e.target.value.length === 1 && index < inputs.length - 1) {
        inputs[index + 1].focus();
      }
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Backspace' && !e.target.value && index > 0) {
        inputs[index - 1].focus();
      }
    });

    input.addEventListener('paste', (e) => {
      e.preventDefault();
      const pasteData = e.clipboardData.getData('text').replace(/[^0-9]/g, '').slice(0, inputs.length);
      pasteData.split('').forEach((char, i) => {
        if (inputs[i]) inputs[i].value = char;
      });
      const nextIndex = Math.min(pasteData.length, inputs.length - 1);
      inputs[nextIndex].focus();
    });
  });
}

function getOtpValue(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return "";
  const inputs = container.querySelectorAll('.otp-box');
  const code = Array.from(inputs).map(i => i.value.trim()).join('');
  console.log(`OTP from ${containerId}:`, code); // Debug log
  return code;
}

async function sendSignupOTP() {
  const email = document.getElementById("signupEmail").value.trim();
  const user = document.getElementById("signupUser").value.trim();
  const pass = document.getElementById("signupPass").value;
  const err = document.getElementById("signupError");
  const btn = document.getElementById("btnSendSignupOtp");

  if (!email || !user || !pass) { err.textContent = "All fields required"; return; }
  if (!email.includes("@")) { err.textContent = "Invalid email address"; return; }

  btn.disabled = true;
  btn.textContent = "Sending...";
  err.textContent = "";

  try {
    const res = await fetch('/api/otp/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, username: user, purpose: 'signup' })
    });
    const data = await res.json();
    if (res.ok) {
      document.getElementById("signupStep1").style.display = "none";
      document.getElementById("signupStep2").style.display = "block";
    } else {
      err.textContent = data.error || "Failed to send OTP";
    }
  } catch (e) {
    err.textContent = "Server error";
  } finally {
    btn.disabled = false;
    btn.textContent = "Get OTP";
  }
}

async function signup() {
  const u = document.getElementById("signupUser").value.trim();
  const p = document.getElementById("signupPass").value;
  const e = document.getElementById("signupEmail").value.trim();
  const otp = getOtpValue("signupOtpContainer");
  const err = document.getElementById("signupOtpError");
  
  if (otp.length < 6) { 
    err.textContent = `Enter all 6 digits (currently ${otp.length})`; 
    return; 
  }

  try {
    const res = await fetch('/api/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: u, password: p, email: e, otp })
    });
    const data = await res.json();
    if (res.ok) {
      currentUser = data.user;

      // Derive and store master key
      masterKey = await deriveKey(p, currentUser.username);
      sessionStorage.setItem('ii_master_key', await exportKey(masterKey));

      initApp();
    } else {
      err.textContent = data.error || "Signup failed";
    }
  } catch (err_msg) {
    err.textContent = "Server error";
  }
}

async function sendResetOTP() {
  const email = document.getElementById("resetEmail").value.trim();
  const err = document.getElementById("resetError");
  const btn = document.getElementById("btnSendResetOtp");

  if (!email) { err.textContent = "Email required"; return; }

  btn.disabled = true;
  btn.textContent = "Sending...";
  err.textContent = "";

  try {
    const res = await fetch('/api/password_reset/request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email })
    });
    const data = await res.json();
    if (res.ok) {
      document.getElementById("resetStep1").style.display = "none";
      document.getElementById("resetStep2").style.display = "block";
    } else {
      err.textContent = data.error || "Failed to send OTP";
    }
  } catch (e) {
    err.textContent = "Server error";
  } finally {
    btn.disabled = false;
    btn.textContent = "Send Reset Code";
  }
}

async function confirmReset() {
  const e = document.getElementById("resetEmail").value.trim();
  const otp = getOtpValue("resetOtpContainer");
  const p = document.getElementById("resetNewPass").value;
  const err = document.getElementById("resetOtpError");

  if (otp.length < 6) {
    err.textContent = `Enter all 6 digits (currently ${otp.length})`;
    return;
  }
  if (!p) {
    err.textContent = "New password required";
    return;
  }

  try {
    const res = await fetch('/api/password_reset/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: e, otp, password: p })
    });
    const data = await res.json();
    if (res.ok) {
      alert("Password reset successful! Please login.");
      showLogin();
    } else {
      err.textContent = data.error || "Reset failed";
    }
  } catch (err_msg) {
    err.textContent = "Server error";
  }
}

async function logout() {
  await fetch('/api/logout', { method: 'POST' });
  currentUser = null;
  entries = [];
  location.reload();
}

/* ─── ACCOUNT DELETION ─── */
function openDeleteAccount() {
  document.getElementById("deleteStep1").style.display = "block";
  document.getElementById("deleteStep2").style.display = "none";
  document.getElementById("deleteStep3").style.display = "none";
  document.getElementById("deleteStep1Error").textContent = "";
  document.getElementById("deleteAccountModal").classList.add("open");
}

function closeDeleteAccount() {
  document.getElementById("deleteAccountModal").classList.remove("open");
}

async function requestDeleteOTP() {
  const btn = event.target;
  const err = document.getElementById("deleteStep1Error");
  btn.disabled = true;
  btn.textContent = "Sending...";
  err.textContent = "";

  try {
    const res = await fetch('/api/account/delete/request', { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      document.getElementById("deleteStep1").style.display = "none";
      document.getElementById("deleteStep2").style.display = "block";
      setupOtpListeners('deleteOtpContainer');
    } else {
      err.textContent = data.error || "Failed to send OTP";
    }
  } catch (e) {
    err.textContent = "Server error";
  } finally {
    btn.disabled = false;
    btn.textContent = "Yes, send me a verification code";
  }
}

async function confirmDeleteAccount() {
  const otp = getOtpValue("deleteOtpContainer");
  const err = document.getElementById("deleteOtpError");
  if (otp.length < 6) { err.textContent = `Enter all 6 digits (currently ${otp.length})`; return; }

  try {
    const res = await fetch('/api/account/delete/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ otp })
    });
    const data = await res.json();
    if (res.ok) {
      // Show farewell step
      document.getElementById("deleteStep2").style.display = "none";
      document.getElementById("deleteStep3").style.display = "block";
      // Reload after 5 seconds to log out
      setTimeout(() => location.reload(), 5000);
    } else {
      err.textContent = data.error || "Deletion failed";
    }
  } catch (e) {
    err.textContent = "Server error";
  }
}

// Redraw canvas on resize
window.addEventListener("resize", renderMoodChart);

/* ─── PROBLEM REPORTING ─── */
function openProblemModal() {
  document.getElementById("problemDesc").value = "";
  document.getElementById("problemError").textContent = "";
  document.getElementById("problemModal").classList.add("open");
  setTimeout(() => document.getElementById("problemDesc").focus(), 80);
}

function closeProblemModal() {
  document.getElementById("problemModal").classList.remove("open");
}

async function submitProblem() {
  const desc = document.getElementById("problemDesc").value.trim();
  const fileInput = document.getElementById("problemImage");
  const errEl = document.getElementById("problemError");
  const btn = document.getElementById("btnSubmitProblem");

  if (!desc) {
    errEl.textContent = "Please describe the problem.";
    return;
  }
  
  if (desc.length > 5000) {
    errEl.textContent = "Description is too long. Please keep it under 5000 characters.";
    return;
  }

  btn.disabled = true;
  btn.textContent = "Sending...";
  errEl.textContent = "";

  let imageData = null;
  if (fileInput.files && fileInput.files[0]) {
    const file = fileInput.files[0];
    if (file.size > 2 * 1024 * 1024) { // 2MB limit
      errEl.textContent = "Image size should be less than 2MB.";
      btn.disabled = false;
      btn.textContent = "Send Report";
      return;
    }
    
    // Convert to base64
    imageData = await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = (e) => resolve(e.target.result.split(',')[1]);
      reader.readAsDataURL(file);
    });
  }

  try {
    const res = await fetch('/api/report_problem', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ error: desc, image: imageData })
    });
    const data = await res.json();
    if (res.ok) {
      btn.textContent = "Sent! ✨";
      btn.style.background = "#27ae60"; // Green for success
      
      setTimeout(() => {
        closeProblemModal();
        // Reset button for next time
        btn.textContent = "Send Report";
        btn.style.background = ""; 
        btn.disabled = false;
      }, 1500);
      
    } else {
      errEl.textContent = data.error || "Failed to send report.";
      btn.disabled = false;
      btn.textContent = "Send Report";
    }
  } catch (e) {
    errEl.textContent = "Server error. Please try again later.";
    btn.disabled = false;
    btn.textContent = "Send Report";
  }
}

// Close overlays on background click
document.querySelectorAll(".overlay").forEach(el => {
  el.addEventListener("click", e => { 
    if (e.target === el && !el.classList.contains('auth-overlay')) {
      el.classList.remove("open"); 
    }
  });
});

/* ─── DATA WIPE LOGIC ────────────────────────────────────── */
function openWipeData() {
  closeSettings();
  document.getElementById("wipeStep1").style.display = "block";
  document.getElementById("wipeStep2").style.display = "none";
  document.getElementById("wipeStep1Error").textContent = "";
  document.getElementById("wipeOtpError").textContent = "";
  document.getElementById("wipeDataModal").classList.add("open");
}

function closeWipeData() {
  document.getElementById("wipeDataModal").classList.remove("open");
}

async function requestWipeOTP() {
  const err = document.getElementById("wipeStep1Error");
  err.textContent = "";
  try {
    const res = await fetch('/api/data/wipe/request', { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      document.getElementById("wipeStep1").style.display = "none";
      document.getElementById("wipeStep2").style.display = "block";
      setupOtpInput("wipeOtpContainer");
    } else {
      err.textContent = data.error || "Failed to send OTP.";
    }
  } catch (e) {
    err.textContent = "Server error. Try again.";
  }
}

async function confirmWipe() {
  const err = document.getElementById("wipeOtpError");
  err.textContent = "";
  const otp = Array.from(document.querySelectorAll("#wipeOtpContainer .otp-box")).map(i => i.value).join("");
  if (otp.length < 6) {
    err.textContent = "Please enter all 6 digits.";
    return;
  }

  try {
    const res = await fetch('/api/data/wipe/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ otp })
    });
    const data = await res.json();
    if (res.ok) {
      currentUser.data_wipe_scheduled = true;
      currentUser.data_wipe_date = data.data_wipe_date;
      closeWipeData();
      initApp();
    } else {
      err.textContent = data.error || "Verification failed.";
    }
  } catch (e) {
    err.textContent = "Server error. Try again.";
  }
}

async function restoreWipedData() {
  try {
    const res = await fetch('/api/data/wipe/restore', { method: 'POST' });
    if (res.ok) {
      currentUser.data_wipe_scheduled = false;
      currentUser.data_wipe_date = null;
      initApp();
    } else {
      alert("Failed to restore data.");
    }
  } catch (e) {
    alert("Connection error.");
  }
}

/* Helper for OTP inputs */
function setupOtpInput(containerId) {
  const inputs = document.querySelectorAll(`#${containerId} .otp-box`);
  inputs.forEach((input, index) => {
    input.value = "";
    input.addEventListener("input", (e) => {
      if (e.target.value.length === 1 && index < inputs.length - 1) {
        inputs[index + 1].focus();
      }
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Backspace" && !e.target.value && index > 0) {
        inputs[index - 1].focus();
      }
    });
  });
  if (inputs.length > 0) inputs[0].focus();
}
