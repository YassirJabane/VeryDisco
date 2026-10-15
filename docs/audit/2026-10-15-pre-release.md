# Audit pre-release — 15 ottobre 2026

## Perimetro e decisione

Analisi del checkout Git reale, aggiornato con `git fetch origin --prune`. Base: `beta`/`origin/beta` a `0af14a7`; `origin/main` a `5a076de`, con 59 commit della beta ancora da integrare. Ultimo tag esistente: `v1.0.0`. Per istruzione dell'utente, le correzioni di questo audit vengono pubblicate esclusivamente su `beta`: nessuna modifica a main, nessun tag stabile.

Esaminati bootstrap/configurazione, autenticazione e isolamento utenti, SQLite e migrazioni, scheduler, ListenBrainz/Spotify, ricerca e trasferimenti slskd, pubblicazione file, metadata rebuild/rollback, naming/duplicati, testi/instrumental, artwork/cache, UI/API e distribuzione Docker/Actions. Verifiche locali con database e media temporanei e servizi simulati. Nessun download reale né modifica della libreria del server.

Conclusione: la beta contiene miglioramenti sostanziali, ma i test automatici non bastano a certificare la release stabile. Prima della promozione servono una verifica reale su Docker/Linux e la chiusura dei problemi residui di identità e mutazione dei file elencati sotto.

## Difetti verificati e corretti in questa tranche

| Area | Problema | Correzione |
| --- | --- | --- |
| Credenziali ListenBrainz | Starred sync, refresh feedback e sync manuale potevano ereditare l'account globale per un utente normale. Anche un admin con un altro username personale poteva ereditare il token sbagliato. | Risoluzione comune delle credenziali per HTTP e background; fallback globale limitato all'account admin corrispondente. |
| Esplorazione | Il controllo libreria ignorava il recording MBID e sovrascriveva nella mappa tracce omonime. Usava anche Album Artist come unico artista per il matching. | Recording MBID prioritario; rifiuto di MBID locali in conflitto; fallback con album e artista della traccia; scelta deterministica fra copie. |
| Completezza album | Il ramo di controllo per release MBID continuava a certificare `full` dai totali dei tag locali, anche con una tracklist ufficiale più lunga. Il fallback sul solo titolo poteva prendere l'album di un altro artista. | Totale esterno o tracklist persistita necessari; fallback limitato ai crediti artista compatibili. |
| Qualità | I FLAC con sample rate `None` potevano causare un TypeError nel controllo batch. | Valori mancanti normalizzati e sample rate dell'indice utilizzato nel confronto. |
| Artwork | Ogni polling Explore ripeteva le ricerche; soltanto le prime dieci cover mancanti venivano tentate. | Cache SQLite delle ricerche, cache/promesse condivise UI, concorrenza limitata e risoluzione di tutte le cover. Le risposte di una tab precedente non sostituiscono la tab corrente. Album verificato quando disponibile. |
| Stato Explore | File presenti soltanto in Explore potevano apparire `In Library`; stato del run letto senza filtro utente/source. | Posizione esplicita `library`/`explore`; stato dei run limitato a utente e playlist. |
| Scheduler | Una selezione vuota di playlist poteva riattivare quelle globali. | Nessun fallback globale quando l'utente ha disabilitato tutte le playlist. |
| Playlist importate | Il recupero dei metadati provider, dopo un riavvio, poteva annullare aggiunte/rimozioni locali delle tracce. | La lettura recupera soltanto dati descrittivi mancanti e preserva tracce, nomi e copertine locali già presenti. |
| Retry download | Si perdevano force, Explore, destinazione, release override e target della sostituzione. Accesso ai job limitato alle ultime 500 righe. | Contesto persistente in SQLite con migrazione additiva; ricerca diretta per ID con controllo proprietario e nuova validazione dei percorsi. Retry consentito solo per fallimenti/interruzioni. |
| Annullamento | Un task fermato anche mentre aspettava il semaforo poteva restare attivo nel journal. | Stato `interrupted`, motivo persistente e retry disponibile. |
| Pubblicazione singoli | Un download forzato/grab poteva sovrascrivere il file precedente prima di completare i tag. | Copia privata, tagging e poi pubblicazione; file precedente e trasferimento raw conservati se il tagging fallisce. Default metadata inizializzati anche quando il provider fallisce. Un errore di rescan non invalida un download già salvato. |
| Metadata rebuild | Apply cancellato non ripristinava l'album; un writer fallito dopo una modifica poteva non essere ripristinato; rollback senza lock e senza controllo modifiche successive. | Attesa dei writer in thread prima del ripristino; recupero anche del file corrente; lock e coordinamento con lo scan; fingerprint prima del rollback per preservare modifiche successive. |
| Testi/instrumental | Il fallback poteva scegliere il primo risultato non correlato e marcarlo instrumental. Diverse chiamate non passavano l'album. | Titolo esatto più artista o album compatibile; nessun instrumental da risultato arbitrario; album passato ai lookup. |
| Feedback | Un MBID non risolto poteva essere presentato come feedback salvato; il modello API non accettava il MBID già disponibile. | MBID trasmesso dalla UI e persistito in cache; esito remoto distinto dall'eventuale download album; il cuore viene ripristinato e viene mostrato l'avviso se il feedback non è stato salvato. Cache iniziale popolata indipendentemente dal filtro/pagina richiesti. |
| Attività | Proprietario mancante in vari task; artisti con slash nel task ID non cancellabili dalla route; stato AcoustID/retag condiviso fra utenti. | Proprietario propagato, ID tracce con utente, route compatibile con slash, task e riepiloghi AcoustID/retag per utente. |
| Catalogo | Un tentativo di eliminazione duplicati fallito eliminava comunque la riga dall'indice. | Invalidazione limitata ai file eliminati con successo. |
| Analytics legacy | `getNowPlaying` trattato come storico, con statistiche inventate e client configurato con l'account globale. | Nessuna statistica storica derivata dal now-playing; valori sconosciuti espliciti; eventuali album frequenti ottenuti con credenziali dell'utente. L'endpoint non risulta attualmente consumato dalla UI. |

