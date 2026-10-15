# Metadata, lyrics, edizioni e logs — beta 15 ottobre 2026

## Difetti verificati nel codice e corretti

- Il genere era accettato dal writer ma quasi mai fornito dai chiamanti. Ora il resolver recupera generi per recording/release MBID da MusicBrainz, con fallback ai generi di un album Deezer che corrisponde esattamente ad artista e titolo completo. I risultati opzionali MusicBrainz sono cached per ID; un errore temporaneo non viene memorizzato come risultato definitivo.
- Il wipe eliminava anche dati musicali utili. Una whitelist conserva genere, ISRC, label, data originale, release-group/artist MBID, barcode, compositore, autore del testo, direttore, copyright, lingua, sottotitolo disco e ReplayGain. I valori noti del provider hanno precedenza; i link promozionali vengono ancora rimossi. I testi incorporati vengono preservati quando non esiste un rimpiazzo. Le compilazioni Various Artists hanno il flag corretto.
- Il wipe/rebuild lavora su una copia privata e pubblica solo dopo la riuscita di tutti i tag. Su hardlink mantiene l'inode con copia di ripristino. OGG usa il writer Vorbis corretto, non FLAC. I formati non supportati falliscono esplicitamente invece di dichiarare un embedding mai eseguito.
- Anche download album, copie riutilizzate e promozione da Explore ora preparano i tag prima della pubblicazione. Nell'upgrade gli originali e gli eventuali file sostituiti sono recuperabili in `/data/recovery/<nome-root-musica>/…` nel container; fuori Docker, in `.verydisco-recovery` nella directory madre della root musica. È possibile impostare `VERYDISCO_RECOVERY_DIR` per scegliere una destinazione dedicata. Il percorso di ogni copia è scritto nei log; la destinazione deve rimanere fuori dalla libreria indicizzata.
- Lyrics Manager escludeva esplicitamente i testi plain dalla query degli upgrade. Ora distingue missing/plain, attende la conclusione del refresh, aggiorna l'indice dopo il salvataggio e non rimuove una traccia se il testo rimane plain. Rileva timestamp anche dopo intestazioni lunghe; la sola estensione `.lrc` non prova che il testo sia synced.
- I log iniziali venivano persi prima dell'assegnazione del DB e quelli nei worker thread non erano affidabili. Il nuovo writer riceve anche i log pre-DB e dei thread, persiste in ordine e trasmette dopo il commit. SSE riproduce lo storico dall'avvio, usa ID per riprendere senza duplicati, heartbeat e riconnessione dei client lenti. Il menu ora invia davvero il limite al backend; il default è tutto dall'avvio. La pausa blocca lo scroll, non la raccolta.
- Scary Hours Edition non rientrava nei nomi di edizione riconosciuti; senza playlist attive un ramo non cercava nella libreria; la qualità veniva confrontata solo col primo profilo. Ora le edizioni nominate sono riconosciute, il lookup libreria è indipendente dalle playlist e si riusano file che soddisfano un profilo configurato. La scelta di un'edizione nella Artist tab non forza più il riscaricamento dal generico stato full del gruppo. Titoli/recording MBID in conflitto non sono riutilizzati.
- Le cover potevano appartenere a un'altra edizione: Deezer eliminava parentesi e qualificatori; metadata rebuild preferiva iTunes e poi la release-group alla release. Ora il release MBID ha priorità, senza fallback implicito alla cover di un'altra release del gruppo; i fallback testuali richiedono il titolo completo. JPEG/PNG vengono verificati, e CAA segue i redirect.
- L'automatismo dei preferiti non certifica più un album dalla sola cartella non vuota: confronta la tracklist ufficiale una volta per file. I duplicati e i recording MBID in conflitto non possono riempire i brani mancanti.
- Il refresh puntuale dell'indice poteva fallire su NTFS per inode più grandi degli interi SQLite: gli identificatori fuori range sono conservati come stringhe esadecimali senza perdita di precisione.

## Ispezione dei campi e limiti dei provider

Questa verifica riguarda writer, resolver e fixture locali, non un censimento dei file del server NAS. Dopo il wipe devono essere ricostruiti titolo, crediti artista/album artista, album, posizioni/totali disco e traccia, date e identificatori conosciuti; cover e lyrics devono avere una corrispondenza verificata. I campi opzionali non vanno inventati: genere, label e ISRC possono mancare anche su MusicBrainz. Non si deduce il genere dall'artista e non si considera instrumental una traccia per la quale non sono stati trovati testi.

Metadata Rebuild recupera e propone anche i generi della release, label, ISRC disponibili, data originale e identificatori aggiuntivi. Le informazioni locali presenti vengono conservate; i nuovi download ricevono gli stessi campi attraverso il resolver comune. Per correggere cover già scritte serve applicare un rebuild con artwork sulla release corretta: un semplice scan non riscrive retroattivamente tutte le immagini né interroga tutti i provider per ogni file.

## Pipeline remaster

1. Separare titolo visualizzato e identità di versione. Riconoscere suffissi come `2011 Remaster`, `Remastered 2011` e `Remastered`; conservare il titolo originale nei tag. Numeri che sono il titolo (`1999`, `1985`) non vengono eliminati.
2. Usare release MBID per l'edizione e recording MBID per la registrazione. Il recording MBID da solo non dimostra che due release abbiano lo stesso mastering.
3. Filtrare candidati slskd, lookup locali, merge e rebuild: originale, remaster non datato e remaster di anni diversi non sono intercambiabili automaticamente. Il contesto album/cartella viene considerato anche quando il titolo della singola traccia non porta il suffisso.
4. Riutilizzare i brani condivisi di un'edizione estesa solo se non ci sono conflitti di registrazione/master; scaricare i mancanti. Lasciare nel vecchio album eventuali bonus o versioni non appartenenti alla tracklist scelta.
5. Solo per cercare **lyrics**, consentire il titolo base senza suffisso remaster, mantenendo la verifica artista/album. Questo non autorizza a prendere audio o copertine di un altro mastering.

Limite intenzionale: non si pretende di riconoscere il mastering dai campioni audio. Release prive di indicazioni affidabili e suffissi non riconosciuti richiedono la selezione esplicita dell'edizione; non viene indovinato l'anno.

## Verifica e lavoro residuo

Test locali: **206 test backend/regressioni e 7 test frontend passati**, TypeScript, build Vite/PWA e controllo del diff. Il workflow beta ripete i test e lo smoke Docker/Linux prima della pubblicazione GHCR. Nessun download reale o riscrittura massiva della libreria NAS è stato eseguito da questa sessione.

Restano aperti i lavori architetturali dell'audit precedente non completati da questa tranche: un unico ledger/coordinatore per **tutte** le mutazioni legacy e i relativi override instrumental/M3U, recording MBID end-to-end su ogni richiesta/grab, copertura coerente WAV/ALAC e altri formati, paginazione/serializzazione completa del feedback e centralizzazione definitiva delle entità artista. Questi punti non vengono dichiarati risolti dalla sola presenza di test verdi.
