# Libri — Guida Completa al Progetto

> 📅 Ultimo aggiornamento: 09/06/2026
> Progetto: Ricerca automatica quotidiana di novità editoriali di alto profilo

---

## 1. COS'È QUESTO PROGETTO

Workflow automatico **Zero-Touch** che ogni giorno:
1. **Cerca** sul web (via Tavily API) novità editoriali: vincitori/finalisti di premi letterari, recensioni da inserti culturali italiani, bestseller di narrativa straniera tradotta
2. **Filtra** con DeepSeek AI (V4 Flash + V4 Pro/Reasoner) scartando self-publishing e libri di basso profilo
3. **Elimina duplicati** usando uno storico persistente (`storico_libri.json`)
4. **Genera** una pagina `index.html` elegante (tema scuro #1a1a2e, rosso #e50914)
5. **Pubblica** su server Aruba via FTP

**URL live:** https://www.exmu.it/libri/

---

## 2. STRUTTURA DEL PROGETTO

```
libri/
│
├── index.html              ← HOME PAGE GENERATA (output dello script)
├── script.py               ← Orchestratore Python (avvia MCP, coordina fasi)
├── requirements.txt        ← Dipendenze Python (solo librerie standard)
├── storico_libri.json      ← Database duplicati (generato automaticamente)
├── GUIDA_COMPLETA.md       ← QUESTO FILE
├── .gitignore
│
├── mcp-server/             ← MCP Server (Node.js)
│   ├── package.json        ← Dipendenze: @tavily/core, openai, @modelcontextprotocol/sdk
│   ├── index.js            ← Server con 3 tool MCP
│   └── node_modules/       ← Installato con npm install
│
└── .github/
    └── workflows/
        └── main.yml        ← GitHub Actions: cron 06:00 + FTP Deploy
```

---

## 3. ARCHITETTURA: FLUSSO DI LAVORO

```
Ogni giorno alle 06:00 (GitHub Actions cron)
         │
         ▼
┌─────────────────────────────────────────────┐
│  1. script.py avvia MCP server (subprocess) │
│     mcp-server/index.js                     │
└─────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────┐
│  2. Tool: search_novita_editoriali          │
│     Tavily API → cerca su web:             │
│     • Vincitori/finalisti: Strega,          │
│       Strega Europeo, Campiello,            │
│       Booker, Pulitzer, Nobel               │
│     • Recensioni: La Lettura, Robinson,     │
│       Tuttolibri, Domenica Sole 24 Ore      │
│     • Classifiche: IBS, Mondadori           │
└─────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────┐
│  3. Tool: filtra_con_deepseek               │
│     DeepSeek V4 Flash (primo passaggio)     │
│     + V4 Pro/Reasoner (approfondimento)     │
│     → Scarta self-publishing               │
│     → Scarta libri senza recensioni         │
│     → Scarta usciti >12 mesi                │
│     → Scarta duplicati (storico)            │
│     → Estrae: titolo, autore, editore,      │
│       traduttore, sinossi, motivazione      │
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
│  5. Tool: genera_html_libri                 │
│     + script.py genera index.html completo  │
│     Tema scuro #1a1a2e, rosso #e50914       │
│     Card con badge premio, metadati,        │
│     sinossi, motivazione inclusione         │
└─────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────┐
│  6. FTP Deploy su Aruba                     │
│     SamKirkland/FTP-Deploy-Action@v4.3.0    │
│     → Carica index.html su server-dir       │
└─────────────────────────────────────────────┘
```

---

## 4. PREMI E FONTI MONITORATI

| Categoria | Dettaglio |
|-----------|-----------|
| 🏆 **Premi Letterari** | Premio Strega, Strega Europeo, Campiello, Booker Prize, Pulitzer (narrativa tradotta), Nobel Letteratura |
| 📰 **Inserti Culturali** | La Lettura (Corriere), Robinson (Repubblica), Tuttolibri (Stampa), Domenica (Il Sole 24 Ore) |
| 📊 **Classifiche** | IBS, Mondadori, Amazon bestseller narrativa straniera |

---

## 5. TECNOLOGIE UTILIZZATE

| Componente | Tecnologia |
|------------|------------|
| Ricerca web | **Tavily API** (20 query mirate per sessione) |
| AI/Analisi | **DeepSeek API** (deepseek-chat V4 Flash + deepseek-reasoner V4 Pro) |
| MCP Server | **Node.js** + @modelcontextprotocol/sdk |
| Orchestratore | **Python 3** (solo librerie standard) |
| Workflow CI/CD | **GitHub Actions** (cron 06:00 Italia) |
| FTP Deploy | **SamKirkland/FTP-Deploy-Action@v4.3.0** |
| Hosting | **Aruba** (ftp.exmu.it) |

---

## 6. GITHUB SECRETS DA CONFIGURARE

Prima di attivare il workflow, impostare nei **Settings → Secrets and variables → Actions** del repository GitHub:

| Secret | Descrizione | Esempio |
|--------|-------------|---------|
| `DEEPSEEK_API_KEY` | Chiave API DeepSeek | `sk-6340d596aab9495984c27a86420a9b6b` |
| `TAVILY_API_KEY` | Chiave API Tavily | `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` |
| `FTP_HOST` | Host FTP Aruba | `ftp.exmu.it` |
| `FTP_USER` | Utente FTP Aruba | `1274854@aruba.it` |
| `FTP_PASS` | Password FTP Aruba | `4Ba34qaq!!` |
| `FTP_TARGET_DIR` | Cartella remota | `/www.exmu.it/libri` |

---

## 7. COMANDI RAPIDI

```bash
# Installare dipendenze MCP server
cd mcp-server
npm install

# Eseguire lo script manualmente (locale)
# Imposta prima le variabili d'ambiente:
set DEEPSEEK_API_KEY=sk-...
set TAVILY_API_KEY=tvly-...
python script.py

# Avviare il server MCP in modalità standalone (test)
cd mcp-server
node index.js

# Generare un file storico_libri.json vuoto
echo {} > storico_libri.json

# Test rapido struttura
python -c "
from pathlib import Path
p = Path('.')
for f in ['script.py', 'requirements.txt', '.github/workflows/main.yml', 'mcp-server/index.js', 'mcp-server/package.json']:
    print(f'✓ {f}' if Path(f).exists() else f'✗ {f}")
```

---

## 8. ESECUZIONE LOCALE (TEST)

Per testare localmente senza GitHub Actions:

1. **Installa dipendenze Node.js:**
   ```bash
   cd mcp-server
   npm install
   ```

2. **Imposta le variabili d'ambiente:**
   ```bash
   # Su Windows (PowerShell)
   $env:DEEPSEEK_API_KEY="sk-..."
   $env:TAVILY_API_KEY="tvly-..."
   
   # Su Windows (CMD)
   set DEEPSEEK_API_KEY=sk-...
   set TAVILY_API_KEY=tvly-...
   ```

3. **Esegui lo script:**
   ```bash
   python script.py
   ```

4. **Apri index.html generato** nel browser per vedere il risultato.

---

## 9. CREDENZIALI FTP

Le credenziali sono le stesse del progetto `ai-news`:

```
FTP_HOST=ftp.exmu.it
FTP_USER=1274854@aruba.it
FTP_TARGET_DIR=/www.exmu.it/libri
```

> ⚠️ La password FTP va inserita come GitHub Secret (`FTP_PASS`), mai nel codice.

---

## 10. FILE GENERATI AUTOMATICAMENTE

| File | Generato da | Descrizione |
|------|-------------|-------------|
| `index.html` | `script.py` | Pagina web con novità editoriali |
| `storico_libri.json` | `script.py` | Database storico libri pubblicati |

---

## 11. DEBUG E RISOLUZIONE PROBLEMI

### MCP server non si avvia
```bash
cd mcp-server
npm install
node index.js
# Se funziona in standalone, il problema è nel subprocess Python
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

### FTP deploy fallisce
- Verificare che i secrets GitHub siano impostati correttamente
- Verificare che la cartella remota esista su Aruba
- Aruba richiede il path completo: `/www.exmu.it/libri/`

### Cache Aruba
Aruba ha una cache aggressiva. Dopo il deploy:
- Aggiungere `?v=2` ai CSS/JS (già inline nel progetto, nessun problema)
- Premere Ctrl+F5 nel browser per forzare refresh

---

## 12. MODIFICARE LO STILE GRAFICO

Lo stile CSS è **inline** in `script.py` nella funzione `genera_html_completo()`.

Per cambiare i colori, modificare le variabili CSS nella sezione `:root`:
```css
:root {
  --primary: #e50914;       /* Rosso principale */
  --primary-dark: #b20710;  /* Rosso scuro hover */
  --dark: #1a1a2e;         /* Sfondo header/footer */
  --gray-bg: #f5f5f5;      /* Sfondo pagina */
}
```

---

## 13. ESTENDERE LE QUERY DI RICERCA

Le query di ricerca sono in `mcp-server/index.js`, variabili:
- `PREMI_QUERIES` — query per premi letterari
- `RECENSIONI_QUERIES` — query per recensioni e classifiche

Per aggiungere nuove fonti, basta aggiungere una stringa alla lista appropriata.

---

## 14. STORICO BUG NOTI

| Data | Problema | Soluzione |
|------|----------|-----------|
| 09/06 | @tavily/core versione errata | Corretto da ^0.1.0 a ^0.7.5 |
| 09/06 | Tavily import errato (TavilyClient) | Corretto con factory function tavily() |
| 09/06 | Entità HTML nel replace | Usato .concat() per evitare sintassi escaped |

---

*Fine guida — per modifiche, aggiorna questo file.*