Il contesto originale dei job precedenti alla nuova colonna non è ricostruibile: i vecchi job conservano `{}` e il retry mantiene il comportamento legacy. Il salvataggio audio e quello del sidecar lyrics restano due operazioni distinte; un errore del sidecar è segnalato senza riscaricare audio già pubblicato.

## Pipeline CI e distribuzione

- Le ultime otto Actions prima di queste modifiche risultano completate con successo; l'ultima è [run 38750546105](https://github.com/YassirJabane/VeryDisco/actions/runs/38750546105), SHA `0af14a7`.
- Aggiunto job `container-smoke`: costruzione dell'immagine di produzione e verifica avvio non-root, SQLite, frontend, protezione API e fpcalc. Pubblicazione condizionata sia ai test sia a questo job. Verifica di bootstrap, non simulazione dei servizi esterni.
- Aggiunta serializzazione dei workflow per ref per evitare corse fra pubblicazioni beta. Tag stabili limitati a `vMAJOR.MINOR.PATCH` e commit contenuti in main.
- `.dockerignore` esclude librerie musicali, download, configurazione privata, database, virtualenv e node_modules dal contesto di build.
- APScheduler vincolato a `<4`, coerentemente con l'API 3.x importata dal codice.
- Restano da migliorare: lock delle dipendenze Python e delle immagini base, test browser dei flussi completi, migrazione di un DB reale anonimizzato, controllo vulnerabilità dell'immagine effettivamente pubblicata. Il workflow non configura `platforms`: QEMU da solo non produce un'immagine ARM.

## Verifiche

