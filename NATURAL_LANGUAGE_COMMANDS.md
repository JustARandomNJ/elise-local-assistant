# Natural-language commands

Elise first normalizes apostrophes and applies conversational restrictions, then
uses a conservative declarative registry for high-confidence command aliases.
Every match becomes the canonical slash command and returns to its existing
handler. Consequently, confirmations, permissions, vault locks, provider
restrictions, and failure-terminal behavior are identical for slash and prose.

## Canonical mapping

The supported deterministic mappings are below. Each row represents a phrase
family with several registered forms; examples are representative rather than
an exhaustive grammar.

| Area | Canonical command | Representative natural language |
|---|---|---|
| Help/session | `/help` | `show the command help`; `what commands can I use?`; `show me what you can do` |
| Help/session | `/clear` | `clear our conversation`; `forget the current chat context`; `start a fresh conversation` |
| Help/session | `/exit` | `exit Elise`; `shut down Elise`; `close this session` |
| Documents | `/documents` | `show my indexed documents`; `list my documents`; `what documents do you have?` |
| Documents | `/search <query>` | `search my documents for <query>`; `find <query> in my documents`; `look through the documents for <query>` |
| Documents | `/reindex` | `reindex my documents`; `refresh the document index`; `scan my documents again` |
| Files | `/list-files [root] [path] [--recursive]` | `list files in documents`; `show files in the project`; `recursively list files in documents notes` |
| Files | `/read-file <root> <path>` | `read documents notes.txt`; `show the project file README.md`; `open documents file plans/today.md` |
| Profile | `/preview-profile <file>` | `preview profile file profile.json`; `validate the profile file import.json`; `inspect profile JSON profile.json` |
| Memory | `/memories [category]` | `show your memories`; `what do you remember about me?`; `show my project memories` |
| Memory | `/search-memories <query>` | `search your memories for <query>`; `what do you remember about <query>?`; `look through memory for <query>` |
| Memory | `/memory <id>` | `show memory <id>`; `inspect stored memory <id>`; `show details for memory <id>` |
| Memory | `/memory-why [limit]` | `why did you use my memories?`; `show recent memory retrieval reasons`; `show the last 5 memory reasons` |
| Memory | `/remember <category> <text>` | `remember as a preference that <text>`; `save as a project memory: <text>`; `store this fact: <text>` |
| Memory | `/forget <id>` | `forget memory <id>`; `delete stored memory <id>`; `remove memory number <id>` |
| Review | `/memory-review status\|on\|off` | `show memory review status`; `enable automatic memory review`; `turn memory review off` |
| Review | `/memory-suggestions [all]` | `show pending memory suggestions`; `list memory review suggestions`; `show all memory suggestions` |
| Review | `/approve-memory <id>` | `approve memory suggestion <id>`; `accept memory suggestion <id>`; `save suggestion <id>` |
| Review | `/reject-memory <id>` | `reject memory suggestion <id>`; `decline memory suggestion <id>`; `discard suggestion <id>` |
| Internet | `/internet status\|on\|off` | `show internet status`; `go online`; `turn the internet off` |
| Internet | `/web-search <query>` | `search the web for <query>`; `look online for <query>`; `find this on the internet: <query>` |
| Internet | `/fetch-url <url>` | `fetch <url>`; `read the page at <url>`; `open this web page: <url>` |
| Tools | `/tools` | `show available tools`; `what tools can you use?`; `list your tools` |
| Tools | `/tool-permissions` | `show tool permissions`; `what are the tool permission rules?`; `list tool safety settings` |
| Tools | `/tool-log [limit]` | `show the tool log`; `show recent tool activity`; `show the last 10 tool actions` |
| ASCII | `/ascii on\|off\|status` | `enable ASCII art`; `stop using ASCII art`; `is ASCII mode on?` |
| Spotify | `/spotify-login` | `log into Spotify`; `connect my Spotify account`; `start Spotify login` |
| Spotify | `/spotify-status` | `is Spotify connected?`; `what's my Spotify status?`; `check Spotify` |
| Spotify | `/spotify-logout` | `log out of Spotify`; `disconnect my Spotify account`; `remove Spotify login` |
| Spotify | `/spotify-devices` | `what Spotify devices are available?`; `show my Spotify devices`; `where can you play music?` |
| Spotify | `/spotify-device <n>` | `play Spotify on device <n>`; `switch Spotify to the second device`; `use <device name> for Spotify` |
| Spotify | `/music-select <n>` | With a pending music list: `play the second one`; `choose result 2`; `pick <unique track name>` |
| Spotify | `/music-cancel` | `cancel the music selection`; `never mind those Spotify results`; `clear the pending songs` |
| YouTube | `/play-latest <creator>` | `play the latest Alpharad video`; `open the newest video from Alpharad`; `watch Alpharad's latest upload` |
| YouTube | `/creator-select <n>` | With pending creators: `the first one`; `choose result 2`; `use the Alpharad channel` |
| YouTube | `/creator-cancel` | `cancel the creator selection`; `never mind those channels`; `clear the pending creator choice` |
| YouTube | `/video-select <n>` | With pending videos: `play number 3`; `choose the second video`; `play <unique title>` |
| YouTube | `/video-cancel` | `cancel the video selection`; `never mind those videos`; `clear the pending video results` |
| YouTube | `/creator aliases` | `show saved creator aliases`; `list my YouTube creators`; `what creator aliases are saved?` |
| YouTube | `/creator forget <alias>` | `forget the creator alias <alias>`; `remove <alias> from creator aliases`; `delete the saved creator <alias>` |
| Display | `/display status\|pause\|resume\|close` | `check the video window`; `pause the media display`; `resume the media display`; `close the video window` |
| Host | `/time` | `what time is it?`; `show the local time`; `what's the computer's time?` |
| Workflows | `/workflows [limit]` | `show recent workflows`; `list my workflows`; `show the last 5 workflows` |
| Workflows | `/workflow <id>` | `show workflow <id>`; `check workflow <id>`; `what is the status of workflow <id>?` |
| Workflows | `/run-workflow <id>` | `run workflow <id>`; `execute workflow <id>`; `start workflow <id>` |
| Workflows | `/resume-workflow <id>` | `resume workflow <id>`; `continue workflow <id>`; `restart workflow <id>` |
| Workflows | `/cancel-workflow <id>` | `cancel workflow <id>`; `stop workflow <id>`; `abort workflow <id>` |

