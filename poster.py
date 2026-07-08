#!/usr/bin/env python3
"""
Uncommon Sense autoposter.

Reads queue.csv, finds the next due post, and publishes it to whichever
platforms are configured (via environment variables / GitHub secrets):

  Bluesky    BLUESKY_HANDLE, BLUESKY_APP_PASSWORD
  Facebook   META_PAGE_ID, META_ACCESS_TOKEN            (a Page access token)
  Instagram  IG_USER_ID, META_ACCESS_TOKEN              (same token as Facebook)
  LinkedIn   LINKEDIN_ACCESS_TOKEN                      (renew every ~60 days)

A platform is simply skipped if its variables aren't set, so the system
works with Bluesky alone and grows as the others are switched on.

Usage:
  python poster.py               post the next due item
  python poster.py --dry-run     show what would be posted, post nothing
  python poster.py --validate    check the whole queue for problems

Queue format (queue.csv), one row per post:
  date       YYYY-MM-DD (posts on or after this date, one per daily run)
  image      path within the repo, e.g. images/card-001.jpg (JPEG for Instagram)
  caption    the post text; keep <= 300 characters so it fits Bluesky
  alt_text   image description for accessibility
  platforms  "all", or pipe-separated subset, e.g. "bluesky|linkedin"
  status     managed by the script - leave blank for new rows
"""

import csv
import io
import json
import os
import re
import sys
from datetime import date, datetime, timezone

import requests

QUEUE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.csv")
FIELDS = ["date", "image", "caption", "alt_text", "platforms", "status"]
GRAPH = "https://graph.facebook.com/v21.0"
BSKY = "https://bsky.social/xrpc"
LINKEDIN_VERSION = "202506"
BSKY_MAX_CHARS = 300
BSKY_MAX_IMAGE_BYTES = 950_000
URL_RE = re.compile(r"https?://[^\s)\]}>,]+")


# ---------------------------------------------------------------- helpers

def repo_root():
    return os.path.dirname(os.path.abspath(__file__))


def public_image_url(image_path):
    """Public raw URL for an image in this repository (needed by Instagram)."""
    base = os.environ.get("IMAGE_BASE_URL")
    if not base:
        repo = os.environ.get("GITHUB_REPOSITORY")  # e.g. craig/uncommon-autoposter
        if not repo:
            return None
        branch = os.environ.get("GITHUB_REF_NAME", "main")
        base = f"https://raw.githubusercontent.com/{repo}/{branch}/"
    return base.rstrip("/") + "/" + image_path.lstrip("/")


def read_image(image_path):
    full = os.path.join(repo_root(), image_path)
    with open(full, "rb") as f:
        return f.read()


def shrink_for_bluesky(data):
    """Bluesky rejects blobs over ~976 KB; recompress if needed."""
    if len(data) <= BSKY_MAX_IMAGE_BYTES:
        return data, None
    from PIL import Image  # only imported when actually needed

    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    for quality in (85, 75, 65, 55):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        if buf.tell() <= BSKY_MAX_IMAGE_BYTES:
            return buf.getvalue(), "image/jpeg"
    # last resort: halve dimensions
    img = img.resize((img.width // 2, img.height // 2))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue(), "image/jpeg"


def guess_mime(image_path):
    ext = image_path.lower().rsplit(".", 1)[-1]
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")


def configured_platforms():
    p = []
    if os.environ.get("BLUESKY_HANDLE") and os.environ.get("BLUESKY_APP_PASSWORD"):
        p.append("bluesky")
    if os.environ.get("META_PAGE_ID") and os.environ.get("META_ACCESS_TOKEN"):
        p.append("facebook")
    if os.environ.get("IG_USER_ID") and os.environ.get("META_ACCESS_TOKEN"):
        p.append("instagram")
    if os.environ.get("LINKEDIN_ACCESS_TOKEN"):
        p.append("linkedin")
    return p


def platforms_for_row(row, configured):
    wanted = row["platforms"].strip().lower()
    if not wanted or wanted == "all":
        return list(configured)
    return [w.strip() for w in wanted.split("|") if w.strip() in configured]


def parse_status(status):
    """'bluesky:ok facebook:error' -> {'bluesky': 'ok', 'facebook': 'error'}"""
    out = {}
    for token in status.split():
        if ":" in token:
            name, state = token.split(":", 1)
            out[name] = state
    return out


def format_status(state):
    return " ".join(f"{k}:{v}" for k, v in sorted(state.items()))


# ---------------------------------------------------------------- platforms

def post_bluesky(caption, image_data, alt_text, mime):
    handle = os.environ["BLUESKY_HANDLE"]
    password = os.environ["BLUESKY_APP_PASSWORD"]

    r = requests.post(f"{BSKY}/com.atproto.server.createSession",
                      json={"identifier": handle, "password": password}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Bluesky login failed ({r.status_code}): {r.text[:200]}")
    session = r.json()
    auth = {"Authorization": f"Bearer {session['accessJwt']}"}

    data, new_mime = shrink_for_bluesky(image_data)
    r = requests.post(f"{BSKY}/com.atproto.repo.uploadBlob", data=data,
                      headers={**auth, "Content-Type": new_mime or mime}, timeout=60)
    r.raise_for_status()
    blob = r.json()["blob"]

    text = caption if len(caption) <= BSKY_MAX_CHARS else caption[:BSKY_MAX_CHARS - 1] + "…"

    # make any URLs in the caption clickable
    facets = []
    text_bytes = text.encode("utf-8")
    for m in URL_RE.finditer(text):
        start = len(text[:m.start()].encode("utf-8"))
        end = start + len(m.group().encode("utf-8"))
        facets.append({
            "index": {"byteStart": start, "byteEnd": end},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group()}],
        })

    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "langs": ["en-GB"],
        "embed": {"$type": "app.bsky.embed.images",
                  "images": [{"alt": alt_text or "", "image": blob}]},
    }
    if facets:
        record["facets"] = facets

    r = requests.post(f"{BSKY}/com.atproto.repo.createRecord", headers=auth, timeout=30,
                      json={"repo": session["did"], "collection": "app.bsky.feed.post",
                            "record": record})
    r.raise_for_status()


