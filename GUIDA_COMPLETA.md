# Libri — Guida Completa al Progetto

> 📅 Ultimo aggiornamento: 10/06/2026
> Versione: 3.0.0
> Progetto: Ricerca automatica quotidiana di novità editoriali di alto profilo — 3 sezioni distinte

---

## 1. COS'È QUESTO PROGETTO

Workflow automatico **Zero-Touch** che ogni giorno:
1. **Cerca** sul web (via Tavily API + RSS feed culturali) novità editoriali in 3 categorie: Premi, Recensioni, Classifiche
2. **Filtra** con DeepSeek AI (V4 Flash + V4 Pro/Reasoner) scartando self-publishing e libri di basso profilo
3. **Elimina duplicati** usando uno storico persistente (`storico_libri.json`)
4. **Genera** una pagina `index.html` con **3 sezioni distinte** (🏆 Premi, 📰 Recensioni, 📊 Classifiche) navigabili via tab
5. **Pubblica** su server Aruba via FTP

**URL live:** https://www.exmu.it/libri/

---

## 2. STRUTTURA DEL PROGETTO

```
libri/
│
├── index.html              ← HOME PAGE GENERATA (output dello script)
├── dettaglio.html          ← Pagina dettaglio libro (template + dati embedded)
├── script.py               ← Orchestratore Python v3.0 (avvia MCP, coordina fasi)
├── requirements.txt        ← Dipendenze Python (solo librerie standard)
├── storico_libri.json      ← Database storico (generato automaticamente)
├── GUIDA_COMPLETA.md       ← QUESTO FILE
├── deploy.bat              ← Deploy manuale FTP (per test locale)
├── .gitignore
│
├── mcp-server/             ← MCP Server (Node.js) v3.0
│   ├── package.json        ← Dipendenze: @tavily/core, openai, @modelcontextprotocol/sdk, dotenv, zod
│   ├── index.js            ← Server con 3 tool MCP + RSS feed fetcher
│   └── node_modules/       ← Installato con npm install
│
└── .github/
    └── workflows/
        └── main.yml        ← GitHub Actions: cron 06:00 + FTP Deploy
```

---

## 3. ARCHITETTURA: FLUSSO DI LAVORO v3.0

```
Ogni giorno alle 06:00 (GitHub Actions cron)
         │
         ▼
┌─────────────────────────────────────────────┐
│  1. script.py avvia MCP server (subprocess) │
│     mcp-server/index.js v3.0                │
└─────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────┐
│  2. Tool: search_novita_editoriali          │
│     Tavily API (84 query) + 6 RSS feed:     │
│     • 20 query Premi (Strega, Campiello,    │
│       Pulitzer, Booker, Nobel, Goncourt...)  │
│     • 22 query Recensioni (dork per         │
│       La Lettura, Robinson, Tuttolibri,     │
│       Domenica, critica letteraria)         │
│     • 30 query Classifiche (IBS,            │
│       Feltrinelli, Mondadori, Amazon,       │
│       GFK/Arianna, Giornale Libreria)       │
│     • 12 query Aggregatori (Goodreads,      │
│       Anobii, rassegna stampa editori,      │
│       bookstagram/booktok)                  │
│     • RSS: Corriere, Repubblica, La         │
│       Stampa, Sole 24 Ore, Anobii,          │
│       Goodreads                             │
└─────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────┐
│  3. Tool: filtra_con_deepseek               │
│     DeepSeek V4 Flash (primo passaggio)     │
│     + V4 Pro/Reasoner (approfondimento)     │
│     → Scarta self-publishing               │
│     → Scarta saggistica commerciale          │
│     → Scarta usciti >12 mesi                │
│     → Estrae: titolo, autore, editore,      │
│       traduttore, sinossi, motivazione      │
│     → Tagga sezione: premi, recensioni,     │
│       classifiche (anche multiple)          │
└─────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────┐
│  4. script.py: Filtro duplicati locale      │
│     Confronta con storico_libri.json        │
│     → Solo libri nuovi nello storico        │
└─────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────┐
│  5. script.py: Genera index.html            │
│     3 sezioni distinte con tabs:            │
│     🏆 Premi | 📰 Recensioni | 📊 Classifiche│
│     + Hero card, grid cards, placeholder    │
│     + Navigazione per data, ricerca globale │
│     + Stats bar per sezione                 │
└─────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────┐
│  6. FTP Deploy su Aruba                     │
│     Python inline script in GitHub Actions  │
│     → Carica index.html + dettaglio.html     │
└─────────────────────────────────────────────┘
```

---

## 4. LE 3 SEZIONI DELLA DASHBOARD

