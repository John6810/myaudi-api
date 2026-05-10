---
description: Walk through the 13-step OAuth flow in audi_connect/oauth.py with the current state, useful when login breaks
allowed-tools: Read, Grep
---

The OAuth flow in `audi_connect/oauth.py` is the most fragile part of the codebase — reverse-engineered from the Android myAudi app v4.31.0, depends on Audi's HTML pages staying stable, and the `xqmauth_secret` byte array can be rotated upstream at any time.

This command walks through the flow step by step so you can pinpoint where it's breaking.

## The 13 steps (from `AudiOAuth.login`)

1. **Market config** — `GET https://content.app.my.audi.com/service/mobileapp/configurations/markets`
   Returns the country/language matrix. Failure here = country code wrong, or Audi changed the URL.

2. **Dynamic config** — `GET .../service/mobileapp/configurations/market/{country}/{lang}`
   Pulls `idkClientIDAndroidLive`, `myAudiAuthorizationServerProxyServiceURLProduction`, `mbbOAuthBaseURLLive`. Falls back to hardcoded defaults if missing.

3. **OpenID discovery** — `GET .../login/v1/idk/openid-configuration`
   Pulls `authorization_endpoint` and `token_endpoint`.

4. **PKCE challenge** — pure crypto, can't fail unless Python is broken.

5. **Login page** — `GET <authorization_endpoint>` with PKCE params
   Returns the email entry form. Cookies must be preserved.

6. **Submit email** — `POST` the email form
   Returns the password entry page. **Failure here usually means the HTML form changed shape.**

7. **Submit password** — `POST` the password form
   Look at the regex: `re.findall('"hmac"\\s*:\\s*"[0-9a-fA-F]+"', email_rsptxt)` — this scrapes an `hmac` value out of inline JS. If Audi removed or renamed it, login dies here. Falls back to hidden form inputs.

8. **Follow 3 redirects** — gets the authorization code from `myaudi:///?code=...`
   The redirect URI is the app deep link. Failure here often means MFA was triggered or credentials are wrong.

9. **Exchange code for IDK token** — `POST <token_endpoint>` with PKCE verifier + `X-QMAuth` header
   X-QMAuth is HMAC-SHA256 with a secret extracted from the APK (`xqmauth_secret` byte array). **If this fails with 401, Audi may have rotated the secret.** That's the worst-case scenario for this project.

10. **AZS (Audi) token** — `POST <authorization_server_base_url>/token` with the IDK id_token
    Used for the GraphQL vehicle list endpoint.

11. **Register MBB OAuth client** — `POST <mbb_oauth_base_url>/mobile/register/v1`
    Returns an `xclient_id`. Identifies the "Samsung Galaxy A40 running myAudi 4.31.0" device.

12. **Get MBB token** — `POST <mbb_oauth_base_url>/mobile/oauth2/v1/token` with `grant_type=id_token`
    Used for the legacy MBB API (trips, lock/unlock).

13. **Refresh MBB token immediately** — second token call
    The Android app does this and so do we. Skipping it sometimes works, sometimes breaks downstream calls.

## Diagnostic checklist when login breaks

Read `audi_connect/oauth.py` and confirm each of the above is still in the code unchanged. Then ask the user:

1. **What error message?** Map the message to a step:
   - `CountryNotSupportedError` → step 1
   - HTTP 401 on `/login/v1/idk/token` → step 9 (X-QMAuth secret may have rotated)
   - HTTP 4xx in step 6/7 → HTML form changed, BeautifulSoup selector failed
   - `KeyError: 'hmac'` or fallthrough to hidden inputs unexpectedly → step 7 regex broken
   - Empty `data` in GraphQL response → AZS token (step 10) or vehicle list call

2. **Was credentials.AUDI_PASSWORD recently changed?** Some special characters need URL encoding in submit forms.

3. **Is MFA enabled on the myAudi account?** Not supported by this client.

4. **Has it been working recently?** If it broke without code changes, it's most likely Audi changed something on their side. Compare timestamps with Audi service status / community forums.

## Things NOT to do

- **Don't refactor `oauth.py` opportunistically.** Even renaming variables risks breaking the flow because the order of HTTP calls and cookie passing is load-bearing.
- **Don't add retries inside the OAuth flow.** Login errors are usually deterministic (bad creds, changed HTML) and retrying just hammers the auth endpoint.
- **Don't log raw response bodies.** They contain access tokens, id tokens, and refresh tokens.

## Output

After reading the flow, produce a focused diagnosis:

```
## OAuth Diagnosis

### Likely failure point
Step N: <name>

### Why
<explanation tied to specific lines>

### Suggested next step
<reproduce locally / inspect HTML / wait for Audi / etc.>
```
