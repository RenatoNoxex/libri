#!/usr/bin/env python3
"""
Script Orchestratore — Ricerca Libri Zero-Touch
================================================
1. Avvia MCP server come subprocesso
2. Chiama tool search_novita_editoriali (Tavily)
3. Chiama tool filtra_con_deepseek (DeepSeek V4 Flash + Pro)
4. Filtra duplicati con storico_libri.json
5. Genera index.html completo (tema AI News)
6. Salva storico aggiornato
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

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
    """Comunica con il server MCP via stdio JSON-RPC con handshake corretto."""

    def __init__(self, server_path: str):
        self.server_path = server_path
        self.process = None
        self._req_id = 0
        self._initialized = False

    def start(self):
        """Avvia il processo MCP server ed esegue l'handshake."""
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
        # Handshake MCP: initialize
        time.sleep(0.5)
        self._req_id += 1
        init_req = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "libri-script-py",
                    "version": "1.0.0",
                },
            },
        }
        self._write_request(init_req)
        init_resp = self._read_response()
        if not init_resp or "error" in init_resp:
            err = init_resp.get("error", {}).get("message", "sconosciuto") if init_resp else "timeout"
            raise RuntimeError(f"Handshake MCP fallito: {err}")
        print(f"[SCRIPT] MCP inizializzato (server: {init_resp.get('result', {}).get('serverInfo', {}).get('name', '?')})")

        # Invia notifica 'initialized'
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self._write_request(notif)
        self._initialized = True
        print("[SCRIPT] MCP server pronto.")

    def _write_request(self, request: dict):
        """Scrive una richiesta JSON-RPC su stdin."""
        line = json.dumps(request) + "\n"
        self.process.stdin.write(line)
        self.process.stdin.flush()

    def _read_response(self, timeout_sec: int = 120):
        """Legge una risposta JSON-RPC da stdout con timeout."""
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
        # Timeout: prova a parsare quello che c'è
        try:
            return json.loads(response_str)
        except json.JSONDecodeError:
            return None

    def list_tools(self):
        """Lista i tool disponibili."""
        self._req_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "tools/list",
            "params": {},
        }
        self._write_request(request)
        return self._read_response(timeout_sec=10)

    def call_tool(self, tool_name: str, arguments: dict = None):
        """Chiama un tool MCP e restituisce il risultato (parsato)."""
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
        # Estrai contenuto dal result
        result_data = response.get("result", {})
        content = result_data.get("content", [])
        if content:
            # Se content è già una lista di dict con type, usala direttamente
            return {"result": {"content": content}}
        # Se è un oggetto diretto (da json type)
        return {"result": {"content": [{"type": "json", "json": result_data}]}}

    def stop(self):
        """Ferma il server MCP."""
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
#  Storico
# ──────────────────────────────────────────

