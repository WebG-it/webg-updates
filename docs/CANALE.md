# Il canale di aggiornamento dei prodotti a pagamento

Vale per WebG Pay e WebG Automations. Chi acquista una licenza riceve da WebG una **chiave di download**: la scrive una volta in Joomla e da quel momento
*Sistema > Aggiorna* trova e installa le nuove versioni.

## Dove si mette la chiave

1. In Joomla vai su *Sistema > Siti di aggiornamento* e apri la riga *WebG Pay* (o *WebG Automations*).
2. Incolla la chiave in *Chiave di download* e salva.
3. Da *Sistema > Aggiorna > Estensioni* controlla gli aggiornamenti.

La chiave si inserisce una sola volta e resta dopo gli aggiornamenti. Dopo la prima installazione Joomla mostra un messaggio che ricorda dove metterla.

## Come funziona, in breve

Joomla legge da questo repository un piccolo file (`webgpay.xml`, `webgautomations.xml`) che dice qual e' l'ultima versione. Lo zip si scarica da
`https://updates.webg.it/<prodotto>/<versione>.zip`, dove la chiave viene controllata. Questo repository contiene solo quei file: nessun codice dei prodotti, nessuno zip, nessuna chiave.

## Senza chiave, con chiave sbagliata o scaduta

Joomla vede comunque che esiste un aggiornamento, ma il download viene rifiutato e non si installa nulla; il sito continua a funzionare come prima.
Joomla mostra un errore di scarico con il link all'indirizzo dello zip: aprendolo si legge il motivo. Il messaggio e' sempre lo stesso per ogni chiave non valida.
Una chiave scaduta si rinnova scrivendo a WebG.

## Se il canale non risponde

I siti non si rompono: sono solo gli aggiornamenti a non scaricarsi. Si riprova piu' tardi; se il problema dura, WebG puo' consegnare lo zip a mano, da installare in *Sistema > Installa*.

## Attenzione alla chiave

La chiave autorizza solo a scaricare gli aggiornamenti di quel prodotto e si revoca in un istante, ma non va condivisa. Se un download fallisce, Joomla mostra a schermo l'indirizzo con la chiave dentro:
se mandi uno screenshot di quell'errore al supporto, copri la chiave. Se una chiave e' finita dove non doveva, chiedi a WebG di sostituirla.

## Contatti

Licenze, chiavi e assistenza: [www.webg.it](https://www.webg.it).
