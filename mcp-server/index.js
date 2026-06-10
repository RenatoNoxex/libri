#!/usr/bin/env node

/**
 * MCP Server — Ricerca Novità Editoriali
 * 
 * Tools esposti:
 *   1. search_novita_editoriali  — cerca novità con Tavily (Premi, Recensioni, Classifiche) + RSS
 *   2. filtra_con_deepseek       — analisi e filtro qualità con DeepSeek (meno restrittivo)
 *   3. genera_html_libri         — genera blocco HTML contenuto libri
 * 
 * Chiamata stdio transport (MCP nativo).
 * 
 * v3.1 — Siti specializzati (Il Libraio, Minima&Moralia, Rivista Studio, Finzioni),
 *        Preferenze lettori (Goodreads, IBS recensioni), ottimizzazione velocità,
 *        tag_evidenza ("Scelto dai lettori", "Curiosità dal web").
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
import https from "https";
import http from "http";

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

const DEEPSEEK_FLASH = "deepseek-chat";
const DEEPSEEK_PRO  = "deepseek-reasoner";

// ──────────────────────────────────────────
//  Helpers
// ──────────────────────────────────────────

function getCurrentDateString() {
  return new Date().toISOString().split("T")[0];
}

function formatDateItalian(dateStr) {
  if (!dateStr) return "";
  const [y, m, d] = dateStr.split("-");
  return `${d}/${m}/${y}`;
}

async function searchTavily(query, retries = 2, maxRes = 5) {
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const result = await tavilyClient.search(query, {
        searchDepth: "advanced",
        maxResults: maxRes,
        includeAnswer: true,
      });
      return result.results || [];
    } catch (err) {
      if (attempt === retries) {
        console.error(`[Tavily] Fallimento per "${query.substring(0, 50)}": ${err.message}`);
        return [];
      }
      console.error(`[Tavily] Retry ${attempt + 1}/${retries} per "${query.substring(0, 50)}": ${err.message}`);
      await new Promise(r => setTimeout(r, 1500));
    }
  }
  return [];
}

// ──────────────────────────────────────────
//  RSS Feed Fetcher
// ──────────────────────────────────────────

const RSS_FEEDS = [
  { name: "Corriere della Sera - Cultura", url: "https://www.corriere.it/rss/cultura.xml", testata: "Corriere della Sera" },
  { name: "La Repubblica - Cultura", url: "https://www.repubblica.it/rss/cultura/rss2.0.xml", testata: "Robinson" },
  { name: "La Stampa - Cultura", url: "https://www.lastampa.it/rss/cultura.xml", testata: "Tuttolibri" },
  { name: "Il Sole 24 Ore - Cultura", url: "https://www.ilsole24ore.com/rss/cultura.xml", testata: "Domenica" },
  { name: "Anobii - Ultime recensioni", url: "https://www.anobii.com/feed/recent_reviews", testata: "Anobii" },
  { name: "Goodreads - Popular", url: "https://www.goodreads.com/review/list_rss/", testata: "Goodreads" },
];

async function fetchRSSFeed(feedUrl, feedName) {
  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      console.error(`[RSS] Timeout dopo 8s per ${feedName}`);
      resolve([]);
    }, 8000);

    const urlObj = new URL(feedUrl);
    const client = urlObj.protocol === "https:" ? https : http;

    client.get(feedUrl, { timeout: 6000 }, (res) => {
      let data = "";
      res.on("data", (chunk) => { data += chunk; });
      res.on("end", () => {
        clearTimeout(timeout);
        try {
          const items = [];
          const itemRegex = /<item>([\s\S]*?)<\/item>/gi;
          let match;
          while ((match = itemRegex.exec(data)) !== null) {
            const itemXml = match[1];
            const titleMatch = itemXml.match(/<title[^>]*><!\[CDATA\[([^\]]*)\]\]><\/title>|<title[^>]*>([^<]*)<\/title>/);
            const linkMatch = itemXml.match(/<link[^>]*>([^<]*)<\/link>/);
            const descMatch = itemXml.match(/<description[^>]*><!\[CDATA\[([^\]]*)\]\]><\/description>|<description[^>]*>([^<]*)<\/description>/);
            const pubDateMatch = itemXml.match(/<pubDate[^>]*>([^<]*)<\/pubDate>/);

            if (titleMatch) {
              items.push({
                title: (titleMatch[1] || titleMatch[2] || "").replace(/<[^>]*>/g, "").trim(),
                url: (linkMatch ? linkMatch[1] : "").trim(),
                content: (descMatch ? (descMatch[1] || descMatch[2] || "") : "").replace(/<[^>]*>/g, "").trim().substring(0, 500),
                pubDate: pubDateMatch ? pubDateMatch[1].trim() : null,
                feed_name: feedName,
              });
            }
          }
          if (items.length > 0) {
            console.error(`[RSS] ✓ ${feedName}: ${items.length} articoli trovati`);
          }
          resolve(items.slice(0, 10));
        } catch (err) {
          console.error(`[RSS] Errore parsing ${feedName}: ${err.message}`);
          resolve([]);
        }
      });
    }).on("error", (err) => {
      clearTimeout(timeout);
      console.error(`[RSS] Errore rete ${feedName}: ${err.message}`);
      resolve([]);
    }).on("timeout", () => {
      clearTimeout(timeout);
      console.error(`[RSS] Timeout rete ${feedName}`);
      resolve([]);
    });
  });
}

function convertRSSItemsToResults(items, testata) {
  return items.map(item => ({
    title: item.title,
    url: item.url,
    content: item.content,
    query_type: "recensioni",
    query: `RSS ${testata}`,
    source_type: "rss",
    pubDate: item.pubDate,
  }));
}

// ──────────────────────────────────────────
//  Tool 1: search_novita_editoriali  (104 QUERY)
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
  "International Booker Prize 2026 traduzione italiana",
  "Premio Goncourt 2025 2026 edizione italiana",
  "National Book Award 2025 traduzione italiana",
  "Premio Bagutta 2026 vincitore",
  "Premio Viareggio 2026 libro narrativa",
  "Premio Brancati 2026 libro vincitore",
  "Premio Flaiano 2026 narrativa vincitore",
  "Premio Grinzane 2026 libro narrativa",
  "Premio Italo Calvino 2026 vincitore libro",
  "Premio Stresa di Narrativa 2026 vincitore",
];

const RECENSIONI_QUERIES = [
  'site:corriere.it "La Lettura" recensione libro 2026',
  'site:corriere.it "La Lettura" recensione narrativa straniera 2026',
  'site:repubblica.it "Robinson" recensione libro 2026',
  'site:repubblica.it/venerdi recensione libro 2026',
  'site:repubblica.it "Robinson" narrativa tradotta 2026',
  'site:lastampa.it "Tuttolibri" recensione libro 2026',
  'site:lastampa.it "Tuttolibri" narrativa straniera 2026',
  'site:ilsole24ore.com "Domenica" recensione libro 2026',
  'site:ilsole24ore.com recensione narrativa straniera 2026',
  "recensioni libri La Lettura Corriere della Sera 2026",
  "recensioni libri Robinson Repubblica 2026",
  "recensioni libri Tuttolibri La Stampa 2026",
  "recensioni libri Domenica Il Sole 24 Ore 2026",
  "nuove uscite narrativa straniera recensione 2026 Italia",
  "migliori libri 2026 recensione inserti culturali italiani",
  "recensioni narrativa tradotta 2026 La Lettura Robinson Tuttolibri",
  "inserti culturali libri consigliati 2026 narrativa straniera",
  "recensione libro tradotto italiano 2026 edito Einaudi Mondadori Feltrinelli",
  "recensione libro appena uscito narrativa straniera tradotta giugno 2026",
  "rassegna stampa libro recensione Corriere Repubblica Stampa 2026",
  "critica letteraria libro straniero tradotto italiano 2026",
];

const CLASSIFICHE_QUERIES = [
  "classifica libri più venduti narrativa straniera IBS 2026",
  "classifica IBS narrativa straniera giugno 2026",
  "classifica IBS saggistica straniera 2026",
  "IBS top 20 narrativa straniera 2026",
  "classifica libri più venduti Feltrinelli narrativa straniera 2026",
  "classifica Feltrinelli narrativa tradotta 2026",
  "top 10 Feltrinelli narrativa straniera 2026",
  "classifica Mondadori Store narrativa straniera 2026",
  "classifica Mondadori libri più venduti narrativa tradotta 2026",
  "classifica Amazon libri narrativa straniera 2026",
  "bestseller Amazon narrativa straniera tradotta Italia 2026",
  "Amazon top vendite narrativa straniera 2026",
  "classifiche Arianna GFK libri più venduti narrativa straniera 2026",
  "Giornale della Libreria classifica narrativa straniera 2026",
  "GFK classifica libri venduti narrativa straniera Italia 2026",
  "bestseller narrativa straniera tradotta Italia 2026",
  "top 10 narrativa straniera classifica libri 2026",
  "top 20 libri più venduti narrativa tradotta giugno 2026",
  "classifica narrativa straniera maggio 2026",
  "classifica libri più venduti aprile 2026 narrativa straniera",
  "classifica libri più venduti marzo 2026 narrativa straniera",
  "classifica narrativa straniera 2026 bestseller editore italiano",
  "classifica saggistica straniera IBS 2026",
  "bestseller saggistica straniera tradotta Italia 2026",
  "top 20 saggistica straniera classifica 2026",
  "narrativa contemporanea straniera classifica vendite 2026",
  "romanzo storico straniero classifica bestseller Italia 2026",
  "thriller straniero classifica vendite Italia 2026",
  "narrativa nord europea classifica libri più venduti 2026",
  "narrativa giapponese classifica vendite Italia 2026",
];

const AGGREGATORI_QUERIES = [
  "Goodreads libri più votati narrativa straniera tradotta italiano 2026",
  "Anobii libri più letti narrativa straniera 2026",
  "rassegna stampa Einaudi libri stranieri 2026",
  "rassegna stampa Mondadori narrativa tradotta 2026",
  "rassegna stampa Feltrinelli libri stranieri 2026",
  "rassegna stampa Bompiani libri tradotti 2026",
  "rassegna stampa Adelphi narrativa straniera 2026",
  "rassegna stampa Neri Pozza libri tradotti 2026",
  "rassegna stampa Iperborea libri 2026",
  "nuove uscite narrativa straniera casa editrice italiana 2026",
  "bookstagram libri consigliati narrativa straniera tradotta 2026",
  "booktok libri narrativa straniera consigliati 2026",
  "blog letterari recensione libro straniero tradotto 2026",
];

// SITI SPECIALIZZATI
const SPECIALIZZATI_QUERIES = [
  'site:illibraio.it recensione libro narrativa straniera 2026',
  'site:illibraio.it novità libro straniero tradotto 2026',
  'site:minimaetmoralia.it libro narrativa straniera 2026',
  'site:minimaetmoralia.it recensione libro 2026',
  'site:rivistastudio.com libri narrativa straniera tradotta 2026',
  'site:rivistastudio.com recensione libro 2026',
  'site:finzionimagazine.it recensione libro straniero 2026',
  'site:rivistailmulino.it/temi/libri libro narrativa straniera 2026',
  "Il Libraio novità narrativa straniera 2026 recensione",
  "Minima&Moralia recensione libro straniero 2026",
  "Rivista Studio libri consigliati narrativa straniera 2026",
  "Finzioni magazine recensione narrativa straniera 2026",
];

// PREFERENZE LETTORI
const LETTORI_QUERIES = [
  'site:goodreads.com "narrativa straniera" "traduzione italiana" rating 2026',
  "Goodreads Italia libri più votati narrativa straniera 2026",
  "Goodreads nuovi libri narrativa straniera rating alto 2026",
  "recensioni utenti IBS narrativa straniera più amata 2026",
  "Feltrinelli libri più recensiti narrativa straniera 2026",
  "libri consigliati bookstagram Italia narrativa straniera 2026",
  "libri più discussi community lettori narrativa straniera tradotta 2026",
  "nuove uscite narrativa straniera recensioni positive Italia 2026",
];

async function search_novita_editoriali() {
  console.error("[MCP] search_novita_editoriali: avvio ricerca avanzata v3.1 (104 query)...");

  const allResults = [];
  const errors = [];

  // RSS in background
  console.error("[MCP] FASE RSS: fetch feed culturali e aggregatori...");
  const rssPromises = RSS_FEEDS.map(feed =>
    fetchRSSFeed(feed.url, feed.name)
      .then(items => convertRSSItemsToResults(items, feed.testata))
      .catch(err => {
        console.error(`[RSS] Fallimento ${feed.name}: ${err.message}`);
        return [];
      })
  );

  const rssPromise = Promise.all(rssPromises).then(results => {
    const allRSS = results.flat();
    console.error(`[RSS] Totale articoli RSS raccolti: ${allRSS.length}`);
    allResults.push(...allRSS);
  }).catch(err => {
    console.error(`[RSS] Errore globale RSS: ${err.message}`);
  });

  // Query Tavily ottimizzate (gruppi paralleli)
  const runQueryGroup = async (queries, queryType, maxRes = 3) => {
    for (const q of queries) {
      try {
        const res = await searchTavily(q, 1, maxRes);
        if (res.length > 0) {
          allResults.push(...res.slice(0, maxRes).map(r => ({ ...r, query_type: queryType, query: q })));
          console.error(`[MCP]   ✓ ${queryType}: "${q.substring(0, 60)}..." → ${Math.min(res.length, maxRes)} risultati`);
        }
      } catch (err) {
        errors.push(`[${queryType}] "${q}": ${err.message}`);
        console.error(`[MCP]   ✗ ${queryType}: "${q.substring(0, 60)}" — ${err.message}`);
      }
      await new Promise(r => setTimeout(r, 150));
    }
  };

  // Blocco A: Premi + Recensioni in parallelo
  console.error("[MCP] BLOCCO A: Premi (20 query) + Recensioni (22 query) in parallelo...");
  await Promise.all([
    runQueryGroup(PREMI_QUERIES, "premi"),
    runQueryGroup(RECENSIONI_QUERIES, "recensioni"),
  ]);

  // Blocco B: Classifiche + Aggregatori + Specializzati + Lettori in parallelo
  console.error("[MCP] BLOCCO B: Classifiche (30q maxRes=3) + Aggregatori (12q) + Specializzati (12q) + Lettori (8q) in parallelo...");
  await Promise.all([
    runQueryGroup(CLASSIFICHE_QUERIES, "classifiche", 3),
    runQueryGroup(AGGREGATORI_QUERIES, "classifiche"),
    runQueryGroup(SPECIALIZZATI_QUERIES, "recensioni"),
    runQueryGroup(LETTORI_QUERIES, "classifiche"),
  ]);

  // Attendi RSS
  await rssPromise;

  // Deduplica per URL
  const seen = new Set();
  const unique = allResults.filter(r => {
    if (!r.url || seen.has(r.url)) return false;
    seen.add(r.url);
    return true;
  });

  const stats = {};
  unique.forEach(r => {
    const t = r.query_type || "other";
    stats[t] = (stats[t] || 0) + 1;
  });

  console.error(`[MCP] Totale risultati unici: ${unique.length} (premi: ${stats.premi || 0}, recensioni: ${stats.recensioni || 0}, classifiche: ${stats.classifiche || 0})`);
  if (errors.length > 0) {
    console.error(`[MCP] ${errors.length} query hanno dato errore (continuo comunque)`);
  }

  return {
    risultati: unique.slice(0, 150),
    data_ricerca: getCurrentDateString(),
    totale_grezzi: unique.length,
    statistiche: stats,
    errori_query: errors.length,
    fonti_utilizzate: {
      premi: PREMI_QUERIES.length,
      recensioni: RECENSIONI_QUERIES.length,
      classifiche: CLASSIFICHE_QUERIES.length,
      aggregatori: AGGREGATORI_QUERIES.length,
      specializzati: SPECIALIZZATI_QUERIES.length,
      lettori: LETTORI_QUERIES.length,
      rss_feeds: RSS_FEEDS.length,
    },
  };
}

// ──────────────────────────────────────────
//  Tool 2: filtra_con_deepseek  (PROMPT CON TAG_EVIDENZA)
// ──────────────────────────────────────────

const FILTRO_SYSTEM_PROMPT = `Sei un critico letterario esperto e curatore editoriale. Il tuo obiettivo è MASSIMIZZARE l'accuratezza dei dati, non escludere.

REGOLE DI INCLUSIONE (LIBERALE):
1. INCLUDI qualsiasi libro che abbia un editore riconosciuto.
2. INCLUDI AUTOMATICAMENTE qualsiasi libro menzionato in:
   - Inserti culturali italiani: "La Lettura" (Corriere), "Robinson" (Repubblica), "Tuttolibri" (La Stampa), "Domenica" (Il Sole 24 Ore)
   - Premi letterari: Strega, Campiello, Pulitzer, Booker, Nobel, Goncourt, Viareggio, Bagutta, Strega Europeo, International Booker, National Book Award
   - Classifiche dei grandi distributori: IBS, Feltrinelli, Mondadori Store, Amazon, GFK/Arianna, Giornale della Libreria
   - Siti specializzati: Il Libraio, Minima&Moralia, Rivista Studio, Finzioni
   - Preferenze lettori: Goodreads, recensioni utenti IBS, Feltrinelli, bookstagram
3. Se un libro è presente in una classifica o riceve una recensione su queste testate, DEVE essere incluso.

REGOLE DI ESCLUSIONE (SOLO QUESTE):
1. SCARTA ESCLUSIVAMENTE il self-publishing evidente (Amazon KDP, Youcanprint, pubMe, StreetLib, Lulu.com senza nessun editore noto).
2. SCARTA la saggistica aziendale, manualistica tecnica, libri scolastici e guide pratiche.
3. SCARTA libri pubblicati da più di 12 mesi dalla data odierna (a meno che non siano recentemente tradotti in italiano per la prima volta).

REGOLA D'ORO: Se sei in dubbio, INCLUDI il libro. È meglio includere un libro borderline che perdere un libro di qualità.

CAMPI DA ESTRARRE (per ogni libro incluso):
- titolo_it: titolo in italiano
- titolo_originale: titolo originale (se noto, altrimenti "N/D")
- autore: nome autore
- editore: casa editrice italiana (N/D se non ancora annunciata)
- traduttore: nome traduttore (se noto, altrimenti "N/D")
- sezione: stringa con i tag delle sezioni separate da virgola. Valori possibili: "premi", "recensioni", "classifiche" (es. "premi,classifiche" se un libro vincitore è anche in classifica)
- sinossi_critica: sinossi critica di 2-3 frasi, non banale
- motivazione_inclusione: motivo preciso e verificabile (es. "Vincitore Premio Strega 2026", "Recensito su Robinson – la Repubblica del 01/06/2026", "Presente nella Top 10 Narrativa Straniera IBS – giugno 2026", "Finalista Booker Prize 2025, tradotto da Einaudi")
- premio: nome premio se collegato, altrimenti null
- fonte_recensione: nome inserto/testata se recensito, altrimenti null
- data_pubblicazione: data uscita Italia (YYYY-MM se nota, altrimenti null)
- copertina_url: URL copertina se trovato, altrimenti null
- fonte_url: URL della fonte più autorevole (recensione > scheda premio > classifica)
- tag_evidenza: null (default), "Scelto dai lettori" (se il libro proviene da recensioni utenti Goodreads/IBS/Feltrinelli, community, bookstagram), "Curiosità dal web" (se proviene da siti specializzati tipo Il Libraio, Minima&Moralia, Rivista Studio, Finzioni)

Rispondi SOLO con un JSON valido nella forma:
{
  "libri": [{ ... }],
  "scartati": ["motivo scarto 1", "motivo scarto 2"],
  "riepilogo": "Breve riassunto dell'analisi per sezione: numero libri trovati per premi, recensioni, classifiche"
}`;

async function filtra_con_deepseek(risultatiGrezzi, storico) {
  console.error("[MCP] filtra_con_deepseek: invio a DeepSeek V4 Flash (primo passaggio)...");

  const testiInput = risultatiGrezzi.map((r, i) => {
    const tipo = r.query_type || "web";
    const sourceType = r.source_type || "web";
    return `[${i + 1}] Tipo: ${tipo}${sourceType === 'rss' ? ' (RSS)' : ''}\nTitolo: ${r.title || "N/D"}\nFonte: ${r.url || "N/D"}\nSnippet: ${r.content || r.answer || "N/D"}\n`;
  }).join("\n---\n");

  let storicoLibri = [];
  if (storico) {
    if (Array.isArray(storico)) {
      storicoLibri = storico;
    } else if (storico.raccolte) {
      for (const [dateKey, raccolta] of Object.entries(storico.raccolte)) {
        if (raccolta.libri) storicoLibri.push(...raccolta.libri);
      }
    } else if (storico.libri) {
      storicoLibri = storico.libri;
    }
  }

  const userMessage = `DATA CORRENTE: ${getCurrentDateString()}

STORICO LIBRI GIÀ PUBBLICATI (da NON reinserire):
${storicoLibri.length > 0 ? storicoLibri.map(l => `- "${l.titolo_it}" di ${l.autore}`).join("\n") : "(nessuno storico)"}

RISULTATI RICERCA GREZZI DA ANALIZZARE:
${testiInput}

RICORDA: includi TUTTI i libri con editore riconosciuto. Scarta SOLO self-publishing, saggistica commerciale e libri >12 mesi. Se in dubbio, INCLUDI.
TAGGA ogni libro con la sezione corretta: "premi", "recensioni", "classifiche" (anche multiple separate da virgola).
Assegna tag_evidenza "Scelto dai lettori" per libri da Goodreads/community/IBS recensioni utenti, oppure "Curiosità dal web" per libri da Il Libraio/Minima&Moralia/Rivista Studio/Finzioni.`;

  try {
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
      const proResp = await deepseek.chat.completions.create({
        model: DEEPSEEK_PRO,
        messages: [
          { role: "system", content: FILTRO_SYSTEM_PROMPT },
          { role: "user", content: userMessage + "\n\nRispondi SOLO con JSON valido." },
        ],
        temperature: 0.1,
      });
      const proContent = proResp.choices[0]?.message?.content || "{}";
      const jsonMatch = proContent.match(/\{[\s\S]*\}/);
      parsed = jsonMatch ? JSON.parse(jsonMatch[0]) : { libri: [], scartati: ["Errore parsing risposta DeepSeek"] };
    }

    if (parsed.libri && parsed.libri.length < Math.max(5, Math.floor(risultatiGrezzi.length / 8))) {
      console.error(`[MCP] Solo ${parsed.libri.length} libri trovati su ${risultatiGrezzi.length} risultati. Approfondimento con V4 Pro...`);
      const proResp = await deepseek.chat.completions.create({
        model: DEEPSEEK_PRO,
        messages: [
          { role: "system", content: FILTRO_SYSTEM_PROMPT + "\n\nFAI UN'ANALISI APPROFONDITA. Cerca TUTTI i libri validi, sii inclusivo." },
          { role: "user", content: userMessage },
        ],
        temperature: 0.2,
      });
      const proContent = proResp.choices[0]?.message?.content || "{}";
      const jsonMatch = proContent.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        try {
          const proParsed = JSON.parse(jsonMatch[0]);
          if (proParsed.libri && proParsed.libri.length > parsed.libri.length) {
            console.error(`[MCP] Approfondimento: trovati ${proParsed.libri.length} libri (prima: ${parsed.libri.length})`);
            parsed = proParsed;
          }
        } catch (e) {
          console.error(`[MCP] Errore parsing approfondimento: ${e.message}`);
        }
      }
    }

    const sezioniStats = {};
    (parsed.libri || []).forEach(l => {
      if (l.sezione) {
        l.sezione.split(",").forEach(s => {
          const key = s.trim();
          sezioniStats[key] = (sezioniStats[key] || 0) + 1;
        });
      }
    });
    console.error(`[MCP] Libri filtrati: ${(parsed.libri || []).length} totali. Per sezione: ${JSON.stringify(sezioniStats)}`);

    return {
      libri: parsed.libri || [],
      scartati: parsed.scartati || [],
      riepilogo: parsed.riepilogo || "Analisi completata.",
      modelli_usati: ["deepseek-chat", "deepseek-reasoner"],
      statistiche_sezioni: sezioniStats,
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
   - Badge con premio o fonte recensione
   - Titolo italiano in h3 con LINK: <a href="dettaglio.html?id=ID_PLACEHOLDER">Titolo</a>
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
    html = html.replace(/```html/g, "").replace(/```/g, "").trim();
    return html;
  } catch (err) {
    console.error(`[MCP] Errore generazione HTML via DeepSeek: ${err.message}`);
    return generateHtmlFallback(libri, dataFormattata);
  }
}

function generateHtmlFallback(libri, dataFormattata) {
  const cards = libri.map((l) => `
    <div class="news-card">
      <div class="card-body">
        ${l.premio ? `<span class="cat-badge">🏆 ${l.premio}</span>` : l.fonte_recensione ? `<span class="cat-badge">📰 ${l.fonte_recensione}</span>` : `<span class="cat-badge">📖 Novità</span>`}
        <h3><a href="dettaglio.html?id=ID_PLACEHOLDER">${escapeHtml(l.titolo_it || "Titolo sconosciuto")}</a></h3>
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
//  MCP Server v3.1.0
// ──────────────────────────────────────────

const server = new Server(
  { name: "libri-search-mcp", version: "3.1.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "search_novita_editoriali",
      description: "Cerca sul web (Tavily + RSS feed) le ultime novità editoriali con 104 query capillari: Premi (20), Recensioni testate+dork (22), Classifiche (30), Aggregatori (13), Siti specializzati/Il Libraio (12), Preferenze lettori/Goodreads (8). Include RSS feed di 6 fonti. Non richiede parametri.",
      inputSchema: {
        type: "object",
        properties: {},
      },
    },
    {
      name: "filtra_con_deepseek",
      description: "Analizza i risultati grezzi con DeepSeek. Filtro liberale: include tutto tranne self-publishing, saggistica commerciale e libri >12 mesi. Etichetta ogni libro con la sezione (premi, recensioni, classifiche) e tag_evidenza ('Scelto dai lettori', 'Curiosità dal web').",
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
      description: "Genera il blocco HTML interno per la sezione libri, con stile AI News (tema scuro + rosso #e50914).",
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

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  try {
    switch (name) {
      case "search_novita_editoriali": {
        const result = await search_novita_editoriali();
        return { content: [{ type: "json", json: result }] };
      }
      case "filtra_con_deepseek": {
        const risultati_grezzi = args?.risultati_grezzi || [];
        const storico = args?.storico || [];
        const result = await filtra_con_deepseek(risultati_grezzi, storico);
        return { content: [{ type: "json", json: result }] };
      }
      case "genera_html_libri": {
        const libri = args?.libri || [];
        const data_generazione = args?.data_generazione || getCurrentDateString();
        const html = await genera_html_libri(libri, data_generazione);
        return { content: [{ type: "text", text: html }] };
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

async function main() {
  console.error("[MCP] Avvio server libri-search-mcp v3.1...");
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("[MCP] Server pronto su stdio.");
}

main().catch((err) => {
  console.error(`[MCP] Fatal error: ${err.message}`);
  process.exit(1);
});