File creation/replacement, directory creation, private-vault administration,
profile import, memory editing/policy/expiry, and workflow construction remain
slash-explicit. Their argument grammars contain arbitrary paths or sensitive
content, and broad conversational aliases would be more likely to over-route.
`/project-state` and raw audit/debug-oriented variants are likewise intentionally
not broadened. This does not remove or alter any canonical command.

## Inventory and safety classification

The help text and command handlers expose 62 slash-command families. Forty-six
have deterministic conversational equivalents (including contextual media
selection); their classifications are stored alongside their patterns in
`COMMAND_INTENTS`. The remaining 16 stay slash-explicit:

| Canonical command | Class | Reason for no broad natural-language alias |
|---|---:|---|
| `/create-file`, `/replace-file`, `/append-file`, `/replace-text`, `/create-dir` | C | Writes arbitrary path/content; slash syntax keeps the confirmation preview deliberate. |
| `/remember-personal`, `/edit-memory`, `/set-memory-policy`, `/expire-memory` | C | Changes private text or retrieval/privacy policy. |
| `/private-memory <command>` | C | Locked encrypted vault administration; explicit syntax is part of its privacy boundary. |
| `/import-profile <file>` | C | Stages a batch of personal-memory suggestions from a file. |
| `/new-workflow`, `/new-summary-workflow`, `/new-actions-workflow`, `/new-compare-workflow` | C | Constructs persistent, later write-capable workflows from arbitrary paths. |
| `/project-state` | D | Meta/developer implementation report, not a normal conversational operation. |

Class A is safe/read-only, B is low-risk session or persistent state, C is
write/destructive/sensitive, and D is meta/developer-only. C routes that do have
NL forms—such as `/forget`, `/approve-memory`, and `/run-workflow`—still enter the
same confirmation or approval-gated handler.

## Spotify music playback

Elise can search Spotify for tracks and send playback controls to the official
Spotify client or a Spotify Connect device. Elise never downloads, proxies,
caches, decodes, or streams Spotify audio. Spotify playback requires internet,
a Spotify Premium account, and an available controllable Spotify device; the
rest of Elise remains offline-capable when Spotify is unavailable or unconfigured.

Setup requires your own Spotify Developer application. Register the redirect URI
`http://127.0.0.1/callback` without a port; Elise adds a dynamic loopback port to
the authorization request as Spotify permits for IP-literal loopback redirects. Then
set its Client ID locally as `ELISE_SPOTIFY_CLIENT_ID`. Do not use `localhost` or
add a client secret. Elise requests only `user-modify-playback-state` and
`user-read-playback-state`. OAuth access and refresh credentials are stored in
the ignored, narrowly scoped `data/spotify_tokens.json` file and are never shown
in normal output or audit logs.

Use `/spotify-login`, `/spotify-status`, and `/spotify-logout` for authentication.
Use `/spotify-devices` and `/spotify-device <number>` to inspect or choose a
Spotify Connect device. Active unrestricted devices are preferred; a sole usable
inactive device is targeted directly, while multiple inactive devices
require a choice. Device state is refreshed and restricted devices are never
controlled.

Examples include `play Bohemian Rhapsody on Spotify`, `play the song Pink + White
by Frank Ocean`, `play music by Kendrick Lamar on Spotify`, `find songs called
Ivy by Frank Ocean on Spotify`, `pause the music`, `resume Spotify`, `next song`,
`previous song`, and `what's playing?`. Ambiguous/list-only searches show up to
five results; use `/music-select <number>` or `/music-cancel`. Music-result,
video-result, and creator selections are independent. Explicit Spotify failures
never fall back to YouTube.

