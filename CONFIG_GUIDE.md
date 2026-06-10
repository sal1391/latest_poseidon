# Hermes Configuration Guide

## The Two Switches

Hermes has two main switches at the top of `config.py` that control how the app behaves:

---

### Switch 1: DEPLOY_MODE (`"local"` or `"aws"`)

Controls **where the app gets its passwords and credentials from**.

- **`"local"` (default)** — The app uses passwords you typed directly into `config.py`.
  Think of this as writing your Wi-Fi password on a sticky note next to your computer.
  Quick and easy for testing on your own machine.

- **`"aws"`** — The app retrieves passwords from a secure vault (AWS Secrets Manager).
  Think of this as a locked safe that only authorized systems can open.
  This is what you use when the app runs on a real server.

### Switch 2: AUTH0_ENABLED (`True` or `False`)

Controls **whether users have to log in**.

- **`False` (default)** — No login screen. The app opens immediately and treats you as
  an authorized user. Like leaving the front door unlocked while you set things up.

- **`True`** — A login screen appears. Users must sign in with corporate credentials
  and have the right role (`Poseidon:Sales`) to get access. This is the locked front
  door with a keycard.

---

## Common Scenarios

| Scenario | DEPLOY_MODE | AUTH0_ENABLED | What happens |
|----------|-------------|---------------|--------------|
| Testing on your laptop | `"local"` | `False` | App starts instantly, uses your hardcoded passwords, no login |
| Live on company server | `"aws"` | `True` | Login screen shown, passwords from secure vault, role-gated access |

---

## How to Set Up for Local Testing

1. Open `config.py`
2. Make sure the top two lines say:
   ```
   DEPLOY_MODE = "local"
   AUTH0_ENABLED = False
   ```
3. Fill in your Snowflake credentials in the `_LOCAL_SNOWFLAKE_CONNECTION` section
4. Fill in your Perplexity API key in `_LOCAL_PERPLEXITY_API_KEY`
5. Run: `streamlit run app.py`

## How to Switch to Production

1. Open `config.py`
2. Change the top two lines to:
   ```
   DEPLOY_MODE = "aws"
   AUTH0_ENABLED = True
   ```
3. Make sure your AWS environment has:
   - The `hermes_secret_json` secret in Secrets Manager (Snowflake creds)
   - `PERPLEXITY_API_KEY` environment variable
   - `CLIENTID` and `DOMAIN` environment variables (Auth0)
4. Deploy and run
