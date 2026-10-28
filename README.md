# WebG updates

Update channel of the **paid** Joomla extensions of [WebG](https://www.webg.it). This repository holds **only** the update XML files
read by Joomla (no code of the extensions, no zip, no key, no secret). The zips live in private Releases and are handed out by a
distributor that checks a download key; how it all works is in [docs/CANALE.md](docs/CANALE.md).

The free extensions of WebG have their own repositories and update servers
([WebG-it/joomla-extensions](https://github.com/WebG-it/joomla-extensions)); this channel is only for the paid ones.

| Extension | Package | Update server (Joomla reads it) |
|---|---|---|
| WebG Pay | `pkg_webgpay` | `https://raw.githubusercontent.com/WebG-it/webg-updates/main/webgpay.xml` |
| WebG Automations | `pkg_webgautomations` | `https://raw.githubusercontent.com/WebG-it/webg-updates/main/webgautomations.xml` |

The packages declare these addresses and `<dlid prefix="key=">` in their manifest, so Joomla asks for the **download key** in
*System > Update Sites* (open the extension, fill in *Download Key*). Without a valid key Joomla still sees that an update exists, but the
download is refused with a clear message. The download address in the XML is `https://updates.webg.it/<product>/<version>.zip`; Joomla adds
`?key=<your key>`.

## Files

```
webgpay.xml, webgautomations.xml   the update XML, written by tools/release.py (version 0.0.0 = nothing published yet)
tools/release.py                   publishes a version: private Release with the zip, then these XML (see its header)
webgpay-changelog.xml               the changelog Joomla shows on the Update screen (public, no key), written by release.py from the "In breve" paragraphs of CHANGELOG.md
tools/check_xml.py                 checks the XML (fields, download host must be updates.webg.it, sha256, no key-like value, no private address); run on every push by .github/workflows
tests/                             tests of the two tools with a stand-in for gh:   python -I tests/test_release.py
e2e/                               end-to-end test on a real Joomla 5 and 6 in Docker with a fake channel (t_update_channel.py)
docs/CANALE.md                     for who installs: where to put the key, what happens without it, contacts
```

## Publish a version

```bash
python -I tools/release.py --product webgpay --zip ../webgpay-git/dist/pkg_webgpay-0.2.2.zip --dry-run   # checks only
python -I tools/release.py --product webgpay --zip ../webgpay-git/dist/pkg_webgpay-0.2.2.zip --push
```

This repository is public only because Joomla's update servers must be readable without a login. Support and licenses: [www.webg.it](https://www.webg.it).
