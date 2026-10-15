# FATD / MusicBrainz — follow-up dei log del 15 ottobre 2026

## Evidenza

I log forniti mostrano una tracklist MusicBrainz di 29 tracce per la Scary Hours Edition e numerosi messaggi "Skipping download ... using existing". Per quelle tracce non provano un nuovo trasferimento audio: mostrano invece tagging e lookup lyrics ripetuti. L'errore 503 riportato nella pagina rebuild riguarda un'altra richiesta release, e non è dimostrato che abbia avviato il job album.

## Difetti corretti

1. Il controllo "già nella destinazione" confrontava soltanto la directory immediata: le tracce in `Disc 01` venivano trattate come donatori esterni e ritaggate ogni volta. Ora considera l'intero albero dell'album, mantenendo prima i controlli di master/recording MBID e qualità.
2. Le edizioni con nome senza parentesi, come `For All The Dogs Scary Hours Edition`, non riconoscevano la cartella base da migrare. Il confine del nome viene ora dedotto da una cartella base realmente presente, con suffisso nominato `... Edition`; nessuna regola specifica per Drake. Restano i filtri di master, tracklist e recording MBID prima della migrazione; gli originali migrati restano nella recovery.
3. Un percorso `str` veniva usato come `existing_path.name`: corretto con `Path(existing_path).name` nell'aggiornamento M3U.
4. Se una parte dell'album richiedeva download, le tracce riusate non entravano nell'elenco di completezza. Dopo un errore del peer il fallback poteva quindi richiederle nuovamente. Ora sono registrate anche nei tentativi misti, e il fallback riceve solo le tracce mancanti.
5. Una copia/tag locale fallita aggiungeva il file alla coda download. Ora fallisce il job preservando l'originale: riscaricare gli stessi byte non risolve un accesso negato o un errore del writer.
6. La scelta manuale di un peer invocava un helper definito soltanto nella ricerca automatica: rimossa la chiamata inutilizzata. Inizializzati inoltre i fallback metadata anche per album interamente già locali.
7. Rebuild e download avevano limitatori MusicBrainz indipendenti. Ora condividono un unico gate con intervallo minimo 1,25 s, timeout e massimo tre tentativi per errori transitori, backoff e `Retry-After`. Errori persistenti non diventano risultati "release inesistente" nella cache. Il rebuild li presenta come temporanei, senza suggerire che cambiare MBID risolva un'indisponibilità.

MusicBrainz richiede [al massimo una richiesta al secondo](https://musicbrainz.org/doc/MusicBrainz_API); il servizio può rispondere [503 anche per rate limiting o carico](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting). I due limitatori separati erano una causa possibile di sovrapposizione, non una prova che il particolare 503 dipendesse soltanto dall'app. Il gate è condiviso nel processo VeryDisco, non fra altri container/app sullo stesso IP.

## Verifica e applicazione

11 regressioni nuove: retry 503 con Retry-After, gate condiviso, fallimento persistente non cachato, 404 senza retry, edizioni nominate con/senza parentesi, cartelle Disc 01, migrazione della base con recording ID e recovery, coda del peer fallita con fallback limitato alle extra, aggiornamento M3U da percorso stringa e stop senza redownload su errore locale. Test su directory temporanee con servizi simulati, non sulla libreria NAS.

Aggiornare il container beta dopo il workflow. Il codice nuovo non modifica un job già in esecuzione nel vecchio processo. Riprovare il metadata scan quando MusicBrainz è disponibile: i vecchi piani con errore non vengono riscritti automaticamente. Nessuna pulizia manuale o cancellazione dei file già presenti è stata eseguita; eventuali copie già create dalla versione precedente richiedono prima un inventario.

## Follow-up ricerca musicale: dialogo Re-download

Un ulteriore difetto era nella selezione `Release Version` di Search Music: le versioni alternative arrivavano senza stato libreria e la UI usava come fallback lo stato dell'album originario. Una base `full` faceva quindi apparire `full` anche un'edizione non ancora controllata, e la conferma proponeva `force=true`.

Ogni edizione selezionata viene ora controllata separatamente con ID e totale tracce propri (tracklist provider recuperata se il totale manca). Durante il controllo il download è disabilitato; un errore lascia disponibilità sconosciuta, non full/missing. Risposte tardive non sostituiscono una selezione successiva o il ritorno alla versione iniziale. Badge, etichetta del pulsante e azione usano tutti lo stesso stato, senza ereditarlo dalla base.

Album parziale: `Complete album`, richiesta non forzata. Album completo senza upgrade segnalato: `Re-download`, dialogo esplicito che avvisa del nuovo download di tutte le tracce. Nessuna richiesta è inviata prima della conferma; non viene più affermato che il semplice stato `full` certifichi anche la qualità audio. Quattro regressioni UI coprono controllo pendente/parziale, conferma esplicita su album completo, errore del controllo e cambio selezione mentre la richiesta è pendente. Non è una verifica dello stato reale della libreria NAS: se l'edizione è effettivamente già completa, il dialogo di re-download resta intenzionale.