- Baseline: 160 test backend/regressioni passati; 3 test frontend passati.
- Dopo i fix: 184 test backend/regressioni passati (24 in più), 5 test frontend passati, TypeScript e build Vite/PWA completati. YAML workflow, sintassi dello smoke script e `git diff --check` verificati. Le 16 warning backend riguardano principalmente l'uso legacy di `datetime.utcnow`; nessun test fallito.
- `npm audit --json`: zero advisory sulle dipendenze risolte dal lockfile, controllo effettuato il 15 ottobre 2026.
- OSV sulle versioni Python installate localmente: advisory per `ecdsa 0.19.2`, `python-jose 3.5.0` e il tool locale `pip 25.1.1`. Non è una scansione dell'immagine Docker pubblicata.
- [ecdsa / Minerva](https://github.com/tlsfuzzer/python-ecdsa/security/advisories/GHSA-wj6h-64fc-37mp) riguarda firme ECDSA/operazioni P-256. [python-jose / DER algorithm confusion](https://github.com/advisories/GHSA-3qf3-8w2g-rqmx) riguarda l'uso di chiavi pubbliche asimmetriche come segreti HMAC senza la corretta restrizione degli algoritmi. L'app usa un segreto casuale simmetrico e `algorithms=["HS256"]`: i percorsi descritti non corrispondono all'autenticazione attuale. Questa è una valutazione del codice, non un risultato "zero vulnerabilità"; resta consigliata la rimozione delle dipendenze crittografiche inutilizzate/migrazione della libreria JWT.
- Docker Engine non disponibile su questo host: il nuovo smoke test viene eseguito su GitHub Actions dopo il push. Nessun test end-to-end con slskd, Navidrome e volume NAS reali.

## Problemi residui da chiudere prima della stabile

1. **Unificare le scritture sulla libreria.** `scheduler._promote_track_sync`, merge/upgrade ed endpoint legacy di retag/organize hanno percorsi diversi dal nuovo staging. La promozione dei preferiti sposta prima di taggare; alcune operazioni legacy cancellano tutti i tag. Il coordinatore serializza scan/apply/rollback, ma non ancora tutti i download e tutte le mutazioni. Serve un'unica operazione con journal, piano, conflitti e ripristino.
2. **Completezza nell'automatismo dei preferiti.** `sync_navidrome_starred` considera l'album presente se la cartella contiene qualunque file. Una sola traccia appena promossa può quindi impedire il download dell'album restante. Deve usare la tracklist ufficiale e i brani mancanti.
3. **Recording MBID fino al trasferimento.** Il controllo UI ora lo usa, ma `DownloadTrackRequest` e il grab restano basati su artista/titolo/album. L'identità va trasmessa anche alla selezione, al fingerprint e ai tag finali; i titoli omonimi non bastano.
4. **Formati coerenti.** Preset e filtri ammettono WAV/ALAC, mentre reader/indice e vari scan non li coprono; `sync.embed_metadata` tratta ancora `.ogg` con il writer FLAC. Serve una matrice unica e fixture audio vere per ogni formato annunciato.
5. **Instrumental legato al percorso.** Gli override hanno `filepath` come chiave; rinomine e trasferimenti non migrano sistematicamente il flag. Va preservato nei tag/identità persistente e durante ogni spostamento. Nessun risultato mancante deve essere interpretato come instrumental.
6. **Feedback completo e coerente.** Il refresh conserva un massimo di 1000 elementi e la UI Feedback mostra normalmente i primi 100; manca una paginazione completa. Lo snapshot periodico può concorrere con un'aggiunta/rimozione e con il cambio account: servono versione del profilo e aggiornamenti serializzati prima del commit.
7. **Identità artista/normalizzazioni.** Le regole sono distribuite tra sync, indice, provider e DB refresh. `artist_norm` spesso rappresenta Album Artist. Servono entità artist/recording/release con MBID, crediti separati e una normalizzazione unica per la ricerca; preservare i crediti storici e tenere separati progetti distinti.
8. **Catalogo e copie.** Alcuni controlli di completezza contano file invece di tracce ufficiali abbinate; duplicati ed edizioni possono gonfiare il totale. Naming/organize, sidecar e riferimenti M3U devono condividere un ledger degli spostamenti. Preferibile quarantena recuperabile alle cancellazioni definitive.

## Direzione consigliata

Prima stabilizzare identità e mutazioni dei file; poi centralizzare la queue persistente e l'invalidazione cache/eventi. Le feature con maggior valore sono un pannello "corrispondenza e motivo della decisione" per ogni brano, quarantena/undo, gestione esplicita delle edizioni, e aggiornamenti UI tramite eventi con dataset e timestamp di freschezza. La release main va promossa solo dopo aver verificato la stessa immagine beta con download, preferiti, import/edit playlist, rename, scan, riavvio e rollback su una libreria di prova reale.
