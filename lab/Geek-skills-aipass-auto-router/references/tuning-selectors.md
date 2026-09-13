# Fixing selectors when the site changes

Every element the router touches is a list of CSS selectors in
`config/models.json` under `selectors`. Each list is tried in order and the
**last visible match** wins — chat inputs and the newest message are usually
last in the DOM. If the site rewrites its markup, only this file needs editing.

## Find out what broke

```bash
python3 scripts/router.py doctor
```

| Field | If false |
|-------|----------|
| `input_found` | fix `selectors.input` |
| `send_button_found` | fix `selectors.send_button` — or leave it, the router falls back to pressing Enter |
| `model_picker_found` | fix `selectors.model_picker`, otherwise model switching is impossible |
| `assistant_message_selector_matches` | fix `selectors.assistant_message` — false is fine on an empty chat, but on a chat with replies it means the text-diff fallback will be used |

```bash
python3 scripts/router.py models
```

`picker_found: false` or an empty `options` list means the dropdown selectors
are wrong. The `options` it prints are the site's real model names — every
`match` entry in `config/models.json` is matched case-insensitively as a
substring of those, so copy from here.

## Get the right selector

In the Brave window, right-click the element → Inspect. Look for a stable hook,
in this order of preference:

1. `data-testid`, `data-role`, `data-message-author-role`
2. `aria-label`, `role`, `aria-haspopup`
3. tag plus a structural attribute: `textarea[placeholder]`, `form button[type='submit']`

Avoid generated class names like `.css-1x2y3z` — they change on every deploy.

Try a candidate in the browser console before editing the file:

```js
[...document.querySelectorAll("YOUR SELECTOR")].length
```

One or a small number is right. Zero, or hundreds, is wrong.

## Add it

Put the new selector **first** in the list and keep the old ones as fallbacks:

```json
"input": [
  "div[data-testid='chat-input']",
  "div[contenteditable='true']",
  "textarea"
]
```

Then re-run `doctor`.

## Keep your edits out of the repo

```bash
mkdir -p ~/.aipass-router
cp config/models.json ~/.aipass-router/models.json
```

That copy takes priority over the bundled one, so a skill update will not
overwrite your selectors.

## Answer detection, and why it can look odd

Preferred path: `selectors.assistant_message` matches, and the router reads the
last matching element once its text stops growing.

Fallback, when nothing matches: it diffs `document.body.innerText` from just
before the send against after, and takes the new tail. That works on most
layouts but can pick up your own question or a stray sidebar update. If answers
come back with extra text glued on, that is the fallback running — set the real
assistant selector and it goes away.

## Waiting rules

Under `answer` in the config:

| Key | Meaning |
|-----|---------|
| `first_token_timeout_s` | give up on a model that has produced nothing at all |
| `max_wait_s` | hard cap for one answer |
| `stable_for_s` | how long the text must stop changing before it counts as finished |
| `poll_interval_s` | how often to look |

Raise `max_wait_s` for slow reasoning models. Raise `stable_for_s` if answers
get cut off mid-sentence — that means the site pauses while streaming.
