# Starting Brave with a debug port

The router talks to the browser through the Chrome DevTools Protocol. That
port only exists if the browser was launched with `--remote-debugging-port`.
A running browser cannot be switched into debug mode after the fact — quit it
first.

เบราว์เซอร์ต้องเปิดด้วยแฟล็กนี้ตั้งแต่แรก ปิดโปรแกรมให้หมดก่อน แล้วเปิดใหม่

## Quit completely first

- macOS: `Cmd+Q`, then check with `pgrep -fl "Brave Browser" | head`
- Windows: Task Manager, end every `brave.exe`
- Linux: `pkill -f brave` (or close all windows)

Closing the last window is not enough on macOS — the app keeps running.

## Launch

```bash
# macOS
/Applications/Brave\ Browser.app/Contents/MacOS/Brave\ Browser --remote-debugging-port=9222

# Windows (PowerShell)
& "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" --remote-debugging-port=9222

# Linux
brave-browser --remote-debugging-port=9222
```

Chrome, Edge and any other Chromium browser work the same way; swap the binary.

## Keeping your logins

By default the flags above reuse your **normal profile**, so every site you are
already signed in to stays signed in. That is the point of this skill.

If you would rather isolate it, add a separate profile directory and log in
once inside it:

```bash
brave-browser --remote-debugging-port=9222 --user-data-dir="$HOME/.brave-router-profile"
```

A separate profile starts logged out. Sign in there once; the session persists
across restarts as long as you keep passing the same `--user-data-dir`.

## Verify

```bash
curl -s http://127.0.0.1:9222/json/version
python3 scripts/router.py doctor
```

The first should return JSON naming your browser build. The second should show
`browser_reachable: true` and `input_found: true`.

If `curl` works but Python does not, an HTTP proxy is probably intercepting
localhost. The client already bypasses proxies for debug traffic; if your shell
forces one, unset it for the run:

```bash
NO_PROXY=127.0.0.1,localhost python3 scripts/router.py doctor
```

## A different port

```bash
brave-browser --remote-debugging-port=9333
export AIPASS_CDP_PORT=9333        # or pass --port 9333 to every command
```

## Security

An open debug port is complete control of that browser: read any open page,
act as you on any site you are logged into. It listens on loopback only, but
any process on the same machine can use it.

- Open it when you need it; quit the browser when you are done.
- Never pass `--remote-debugging-address=0.0.0.0`. That exposes the port to
  your whole network.
- Don't do this on a shared or untrusted machine.
- Use a separate profile that only holds the accounts this skill needs if you
  want a smaller blast radius.
