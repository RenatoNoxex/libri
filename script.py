#!/usr/bin/env python3
"""
Script Orchestratore — Ricerca Libri Zero-Touch
================================================
1. Avvia MCP server come subprocesso
2. Chiama tool search_novita_editoriali (Tavily)
3. Chiama tool filtra_con_deepseek (DeepSeek V4 Flash + Pro)
4. Filtra duplicati con storico_libri.json
5. Genera index.html completo (navigazione per data, ricerca globale)
6. Genera dettaglio.html (array piatto di tutti i libri)
7. Salva storico aggiornato (raccolte per data)
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from collections import OrderedDict

# ──────────────────────────────────────────
#  Config
# ──────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
MCP_SERVER_DIR = BASE_DIR / "mcp-server"
HISTORIC_FILE = BASE_DIR / "storico_libri.json"
OUTPUT_HTML = BASE_DIR / "index.html"

# Data corrente
DATA_CORRENTE = datetime.now()
DATA_STR = DATA_CORRENTE.strftime("%Y-%m-%d")
DATA_ITALIANA = DATA_CORRENTE.strftime("%d/%m/%Y")


# ──────────────────────────────────────────
#  MCP Client via subprocess
# ──────────────────────────────────────────

class MCPClient:
    """Comunica con il server MCP via stdio JSON-RPC."""

    def __init__(self, server_path: str):
        self.server_path = server_path
        self.process = None
        self._req_id = 0
        self._initialized = False

    def start(self):
        print("[SCRIPT] Avvio MCP server...")
        self.process = subprocess.Popen(
            ["node", self.server_path],
            cwd=str(MCP_SERVER_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ},
        )
        time.sleep(0.5)
        self._req_id += 1
        init_req = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "libri-script-py", "version": "1.0.0"},
            },
        }
        self._write_request(init_req)
        init_resp = self._read_response()
        if not init_resp or "error" in init_resp:
            err = init_resp.get("error", {}).get("message", "sconosciuto") if init_resp else "timeout"
            raise RuntimeError(f"Handshake MCP fallito: {err}")
        print(f"[SCRIPT] MCP inizializzato (server: {init_resp.get('result', {}).get('serverInfo', {}).get('name', '?')})")

        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self._write_request(notif)
        self._initialized = True
        print("[SCRIPT] MCP server pronto.")

    def _write_request(self, request: dict):
        line = json.dumps(request) + "\n"
        self.process.stdin.write(line)
        self.process.stdin.flush()

    def _read_response(self, timeout_sec: int = 120):
        deadline = time.time() + timeout_sec
        response_str = ""
        depth = 0
        in_string = False
        escaped = False
        while time.time() < deadline:
            ch = self.process.stdout.read(1)
            if not ch:
                if response_str:
                    break
                time.sleep(0.05)
                continue
            response_str += ch
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"' and not escaped:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(response_str)
                    except json.JSONDecodeError:
                        return None
        try:
            return json.loads(response_str)
        except json.JSONDecodeError:
            return None

    def call_tool(self, tool_name: str, arguments: dict = None):
        self._req_id += 1
        params = {"name": tool_name}
        if arguments:
            params["arguments"] = arguments
        request = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "tools/call",
            "params": params,
        }
        self._write_request(request)
        response = self._read_response(timeout_sec=180)
        if not response:
            return {"error": "Timeout chiamata tool"}
        if "error" in response:
            return {"error": response["error"]}
        result_data = response.get("result", {})
        content = result_data.get("content", [])
        if content:
            return {"result": {"content": content}}
        return {"result": {"content": [{"type": "json", "json": result_data}]}}

    def stop(self):
        if self.process:
            print("[SCRIPT] Arresto MCP server...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            print("[SCRIPT] MCP server arrestato.")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


# ──────────────────────────────────────────
#  Storico (nuovo formato: raccolte per data)
# ──────────────────────────────────────────

def carica_storico():
    """Carica lo storico. Auto-migra dal vecchio formato se necessario."""
    if not HISTORIC_FILE.exists():
        return {"raccolte": OrderedDict()}

    with open(HISTORIC_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Auto-migrazione vecchio formato
    if "raccolte" not in data and "libri" in data:
        key = data.get("ultimo_aggiornamento", DATA_STR)
        parts = key.split("-")
        data_ita = f"{parts[2]}/{parts[1]}/{parts[0]}"
        data = {
            "raccolte": OrderedDict([
                (key, {"data_italiana": data_ita, "libri": data.get("libri", [])})
            ])
        }
    elif "raccolte" in data:
        # Converti dict normale in OrderedDict ordinato per data decrescente
        ordinato = OrderedDict()
        for k in sorted(data["raccolte"].keys(), reverse=True):
            ordinato[k] = data["raccolte"][k]
        data["raccolte"] = ordinato

    total = sum(len(r.get("libri", [])) for r in data.get("raccolte", {}).values())
    print(f"[SCRIPT] Storico caricato: {len(data.get('raccolte', {}))} giorni, {total} libri totali.")
    return data


def salva_storico(storico):
    """Salva lo storico aggiornato."""
    # Converti OrderedDict in dict normale per JSON
    out = {"raccolte": {}}
    for k, v in storico.get("raccolte", {}).items():
        out["raccolte"][k] = v
    with open(HISTORIC_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    total = sum(len(r.get("libri", [])) for r in out["raccolte"].values())
    print(f"[SCRIPT] Storico salvato: {len(out['raccolte'])} giorni, {total} libri totali.")


def filtra_nuovi_libri(libri_nuovi, storico):
    """Filtra solo i libri non già presenti in nessuna raccolta dello storico."""
    if not libri_nuovi:
        return []

    esistenti = set()
    for raccolta in storico.get("raccolte", {}).values():
        for l in raccolta.get("libri", []):
            key = f"{l.get('titolo_it', '')}|{l.get('autore', '')}".lower().strip()
            esistenti.add(key)

    nuovi = []
    for l in libri_nuovi:
        key = f"{l.get('titolo_it', '')}|{l.get('autore', '')}".lower().strip()
        if key and key not in esistenti:
            nuovi.append(l)
            esistenti.add(key)

    print(f"[SCRIPT] Filtro duplicati: {len(libri_nuovi)} ricevuti, {len(nuovi)} nuovi.")
    return nuovi


def libri_totali_ordinati(storico):
    """Restituisce tutti i libri in ordine cronologico (più recente prima), con il loro ID globale."""
    tutti = []
    for data_key in sorted(storico.get("raccolte", {}).keys(), reverse=True):
        raccolta = storico["raccolte"][data_key]
        for l in raccolta.get("libri", []):
            tutti.append((data_key, l))
    return tutti


# ──────────────────────────────────────────
#  Generazione HTML
# ──────────────────────────────────────────

CSS_TEMPLATE = """
    /* ===== RESET & BASE ===== */
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f5f5f5;
      color: #222;
      line-height: 1.6;
    }
    :root {
      --primary: #e50914;
      --primary-dark: #b20710;
      --dark: #1a1a2e;
      --dark2: #16213e;
      --gray-bg: #f5f5f5;
      --gray-light: #e8e8e8;
      --gray-text: #666;
      --dark-text: #222;
      --white: #ffffff;
      --shadow: 0 2px 12px rgba(0,0,0,0.08);
      --shadow-hover: 0 6px 24px rgba(0,0,0,0.14);
      --radius: 10px;
      --max-width: 1200px;
      --header-height: 70px;
    }
    /* ===== HEADER ===== */
    .header {
      background: var(--dark);
      position: sticky;
      top: 0;
      z-index: 1000;
      height: var(--header-height);
      display: flex;
      align-items: center;
      box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }
    .header-inner {
      max-width: var(--max-width);
      width: 100%;
      margin: 0 auto;
      padding: 0 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    .logo {
      display: flex;
      align-items: center;
      gap: 12px;
      text-decoration: none;
      flex-shrink: 0;
    }
    .logo-icon {
      width: 40px;
      height: 40px;
      background: var(--primary);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      font-size: 20px;
      color: white;
    }
    .logo-text {
      color: white;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }
    .logo-text span { color: var(--primary); }
    .badge-date {
      background: rgba(255,255,255,0.1);
      color: #aaa;
      padding: 6px 14px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 500;
      flex-shrink: 0;
    }
    /* ===== SEARCH BAR IN HEADER ===== */
    .search-wrapper {
      flex: 1;
      max-width: 400px;
      position: relative;
    }
    .search-wrapper input {
      width: 100%;
      padding: 10px 16px 10px 40px;
      border: 2px solid rgba(255,255,255,0.15);
      border-radius: 24px;
      background: rgba(255,255,255,0.08);
      color: white;
      font-size: 14px;
      font-family: inherit;
      outline: none;
      transition: all 0.2s;
    }
    .search-wrapper input::placeholder { color: #888; }
    .search-wrapper input:focus {
      border-color: var(--primary);
      background: rgba(255,255,255,0.14);
    }
    .search-wrapper .search-icon {
      position: absolute;
      left: 14px;
      top: 50%;
      transform: translateY(-50%);
      font-size: 16px;
      color: #999;
      pointer-events: none;
    }
    .search-results-count {
      color: #aaa;
      font-size: 12px;
      text-align: center;
      margin: 6px 0 -10px;
      display: none;
    }
    .search-results-count.visible { display: block; }
    /* ===== DATE NAV BAR ===== */
    .date-nav {
      background: white;
      border-bottom: 1px solid var(--gray-light);
      position: sticky;
      top: var(--header-height);
      z-index: 999;
      overflow-x: auto;
      white-space: nowrap;
      -webkit-overflow-scrolling: touch;
    }
    .date-nav::-webkit-scrollbar { height: 0; }
    .date-nav-inner {
      max-width: var(--max-width);
      margin: 0 auto;
      padding: 0 20px;
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .date-nav-inner .nav-label {
      font-size: 12px;
      font-weight: 700;
      color: #999;
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-right: 4px;
      flex-shrink: 0;
    }
    .date-btn {
      display: inline-block;
      padding: 10px 16px;
      font-size: 13px;
      font-weight: 600;
      color: var(--gray-text);
      text-decoration: none;
      border-bottom: 3px solid transparent;
      transition: all 0.2s;
      cursor: pointer;
      background: none;
      border-top: none;
      border-left: none;
      border-right: none;
      font-family: inherit;
      white-space: nowrap;
    }
    .date-btn:hover { color: var(--primary); }
    .date-btn.active {
      color: var(--primary);
      border-bottom-color: var(--primary);
    }
    .date-btn-all {
      color: #999;
      font-style: italic;
    }
    /* ===== MAIN CONTAINER ===== */
    .container {
      max-width: var(--max-width);
      margin: 0 auto;
      padding: 30px 20px;
    }
    /* ===== DAY GROUP ===== */
    .day-group { margin-bottom: 40px; }
    .day-group-header {
      font-size: 18px;
      font-weight: 700;
      color: var(--dark);
      margin-bottom: 16px;
      padding-bottom: 8px;
      border-bottom: 3px solid var(--primary);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .day-group-header .day-badge {
      background: var(--primary);
      color: white;
      padding: 3px 12px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
    }
    /* ===== NEWS GRID ===== */
    .news-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 24px;
      margin-bottom: 10px;
    }
    .news-card {
      background: white;
      border-radius: var(--radius);
      overflow: hidden;
      box-shadow: var(--shadow);
      transition: all 0.3s ease;
      display: flex;
      flex-direction: column;
    }
    .news-card:hover {
      transform: translateY(-4px);
      box-shadow: var(--shadow-hover);
    }
    .news-card.hidden { display: none; }
    .news-card .card-body {
      padding: 22px;
      flex: 1;
      display: flex;
      flex-direction: column;
    }
    .news-card .cat-badge {
      display: inline-block;
      background: var(--primary);
      color: white;
      padding: 3px 12px;
      border-radius: 20px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 12px;
      align-self: flex-start;
    }
    .news-card h3 { font-size: 18px; font-weight: 700; line-height: 1.4; margin-bottom: 6px; color: var(--dark-text); }
    .news-card .original-title { font-size: 13px; color: #999; margin-bottom: 8px; font-style: italic; }
    .news-card .meta-info { font-size: 13px; color: #666; margin-bottom: 10px; }
    .news-card .meta-info strong { color: #444; }
    .news-card .excerpt {
      font-size: 14px;
      color: var(--gray-text);
      line-height: 1.6;
      flex: 1;
      margin-bottom: 14px;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .news-card .card-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      color: #999;
      padding-top: 14px;
      border-top: 1px solid var(--gray-light);
      gap: 10px;
    }
    .news-card .card-footer .source {
      color: var(--primary);
      font-weight: 500;
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .news-card h3 a { color: var(--dark-text); text-decoration: none; transition: color 0.2s; }
    .news-card h3 a:hover { color: var(--primary); }
    /* ===== HERO CARD (primo libro di ogni giorno) ===== */
    .news-card-hero {
      background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
      border-radius: 16px;
      overflow: hidden;
      box-shadow: 0 4px 24px rgba(0,0,0,0.15);
      margin-bottom: 28px;
      display: flex;
      flex-direction: row;
      min-height: 260px;
      transition: all 0.3s ease;
    }
    .news-card-hero:hover {
      transform: translateY(-3px);
      box-shadow: 0 8px 32px rgba(0,0,0,0.22);
    }
    .news-card-hero.hidden { display: none; }
    .news-card-hero .hero-body {
      padding: 36px 40px;
      flex: 1;
      display: flex;
      flex-direction: column;
      justify-content: center;
    }
    .news-card-hero .cat-badge {
      background: var(--primary);
      font-size: 13px;
      padding: 5px 18px;
      margin-bottom: 16px;
    }
    .news-card-hero h3 {
      font-size: 28px;
      font-weight: 800;
      line-height: 1.2;
      margin-bottom: 6px;
    }
    .news-card-hero h3 a {
      color: white;
      text-decoration: none;
      transition: color 0.2s;
    }
    .news-card-hero h3 a:hover { color: var(--primary); }
    .news-card-hero .original-title {
      font-size: 16px;
      color: #aaa;
      margin-bottom: 12px;
      font-style: italic;
    }
    .news-card-hero .meta-info {
      font-size: 15px;
      color: #ccc;
      margin-bottom: 14px;
    }
    .news-card-hero .meta-info strong { color: #eee; }
    .news-card-hero .excerpt {
      font-size: 15px;
      color: #bbb;
      line-height: 1.7;
      flex: 1;
      display: -webkit-box;
      -webkit-line-clamp: 4;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .news-card-hero .card-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 13px;
      color: #999;
      padding-top: 16px;
      border-top: 1px solid rgba(255,255,255,0.1);
      gap: 14px;
    }
    .news-card-hero .card-footer .source {
      color: var(--primary);
      font-weight: 600;
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .news-card-hero .hero-emoji {
      flex-shrink: 0;
      width: 180px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 96px;
      background: rgba(255,255,255,0.03);
    }
    @media (max-width: 768px) {
      .news-card-hero { flex-direction: column; min-height: auto; }
      .news-card-hero .hero-body { padding: 24px; }
      .news-card-hero h3 { font-size: 22px; }
      .news-card-hero .hero-emoji { width: 100%; height: 80px; font-size: 56px; }
    }
    /* ===== EMPTY STATE ===== */
    .empty-search {
      text-align: center;
      padding: 60px 20px;
      color: #999;
      display: none;
    }
    .empty-search.visible { display: block; }
    .empty-search .emoji { font-size: 48px; margin-bottom: 12px; }
    /* ===== FOOTER ===== */
    .footer {
      background: var(--dark);
      color: #888;
      padding: 30px 20px;
      text-align: center;
      font-size: 14px;
      margin-top: 60px;
    }
    .footer strong { color: #ccc; }
    /* ===== RESPONSIVE ===== */
    @media (max-width: 768px) {
      :root { --header-height: 60px; }
      .logo-text { font-size: 16px; }
      .logo-icon { width: 34px; height: 34px; font-size: 16px; }
      .badge-date { display: none; }
      .search-wrapper { max-width: 200px; }
      .news-grid { grid-template-columns: 1fr; gap: 16px; }
      .container { padding: 20px 16px; }
      .date-btn { padding: 8px 12px; font-size: 12px; }
    }
    @media (max-width: 480px) {
      .news-card .card-body { padding: 16px; }
      .news-card h3 { font-size: 16px; }
      .header-inner { padding: 0 10px; gap: 8px; }
      .search-wrapper { max-width: 140px; }
      .search-wrapper input { font-size: 12px; padding: 8px 12px 8px 32px; }
    }
"""


def genera_html_completo(storico: dict) -> str:
    """Genera l'index.html completo con navigazione per data e ricerca globale."""

    raccolte = storico.get("raccolte", {})
    date_keys = sorted(raccolte.keys(), reverse=True)  # più recente prima

    # --- Genera i bottoni data ---
    date_buttons = ""
    for i, dk in enumerate(date_keys):
        label = raccolte[dk].get("data_italiana", dk)
        active = "active" if i == 0 else ""
        date_buttons += f'        <button class="date-btn {active}" data-date="{dk}">📅 {label}</button>\n'

    # Pulsante "Tutti" per mostrare tutte le date
    date_buttons += '        <button class="date-btn date-btn-all" data-date="all">📚 Tutti</button>\n'

    # --- Genera i gruppi giorno (day-groups) ---
    # Appiattisci tutti i libri con ID globale
    libri_globali = libri_totali_ordinati(storico)

    # Mappa data_key -> lista di (idx_globale, libro)
    libri_per_data = {}
    for idx, (dk, l) in enumerate(libri_globali):
        libri_per_data.setdefault(dk, []).append((idx, l))

    day_groups_html = ""

    for dk in date_keys:
        raccolta = raccolte[dk]
        label = raccolta.get("data_italiana", dk)
        libri_del_giorno = libri_per_data.get(dk, [])

        hero_html = ""
        grid_cards = ""
        for i, (global_idx, l) in enumerate(libri_del_giorno):
            if i == 0:
                hero_html = genera_hero_html(l, global_idx)
            else:
                grid_cards += genera_card_html(l, global_idx)

        grid_section = f'<div class="news-grid">{grid_cards}</div>' if grid_cards.strip() else ''

        # Il primo giorno è visibile di default, gli altri nascosti
        display_style = "" if dk == date_keys[0] else ' style="display:none;"'

        day_groups_html += f"""
    <section class="day-group" data-date="{dk}"{display_style}>
      <div class="day-group-header">
        📅 <span class="day-badge">{label}</span>
      </div>
      {hero_html}
      {grid_section}
    </section>"""

    # --- Costruisci HTML finale ---
    html = f"""<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Libri — Novità editoriali selezionate</title>
  <meta name="description" content="Novità editoriali di alto profilo: premi letterari, recensioni dagli inserti culturali italiani, bestseller di narrativa straniera tradotta.">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📚</text></svg>">
  <style>
{CSS_TEMPLATE}
  </style>
</head>
<body>

  <!-- HEADER -->
  <header class="header">
    <div class="header-inner">
      <a href="index.html" class="logo">
        <div class="logo-icon">📚</div>
        <div class="logo-text"><span>Libri</span></div>
      </a>
      <div class="search-wrapper">
        <span class="search-icon">🔍</span>
        <input type="text" id="search-input" placeholder="Cerca titolo, autore, editore..." autocomplete="off">
      </div>
      <span class="badge-date">{DATA_ITALIANA}</span>
    </div>
  </header>

  <!-- DATE NAVIGATION -->
  <nav class="date-nav">
    <div class="date-nav-inner">
      <span class="nav-label">Giorni</span>
{date_buttons}
    </div>
  </nav>

  <!-- SEARCH RESULTS COUNT -->
  <div class="search-results-count" id="search-count"></div>

  <!-- MAIN CONTENT -->
  <div class="container" id="main-content">
{day_groups_html}
  </div>

  <!-- EMPTY SEARCH STATE -->
  <div class="empty-search" id="empty-search">
    <div class="emoji">🔍</div>
    <p>Nessun libro trovato per la ricerca corrente.</p>
  </div>

  <!-- FOOTER -->
  <footer class="footer">
    <p><strong>Libri — Novità editoriali</strong> — Selezione automatica quotidiana</p>
    <p style="margin-top:6px;font-size:12px;">Dati raccolti da fonti pubbliche · Ricerca via Tavily · Analisi DeepSeek AI · Aggiornato il {DATA_ITALIANA}</p>
  </footer>

  <script>
    // ──────────────────────────────────────────
    //  Navigazione per data + Ricerca globale
    // ──────────────────────────────────────────
    (function() {{
      const dateButtons = document.querySelectorAll('.date-btn');
      const dayGroups = document.querySelectorAll('.day-group');
      const searchInput = document.getElementById('search-input');
      const searchCount = document.getElementById('search-count');
      const emptySearch = document.getElementById('empty-search');

      let currentDate = null; // null = mostra solo il primo giorno, 'all' = tutti

      function showDate(dateKey) {{
        currentDate = dateKey;
        // Aggiorna bottoni
        dateButtons.forEach(btn => {{
          btn.classList.toggle('active', btn.dataset.date === dateKey);
        }});
        // Mostra/nascondi gruppi
        dayGroups.forEach(group => {{
          if (dateKey === 'all') {{
            group.style.display = '';
          }} else {{
            group.style.display = group.dataset.date === dateKey ? '' : 'none';
          }}
        }});
        // Ri-applica eventuale filtro di ricerca
        applySearch();
        // Scroll top
        window.scrollTo({{ top: 0, behavior: 'smooth' }});
      }}

      function applySearch() {{
        const query = searchInput.value.toLowerCase().trim();
        let visibleCount = 0;

        dayGroups.forEach(group => {{
          // Considera solo i gruppi visibili (per data)
          if (group.style.display === 'none') return;

          const allCards = group.querySelectorAll('.news-card, .news-card-hero');
          let groupVisibleCards = 0;
          allCards.forEach(card => {{
            if (!query) {{
              card.classList.remove('hidden');
              visibleCount++;
              groupVisibleCards++;
            }} else {{
              // Cerca in tutti i campi: titolo, autore, editore, sinossi, premio, motivazione, titolo originale, traduttore
              const searchText = (card.dataset.searchText || '').toLowerCase();
              if (searchText.includes(query)) {{
                card.classList.remove('hidden');
                visibleCount++;
                groupVisibleCards++;
              }} else {{
                card.classList.add('hidden');
              }}
            }}
          }});

          // Nascondi il gruppo se non ha carte visibili
          if (groupVisibleCards === 0 && query) {{
            group.style.display = 'none';
          }} else if (!query) {{
            // Ripristina visibilità gruppo
            if (currentDate === 'all') {{
              group.style.display = '';
            }} else if (currentDate) {{
              group.style.display = group.dataset.date === currentDate ? '' : 'none';
            }}
          }} else {{
            group.style.display = '';
          }}
        }});

        // Aggiorna contatore e stato vuoto
        if (query) {{
          searchCount.textContent = visibleCount + ' libr' + (visibleCount === 1 ? 'o' : 'i') + ' trovati';
          searchCount.classList.add('visible');
          emptySearch.classList.toggle('visible', visibleCount === 0);
        }} else {{
          searchCount.classList.remove('visible');
          emptySearch.classList.remove('visible');
        }}
      }}

      // Click sui bottoni data
      dateButtons.forEach(btn => {{
        btn.addEventListener('click', function() {{
          showDate(this.dataset.date);
        }});
      }});

      // Input ricerca
      searchInput.addEventListener('input', function() {{
        applySearch();
      }});

      // Inizializza: imposta la prima data come attiva
      if (dayGroups.length > 0) {{
        const firstDate = dayGroups[0].dataset.date;
        currentDate = firstDate;
        dateButtons.forEach(btn => {{
          btn.classList.toggle('active', btn.dataset.date === firstDate);
        }});
      }}
    }})();
  </script>

</body>
</html>"""

    return html


def genera_card_html(l, global_idx):
    """Genera il HTML di una singola card."""
    badge = ""
    if l.get("premio"):
        badge = f'<span class="cat-badge">🏆 {escape_html(l["premio"])}</span>'
    elif l.get("fonte_recensione"):
        badge = f'<span class="cat-badge">📰 {escape_html(l["fonte_recensione"])}</span>'
    else:
        badge = '<span class="cat-badge">📖 Novità</span>'

    titolo_originale = ""
    if l.get("titolo_originale") and l["titolo_originale"] != "N/D":
        titolo_originale = f'<p class="original-title">{escape_html(l["titolo_originale"])}</p>'

    traduttore = ""
    if l.get("traduttore") and l["traduttore"] != "N/D":
        traduttore = f' · <strong>Traduttore:</strong> {escape_html(l["traduttore"])}'

    data_pub = ""
    if l.get("data_pubblicazione"):
        data_pub = f'<span>📅 {escape_html(l["data_pubblicazione"])}</span>'

    sinossi = escape_html(l.get('sinossi_critica', 'Sinossi non disponibile.'))

    # Costruisci il testo di ricerca (tutti i campi concatenati)
    search_text = " ".join([
        l.get('titolo_it') or '',
        l.get('titolo_originale') or '',
        l.get('autore') or '',
        l.get('editore') or '',
        l.get('traduttore') or '',
        l.get('sinossi_critica') or '',
        l.get('motivazione_inclusione') or '',
        l.get('premio') or '',
        l.get('fonte_recensione') or '',
        l.get('data_pubblicazione') or '',
    ])

    return f"""
    <div class="news-card" data-search-text="{escape_html(search_text)}">
      <div class="card-body">
        {badge}
        <h3><a href="dettaglio.html?id={global_idx}">{escape_html(l.get('titolo_it', 'Titolo sconosciuto'))}</a></h3>
        {titolo_originale}
        <p class="meta-info">
          <strong>Autore:</strong> {escape_html(l.get('autore', 'N/D'))} · <strong>Editore:</strong> {escape_html(l.get('editore', 'N/D'))}{traduttore}
        </p>
        <div class="excerpt">{sinossi}</div>
        <div class="card-footer">
          <span class="source">{escape_html(l.get('motivazione_inclusione', ''))}</span>
          {data_pub}
        </div>
      </div>
    </div>"""


def genera_hero_html(l, global_idx):
    """Genera un box hero in evidenza (primo libro del giorno)."""
    badge = ""
    if l.get("premio"):
        badge = f'<span class="cat-badge">🏆 {escape_html(l["premio"])}</span>'
    elif l.get("fonte_recensione"):
        badge = f'<span class="cat-badge">📰 {escape_html(l["fonte_recensione"])}</span>'
    else:
        badge = '<span class="cat-badge">📖 Novità</span>'

    titolo_originale = ""
    if l.get("titolo_originale") and l["titolo_originale"] != "N/D":
        titolo_originale = f'<p class="original-title">{escape_html(l["titolo_originale"])}</p>'

    traduttore = ""
    if l.get("traduttore") and l["traduttore"] != "N/D":
        traduttore = f' · <strong>Traduttore:</strong> {escape_html(l["traduttore"])}'

    data_pub = ""
    if l.get("data_pubblicazione"):
        data_pub = f'<span>📅 {escape_html(l["data_pubblicazione"])}</span>'

    sinossi = escape_html(l.get('sinossi_critica', 'Sinossi non disponibile.'))

    search_text = " ".join([
        l.get('titolo_it') or '',
        l.get('titolo_originale') or '',
        l.get('autore') or '',
        l.get('editore') or '',
        l.get('traduttore') or '',
        l.get('sinossi_critica') or '',
        l.get('motivazione_inclusione') or '',
        l.get('premio') or '',
        l.get('fonte_recensione') or '',
        l.get('data_pubblicazione') or '',
    ])

    return f"""
    <div class="news-card-hero" data-search-text="{escape_html(search_text)}">
      <div class="hero-body">
        {badge}
        <h3><a href="dettaglio.html?id={global_idx}">{escape_html(l.get('titolo_it', 'Titolo sconosciuto'))}</a></h3>
        {titolo_originale}
        <p class="meta-info">
          <strong>Autore:</strong> {escape_html(l.get('autore', 'N/D'))} · <strong>Editore:</strong> {escape_html(l.get('editore', 'N/D'))}{traduttore}
        </p>
        <div class="excerpt">{sinossi}</div>
        <div class="card-footer">
          <span class="source">{escape_html(l.get('motivazione_inclusione', ''))}</span>
          {data_pub}
        </div>
      </div>
      <div class="hero-emoji">📚</div>
    </div>"""


def escape_html(text):
    if not text:
        return ""
    text = str(text)
    amp = "&" + "amp;"
    lt = "&" + "lt;"
    gt = "&" + "gt;"
    quot = "&" + "quot;"
    text = text.replace("&", amp)
    text = text.replace("<", lt)
    text = text.replace(">", gt)
    text = text.replace('"', quot)
    return text


def genera_dettaglio_html(storico):
    """Genera dettaglio.html con tutti i libri in array piatto (ID globali)."""
    DETTAGLIO_PATH = BASE_DIR / "dettaglio.html"

    if not DETTAGLIO_PATH.exists():
        print("[WARNING] Template dettaglio.html non trovato.")
        return

    with open(DETTAGLIO_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    # Appiattisci tutti i libri in ordine cronologico
    tutti = libri_totali_ordinati(storico)
    libri = [l for _, l in tutti]

    libri_json = json.dumps(libri, ensure_ascii=False, indent=2)
    nuovo_blocco = f"const LIBRI_DATA = {libri_json};"

    pattern = r"const LIBRI_DATA\s*=\s*\[[\s\S]*?\];"
    html_output = re.sub(pattern, nuovo_blocco, html, count=1)

    if "const LIBRI_DATA" not in html_output:
        html_output = html.replace("__LIBRI_JSON_PLACEHOLDER__", libri_json)

    with open(DETTAGLIO_PATH, "w", encoding="utf-8") as f:
        f.write(html_output)

    print(f"[SCRIPT] dettaglio.html aggiornato con {len(libri)} libri (array piatto).")


# ──────────────────────────────────────────
#  Main
# ──────────────────────────────────────────

def main():
    print("=" * 60)
    print("  LIBRI — Ricerca Novità Editoriali Zero-Touch")
    print(f"  Data: {DATA_ITALIANA}")
    print("=" * 60)

    # 1. Carica storico
    storico = carica_storico()

    # 2. Avvia MCP e recupera dati
    server_script = str(MCP_SERVER_DIR / "index.js")

    if not os.path.exists(server_script):
        print(f"[ERRORE] MCP server non trovato: {server_script}")
        sys.exit(1)

    nuovi_libri_oggi = []

    with MCPClient(server_script) as mcp:
        # 2a. Ricerca novità editoriali
        print("\n[FASE 1] Ricerca novità editoriali (Tavily)...")
        search_result = mcp.call_tool("search_novita_editoriali")

        if search_result and "result" in search_result:
            content = search_result["result"].get("content", [])
            if content and content[0].get("type") == "json":
                search_data = content[0]["json"]
                risultati_grezzi = search_data.get("risultati", [])
                print(f"  → {len(risultati_grezzi)} risultati grezzi trovati")
            else:
                print("  → Nessun risultato dalla ricerca")
                risultati_grezzi = []
        else:
            print(f"  → Errore: {search_result}")
            risultati_grezzi = []

        if not risultati_grezzi:
            print("[INFO] Nessun risultato dalla ricerca. Genero il sito con lo storico esistente.")
            # Genera comunque index.html e dettaglio.html
            full_html = genera_html_completo(storico)
            with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
                f.write(full_html)
            print(f"[OK] {OUTPUT_HTML} generato con libri dello storico.")
            genera_dettaglio_html(storico)
            print("[OK] dettaglio.html aggiornato.")
            return

        # 2b. Filtra con DeepSeek
        print("\n[FASE 2] Filtro qualità con DeepSeek (V4 Flash + Pro)...")
        filter_result = mcp.call_tool("filtra_con_deepseek", {
            "risultati_grezzi": risultati_grezzi,
            "storico": storico,
        })

        if filter_result and "result" in filter_result:
            content = filter_result["result"].get("content", [])
            if content and content[0].get("type") == "json":
                filter_data = content[0]["json"]
                libri_filtrati = filter_data.get("libri", [])
                scartati = filter_data.get("scartati", [])
                riepilogo = filter_data.get("riepilogo", "")
                print(f"  → {len(libri_filtrati)} libri selezionati da DeepSeek")
                if scartati:
                    print(f"  → {len(scartati)} elementi scartati")
                    for s in scartati[:5]:
                        print(f"    - {s}")
                if riepilogo:
                    print(f"  → Riepilogo: {riepilogo[:200]}...")
            else:
                print("  → Nessun contenuto valido nella risposta")
                libri_filtrati = []
        else:
            print(f"  → Errore: {filter_result}")
            libri_filtrati = []

        # 3. Filtra duplicati con storico
        print("\n[FASE 3] Filtro duplicati...")
        nuovi_libri_oggi = filtra_nuovi_libri(libri_filtrati, storico)

        # 4. Aggiungi i nuovi libri alla raccolta di oggi
        if nuovi_libri_oggi:
            print(f"\n[FASE 4] Aggiunta di {len(nuovi_libri_oggi)} nuovi libri alla raccolta di {DATA_STR}...")
            if DATA_STR not in storico["raccolte"]:
                storico["raccolte"][DATA_STR] = {"data_italiana": DATA_ITALIANA, "libri": []}
            storico["raccolte"][DATA_STR]["libri"].extend(nuovi_libri_oggi)

            # Riordina per data decrescente
            storico["raccolte"] = OrderedDict(
                sorted(storico["raccolte"].items(), key=lambda x: x[0], reverse=True)
            )
            salva_storico(storico)
        else:
            print("[INFO] Nessun libro nuovo oggi. Lo storico rimane invariato.")

    # 5. Genera index.html
    print("\n[FASE 5] Generazione index.html...")
    full_html = genera_html_completo(storico)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"[OK] {OUTPUT_HTML} generato con {sum(1 for _ in libri_totali_ordinati(storico))} libri totali.")

    # 6. Genera dettaglio.html
    print("\n[FASE 6] Generazione dettaglio.html...")
    genera_dettaglio_html(storico)
    print("[OK] dettaglio.html aggiornato.")

    if nuovi_libri_oggi:
        print(f"\n  ➕ {len(nuovi_libri_oggi)} NUOVI LIBRI OGGI:")
        for l in nuovi_libri_oggi:
            badge = l.get("premio") or l.get("fonte_recensione") or "Novità"
            print(f"    • {l.get('titolo_it', '?')} — {badge}")

    print("\n" + "=" * 60)
    print("  OPERAZIONE COMPLETATA CON SUCCESSO")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[SCRIPT] Interrotto dall'utente.")
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERRORE FATALE] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)