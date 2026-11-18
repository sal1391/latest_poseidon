# AWS + Auth0 Setup — Step-by-Step Task List

Two independent tracks (AWS for Bedrock, Auth0 for real identity). Work one
step at a time within a track: you do the step, tell me it's done (or paste
what you're seeing if something looks off), I confirm and we move to the
next. Nothing here needs you to paste a secret into chat — see the callout
in each track for exactly what's safe to share vs. what stays in your own
`.env` file.

Grounded in `docs/architecture/07-infrastructure.md` §7, `docs/architecture/
03-llm-routing.md`, `docs/architecture/05-auth-identity.md` §9, and the
actual code (`core/llm/bedrock.py`, `core/identity_auth0.py`,
`features/auth/auth0Config.ts`) — not guessed.

---

## Track A — AWS (unlocks `LLM_MODE=live` + Bedrock)

- [x] **A1. AWS account.** Done.
- [ ] **A2. Request Bedrock model access, region `us-east-1`.** In the
      Bedrock console → Model access → request access to the Anthropic
      Claude family and Amazon Nova family models. **Caveat:** the exact
      model ids in `backend/poseidon/config/models.yml` are flagged
      "illustrative — verify current ids per provider console" — don't hunt
      for those exact strings, just enable Claude + Nova broadly and we'll
      pin the real ids once you're in the console.
- [x] **A3. Create an IAM dev user.** Done — `poseidon-dev`, policy
      `PoseidonBedrockInvoke` scoped to `bedrock:InvokeModel` /
      `bedrock:InvokeModelWithResponseStream` on `us-east-1` foundation
      models. Note: A2's Anthropic-model-access review may still be
      pending on your account — not blocking, revisit `models.yml` model
      ids once you know which Claude models you actually have.
- [x] **A4. Generate credentials.** Done — access key pair created, set in
      local `backend/.env` (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/
      `AWS_DEFAULT_REGION`), never shared in chat.
- [x] **A5. AWS Budget alert.** Done.
- [x] **A6. Live verification — DONE.** Two real IAM gotchas found and fixed
      along the way (both are standard Bedrock cross-region-inference
      quirks, not project bugs): (1) cross-region inference profile ids
      (the `us.` prefix) need an `inference-profile` resource grant, not
      just `foundation-model`; (2) the underlying `foundation-model`
      permission needs to span all US regions (`arn:aws:bedrock:*::
      foundation-model/*`), since the inference profile can route to
      us-east-1/us-east-2/us-west-2 depending on capacity; (3) Anthropic
      models additionally needed `aws-marketplace:ViewSubscriptions` +
      `aws-marketplace:Subscribe` for the auto-provisioned Marketplace
      subscription. Final `PoseidonBedrockInvoke` policy has both Bedrock
      resource ARNs plus the marketplace actions. Confirmed live: Nova Lite
      (utility role) via the app's own `RoleClient`
      (`test_bedrock_utility_role_live_smoke` passed), and Claude Sonnet
      4.5 (router/memory role) directly. **Track A COMPLETE.**
- [x] **A7 (bonus). Full `router_live` parametrized suite.** 5/6 passed
      against real Claude/Nova calls. One real finding, logged as a
      carryforward item (not fixed here, not blocking): the live router's
      raw argument-extraction (tested in isolation, `parsed=None`) wrote
      `"Port of Singapore"` instead of the certified `"Singapore"` value —
      likely unreachable in the real chat pipeline since `parse_turn`
      always resolves ports before the router runs, but worth resolving
      before anything relies on the router's raw judgment alone. Full
      writeup in `[[poseidon-carryforwards]]` memory.

## Track B — Auth0 (unlocks real login instead of dev-mode bypass)

- [x] **B1. Auth0 tenant.** Done — `dev-ndwxojqej5feb0s4.us.auth0.com`.
- [x] **B2. Create an Application.** Done — SPA, React quickstart's
      generic integration code correctly skipped (this project has its own
      Auth0 wiring already); origin `http://localhost:5173` added, Allowed
      URLs auto-populated. Client ID: `xLYhW7JHmzm8vXlo1dJiFoAr8vOLj0Ds`.
- [x] **B3. Create an API.** Done — identifier `https://poseidon/api`
      (`AUTH0_AUDIENCE`), RS256.

