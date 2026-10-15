# Numeri traccia, inventario rebuild e date complete

## Evidenza e limiti

L'export utente mostra un piano di 22 file per FATD Scary Hours con proposte `2023 → 2023-10-06`; non contiene il percorso o i tag reali di Rich Baby Daddy. Non basta quindi a dimostrare che tutti i file audio contengano soltanto l'anno. Il codice ha però difetti riproducibili che spiegano questi sintomi, verificati su file temporanei e non applicando modifiche al NAS.

## Correzioni

- L'inventario rebuild ricostruiva i tag invariati dall'indice, che conteneva `year` ma non la data completa e altri campi gestiti. Ora un rebuild esplicito legge i tag completi dal disco, anche con fingerprint invariato. Il costo è una lettura per file durante lo scan esplicito; i normali elenchi UI continuano a usare l'indice.
- Un indice parziale era anche l'unico elenco dei file da esaminare. Il nuovo inventario scopre gli audio non ancora indicizzati, esclude staging nascosto e symlink fuori root e raggruppa cartella principale + `Disc 01/Disc 02` in un piano unico. Non unisce automaticamente cartelle di edizioni diverse.
- Il riuso single-track confrontava la directory immediata con l'album root: una traccia in Disc 01 poteva essere spostata nella root e ritaggata senza numero, data o MBID. Ora una traccia già nell'albero dell'album non viene riscritta senza motivo. Se va ricollocata, lo staging conserva posizione/disco, data, identità, artwork, crediti e lyrics, e aggiorna immediatamente i percorsi nell'indice. Errori lasciano il job fallito, non un successo fittizio.
- Il fallback di un album conservava il release MBID solo nella scrittura finale, non nel lookup: poteva perdere il contesto della release e salvare track 00. Il lookup ora riceve il MBID esatto e il fallback porta il contesto ufficiale già risolto (posizione, totale per disco, data, crediti e recording ID), anche nel journal/retry. Con release esplicita ma posizione non verificata, conserva il raw e non pubblica un file 00. I provider non possono sovrascrivere una posizione ufficiale già abbinata.
- Rebuild abbina anche un titolo con credito finale `(feat. ...)` al titolo senza tale credito, senza eliminare qualificatori di mix/versione. Un numero 0 può quindi ricevere la posizione ufficiale per titolo univoco.
- L'indice ha una migrazione additiva per `date` completa, distinta da `year`, mantenuta da scansione completa, refresh incrementale e riepilogo album. La card Library Manager mostra la data completa. Il writer MP3 v2.4 non aggiunge più il vecchio TYER insieme ai timestamp completi. L'harmonizer corregge anche una data presente ma incompleta/diversa, non soltanto quella vuota.
- Download e rebuild usano la stessa politica già prevista dal rebuild: prima data originale del release group, altrimenti data dell'edizione scelta. La data dell'edizione resta disponibile separatamente dal provider. Non vengono inventati mese/giorno quando MusicBrainz conosce soltanto l'anno.
- Il normalizzatore dell'identità album è condiviso fra scansione completa, refresh incrementale e batch availability: preserva Deluxe/Scary Hours/remaster ed elimina soltanto il suffisso disco. Prima la scansione completa eliminava ancora l'edizione mentre il refresh la preservava.

## Verifiche

239 test backend (9 regressioni nuove), 26 frontend, TypeScript e build Vite/PWA. Restano 7 warning legacy su datetime.utcnow. Fra le nuove prove: 22 file Disc 01 + Rich Baby Daddy non indicizzata in root, track 0 e feat nel titolo; un unico piano 23/23 con proposta track 20; apply e secondo scan senza falsi cambi data; riuso senza trasferimento con date/posizioni preservate; fallback a provider indisponibile con e senza posizione ufficiale; persistenza della data completa nel riepilogo album; coerenza della data download/rebuild. I file MP3 delle prove di tagging sono fixture ID3, non una verifica acustica su media di produzione.

## Come applicare ai file esistenti

Aggiornare beta, poi avviare **un nuovo scan Metadata Rebuild**: il vecchio piano da 22 file non cambia retroattivamente. Verificare che la traccia compaia e proponga posizione 20, quindi applicare il piano dell'album. Sono creati i backup del rebuild e viene richiesto il rescan Navidrome. Non serve riscaricare l'album. Il rebuild modifica i tag, non rinomina automaticamente i file esistenti. Se la traccia resta assente, controllare il suo percorso effettivo e i warning `Skipping unreadable metadata rebuild file`: una cartella di un'altra edizione o un errore di lettura non vengono risolti forzando l'unione.

La migrazione SQLite non inventa date per le vecchie righe: si popolano al prossimo refresh/apply/scansione. Nessuna operazione su libreria o trasferimenti reali è stata eseguita in questa tranche.
