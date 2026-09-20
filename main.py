import os
import json
import time
import base64
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    UserStatusOnline,
    UserStatusOffline,
    UserStatusRecently,
    UserStatusLastWeek,
    UserStatusLastMonth,
)

# =========================================================
# CONFIG
# =========================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]

TARGET_ID = int(os.environ["TARGET_USERNAME"])
TARGET_ACCESS_HASH = int(os.environ["TARGET_ACCESS_HASH"])

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]

# DATA REPOSITORY — NOT THE CODE REPOSITORY
DATA_REPO = "dark-shadowblade/tad"
DATA_FILE = "activity_7days.json"

CHECK_INTERVAL = 2

IST = timezone(timedelta(hours=5, minutes=30))

# =========================================================
# LOCAL DATA
# =========================================================

DATA = {
    "sessions": [],
    "current_online_since": None,
    "last_seen": None,
    "last_status": "OFFLINE",
    "updated_at": None,
}

last_uploaded_snapshot = None


# =========================================================
# TIME
# =========================================================

def now_ist():
    return datetime.now(IST)


def iso(dt):
    if dt is None:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(IST).isoformat()


# =========================================================
# GITHUB API
# =========================================================

def github_url():
    return (
        f"https://api.github.com/repos/"
        f"{DATA_REPO}/contents/{DATA_FILE}"
    )


def github_request(method, url, body=None):
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "telegram-activity-logger",
    }

    data = None

    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(
                response.read().decode("utf-8")
            )

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="ignore")

        print(
            f"GitHub API error {e.code}: {error_body}",
            flush=True
        )

        return e.code, None

    except Exception as e:
        print(
            f"GitHub request error: {e}",
            flush=True
        )

        return None, None


# =========================================================
# LOAD DATA FROM DATA REPOSITORY
# =========================================================

def load_remote_data():

    print("Loading activity data from GitHub...", flush=True)

    status, response = github_request(
        "GET",
        github_url()
    )

    if status == 200 and response:

        try:
            content = response["content"]

            decoded = base64.b64decode(
                content
            ).decode("utf-8")

            data = json.loads(decoded)

            if isinstance(data, dict):
                print(
                    "Existing activity data loaded.",
                    flush=True
                )
                return data

        except Exception as e:
            print(
                f"Could not decode GitHub data: {e}",
                flush=True
            )

    elif status == 404:
        print(
            "No activity file exists yet. Creating new one.",
            flush=True
        )

    return {
        "sessions": [],
        "current_online_since": None,
        "last_seen": None,
        "last_status": "OFFLINE",
        "updated_at": None,
    }


# =========================================================
# SAVE DATA TO GITHUB DATA REPOSITORY
# =========================================================

def upload_data(data):

    global last_uploaded_snapshot

    clean_json = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    # Don't create a GitHub commit if nothing changed.
    if clean_json == last_uploaded_snapshot:
        return True

    # Get current file SHA
    status, response = github_request(
        "GET",
        github_url()
    )

    sha = None

    if status == 200 and response:
        sha = response.get("sha")

    file_content = json.dumps(
        data,
        indent=2,
        ensure_ascii=False,
    )

    encoded = base64.b64encode(
        file_content.encode("utf-8")
    ).decode("utf-8")

    payload = {
        "message": "Update Telegram activity data",
        "content": encoded,
    }

    if sha:
        payload["sha"] = sha

    status, response = github_request(
        "PUT",
        github_url(),
        payload,
    )

    if status in (200, 201):

        last_uploaded_snapshot = clean_json

        print(
            "GitHub: activity_7days.json updated.",
            flush=True
        )

        return True

    print(
        "GitHub upload failed. Will retry later.",
        flush=True
    )

    return False


# =========================================================
# CLEAN OLD SESSIONS
# =========================================================

def clean_old_sessions(data):

    cutoff = now_ist() - timedelta(days=7)

    sessions = data.get("sessions", [])

    new_sessions = []

    for session in sessions:

        try:
            start = datetime.fromisoformat(
                session["start"]
            )

            if start.tzinfo is None:
                start = start.replace(
                    tzinfo=IST
                )

            if start >= cutoff:
                new_sessions.append(session)

        except Exception:
            continue

    data["sessions"] = new_sessions


# =========================================================
# TELEGRAM STATUS
# =========================================================

def get_status_name(user):

    status = user.status

    if isinstance(status, UserStatusOnline):
        return "ONLINE"

    if isinstance(status, UserStatusOffline):
        return "OFFLINE"

    if isinstance(status, UserStatusRecently):
        return "RECENTLY"

    if isinstance(status, UserStatusLastWeek):
        return "LAST_WEEK"

    if isinstance(status, UserStatusLastMonth):
        return "LAST_MONTH"

    return "UNKNOWN"