| Sezione | Emoji | Fonti | Colore |
|---------|-------|-------|--------|
| **Premi** | 🏆 | Strega, Strega Europeo, Campiello, Booker, Pulitzer, Nobel, Goncourt, Viareggio, Bagutta, Brancati, Flaiano, Calvino, Stresa | Giallo `#f59e0b` |
| **Recensioni** | 📰 | La Lettura, Robinson, Tuttolibri, Domenica, RSS feed, aggregatori letterari | Blu `#3b82f6` |
| **Classifiche** | 📊 | IBS, Feltrinelli, Mondadori Store, Amazon, GFK/Arianna, Giornale della Libreria | Verde `#10b981` |

Ogni sezione ha:
- **Tab colorato** con contatore
- **Hero card** per il primo libro (sfondo dark gradient)
- **Grid cards** per i successivi
- **Placeholder elegante** se la sezione è vuota (con descrizione della fonte)

---

## 5. FONTI MONITORATE (84 query + 6 RSS feed)

### Premi (20 query)
Premio Strega, Strega Europeo, Campiello, Pulitzer, Booker, Nobel, Goncourt, Bagutta, Viareggio, Brancati, Flaiano, Grinzane, Calvino, Stresa, National Book Award, International Booker

### Recensioni (22 query)
- Dork Google: `site:corriere.it "La Lettura"`, `site:repubblica.it "Robinson"`, `site:lastampa.it "Tuttolibri"`, `site:ilsole24ore.com "Domenica"`
- Query generali per recensioni, critica letteraria, rassegna stampa
- RSS feed culturali da Corriere, Repubblica, La Stampa, Sole 24 Ore

### Classifiche (30 query + 12 aggregatori)
- IBS, Feltrinelli, Mondadori Store, Amazon (multiple per mese)
- GFK/Arianna, Giornale della Libreria
- Classifiche per genere: narrativa contemporanea, romanzo storico, thriller, nord europea, giapponese
- Aggregatori: Goodreads, Anobii, rassegna stampa editori (Einaudi, Mondadori, Feltrinelli, Bompiani, Adelphi, Neri Pozza, Iperborea)
- Social: bookstagram, booktok, blog letterari

### RSS Feed (6)
Corriere della Sera, La Repubblica, La Stampa, Il Sole 24 Ore, Anobii, Goodreads

---

## 6. TECNOLOGIE UTILIZZATE

| Componente | Tecnologia |
|------------|------------|
| Ricerca web | **Tavily API** (84 query mirate per sessione) |
| RSS Feed | **Node.js native http/https** (parsing XML senza librerie esterne) |
| AI/Analisi | **DeepSeek API** (deepseek-chat V4 Flash + deepseek-reasoner V4 Pro) |
| MCP Server | **Node.js** + @modelcontextprotocol/sdk v0.6.0 |
| Orchestratore | **Python 3** (solo librerie standard) |
| Workflow CI/CD | **GitHub Actions** (cron 06:00 Italia) |
| FTP Deploy | **Python ftplib + FTP_TLS** (SSL implicita) |
| Hosting | **Aruba** (ftp.exmu.it) |

---

## 7. GITHUB SECRETS DA CONFIGURARE

Prima di attivare il workflow, impostare nei **Settings → Secrets and variables → Actions** del repository GitHub:

| Secret | Descrizione | Esempio |
|--------|-------------|---------|
| `DEEPSEEK_API_KEY` | Chiave API DeepSeek | `sk-...` |
| `TAVILY_API_KEY` | Chiave API Tavily | `tvly-...` |
| `FTP_HOST` | Host FTP Aruba | `ftp.exmu.it` |
| `FTP_USER` | Utente FTP Aruba | `1274854@aruba.it` |
| `FTP_PASS` | Password FTP Aruba | `********` |
| `FTP_TARGET_DIR` | Cartella remota | `www.exmu.it/libri` |

---

## 8. COMANDI RAPIDI

```bash
# Installare dipendenze MCP server
cd mcp-server
npm install

# Eseguire lo script manualmente (locale)
# Imposta prima le variabili d'ambiente:
# Windows CMD:
set DEEPSEEK_API_KEY=sk-...
set TAVILY_API_KEY=tvly-...
python script.py

# Windows PowerShell:
$env:DEEPSEEK_API_KEY="sk-..."
$env:TAVILY_API_KEY="tvly-..."
python script.py

# Avviare il server MCP in modalità standalone (test)
cd mcp-server
node index.js

# Test sintassi Python
python -c "import py_compile; py_compile.compile('script.py', doraise=True); print('OK')"

# Test sintassi Node.js
node --check mcp-server/index.js

# Test rapido struttura
python -c "
from pathlib import Path
p = Path('.')
for f in ['script.py', 'requirements.txt', '.github/workflows/main.yml', 'mcp-server/index.js', 'mcp-server/package.json']:
    print(f'✓ {f}' if Path(f).exists() else f'✗ {f}")
```

