# Interventi sullo snapshot — 2026-10-02

Questo documento aggiorna la revisione iniziale senza modificarne i risultati storici. Le prove sono locali e isolate: non equivalgono a una verifica del container in esercizio.

## Correzioni implementate

- L'API è protetta per impostazione predefinita; restano pubblici soltanto login, setup iniziale e uno stato anonimo minimale. Le richieste di scrittura con origine esterna sono rifiutate; CORS è limitato a origini esplicitamente configurate. Il ruolo admin è riletto dal database a ogni richiesta. Il setup non è riapribile se esistono già utenti.
- Il server statico serve soltanto file sotto `frontend/dist`. I controlli di percorso usano l'appartenenza reale alla directory, non il prefisso testuale. I percorsi salvati dagli utenti sono vincolati alle radici configurate e alla cartella personale; sono state aggiunte verifiche nei flussi di rename, retag, feat, maintenance, copertine e streaming. Lo streaming è limitato ai formati audio.
- Il download delle copertine accetta solo URL HTTPS da host di immagini iTunes/Deezer, non segue redirect, verifica il Content-Type e limita la risposta a 10 MiB. Le operazioni di stop/delete delle attività sono vincolate al proprietario oppure all'admin.
- Il database vuoto ora applica le migrazioni prima degli indici `user_id`; lo stato dei brani preferiti è identificato da utente e ID brano, con migrazione dello schema precedente. Non viene creato un DB alternativo non persistente se `/data` è inaccessibile. La cache su disco rispetta la scadenza. La chiave JWT generata viene conservata su disco e gli aggiornamenti YAML vengono validati prima della scrittura.
- Lo scheduler registra MBID/hash solo dopo un sync completato, non quando è ignorato o fallisce. Le scansioni schedulate usano funzioni interne per ciascun utente e cache separate. Il localizzatore dei download non accetta più il file audio recente ma non corrispondente. La promozione playlist usa uno swap con backup della generazione precedente e recupero all'avvio, anziché sostituire i file uno per uno. Sono corretti il task registry, i due riferimenti a variabili non definite nel download di tracce singole e la formattazione dei pattern di rinomina.
- Docker Compose usa l'immagine pubblicata `latest` e dispone di `build: .`, crea il mount playlist e pubblica la porta soltanto su loopback; il bind della configurazione non crea accidentalmente una directory. Dockerfile usa `npm ci`. README e workflow CI sono allineati; i test UI hanno aspettative aggiornate.

## Verifiche

- `python -m pytest backend/tests docs/audit/test_snapshot_findings.py -q`: 40 test passati dopo le correzioni principali; rieseguire dopo ogni modifica successiva.
- Vitest: 2 test passati; TypeScript `--noEmit` e build Vite passati. Il bundle segnala un chunk grande.
- Non sono stati eseguiti sincronizzazioni complete, download reali, scansioni AcoustID reali o un avvio Docker: Docker Engine non è disponibile in questo ambiente. I test di migrazione SQLite usano DB temporanei.

## Rischi e lavoro ancora aperto

1. Lo swap della directory playlist usa due rename, quindi non è una transazione atomica unica e deve essere verificato su un volume Docker reale. Il recovery conserva i nuovi file in una directory `.interrupted_*` e ripristina la generazione precedente; un crash prima del recovery può lasciare un breve intervallo con la directory di output assente. Serve comunque un backup indipendente prima del rollout.
2. L'audit dipendenze richiede aggiornamenti e rigenerazione verificata dei lockfile. L'ambiente non consente di eseguire un nuovo audit online o installare versioni corrette. Il controllo precedente segnalava 11 advisory npm e 2 pip; non è prova della situazione dopo un rebuild. Non dichiarare risolto il rischio supply-chain.
3. I percorsi legacy in DB fuori radice sono ora rifiutati durante l'autenticazione: vanno corretti localmente e l'utente deve fare login di nuovo. La validazione YAML impedisce di salvare configurazioni invalide, ma una scrittura interrotta del bind mount non dispone ancora di sostituzione atomica. I test non coprono tutte le 95 route e non eliminano possibili race sui symlink o errori nelle operazioni di file mutate.
4. Il primo setup usa un account admin Navidrome ed è pubblico finché l'installazione è vuota; il deployment deve essere locale o protetto dal reverse proxy. Per accesso remoto impostare HTTPS e `auth.cookie_secure: true`.
5. Mancano test d'integrazione contro Navidrome, slskd e ListenBrainz, test Docker Compose su Linux e prova di aggiornamento da un database reale. Prima del deploy, eseguire backup di `data/`, `config.yml`, libreria e playlist.

Comando di prova non distruttiva in ambiente Docker, dopo backup e senza avviare una sincronizzazione: `docker compose config && docker compose build && docker compose up -d` con `schedule.run_on_startup: false` e nessun job schedulato nel periodo di osservazione. Verificare poi `/healthz` e login; non usarlo come prova dei flussi di download/trasferimento.