def post_facebook(caption, image_path):
    page_id = os.environ["META_PAGE_ID"]
    token = os.environ["META_ACCESS_TOKEN"]
    url = public_image_url(image_path)
    if url:
        r = requests.post(f"{GRAPH}/{page_id}/photos", timeout=60,
                          data={"url": url, "caption": caption, "access_token": token})
    else:  # fall back to direct upload when no public URL is available
        r = requests.post(f"{GRAPH}/{page_id}/photos", timeout=60,
                          data={"caption": caption, "access_token": token},
                          files={"source": read_image(image_path)})
    if r.status_code >= 400:
        raise RuntimeError(f"Facebook error {r.status_code}: {r.text[:300]}")


def post_instagram(caption, image_path):
    ig_user = os.environ["IG_USER_ID"]
    token = os.environ["META_ACCESS_TOKEN"]
    url = public_image_url(image_path)
    if not url:
        raise RuntimeError("Instagram needs a public image URL "
                           "(run from GitHub Actions or set IMAGE_BASE_URL)")
    r = requests.post(f"{GRAPH}/{ig_user}/media", timeout=60,
                      data={"image_url": url, "caption": caption, "access_token": token})
    if r.status_code >= 400:
        raise RuntimeError(f"Instagram create error {r.status_code}: {r.text[:300]}")
    creation_id = r.json()["id"]
    r = requests.post(f"{GRAPH}/{ig_user}/media_publish", timeout=60,
                      data={"creation_id": creation_id, "access_token": token})
    if r.status_code >= 400:
        raise RuntimeError(f"Instagram publish error {r.status_code}: {r.text[:300]}")


def linkedin_escape(text):
    """LinkedIn's Posts API treats these as formatting; escape them in plain text."""
    for ch in "\\|{}[]()<>*~":
        text = text.replace(ch, "\\" + ch)
    return text


def post_linkedin(caption, image_data, mime):
    token = os.environ["LINKEDIN_ACCESS_TOKEN"]
    headers = {"Authorization": f"Bearer {token}",
               "LinkedIn-Version": LINKEDIN_VERSION,
               "X-Restli-Protocol-Version": "2.0.0",
               "Content-Type": "application/json"}

    r = requests.get("https://api.linkedin.com/v2/userinfo",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"LinkedIn auth error {r.status_code} "
                           "(token may have expired - generate a new one): "
                           + r.text[:200])
    person_urn = f"urn:li:person:{r.json()['sub']}"

    r = requests.post("https://api.linkedin.com/rest/images?action=initializeUpload",
                      headers=headers, timeout=30,
                      json={"initializeUploadRequest": {"owner": person_urn}})
    if r.status_code >= 400:
        raise RuntimeError(f"LinkedIn image init error {r.status_code}: {r.text[:300]}")
    value = r.json()["value"]

    r = requests.put(value["uploadUrl"], data=image_data, timeout=120,
                     headers={"Authorization": f"Bearer {token}", "Content-Type": mime})
    if r.status_code >= 400:
        raise RuntimeError(f"LinkedIn image upload error {r.status_code}: {r.text[:300]}")

    post = {
        "author": person_urn,
        "commentary": linkedin_escape(caption),
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED",
                         "targetEntities": [], "thirdPartyDistributionChannels": []},
        "content": {"media": {"id": value["image"]}},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    r = requests.post("https://api.linkedin.com/rest/posts",
                      headers=headers, json=post, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"LinkedIn post error {r.status_code}: {r.text[:300]}")