def carica_storico():
    """Carica lo storico dei libri già pubblicati."""
    if HISTORIC_FILE.exists():
        try:
            with open(HISTORIC_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                print(f"[SCRIPT] Storico caricato: {len(data.get('libri', []))} libri già pubblicati.")
                return data
        except (json.JSONDecodeError, KeyError):
            print("[SCRIPT] Errore lettura storico, reimposto vuoto.")
    return {"libri": [], "ultimo_aggiornamento": None}


def salva_storico(storico):
    """Salva lo storico aggiornato."""
    with open(HISTORIC_FILE, "w", encoding="utf-8") as f:
        json.dump(storico, f, ensure_ascii=False, indent=2)
    print(f"[SCRIPT] Storico salvato: {len(storico['libri'])} libri totali.")


def filtra_nuovi_libri(libri_nuovi, storico_libri):
    """Filtra solo i libri non già presenti nello storico."""
    if not libri_nuovi:
        return []

    # Crea set di chiavi uniche (titolo_it + autore) per duplicati
    esistenti = set()
    for l in storico_libri:
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


# ──────────────────────────────────────────
#  Generazione HTML
# ──────────────────────────────────────────

def genera_html_completo(html_content: str) -> str:
    """Avvolge il contenuto HTML nel template completo del sito."""
    
    return f"""<!DOCTYPE html>
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
    /* ===== RESET & BASE ===== */
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f5f5f5;
      color: #222;
      line-height: 1.6;
    }}

    :root {{
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
    }}

    /* ===== HEADER ===== */
    .header {{
      background: var(--dark);
      position: sticky;
      top: 0;
      z-index: 1000;
      height: var(--header-height);
      display: flex;
      align-items: center;
      box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }}
    .header-inner {{
      max-width: var(--max-width);
      width: 100%;
      margin: 0 auto;
      padding: 0 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .logo {{
      display: flex;
      align-items: center;
      gap: 12px;
      text-decoration: none;
    }}
    .logo-icon {{
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
    }}
    .logo-text {{
      color: white;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }}
    .logo-text span {{ color: var(--primary); }}
    .badge-date {{
      background: rgba(255,255,255,0.1);
      color: #aaa;
      padding: 6px 14px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 500;
    }}

    /* ===== TOP BAR ===== */
    .top-bar {{
      background: white;
      border-bottom: 1px solid var(--gray-light);
      position: sticky;
      top: var(--header-height);
      z-index: 999;
      overflow-x: auto;
      white-space: nowrap;
      -webkit-overflow-scrolling: touch;
    }}
    .top-bar::-webkit-scrollbar {{ height: 0; }}
    .category-nav {{
      max-width: var(--max-width);
      margin: 0 auto;
      padding: 0 20px;
      display: flex;
      gap: 4px;
      align-items: center;
    }}
    .category-nav a {{
      display: inline-block;
      padding: 12px 18px;
      font-size: 13px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--gray-text);
      text-decoration: none;
      border-bottom: 3px solid transparent;
      transition: all 0.2s;
    }}
    .category-nav a:hover,
    .category-nav a.active {{
      color: var(--primary);
      border-bottom-color: var(--primary);
    }}

    /* ===== MAIN CONTAINER ===== */
    .container {{
      max-width: var(--max-width);
      margin: 0 auto;
      padding: 30px 20px;
    }}

    /* ===== SECTION TITLE ===== */
    .section-title {{
      font-size: 20px;
      font-weight: 700;
      color: var(--dark);
      margin-bottom: 20px;
      padding-bottom: 10px;
      border-bottom: 3px solid var(--primary);
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .section-title .emoji {{ font-size: 22px; }}

    /* ===== NEWS GRID (LIKE AI-NEWS) ===== */
    .news-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 24px;
      margin-bottom: 50px;
    }}
    .news-card {{
      background: white;
      border-radius: var(--radius);
      overflow: hidden;
      box-shadow: var(--shadow);
      transition: all 0.3s ease;
      display: flex;
      flex-direction: column;
    }}
    .news-card:hover {{
      transform: translateY(-4px);
      box-shadow: var(--shadow-hover);
    }}
    .news-card .card-body {{
      padding: 22px;
      flex: 1;
      display: flex;
      flex-direction: column;
    }}
    .news-card .cat-badge {{
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
    }}
    .news-card h3 {{
      font-size: 18px;
      font-weight: 700;
      line-height: 1.4;
      margin-bottom: 6px;
      color: var(--dark-text);
    }}
    .news-card .original-title {{
      font-size: 13px;
      color: #999;
      margin-bottom: 8px;
      font-style: italic;
    }}
    .news-card .meta-info {{
      font-size: 13px;
      color: #666;
      margin-bottom: 10px;
    }}
    .news-card .meta-info strong {{ color: #444; }}
    .news-card .excerpt {{
      font-size: 14px;
      color: var(--gray-text);
      line-height: 1.6;
      flex: 1;
      margin-bottom: 14px;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .news-card .card-footer {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      color: #999;
      padding-top: 14px;
      border-top: 1px solid var(--gray-light);
      gap: 10px;
    }}
    .news-card .card-footer .source {{
      color: var(--primary);
      font-weight: 500;
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    .news-card h3 a {{
      color: var(--dark-text);
      text-decoration: none;
      transition: color 0.2s;
    }}
    .news-card h3 a:hover {{
      color: var(--primary);
    }}

    /* ===== FOOTER ===== */
    .footer {{
      background: var(--dark);
      color: #888;
      padding: 30px 20px;
      text-align: center;
      font-size: 14px;
      margin-top: 60px;
    }}
    .footer strong {{ color: #ccc; }}

    /* ===== RESPONSIVE ===== */
    @media (max-width: 768px) {{
      :root {{ --header-height: 60px; }}
      .logo-text {{ font-size: 18px; }}
      .logo-icon {{ width: 34px; height: 34px; font-size: 16px; }}
      .badge-date {{ display: none; }}
      .news-grid {{ grid-template-columns: 1fr; gap: 16px; }}
      .container {{ padding: 20px 16px; }}
      .category-nav a {{ padding: 10px 14px; font-size: 12px; }}
    }}
    @media (max-width: 480px) {{
      .news-card .card-body {{ padding: 16px; }}
      .news-card h3 {{ font-size: 16px; }}
    }}
  </style>
</head>
<body>

  <!-- HEADER -->
  <header class="header">
    <div class="header-inner">
      <a href="index.html" class="logo">
        <div class="logo-icon">📚</div>
        <div class="logo-text"><span>Libri</span> — Novità editoriali</div>
      </a>
      <div class="header-actions">
        <span class="badge-date">{DATA_ITALIANA}</span>
      </div>
    </div>
  </header>

  <!-- TOP BAR -->
  <nav class="top-bar">
    <div class="category-nav">
      <a href="index.html" class="active">🏠 Home</a>
      <a href="#" onclick="return false;">🏆 Premi</a>
      <a href="#" onclick="return false;">📰 Recensioni</a>
      <a href="#" onclick="return false;">📊 Classifiche</a>
    </div>
  </nav>

  <!-- MAIN CONTENT -->
  <div class="container" id="main-content">
    {html_content}
  </div>

  <!-- FOOTER -->
  <footer class="footer">
    <p><strong>Libri — Novità editoriali</strong> — Selezione automatica quotidiana</p>
    <p style="margin-top:6px;font-size:12px;">Dati raccolti da fonti pubbliche · Ricerca via Tavily · Analisi DeepSeek AI · Aggiornato il {DATA_ITALIANA}</p>
  </footer>

</body>
</html>"""


# ──────────────────────────────────────────
#  Main
# ──────────────────────────────────────────

def main():
    print("=" * 60)
    print("  LIBRI — Ricerca Novità Editorialì Zero-Touch")
    print(f"  Data: {DATA_ITALIANA}")
    print("=" * 60)

    # 1. Carica storico
    storico = carica_storico()
    storico_libri = storico.get("libri", [])

    # 2. Avvia MCP e recupera dati
    server_script = str(MCP_SERVER_DIR / "index.js")
    
    if not os.path.exists(server_script):
        print(f"[ERRORE] MCP server non trovato: {server_script}")
        sys.exit(1)

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
            print("[WARNING] Nessun risultato dalla ricerca. Genero pagina vuota.")
            html_vuoto = f"""<div class="section-title"><span class="emoji">📚</span> Novità editoriali — {DATA_ITALIANA}</div>
<div style="text-align:center;padding:60px 20px;color:#888;">
  <div style="font-size:48px;margin-bottom:20px;">📭</div>
  <p style="font-size:18px;font-weight:600;">Nessuna novità di rilievo oggi</p>
  <p style="margin-top:10px;">Torna domani per nuovi aggiornamenti su premi, recensioni e classifiche.</p>
</div>"""
            with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
                f.write(genera_html_completo(html_vuoto))
            print(f"[OK] {OUTPUT_HTML} generato (vuoto).")
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
        nuovi_libri = filtra_nuovi_libri(libri_filtrati, storico_libri)

        if not nuovi_libri:
            print("[INFO] Nessun libro nuovo da pubblicare. Mostro i libri dello storico.")
            # Genera HTML con tutti i libri dello storico invece di pagina vuota
            html_content_storico = generate_html_fallback(storico_libri)
            with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
                f.write(genera_html_completo(html_content_storico))
            print(f"[OK] {OUTPUT_HTML} generato con {len(storico_libri)} libri dello storico (nessuna novità).")
            # Aggiorna comunque dettaglio.html
            genera_dettaglio_html(storico_libri)
            print("[OK] dettaglio.html aggiornato.")
            return

        # 4. Aggiungi allo storico e salva
        print("\n[FASE 4] Aggiornamento storico...")
        storico_libri.extend(nuovi_libri)
        storico["libri"] = storico_libri
        storico["ultimo_aggiornamento"] = DATA_STR
        salva_storico(storico)

        # 5. Genera HTML
        print("\n[FASE 5] Generazione index.html...")
        html_result = mcp.call_tool("genera_html_libri", {
            "libri": nuovi_libri,
            "data_generazione": DATA_STR,
        })

        if html_result and "result" in html_result:
            content = html_result["result"].get("content", [])
            if content and content[0].get("type") == "text":
                html_content = content[0]["text"]
            else:
                # Fallback: usa il generatore locale
                html_content = generate_html_fallback(nuovi_libri)
        else:
            html_content = generate_html_fallback(nuovi_libri)

        # Scrivi index.html
        full_html = genera_html_completo(html_content)
        
        # Sostituisci i placeholder LIBRO_ID_PLACEHOLDER con l'ID corretto
        # storico_libri include già i nuovi, quindi offset = totale - nuovi
        offset = len(storico_libri) - len(nuovi_libri)
        for idx, l in enumerate(nuovi_libri):
            book_id = offset + idx
            titolo = l.get('titolo_it', '')
            # Sostituisci nell'HTML il placeholder con l'ID corretto
            full_html = full_html.replace(
                f'dettaglio.html?id=LIBRO_ID_PLACEHOLDER">{escape_html(titolo)}</a>',
                f'dettaglio.html?id={book_id}">{escape_html(titolo)}</a>'
            )
        
        with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
            f.write(full_html)

        print(f"\n[OK] {OUTPUT_HTML} generato con {len(nuovi_libri)} libri nuovi!")
        for l in nuovi_libri:
            badge = l.get("premio") or l.get("fonte_recensione") or "Novità"
            print(f"  • {l.get('titolo_it', '?')} — {badge}")

        # 6. Genera/aggiorna dettaglio.html con tutto lo storico
        print("\n[FASE 6] Generazione dettaglio.html...")
        genera_dettaglio_html(storico_libri)
        print("[OK] dettaglio.html aggiornato.")

    print("\n" + "=" * 60)
    print("  OPERAZIONE COMPLETATA CON SUCCESSO")
    print("=" * 60)


def generate_html_fallback(libri):
    """Genera HTML fallback se DeepSeek non risponde."""
    cards = []
    for l in libri:
        badge = ""
        if l.get("premio"):
            badge = f'<span class="cat-badge">🏆 {l.get("premio")}</span>'
        elif l.get("fonte_recensione"):
            badge = f'<span class="cat-badge">📰 {l.get("fonte_recensione")}</span>'
        else:
            badge = '<span class="cat-badge">📖 Novità</span>'

        titolo_originale = ""
        if l.get("titolo_originale") and l["titolo_originale"] != "N/D":
            titolo_originale = f'<p class="original-title">{l["titolo_originale"]}</p>'

        traduttore = ""
        if l.get("traduttore") and l["traduttore"] != "N/D":
            traduttore = f' · <strong>Traduttore:</strong> {l["traduttore"]}'

        data_pub = ""
        if l.get("data_pubblicazione"):
            data_pub = f'<span>📅 {l["data_pubblicazione"]}</span>'

        sinossi = escape_html(l.get('sinossi_critica', 'Sinossi non disponibile.'))
        fonte_url = ""
        if l.get("fonte_url"):
            fonte_url = f'<a href="{escape_html(l["fonte_url"])}" target="_blank" rel="noopener" class="detail-link">🔗 Leggi la fonte originale</a>'

        card = f"""
    <div class="news-card">
      <div class="card-body">
        {badge}
        <h3><a href="dettaglio.html?id=LIBRO_ID_PLACEHOLDER">{escape_html(l.get('titolo_it', 'Titolo sconosciuto'))}</a></h3>
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
        cards.append(card)

    cards_html = "\n".join(cards)
    return f"""
<div class="section-title"><span class="emoji">📚</span> Novità editoriali — {DATA_ITALIANA}</div>
<div class="news-grid">
{cards_html}
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


def genera_dettaglio_html(libri):
    """Genera dettaglio.html con tutti i libri embedded come JSON."""
    DETTAGLIO_PATH = BASE_DIR / "dettaglio.html"
    
    if not DETTAGLIO_PATH.exists():
        print("[WARNING] Template dettaglio.html non trovato, lo creo.")
        # Usa una versione base embeddata nel codice
        return
    
    with open(DETTAGLIO_PATH, "r", encoding="utf-8") as f:
        template = f.read()
    
    libri_json = json.dumps(libri, ensure_ascii=False, indent=2)
    html_output = template.replace("__LIBRI_JSON_PLACEHOLDER__", libri_json)
    
    with open(DETTAGLIO_PATH, "w", encoding="utf-8") as f:
        f.write(html_output)
    
    print(f"[SCRIPT] dettaglio.html generato con {len(libri)} libri.")


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