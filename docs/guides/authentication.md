# Authentication

UCloud logs users in through **WAYF** (federated SSO) — there is no
username/password endpoint to automate. But the *token* flow underneath is
separate from WAYF, and that's what `ucloud-api` uses.

## How it works

- After you log in via the browser, UCloud holds a long-lived **refresh token**
  (stored as an httpOnly `refreshToken` cookie) and mints short-lived **access
  tokens** (JWTs, valid ~10 minutes) from it.
- `ucloud-api` takes the refresh token you extract once and calls
  `POST /auth/refresh` with `Authorization: Bearer <refreshToken>` to obtain
  access tokens on demand. It caches each access token until just before its JWT
  `exp`, so it only hits the network when needed.

The refresh token is **just a string** and is **not tied to the machine** that
logged in. That is what makes headless use possible.

## Getting your refresh token (the one browser step)

Do this once, on any machine that has a browser — your laptop is fine.

1. Log in to <https://cloud.sdu.dk>.
2. Open your browser's DevTools:
    - **Application** tab → **Storage** → **Cookies** → `https://cloud.sdu.dk`.
3. Find the cookie named **`refreshToken`** and copy its **value**.

??? tip "Alternative: from the Network tab"
    In DevTools → **Network**, find the `POST /auth/refresh/web` request and copy
    the `refreshToken` from its request **Cookies**. Same value.

## Giving it to the CLI (headless-friendly)

Pipe it in so it never lands in your shell history:

```bash
echo 'PASTE_THE_TOKEN_HERE' | uv run ucloud login
```

Other ways:

```bash
uv run ucloud login                      # interactive, hidden prompt
export UCLOUD_REFRESH_TOKEN='...'        # environment variable
# or put it in a .env file (see Configuration)
```

`ucloud login` verifies the token by minting an access token **before** saving
it, so you never persist a dud.

## Where the token is stored

- File: `~/.config/ucloud-api/credentials.json`, written with `0600` permissions.
- Access-token cache: `~/.config/ucloud-api/token_cache.json` (also `0600`).

Both locations honour the `UCLOUD_CONFIG_DIR` environment variable if you want
them elsewhere.

## Rotating an expired token

Refresh tokens don't last forever. When commands start failing with an auth
error, or `ucloud whoami` fails, just repeat the browser step and run
`ucloud login` again with a fresh token.

## Security notes

- Treat the refresh token like a password — it grants access to your UCloud
  account. Don't commit it; `.env` and `credentials.json` are git-ignored.
- Prefer piping the token or using the hidden prompt over passing `--token` on
  the command line (which can be visible in process listings / history).
- `ucloud-api` never uploads your token anywhere except UCloud's own
  `POST /auth/refresh` endpoint.

## Fallback flow (if `POST /auth/refresh` is rejected)

Some deployments may only accept the browser refresh path
(`POST /auth/refresh/web` with the cookie + an `X-CSRFToken` header). If your
deployment rejects the bearer refresh, please
[open an issue](https://github.com/GuillaumeMougeot/ucloud-api/issues) — the
fallback is straightforward to add.