# =========================================================
# PROCESS STATUS
# =========================================================

def process_status(data, user):

    status = user.status
    status_name = get_status_name(user)

    changed = False

    current_time = now_ist()

    # -----------------------------------------------------
    # ONLINE
    # -----------------------------------------------------

    if isinstance(status, UserStatusOnline):

        if data.get("current_online_since") is None:

            data["current_online_since"] = iso(
                current_time
            )

            print(
                f"{current_time:%Y-%m-%d %H:%M:%S} ONLINE",
                flush=True
            )

            changed = True

        data["last_status"] = "ONLINE"

    # -----------------------------------------------------
    # OFFLINE
    # -----------------------------------------------------

    elif isinstance(status, UserStatusOffline):

        # Telegram may provide the exact last-seen time.
        if status.was_online is not None:

            last_seen = iso(
                status.was_online
            )

            if data.get("last_seen") != last_seen:

                data["last_seen"] = last_seen
                changed = True

        # Finish currently active session.
        if data.get("current_online_since"):

            start_string = data[
                "current_online_since"
            ]

            try:
                start = datetime.fromisoformat(
                    start_string
                )

                if status.was_online is not None:
                    end = status.was_online

                    if end.tzinfo is None:
                        end = end.replace(
                            tzinfo=timezone.utc
                        )

                    end = end.astimezone(IST)

                else:
                    end = current_time

                # Prevent invalid sessions
                if end > start:

                    data["sessions"].append({
                        "start": iso(start),
                        "end": iso(end),
                    })

            except Exception as e:

                print(
                    f"Session processing error: {e}",
                    flush=True
                )

            data["current_online_since"] = None
            changed = True

        data["last_status"] = "OFFLINE"

        print(
            f"{current_time:%Y-%m-%d %H:%M:%S} OFFLINE",
            flush=True
        )

    # -----------------------------------------------------
    # RECENTLY
    # -----------------------------------------------------

    elif isinstance(status, UserStatusRecently):

        data["last_status"] = "RECENTLY"

        print(
            f"{current_time:%Y-%m-%d %H:%M:%S} "
            "RECENTLY",
            flush=True
        )

        changed = True

    # -----------------------------------------------------
    # LAST WEEK
    # -----------------------------------------------------

    elif isinstance(status, UserStatusLastWeek):

        data["last_status"] = "LAST_WEEK"

        print(
            f"{current_time:%Y-%m-%d %H:%M:%S} "
            "LAST_WEEK",
            flush=True
        )

        changed = True

    # -----------------------------------------------------
    # LAST MONTH
    # -----------------------------------------------------

    elif isinstance(status, UserStatusLastMonth):

        data["last_status"] = "LAST_MONTH"

        print(
            f"{current_time:%Y-%m-%d %H:%M:%S} "
            "LAST_MONTH",
            flush=True
        )

        changed = True

    else:

        data["last_status"] = "UNKNOWN"

        print(
            f"{current_time:%Y-%m-%d %H:%M:%S} UNKNOWN",
            flush=True
        )

        changed = True

    # Remove anything older than 7 days.
    old_sessions = len(data["sessions"])

    clean_old_sessions(data)

    if len(data["sessions"]) != old_sessions:
        changed = True

    if changed:

        data["updated_at"] = iso(
            current_time
        )

    return changed


# =========================================================
# MAIN
# =========================================================

async def main():

    global DATA
    global last_uploaded_snapshot

    print(
        "Telegram activity tracker started.",
        flush=True
    )

    print(
        "Checking every 02 seconds...",
        flush=True
    )

    # Load existing 7-day history.
    DATA = load_remote_data()

    # Make sure required fields exist.
    DATA.setdefault("sessions", [])
    DATA.setdefault(
        "current_online_since",
        None
    )
    DATA.setdefault(
        "last_seen",
        None
    )
    DATA.setdefault(
        "last_status",
        "OFFLINE"
    )
    DATA.setdefault(
        "updated_at",
        None
    )

    # Remember current remote version.
    last_uploaded_snapshot = json.dumps(
        DATA,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    # Telegram client
    client = TelegramClient(
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH,
    )

    await client.start()

    print(
        "Telegram connected.",
        flush=True
    )

    # Use the access hash directly.
    from telethon.tl.types import InputPeerUser

    peer = InputPeerUser(
        user_id=TARGET_ID,
        access_hash=TARGET_ACCESS_HASH,
    )

    while True:

        try:

            user = await client.get_entity(
                peer
            )

            changed = process_status(
                DATA,
                user
            )

            if changed:

                upload_data(DATA)

        except Exception as e:

            print(
                f"Tracker error: {e}",
                flush=True
            )

        await __import__("asyncio").sleep(
            CHECK_INTERVAL
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    import asyncio

    asyncio.run(main())