# ---------------------------------------------------------------- queue

def load_queue():
    with open(QUEUE_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for field in FIELDS:
            row.setdefault(field, "")
            row[field] = (row[field] or "").strip()
    return rows


def save_queue(rows):
    with open(QUEUE_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({k: row.get(k, "") for k in FIELDS} for row in rows)


def validate(rows, configured):
    problems = []
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        where = f"row {i} ({row['date'] or 'no date'})"
        try:
            date.fromisoformat(row["date"])
        except ValueError:
            problems.append(f"{where}: bad or missing date (use YYYY-MM-DD)")
        if not row["caption"]:
            problems.append(f"{where}: caption is empty")
        elif len(row["caption"]) > BSKY_MAX_CHARS:
            problems.append(f"{where}: caption is {len(row['caption'])} characters; "
                            f"over {BSKY_MAX_CHARS} it gets truncated on Bluesky")
        if not row["image"]:
            problems.append(f"{where}: image is empty")
        elif not os.path.exists(os.path.join(repo_root(), row["image"])):
            problems.append(f"{where}: image file not found: {row['image']}")
        elif not row["image"].lower().endswith((".jpg", ".jpeg")):
            wants = platforms_for_row(row, configured or ["instagram"])
            if "instagram" in wants or row["platforms"].strip().lower() in ("", "all"):
                problems.append(f"{where}: {row['image']} is not a JPEG - "
                                "Instagram only accepts .jpg images")
        if not row["alt_text"]:
            problems.append(f"{where}: alt_text is empty (accessibility)")
    return problems


def next_due(rows, configured, today):
    for row in rows:
        try:
            row_date = date.fromisoformat(row["date"])
        except ValueError:
            continue
        if row_date > today:
            continue
        wanted = platforms_for_row(row, configured)
        done = parse_status(row["status"])
        remaining = [p for p in wanted if done.get(p) != "ok"]
        if wanted and remaining:
            return row, wanted, remaining
    return None, None, None


# ---------------------------------------------------------------- main

def main():
    dry_run = "--dry-run" in sys.argv
    validate_only = "--validate" in sys.argv
    configured = configured_platforms()

    rows = load_queue()

    if validate_only:
        problems = validate(rows, configured)
        if problems:
            print(f"{len(problems)} problem(s) found:")
            for p in problems:
                print("  -", p)
            sys.exit(1)
        print(f"Queue looks good: {len(rows)} rows, "
              f"configured platforms: {', '.join(configured) or 'none'}")
        return

    if not configured:
        print("No platforms configured - set the environment variables / "
              "GitHub secrets described in the README.")
        sys.exit(1)

    today = date.today()
    row, wanted, remaining = next_due(rows, configured, today)
    if row is None:
        print("Nothing due today - the queue is up to date (or empty). "
              "Add more rows to queue.csv when you're ready.")
        return

    print(f"Post dated {row['date']}: {row['image']}")
    print(f"  caption   : {row['caption'][:80]}{'...' if len(row['caption']) > 80 else ''}")
    print(f"  platforms : {', '.join(wanted)} (still to do: {', '.join(remaining)})")

    if dry_run:
        print("Dry run - nothing posted.")
        return

    image_data = read_image(row["image"])
    mime = guess_mime(row["image"])
    state = parse_status(row["status"])
    failures = []

    for platform in remaining:
        try:
            if platform == "bluesky":
                post_bluesky(row["caption"], image_data, row["alt_text"], mime)
            elif platform == "facebook":
                post_facebook(row["caption"], row["image"])
            elif platform == "instagram":
                post_instagram(row["caption"], row["image"])
            elif platform == "linkedin":
                post_linkedin(row["caption"], image_data, mime)
            state[platform] = "ok"
            print(f"  {platform}: posted")
        except Exception as e:  # noqa: BLE001 - record and carry on
            state[platform] = "error"
            failures.append(f"{platform}: {e}")
            print(f"  {platform}: FAILED - {e}")

    row["status"] = format_status(state)
    save_queue(rows)

    if failures:
        print("\nSome platforms failed; they'll be retried on the next run "
              "(successes won't be repeated).")
        sys.exit(1)
    print("All done.")


if __name__ == "__main__":
    main()
