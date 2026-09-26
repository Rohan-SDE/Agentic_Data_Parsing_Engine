# Email, Google and mobile sign-in

The optional Supabase Auth bridge supports email OTP, Google OAuth with PKCE, and SMS OTP. Both signup and subsequent login use the same buttons. Existing username/password accounts continue to work. Email and phone users do not create an app password.

## Provider setup

1. Create a dedicated hosted Supabase project for this application. Copy its HTTPS project URL and publishable key (or legacy anon key). Never use a service-role or secret key here. App datasets remain in the existing PostgreSQL/data volumes; Supabase handles identity only.
2. Set Authentication > URL Configuration > Site URL to `https://rohan-agentic-engine.duckdns.org`. Add the exact redirect URL `https://rohan-agentic-engine.duckdns.org/api/auth/external/google/callback` to the allowlist. Avoid wildcard redirects.
3. Email: enable the Email provider, retain email confirmation, and configure custom SMTP for messages to real visitors. Edit the Magic Link email template to show the verification code, for example `<p>Your verification code is {{ .Token }}</p>`. Configure a short OTP expiry. The UI accepts 6–10 digit codes. Test delivery and spam folders with an address outside your provider team.
4. Google: create a Google OAuth web client, configure its consent screen and permitted users/publishing status, and use the Supabase callback `https://YOUR_PROJECT_REF.supabase.co/auth/v1/callback` as the authorized redirect URI. Enter the Google client ID and secret in Supabase's Google provider settings. The Google secret belongs in Supabase, not in this app or GitHub.
5. Phone: enable the Phone provider and configure a supported SMS provider, such as Twilio. Set provider spending limits, allowed destination countries, sender requirements and rate limits before enabling it. Test a real number using its international format, e.g. `+919876543210`. SMS is not guaranteed free; check the provider's delivery requirements for India and other intended countries.
6. Provider endpoints can be called directly using the public key, so app rate limits alone do not cap provider usage. Set provider-side limits and billing alerts. This integration does not currently render CAPTCHA; do not turn on mandatory Supabase CAPTCHA without adding a supported frontend challenge/token flow.

## App configuration

Add these settings to the existing `.env.production`, preserving all existing secrets:

```dotenv
ADPE_PUBLIC_REGISTRATION=true
ADPE_SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
ADPE_SUPABASE_PUBLISHABLE_KEY=YOUR_PUBLISHABLE_KEY
ADPE_EMAIL_AUTH_ENABLED=true
ADPE_GOOGLE_AUTH_ENABLED=true
ADPE_PHONE_AUTH_ENABLED=false
```

Enable each method only after configuring and testing its provider. Change phone to true when SMS is ready. All methods default to false. Startup rejects enabled methods without a hosted HTTPS Supabase URL and key.

## Upgrade an existing AWS installation

Run from `~/Agentic_Data_Parsing_Engine/production`. First back up with the currently deployed code:

```bash
sudo python3 scripts/recovery.py backup "$HOME/adpe-backup-$(date +%Y%m%d-%H%M%S)" --project adpe-production
git pull --ff-only origin master
```

Edit `.env.production` as above, then:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml config --quiet
sudo docker compose --env-file .env.production -f compose.production.yaml build api
sudo docker compose --env-file .env.production -f compose.production.yaml stop api worker maintenance
sudo docker compose --env-file .env.production -f compose.production.yaml run --rm migrate
sudo docker compose --env-file .env.production -f compose.production.yaml up -d
```

If migration fails, stop and inspect its error before proceeding. Migration 0003 adds a nullable unique external identity binding to users. Existing account IDs, credentials and dataset ownership are preserved. Keep the previous image and matched backup for rollback; backups from schema 0002 must first be restored with the matching old image, then upgraded.

Verify `/health/ready` and `/api/auth/external/options`. In an incognito browser, test email code delivery/verification, Google consent/callback, SMS delivery/verification, expired or incorrect codes, logout and repeat login. Confirm a new user has no admin controls and cannot access another user's datasets. Automated tests mock the upstream identity service; real provider credentials and delivery require this operator acceptance test.

## Account and security behavior

- External identities bind by Supabase project and immutable user UUID, never by a typed email, phone, display name or administrator flag. No automatic merging into local admin accounts occurs. Supabase may link its own verified identities according to its policies; the same Supabase UUID maps to one app account. Different provider identities may create separate workspaces.
- App usernames for external accounts are generated unique identifiers. Email and mobile numbers are not copied into the app database. Administrators see the generated username in the account list; ask the user for their displayed username when providing support.
- Verification is checked server-side using Supabase's user endpoint. Only then is an HttpOnly, Secure (in production), SameSite=Strict app session issued. Google uses an HttpOnly SameSite=Lax PKCE verifier cookie so the redirect can complete; the cookie expires after ten minutes and is cleared on callback.
- Three send attempts per IP and destination per hour; global sending ceilings are 20 SMS and 100 emails/hour. Verification is limited to 15 attempts per IP and destination/hour. Fixed-window app limits complement provider limits. They can inconvenience users behind shared IPs.
- Provider errors are sanitized, OTP responses do not disclose membership, and account creation/each login is audited without contact details or codes.
- Disabling public registration prevents creation of new local accounts, while existing verified identities can still sign in. Disabling an individual method hides and blocks it. Disable users in the app administration screen to revoke app access; provider-only revocation does not immediately revoke existing app sessions (default eight hours).
- Keep the admin username/password login as a recovery path. Lost phone/email recovery and identity linking are not implemented in this app. Handle them using the provider's verified recovery process; never merge accounts based only on a claimed contact address.

References: https://supabase.com/docs/guides/auth/auth-email-passwordless ; https://supabase.com/docs/guides/auth/social-login/auth-google ; https://supabase.com/docs/guides/auth/phone-login ; https://supabase.com/docs/guides/auth/sessions/pkce-flow
