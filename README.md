# Uncommon Sense autoposter

A free, self-owned alternative to Buffer/Publer. Once set up, it posts one item a day from a queue to your Bluesky, Facebook Page, Instagram and LinkedIn accounts, using each platform's official API. It runs on GitHub Actions (free), so your Mac doesn't need to be on.

**How it works day to day:** the queue is a spreadsheet (`queue.csv`) plus a folder of images. Each row is one post: a date, an image, a caption. Every morning the system posts the next due row to all configured platforms and ticks it off. To add content, you add rows and images. That's it.

The setup is staged. Stage 1 gets Bluesky posting today in about 15 minutes. Stages 2 and 3 (Meta and LinkedIn) are more tedious – allow 45 and 20 minutes – but each is a one-off. The system works from Stage 1 onwards; platforms switch on as you add their credentials.

---

## Stage 1: GitHub + Bluesky (~15 minutes)

### 1. Create a GitHub account and repository

1. Sign up free at github.com.
2. Click **+** (top right) → **New repository**. Name it `uncommon-autoposter`, set it to **Public** (Instagram later needs public image links; everything in it is promotional content anyway), tick **Add a README**, click **Create repository**.

### 2. Upload these files

1. In your new repository, click **Add file → Upload files**.
2. Drag in `poster.py`, `queue.csv`, `requirements.txt` and the `images` folder, then commit.
3. The workflow file has to be added through the web editor (uploads to hidden folders don't work): click **Add file → Create new file**, type `.github/workflows/daily-post.yml` as the name (the slashes create the folders), paste in the contents of that file, and commit.

### 3. Get a Bluesky app password

1. In the Bluesky app or website: **Settings → Privacy and security → App passwords → Add app password**. Name it `autoposter`.
2. Copy the generated password (looks like `xxxx-xxxx-xxxx-xxxx`). This is *not* your main password, and you can revoke it anytime.

### 4. Add the secrets

In your repository: **Settings → Secrets and variables → Actions → New repository secret**. Add two:

- `BLUESKY_HANDLE` – your handle, e.g. `uncommonsense.bsky.social`
- `BLUESKY_APP_PASSWORD` – the app password from step 3

### 5. Test it

1. Edit `queue.csv` (click it, then the pencil icon) so the first row's date is today and its caption is a real test post. Make sure the image it names exists in `images/`.
2. Go to the **Actions** tab → **Daily post** → **Run workflow**.
3. Watch it run (~1 minute), then check your Bluesky feed.

From now on it runs itself every morning (around 8:15–9:45 UK time; GitHub's scheduler is free, so timing is approximate). If a run fails, GitHub emails you.

---

## Stage 2: Facebook + Instagram (~45 minutes, one-off)

The prerequisites, before touching any developer tools:

- Your **Instagram account must be a Business or Creator account** (free: Instagram app → Settings → Account type). Creator suits a publication.
- You need a **Facebook Page** for Uncommon Sense (personal profiles can't be auto-posted to). Create one at facebook.com/pages/create if needed.
- **Link the Instagram account to the Facebook Page**: Page settings → Linked accounts → Instagram.

Then:

1. Go to **developers.facebook.com**, log in with your Facebook account, and register as a developer (free).
2. **Create App** → type **Business** → name it anything (e.g. "US Autoposter"). It stays in Development mode forever – that's fine, because it only ever touches your own accounts.
3. Note the **App ID** and **App Secret** from App settings → Basic.
4. Open the **Graph API Explorer** (developers.facebook.com/tools/explorer):
   - Select your app.
   - Under Permissions, add: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic`, `instagram_content_publish`, `business_management`.
   - Click **Generate Access Token** and approve, choosing your Page and Instagram account when asked.
5. That token expires in an hour, so exchange it for a permanent one. Paste this into your browser's address bar, filling in the three CAPITALISED bits:

   ```
   https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=SHORT_TOKEN
   ```

   Copy the `access_token` from the response (this one lasts 60 days). Then paste:

   ```
   https://graph.facebook.com/v21.0/me/accounts?access_token=LONG_TOKEN
   ```

   The response lists your Page: copy its `id` (this is `META_PAGE_ID`) and its `access_token` – **this Page token doesn't expire**. It's the one you keep.
6. Get your Instagram account's ID – paste:

   ```
   https://graph.facebook.com/v21.0/PAGE_ID?fields=instagram_business_account&access_token=PAGE_TOKEN
   ```

   Copy the number inside `instagram_business_account` (this is `IG_USER_ID`).
7. Add three more repository secrets: `META_PAGE_ID`, `META_ACCESS_TOKEN` (the Page token), `IG_USER_ID`.

Facebook and Instagram posting switches on automatically at the next run. Note: Instagram's API only accepts **JPEG** images, so save all cards as `.jpg`.

If any of this bogs down, bring the error message back to a Claude session and we'll fix it together.

---

## Stage 3: LinkedIn (~20 minutes, then 2 minutes every ~60 days)

1. Go to **developer.linkedin.com** → **Create app**. It requires linking a LinkedIn Page – link the Uncommon Sense company page, or create a bare one for this purpose. Verify the app from the Settings tab (a button posts a verification link to your Page admin).
2. On the app's **Products** tab, request **Share on LinkedIn** and **Sign In with LinkedIn using OpenID Connect** (both self-serve, no review meeting).
3. On the **Auth** tab, open the **OAuth token generator tool**, select the scopes `w_member_social`, `openid` and `profile`, generate a token for your own account, and copy it.
4. Add it as the repository secret `LINKEDIN_ACCESS_TOKEN`.

**The one recurring chore:** LinkedIn tokens last 60 days. When LinkedIn posts start failing (GitHub will email you, and the log will say the token expired), repeat steps 3–4. Two minutes, roughly six times a year.

---

## Living with it

**Adding content in bulk.** Edit `queue.csv` on GitHub (or ask Claude to draft the captions and generate the rows), and upload the matching images to `images/`. Dates control the order; one row is posted per day. Rows dated in the past post one per day until caught up, so gaps don't lose content – they just shift it.

**Targeting platforms.** The `platforms` column takes `all` or a pipe-separated list, e.g. `bluesky|linkedin`.

**Captions.** Keep them at or under 300 characters and they'll fit every platform, including Bluesky. Always fill in `alt_text` – it's the image description for blind and partially sighted readers.

**Checking the queue.** After a big bulk upload you can run a check: Actions tab → run the workflow manually and read the log, or ask Claude to run `python poster.py --validate` on the file before you upload it.

**If posting stops.** Two known causes: the LinkedIn token has expired (see Stage 3), or GitHub pauses scheduled workflows after 60 days with no repository activity – which only happens if the queue has been empty that long. GitHub emails you in both cases; re-enabling is one click on the Actions tab.

**Costs.** Nothing. GitHub's free tier allows vastly more than one small job a day, and all four platform APIs are free for posting to your own accounts.
