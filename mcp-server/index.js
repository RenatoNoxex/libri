#!/usr/bin/env node

/**
 * MCP Server — Ricerca Novità Editorialì
 * 
 * Tools esposti:
 *   1. search_novita_editoriali  — cerca novità con Tavily
 *   2. filtra_con_deepseek       — analisi e filtro qualità con DeepSeek
 *   3. genera_html_libri         — genera blocco HTML contenuto libri
 * 
 * Chiamata stdio transport (MCP nativo).
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { tavily } from "@tavily/core";
import OpenAI from "openai";
import { z } from "zod";
import dotenv from "dotenv";
import { readFileSync, existsSync } from "fs";

dotenv.config();

// ──────────────────────────────────────────
//  Config
// ──────────────────────────────────────────

const DEEPSEEK_API_KEY = process.env.DEEPSEEK_API_KEY || "";
const TAVILY_API_KEY = process.env.TAVILY_API_KEY || "";

if (!DEEPSEEK_API_KEY) {
  console.error("ERRORE: DEEPSEEK_API_KEY non impostata");
  process.exit(1);
}
if (!TAVILY_API_KEY) {
  console.error("ERRORE: TAVILY_API_KEY non impostata");
  process.exit(1);
}

// Tavily client
const tavilyClient = tavily({ apiKey: TAVILY_API_KEY });

// DeepSeek client (compatibile OpenAI)
const deepseek = new OpenAI({
  apiKey: DEEPSEEK_API_KEY,
  baseURL: "https://api.deepseek.com",
});

const DEEPSEEK_FLASH = "deepseek-chat";       // V4 Flash
const DEEPSEEK_PRO  = "deepseek-reasoner";    // V4 Pro/Reasoner

// ──────────────────────────────────────────
//  Helpers
// ──────────────────────────────────────────

function getCurrentDateString() {
  return new Date().toISOString().split("T")[0]; // YYYY-MM-DD
}

function formatDateItalian(dateStr) {
  if (!dateStr) return "";
  const [y, m, d] = dateStr.split("-");
  return `${d}/${m}/${y}`;
}

// ──────────────────────────────────────────
//  Tool 1: search_novita_editoriali
// ──────────────────────────────────────────

const PREMI_QUERIES = [
  "vincitore Premio Strega 2026 libro narrativa",
  "vincitore Premio Strega Europeo 2026 libro tradotto",
  "vincitore Premio Campiello 2026 libro",
  "finalisti Premio Strega 2026 libri",
  "finalisti Premio Campiello 2026 libri",
  "Pulitzer Prize for Fiction 2025 2026 traduzione italiana",
  "Booker Prize 2025 2026 vincitore traduzione italiano",
  "Nobel Letteratura 2025 2026 libri tradotti italiano",
  "Premio Strega 2025 libri vincitori",
  "Premio Strega Europeo 2025 vincitore",
];

const RECENSIONI_QUERIES = [
  "recensioni libri La Lettura Corriere della Sera 2026",
  "recensioni libri Robinson Repubblica 2026",
  "recensioni libri Tuttolibri La Stampa 2026",
  "recensioni libri Domenica Il Sole 24 Ore 2026",
  "classifiche narrativa straniera IBS 2026",
  "bestseller narrativa straniera Mondadori 2026",
  "classifiche libri più venduti narrativa tradotta 2026",
];

async function searchTavily(query) {
  try {
    const result = await tavilyClient.search(query, {
      searchDepth: "advanced",
      maxResults: 5,
      includeAnswer: true,
    });
    return result.results || [];
  } catch (err) {
    console.error(`Tavily error for "${query}": ${err.message}`);
    return [];
  }
}

async function search_novita_editoriali() {
  console.error("[MCP] search_novita_editoriali: avvio ricerca Tavily...");

  const allResults = [];

  // Query premi
  for (const q of PREMI_QUERIES) {
    const res = await searchTavily(q);
    allResults.push(...res.map(r => ({ ...r, query_type: "premio", query: q })));
  }

  // Query recensioni
  for (const q of RECENSIONI_QUERIES) {
    const res = await searchTavily(q);
    allResults.push(...res.map(r => ({ ...r, query_type: "recensione", query: q })));
  }

  // Ricerca supplementare su libri recenti premiati
  const extraQueries = [
    "nuove uscite narrativa straniera tradotta 2026 Italia",
    "libri tradotti dall'inglese pubblicati 2026 Italia recensioni",
    "migliori libri stranieri 2026 tradotti italiano premi letterari",
  ];
  for (const q of extraQueries) {
    const res = await searchTavily(q);
    allResults.push(...res.map(r => ({ ...r, query_type: "extra", query: q })));
  }

  // Filtra duplicati per URL
  const seen = new Set();
  const unique = allResults.filter(r => {
    if (!r.url || seen.has(r.url)) return false;
    seen.add(r.url);
    return true;
  });

  console.error(`[MCP] search_novita_editoriali: ${unique.length} risultati unici`);

  return {
    risultati: unique.slice(0, 60), // max 60
    data_ricerca: getCurrentDateString(),
    totale_grezzi: unique.length,
  };
}

// ──────────────────────────────────────────
//  Tool 2: filtra_con_deepseek
// ──────────────────────────────────────────

const FILTRO_SYSTEM_PROMPT = `Sei un critico letterario esperto. Devi analizzare risultati di ricerca web su novità editoriali.

REGOLE TASSATIVE:
1. SCARTA qualsiasi libro autopubblicato, self-publishing, o senza editore riconosciuto.
2. SCARTA libri senza recensioni rilevanti o di basso profilo.
3. SCARTA libri pubblicati da più di 12 mesi dalla data odierna.
4. SCARTA duplicati già presenti nello storico fornito (confronta per Titolo + Autore).
5. INCLUDI solo libri di alto profilo: vincitori/finalisti di premi letterari, recensiti da inserti culturali italiani (La Lettura, Robinson, Tuttolibri, Domenica Sole 24 Ore), o bestseller nelle classifiche dei grandi distributori.
6. Per ogni libro selezionato, estrai TUTTI questi campi in italiano:
   - titolo_it: titolo italiano
   - titolo_originale: titolo originale (se noto, altrimenti "N/D")
   - autore: nome autore
   - editore: casa editrice italiana
   - traduttore: nome traduttore (se noto, altrimenti "N/D")
   - sinossi_critica: sinossi critica di 2-3 frasi, non banale
   - motivazione_inclusione: motivo preciso (es. "Vincitore Premio Strega 2026", "Recensito su Robinson – la Repubblica del 01/06/2026", "Finalista Booker Prize 2025, tradotto da Einaudi")
   - premio: nome premio se collegato (es. "Premio Strega", "Booker Prize", "Pulitzer", "Nobel" o null)
   - fonte_recensione: nome inserto/giornale se recensito (es. "La Lettura", "Robinson", "Tuttolibri", "Domenica - Il Sole 24 Ore" o null)
   - data_pubblicazione: data di uscita del libro in Italia (se nota, formato YYYY-MM, altrimenti null)
   - copertina_url: URL immagine copertina se trovato, altrimenti null

Rispondi SOLO con un JSON valido nella forma:
{
  "libri": [{ ... }],
  "scartati": ["motivo scarto 1", "motivo scarto 2"],
  "riepilogo": "Breve riassunto dell'analisi"
}`;

async function filtra_con_deepseek(risultatiGrezzi, storico) {
  console.error("[MCP] filtra_con_deepseek: invio a DeepSeek V4 Flash (primo passaggio)...");

  // Prepara input per DeepSeek — testi dei risultati
  const testiInput = risultatiGrezzi.map((r, i) => {
    return `[${i + 1}] Titolo: ${r.title || "N/D"}\nFonte: ${r.url || "N/D"}\nSnippet: ${r.content || r.answer || "N/D"}\nTipo: ${r.query_type || "web"}\n`;
  }).join("\n---\n");

  const storicoLibri = storico && storico.libri ? storico.libri : [];

  const userMessage = `DATA CORRENTE: ${getCurrentDateString()}

STORICO LIBRI GIÀ PUBBLICATI (da NON reinserire):
${storicoLibri.length > 0 ? storicoLibri.map(l => `- "${l.titolo_it}" di ${l.autore}`).join("\n") : "(nessuno storico)"}

RISULTATI RICERCA GREZZI DA ANALIZZARE:
${testiInput}

Analizza e filtra secondo le regole. Restituisci JSON valido.`;

  try {
    // Primo passaggio: V4 Flash per parsing veloce
    const flashResp = await deepseek.chat.completions.create({
      model: DEEPSEEK_FLASH,
      messages: [
        { role: "system", content: FILTRO_SYSTEM_PROMPT },
        { role: "user", content: userMessage },
      ],
      temperature: 0.2,
      response_format: { type: "json_object" },
    });

    const flashContent = flashResp.choices[0]?.message?.content || "{}";
    let parsed;

    try {
      parsed = JSON.parse(flashContent);
    } catch {
      console.error("[MCP] Errore parsing primo passaggio DeepSeek, retry con reasoner...");

      // Fallback: secondo passaggio con V4 Pro/Reasoner
      const proResp = await deepseek.chat.completions.create({
        model: DEEPSEEK_PRO,
        messages: [
          { role: "system", content: FILTRO_SYSTEM_PROMPT },
          { role: "user", content: userMessage + "\n\nRispondi SOLO con JSON valido." },
        ],
        temperature: 0.1,
      });

      const proContent = proResp.choices[0]?.message?.content || "{}";
      // Estrai JSON dal markdown se necessario
      const jsonMatch = proContent.match(/\{[\s\S]*\}/);
      parsed = jsonMatch ? JSON.parse(jsonMatch[0]) : { libri: [], scartati: ["Errore parsing risposta DeepSeek"] };
    }

    // Se il primo passaggio ha funzionato ma pochi libri, usa reasoner per approfondire
    if (parsed.libri && parsed.libri.length < 2 && risultatiGrezzi.length > 10) {
      console.error("[MCP] Primo passaggio ha trovato pochi libri, approfondimento con V4 Pro/Reasoner...");

      const proResp = await deepseek.chat.completions.create({
        model: DEEPSEEK_PRO,
        messages: [
          { role: "system", content: FILTRO_SYSTEM_PROMPT + "\n\nFai un'analisi più approfondita, cerca libri di qualità anche dai dettagli parziali." },
          { role: "user", content: userMessage },
        ],
        temperature: 0.2,
      });

      const proContent = proResp.choices[0]?.message?.content || "{}";
      const jsonMatch = proContent.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        const proParsed = JSON.parse(jsonMatch[0]);
        if (proParsed.libri && proParsed.libri.length > parsed.libri.length) {
          parsed = proParsed;
        }
      }
    }

    return {
      libri: parsed.libri || [],
      scartati: parsed.scartati || [],
      riepilogo: parsed.riepilogo || "Analisi completata.",
      modelli_usati: ["deepseek-chat", "deepseek-reasoner"],
    };

  } catch (err) {
    console.error(`[MCP] Errore chiamata DeepSeek: ${err.message}`);
    return {
      libri: [],
      scartati: [`Errore API DeepSeek: ${err.message}`],
      riepilogo: "Errore durante l'analisi.",
      modelli_usati: [],
      errore: err.message,
    };
  }
}

// ──────────────────────────────────────────
//  Tool 3: genera_html_libri
// ──────────────────────────────────────────

const HTML_SYSTEM_PROMPT = `Sei un web designer specializzato in layout editoriali. 
Genera SOLO il contenuto HTML interno per una sezione di libri, con lo stile visivo del sito AI News (tema scuro #1a1a2e, rosso #e50914).

REGOLE:
1. Usa classi CSS: .news-grid, .news-card, .card-body, .cat-badge, .excerpt, .card-footer
2. Ogni card deve contenere:
   - Badge con premio o fonte recensione (es. "🏆 Premio Strega", "📰 Robinson")
   - Titolo italiano in h3
   - Titolo originale in piccolo grigio sotto
   - Autore, editore, traduttore in metadati
   - Sinossi critica in .excerpt (max 3 frasi)
   - Motivazione inclusione in .card-footer
3. Se lista libri vuota, genera messaggio "Nessuna novità di rilievo oggi"
4. Aggiungi section-title con emoji 📚 prima della griglia
5. Niente html/head/body — solo il contenuto del container

Rispondi SOLO con il codice HTML, senza markdown.`;

async function genera_html_libri(libri, dataGenerazione) {
  console.error(`[MCP] genera_html_libri: ${libri.length} libri da formattare...`);

  const dataFormattata = formatDateItalian(dataGenerazione);

  // Se nessun libro, messaggio vuoto
  if (!libri || libri.length === 0) {
    return `<div class="section-title"><span class="emoji">📚</span> Novità editoriali — ${dataFormattata}</div>
<div style="text-align:center;padding:60px 20px;color:#888;">
  <div style="font-size:48px;margin-bottom:20px;">📭</div>
  <p style="font-size:18px;font-weight:600;">Nessuna novità di rilievo oggi</p>
  <p style="margin-top:10px;">Torna domani per nuovi aggiornamenti su premi, recensioni e classifiche.</p>
</div>`;
  }

  try {
    const resp = await deepseek.chat.completions.create({
      model: DEEPSEEK_FLASH,
      messages: [
        { role: "system", content: HTML_SYSTEM_PROMPT },
        { role: "user", content: `Data: ${dataFormattata}\nLibri da formattare (JSON): ${JSON.stringify(libri, null, 2)}` },
      ],
      temperature: 0.3,
    });

    let html = resp.choices[0]?.message?.content || "";
    // Pulisci eventuali markdown
    html = html.replace(/```html/g, "").replace(/```/g, "").trim();

    return html;

  } catch (err) {
    console.error(`[MCP] Errore generazione HTML via DeepSeek: ${err.message}`);
    // Fallback: template HTML base generato localmente
    return generateHtmlFallback(libri, dataFormattata);
  }
}

function generateHtmlFallback(libri, dataFormattata) {
  const cards = libri.map(l => `
    <div class="news-card">
      <div class="card-body">
        ${l.premio ? `<span class="cat-badge">🏆 ${l.premio}</span>` : l.fonte_recensione ? `<span class="cat-badge">📰 ${l.fonte_recensione}</span>` : `<span class="cat-badge">📖 Novità</span>`}
        <h3>${escapeHtml(l.titolo_it || "Titolo sconosciuto")}</h3>
        ${l.titolo_originale && l.titolo_originale !== "N/D" ? `<p style="font-size:13px;color:#999;margin-bottom:8px;font-style:italic;">${escapeHtml(l.titolo_originale)}</p>` : ""}
        <p style="font-size:13px;color:#666;margin-bottom:10px;">
          <strong>Autore:</strong> ${escapeHtml(l.autore || "N/D")} &nbsp;·&nbsp; <strong>Editore:</strong> ${escapeHtml(l.editore || "N/D")}
          ${l.traduttore && l.traduttore !== "N/D" ? `&nbsp;·&nbsp; <strong>Traduttore:</strong> ${escapeHtml(l.traduttore)}` : ""}
        </p>
        <div class="excerpt">${escapeHtml(l.sinossi_critica || "Sinossi non disponibile.")}</div>
        <div class="card-footer">
          <span class="source">${escapeHtml(l.motivazione_inclusione || "")}</span>
          ${l.data_pubblicazione ? `<span>📅 ${l.data_pubblicazione}</span>` : ""}
        </div>
      </div>
    </div>
  `).join("\n");

  return `
<div class="section-title"><span class="emoji">📚</span> Novità editoriali — ${dataFormattata}</div>
<div class="news-grid">
  ${cards}
</div>`;
}

function escapeHtml(text) {
  if (!text) return "";
  const amp = "&".concat("amp;");
  const lt = "&".concat("lt;");
  const gt = "&".concat("gt;");
  const quot = "&".concat("quot;");
  return String(text)
    .replace(/&/g, amp)
    .replace(/</g, lt)
    .replace(/>/g, gt)
    .replace(/"/g, quot);
}

// ──────────────────────────────────────────
//  MCP Server
// ──────────────────────────────────────────

const server = new Server(
  { name: "libri-search-mcp", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

// Lista tools
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "search_novita_editoriali",
      description: "Cerca sul web (Tavily) le ultime novità editoriali: vincitori/finalisti premi letterari, recensioni da inserti culturali italiani, classifiche distributori. Non richiede parametri.",
      inputSchema: {
        type: "object",
        properties: {},
      },
    },
    {
      name: "filtra_con_deepseek",
      description: "Analizza i risultati grezzi con DeepSeek V4 Flash + V4 Pro/Reasoner. Filtra qualità, scarta self-publishing/duplicati, estrae metadati (titolo, autore, editore, traduttore, sinossi, motivazione).",
      inputSchema: {
        type: "object",
        properties: {
          risultati_grezzi: { type: "array", description: "Array di risultati da Tavily" },
          storico: { type: "array", description: "Storico libri già pubblicati (opzionale)" },
        },
        required: ["risultati_grezzi"],
      },
    },
    {
      name: "genera_html_libri",
      description: "Genera il blocco HTML interno per la sezione libri, con lo stile grafico AI News (tema scuro + rosso #e50914).",
      inputSchema: {
        type: "object",
        properties: {
          libri: { type: "array", description: "Array di libri filtrati da DeepSeek" },
          data_generazione: { type: "string", description: "Data nel formato YYYY-MM-DD" },
        },
        required: ["libri", "data_generazione"],
      },
    },
  ],
}));

// Call tool
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  try {
    switch (name) {
      case "search_novita_editoriali": {
        const result = await search_novita_editoriali();
        return {
          content: [{ type: "json", json: result }],
        };
      }

      case "filtra_con_deepseek": {
        const risultati_grezzi = args?.risultati_grezzi || [];
        const storico = args?.storico || [];
        const result = await filtra_con_deepseek(risultati_grezzi, storico);
        return {
          content: [{ type: "json", json: result }],
        };
      }

      case "genera_html_libri": {
        const libri = args?.libri || [];
        const data_generazione = args?.data_generazione || getCurrentDateString();
        const html = await genera_html_libri(libri, data_generazione);
        return {
          content: [{ type: "text", text: html }],
        };
      }

      default:
        throw new Error(`Tool sconosciuto: ${name}`);
    }
  } catch (err) {
    console.error(`[MCP] Errore tool ${name}: ${err.message}`);
    return {
      content: [{ type: "text", text: `Errore: ${err.message}` }],
      isError: true,
    };
  }
});

// Avvio server
async function main() {
  console.error("[MCP] Avvio server libri-search-mcp...");
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("[MCP] Server pronto su stdio.");
}

main().catch((err) => {
  console.error(`[MCP] Fatal error: ${err.message}`);
  process.exit(1);
});