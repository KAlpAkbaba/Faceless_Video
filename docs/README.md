# OAuth consent screen pages

Google requires a home page, a privacy policy and a terms-of-service page on a
**verified domain** before an app requesting sensitive YouTube scopes can be
published. These three pages exist to satisfy that requirement.

They are plain static HTML with no build step and no external assets.

## Publishing them with GitHub Pages

1. Repo → **Settings** → **Pages**
2. *Source*: **Deploy from a branch**
3. *Branch*: `main`, folder: **`/docs`** → **Save**

After a minute the pages are live at:

```
https://<kullanıcı-adın>.github.io/Faceless_Video/
https://<kullanıcı-adın>.github.io/Faceless_Video/privacy.html
https://<kullanıcı-adın>.github.io/Faceless_Video/terms.html
```

GitHub Pages is free for public repositories. On a **private** repository it
requires a paid plan — either make this repository public (it holds no
credentials; secrets live in GitHub Secrets and `client_secret.json` /
`.env` are git-ignored) or copy this `docs/` folder into a small public
repository of its own.

## Verifying the domain with Google

Google will only accept an authorised domain you have verified.

1. Open [Google Search Console](https://search.google.com/search-console) →
   **Add property** → **URL prefix** → paste the Pages URL above.
2. Choose the **HTML file** verification method and download the
   `googlexxxxxxxx.html` file it offers.
3. Drop that file into this `docs/` folder, commit, and push. It will be served
   at the URL Google expects.
4. Back in Search Console, click **Verify**.

Then in Google Cloud Console → **OAuth consent screen** → **Branding**:

| Field | Value |
|---|---|
| Application home page | the Pages URL |
| Application privacy policy link | `.../privacy.html` |
| Application terms of service link | `.../terms.html` |
| Authorized domains | `<kullanıcı-adın>.github.io` |

Save, then **Audience** → **Publish app**.

## Before publishing these pages

The contact address in `privacy.html` and `terms.html` is the same one set as
the app's support email on the OAuth consent screen, which Google already shows
to anyone who authorises the app. Swap it for a different address if you would
rather not have it on a public page.

Update the channel name in the pages if you change `channel.name` in
`config.yaml`.

> These pages describe what the pipeline in this repository actually does.
> They are not legal advice; read them and make sure they match how you
> intend to run the channel before you publish them under your own name.