---

## 9. ESECUZIONE LOCALE (TEST)

Per testare localmente senza GitHub Actions:

1. **Installa dipendenze Node.js:**
   ```bash
   cd mcp-server
   npm install
   ```

2. **Imposta le variabili d'ambiente:**
   ```bash
   # Windows CMD:
   set DEEPSEEK_API_KEY=sk-...
   set TAVILY_API_KEY=tvly-...
   
   # Windows PowerShell:
   $env:DEEPSEEK_API_KEY="sk-..."
   $env:TAVILY_API_KEY="tvly-..."
   ```

3. **Esegui lo script:**
   ```bash
   python script.py
   ```

4. **Apri index.html generato** nel browser per vedere il risultato.

---

## 10. FILE GENERATI AUTOMATICAMENTE

| File | Generato da | Descrizione |
|------|-------------|-------------|
| `index.html` | `script.py` | Dashboard con 3 sezioni (Premi, Recensioni, Classifiche) |
| `dettaglio.html` | `script.py` | Pagina dettaglio con dati embedded di tutti i libri |
| `storico_libri.json` | `script.py` | Database storico libri pubblicati (formato raccolte per data) |

---

## 11. STRUTTURA JSON LIBRO

```json
{
  "titolo_it": "Titolo in italiano",
  "titolo_originale": "Titolo originale",
  "autore": "Nome Autore",
  "editore": "Casa Editrice Italiana",
  "traduttore": "Nome Traduttore",
  "sezione": "premi,recensioni",
  "sinossi_critica": "Sinossi di 2-3 frasi...",
  "motivazione_inclusione": "Vincitore Premio Strega 2026",
  "premio": "Premio Strega",
  "fonte_recensione": "Robinson",
  "data_pubblicazione": "2025-09",
  "copertina_url": null,
  "fonte_url": "https://..."
}
```

Il campo `sezione` può contenere valori multipli separati da virgola (es. `"premi,classifiche"`).

---

## 12. DEBUG E RISOLUZIONE PROBLEMI

### MCP server non si avvia
```bash
cd mcp-server
npm install
node --check index.js   # verifica sintassi
node index.js            # test standalone
```

### Tavily non restituisce risultati
```bash
# Test manuale API
curl -X POST https://api.tavily.com/search \
  -H "Content-Type: application/json" \
  -d '{"api_key":"tvly-...", "query":"vincitore Premio Strega 2026", "search_depth":"advanced"}'
```

### DeepSeek non risponde
```bash
# Test chiamata API
curl https://api.deepseek.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-..." \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Test"}],"response_format":{"type":"json_object"}}'
```

### RSS Feed vuoti / timeout
Gli RSS feed hanno timeout di 15 secondi e try/catch indipendenti. Se falliscono, il resto della ricerca prosegue. Verifica manualmente gli URL RSS nel browser.

### FTP deploy fallisce
- Verificare che i secrets GitHub siano impostati correttamente
- Verificare che la cartella remota esista su Aruba
- Aruba richiede il path completo senza slash iniziale: `www.exmu.it/libri`

---

## 13. MODIFICARE LO STILE GRAFICO

Lo stile CSS è **inline** in `script.py` nella costante `CSS_TEMPLATE`.

Per cambiare i colori delle sezioni, modificare le variabili CSS:
```css
--premi-color: #f59e0b;
--recensioni-color: #3b82f6;
--classifiche-color: #10b981;
```

---

## 14. ESTENDERE LE QUERY DI RICERCA

Le query sono in `mcp-server/index.js`, costanti:
- `PREMI_QUERIES` — 20 query per premi letterari
- `RECENSIONI_QUERIES` — 22 query per recensioni e dork
- `CLASSIFICHE_QUERIES` — 30 query per classifiche
- `AGGREGATORI_QUERIES` — 12 query per aggregatori e case editrici
- `RSS_FEEDS` — 6 feed RSS da testate e aggregatori

Per aggiungere nuove fonti, aggiungere stringhe alle liste appropriate.

---

## 15. STORICO VERSIONI

| Versione | Data | Modifiche |
|----------|------|-----------|
| 1.0.0 | 09/06/2026 | Prima release: ricerca base con Tavily + DeepSeek |
| 2.0.0 | 10/06/2026 | Query ampliate, prompt meno restrittivo, tag sezione |
| 3.0.0 | 10/06/2026 | **3 sezioni distinte** (Premi/Recensioni/Classifiche), RSS feed, 84 query totali, placeholder eleganti, hero card per sezione, stats bar, colori differenziati per sezione |

---

*Fine guida — per modifiche, aggiorna questo file.*