**Collected so far** (none are secrets): `AUTH0_DOMAIN=dev-ndwxojqej5feb0s4.us.auth0.com`,
`AUTH0_CLIENT_ID=xLYhW7JHmzm8vXlo1dJiFoAr8vOLj0Ds`, `AUTH0_AUDIENCE=https://poseidon/api`.
- [x] **B4. Post-login Action.** Done — "Add Poseidon Roles Claim", adds
      `https://wfscorp.com/custom-claims.roles` as a JSON array from
      `event.authorization.roles`, wired into the actual Login flow
      (Triggers → Post Login → dragged into the flow → Applied — creating
      an Action alone doesn't activate it, this step is easy to miss).
      Later amended (see B7 below) to also set `name`/`email` custom claims
      once the missing-display-name gap was found live.
- [x] **B5. Two test users.** Done — one with `Poseidon:Sales` assigned via
      Auth0 Roles, one with none.
- [x] **B6. Env values wired.** Done — `backend/.env` and a new
      `frontend/.env` (didn't exist before) both set, using the API from
      B3's identifier. **Caveat found later:** don't `Read` the whole
      `backend/.env` file for context — it also holds the AWS secret from
      Track A; use targeted greps for names only. Broke this rule once
      myself while inserting these lines (disclosed to Carlos, recommended
      rotating that AWS key as a precaution).
- [x] **B7. Live login round-trip — DONE**, with three real problems found
      and fixed along the way (all disclosed live as they happened):
      1. **Callback URL mismatch** — the Application Origin wizard step's
         "Add the required Allowed URLs" was skipped past instead of
         confirmed; fixed by setting Callback/Logout/Web-Origin URLs
         directly in Settings.
      2. **`Service not found: https://poseidon/api`** — persisted through
         an extensive investigation (per-app grant, all URLs, grant types,
         RBAC, consent policy, scope contents, server-side logs all checked
         and correct) and a genuine ~15 min real-time wait, ruling out
         simple propagation delay. Root cause never fully confirmed, but
         matched a documented Auth0 community case (invisible whitespace
         corruption in a locked, uneditable Identifier field) closely
         enough that **deleting and recreating the API from scratch fixed
         it immediately** — the fresh object doesn't carry whatever was
         wrong with the original. RBAC + the per-app grant had to be
         redone on the new object (both per-API, not global).
      3. **`the token's nbf claim is in the future`** — a REAL code bug,
         not a config/environment issue: `identity_auth0.py`'s
         `jwt.decode()` call had no `leeway`, so PyJWT's default zero clock-
         skew tolerance tripped on a normal few-seconds difference between
         Auth0's issuing clock and the container's own (confirmed NOT a
         genuine clock-drift issue — host and container clocks matched
         exactly, and real signed AWS calls worked fine with the same
         clock). Fixed with `leeway=60` (standard practice); the existing
         pinned future-nbf test (5 min skew) still correctly fails.
         Committed `3692796`.
      4. **Bonus, found immediately after first successful login:** the
         sidebar's `.user-menu` (logout button + display name) had zero
         CSS anywhere — rendered as unstyled, easy-to-miss browser-default
         text. Fixed and committed `7754670`. Separately, the displayed
         name showed the raw `sub` id instead of a name, because Auth0
         access tokens don't carry `name`/`email` by default (only the ID
         token does) — fixed by extending the SAME post-login Action (B4)
         to also set those two claims from `event.user`.
      All four fixes verified live end-to-end; Carlos confirmed "looks
      good!" after the final round.
      **Afterward:** reverted `docker-compose.yml`'s `IDENTITY_MODE` back
      to `disabled` (the correct local-dev default) and confirmed nothing
      was left uncommitted there — deliberately NOT tracking the trial
      tenant's values in the shared compose file (22-day trial expiry;
      would silently break the default dev loop for anyone running this
      repo). This runbook is the path back to auth0 mode whenever needed.
      **Reminder — hard gate**: Phase 14 hardening (a JWKS-fetch DoS fix)
      needs to land before this goes live in a real deploy. Fine to test
      locally now; not fine to ship auth0 mode to a real environment before
      that phase closes.

---

*Not committed yet — say the word once we're through both tracks, or
sooner if you'd rather lock in progress as we go.*