## Optional ASCII art

Elise can generate model-written visual ASCII art when the request explicitly
mentions ASCII, such as `draw me a cat in ascii` or `make ascii art of a flower`.
Text requests such as `write HELLO in ascii` use a compact deterministic local
banner font instead of asking the model to construct letters. Generic words such as
`art`, `draw`, and `character` do not enable this feature by themselves.

Use `/ascii on`, `/ascii off`, or `/ascii status` to control optional ASCII-art
presentation for the current session. The preference starts off in every fresh
session; an explicit ASCII request works regardless of that preference. ASCII
art and banners are rendered in a fenced text block to preserve spacing. Elise
does not perform image conversion or call an external ASCII-art service.

Visual ASCII generation follows a locally stored style guide inspired by
established ASCII-art conventions. The [ASCII Art Archive](https://www.asciiart.eu/)
is listed as a human/design reference for those conventions, not as an artwork
database or automatically harvested content source. Elise does not contact the
site during generation, so visual ASCII art remains local-first and works offline.

ASCII-art detection is separate from media and conversational intent routing.
Subjects and requested text are passed through unchanged and are never
fuzzy-corrected.

## Natural-language media commands

Elise routes clear requests for a creator's latest YouTube upload through the
same permission, creator-resolution, provider, alias-cache, and Media Display
path as `/play-latest`. It does not claim to understand every possible
sentence.

## Creator-constrained video search

Elise also accepts a creator plus an opaque topic, such as `play an alpharad
nuzlocke`, `pull up alpharad's latest nuzlocke video`, `play alpharad's most
viewed nuzlocke video`, and `show me alpharad nuzlocke videos`. These become a
host-validated `MediaVideoQuery` with `creator`, `query`, an ordering enum
(`relevance`, `date`, or `view_count`), and `list_only`.

The creator is resolved first using the trusted alias, handle, channel-ID, and
confirmation-gated discovery flow. Elise then searches only that validated
channel using `type=video`, `channelId`, the unchanged topic as `q`,
`maxResults=10`, and `videoEmbeddable=true`. Every result is revalidated with
`videos.list`; non-public, unavailable, non-embeddable, malformed, upcoming,
and wrong-channel entries are discarded.

The first eligible API-ranked match plays automatically. `show me` and `find
me some` list up to five matches without playback; use `/video-select
<number>` or `/video-cancel`. This pending state is separate from creator
confirmation. A new search invalidates a stale video selection.

Creator-only requests such as `play the latest alpharad video` always retain
the existing `media.play_latest` uploads-playlist path. A missing creator asks
which creator to use, and a missing topic never becomes an empty query.

## Supported phrase families

The deterministic grammar composes request framing (`play`, `open`, `show`,
`pull up`, `bring up`, `put on`, `start`, `watch`, `load`, `get ... playing`,
`let me watch`, or `I want to watch`) with recency (`latest`, `newest`, `most
recent`, `recent`, or `newest available`) and a media noun (`video`, `upload`,
or `YouTube video`). Polite framing such as `please`, `can you`, `could you`,
`would you mind`, `go ahead and`, `for me`, and `right now` is optional.

Creator placement may be `latest <creator> video`, `<creator>'s latest video`,
`latest video from/by <creator>`, or `latest video <creator> posted`. A small,
explicit typo list applies only to grammar words, including `pul up`, `brign
up`, `latesst`, `vidoe`, and `plaay`.

## Deterministic and fallback parsing

Tier 1 is deterministic and handles the documented families without invoking
a model. If an input still looks like an imperative media request, Tier 2 may
send only that one input to the configured local Ollama model. The model can
only propose a strict `media.play_latest` or `media.play_query` object; it
cannot call a service or a tool. Query proposals are limited to the three
ordering values above and may not provide IDs or URLs. Unknown fields or
intents, malformed output, low confidence, timeout,
and model failure are rejected. Host validation and normal command permissions
remain authoritative.

No memories, retrieved documents, API keys, system prompts, or conversation
history are included in the fallback request.

## Conservative behavior and security boundaries

Clear negations, informational questions, statements, hypotheticals, unrelated
second actions, and ambiguous references do not execute. A request with a
missing creator asks `Which creator should I use?`. Broad creator discovery can
still require the existing confirmation, while a saved alias or unique exact
result retains its existing automatic behavior.

Creator targets retain capitalization, internal punctuation, spaces,
apostrophes, ampersands, `@` handles, and direct `UC` channel IDs. Only
grammatical possessive suffixes, surrounding whitespace, and request
scaffolding are removed. Creator names are never fuzzy-corrected.

Both parser tiers reject URLs, filesystem paths, shell syntax, control
characters, secret-like targets, and multiple requested actions. A recognized
command—including a provider or display failure—ends command routing and never
falls through to research or general model tool selection.
