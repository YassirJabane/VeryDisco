# UI e affidabilità — 15 ottobre 2026

## Perimetro

Tranche sulla beta, base `01426b2`: shell, navigazione, home, temi, player, feedback, lyrics e statistiche/API Navidrome. Nessuna modifica a main e nessun tag stabile. Questo non sostituisce la verifica end-to-end di tutte le pipeline musicali: i problemi architetturali residui del precedente audit restano espliciti.

## Interfaccia

- Home riprogettata con gerarchia leggibile, tre metriche, routine di sincronizzazione, Library pulse, scorciatoie e attività reale delle sorgenti. Nessun badge fittizio "Navidrome online" o "Everything looks clean".
- Le metriche dei run indicano espressamente che sommano l'ultimo run di ciascuna sorgente, non l'intera storia. Completamento = scaricati + saltati + falliti, con errori visibili: 15 scaricati, 34 saltati e 1 fallito su 50 significa 100% elaborato, non 30%.
- Tema scuro slate/viola/mint e chiaro coerente, contrasto di chip/dialog/table migliorato, focus da tastiera e rispetto delle preferenze di movimento ridotto. Raggi corretti per evitare bordi eccessivamente arrotondati.
- Navigatore pagine con Ctrl/Cmd+K, filtro multi-parola, frecce e Invio; destinazioni amministrative escluse agli utenti normali.
- Sidebar collassabile persistente, menu mobile con etichette sempre visibili, indicazione della pagina corrente, link skip-to-content e focus sul contenuto dopo la navigazione. URL sconosciuti e vecchia pagina aliases risolti verso destinazioni valide.
- Profilo collegato alle impostazioni, gestione migliore dei caricamenti e boundary per gli errori delle pagine: navigazione ancora utilizzabile e retry/reload espliciti.
- Player estratto dalla shell, volume iniziale applicato realmente, durata/seek validati, gestione autoplay/errori e promise tardive; chiusura/cambio account puliscono la riproduzione. Controlli mobile e safe-area considerati.
- Lyrics mobile: badge "Plain · upgrade to synced" ora presente anche nelle card, album leggibile e azioni su riga dedicata.

## Correzioni funzionali e sicurezza

- Statistiche Navidrome: errori HTTP/API e pagine parziali non diventano più conteggi zero. Il client rileva pagine ripetute e restituisce solo dataset completi; una libreria realmente vuota resta 0. Endpoint autenticato, credenziali personali, fallback globale solo per l'admin corrispondente. Le eccezioni HTTP non vengono stampate integralmente nei log, perché la URL può contenere token/salt.
- Home: polling senza sovrapposizione, sospensione con tab nascosta, refresh su focus/eventi dati, abort su unmount; snapshot precedente conservato e marcato come non aggiornato se il refresh fallisce. Le statistiche sconosciute sono trattini, non zero. Il timestamp è quello del recupero dello snapshot, non dell'ultima scansione Navidrome.
- Logs globali: accesso riservato agli amministratori anche via endpoint SSE, non soltanto nascondendo il menu.
- Feedback: una cover arrivata in ritardo non ripristina rating rimossi; rimozione senza MBID abbinata ad artista e titolo, non al solo titolo. Risposte obsolete e timer di refresh invalidati all'uscita dalla pagina.
- Conferme: operazioni duplicate bloccate; gli errori mantengono aperto il dialogo e mostrano un messaggio utilizzabile. Errori di validazione strutturati vengono convertiti in testo anziché provocare un errore React.
- Metadata rebuild: descrizione del fallback artwork digitale resa aderente alla logica, senza suggerire che venga privilegiato rispetto alla cover MusicBrainz verificata.

## Prestazioni e verifiche

Import delle icone Material convertiti in import diretti in 32 file. La build locale passa da 11.602 a 1.301 moduli trasformati; l'ultima build impiega circa 3,5 s. La suite frontend passa da circa 269 s a circa 30 s su questo host. Sono osservazioni locali, non benchmark controllati; non viene dichiarata una riduzione del bundle scaricato.

- Backend: **213 test passati**, 5 warning legacy relativi a `datetime.utcnow`.
- Frontend: **20 test passati in 12 file**, inclusi navigazione, command palette, player, boundary, conferme, statistiche sconosciute e feedback con artwork tardivo/titoli omonimi.
- TypeScript e build Vite/PWA completati.
- Verifica browser desktop e mobile 390×844, tema chiaro/scuro, menu mobile, navigazione da tastiera, Home e Lyrics. Nessun overflow orizzontale nei viewport verificati; nessun errore console osservato nei flussi verificati.
- Server LAN consultato in sola lettura; nessuno scan/download o cambiamento della libreria reale. Nuova UI verificata tramite `scripts/ui_preview.py`: serve la build su loopback 127.0.0.1:3011 con dati sintetici e senza endpoint di mutazione. Le immagini/dati del preview non descrivono la libreria reale.
- Docker non disponibile localmente: test container e pubblicazione sono verificati nel workflow beta dopo il push, non dedotti dal solo esito della build frontend.

## Applicazione e limiti

Aggiornare il container al nuovo tag `beta` dopo il workflow e ricaricare il browser; se resta una vecchia UI PWA, chiudere/riaprire l'app o fare un hard refresh. Nessuna migrazione distruttiva o scansione della libreria necessaria per questi cambiamenti. Gli utenti non-admin non vedono più i log globali, intenzionalmente.

Restano prioritari i punti del [precedente audit](2026-10-15-pre-release.md): coordinatore unico per tutte le mutazioni file, identità recording MBID end-to-end, completezza album nello starred sync, matrice formati, override instrumental dopo spostamenti, paginazione completa/coerenza concorrente della cache feedback e normalizzazione artista centralizzata. Questo lavoro migliora la shell e varie pagine, non costituisce un redesign individuale o una certificazione di assenza di bug di ogni schermata. Prima della stabile servono prove su una libreria Docker/NAS di test con servizi reali.
