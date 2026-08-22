"""Pull new comments from Google Docs into local Markdown lessons.

The script never changes document text in Google Drive. It only reads comments,
adds them to the matching local ``INDEX.md`` as an HTML comment block, and
resolves the remote comment after the local file has been written successfully.

Install dependencies::

    pip install -r requirements-google-sync.txt

Run::

    python sync_google_comments.py pull

The first run opens a browser for OAuth consent and stores a refresh token in
the path configured by ``GOOGLE_TOKEN_FILE``.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / ".google_comments_sync_state.json"
DEFAULT_LOCAL_ROOT = ROOT / "done_output_materials"
SCOPES = ["https://www.googleapis.com/auth/drive"]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def oauth_credentials() -> Credentials:
    client_secret = Path(os.environ["GOOGLE_OAUTH_CLIENT_SECRET_FILE"])
    token_file = Path(os.environ.get("GOOGLE_TOKEN_FILE", str(ROOT / ".google_token.json")))
    creds: Credentials | None = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
        try:
            creds = flow.run_local_server(port=0)
        except Exception as exc:
            raise SystemExit(
                "Google OAuth не разрешил доступ. Добавьте аккаунт в список Test users "
                "на OAuth consent screen и запустите скрипт снова. "
                f"Подробности: {exc}"
            ) from exc
    token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_cutoff() -> datetime:
    if not STATE_FILE.exists():
        return datetime.fromtimestamp(0, timezone.utc)
    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return parse_time(data["last_check_started_at"])


def write_cutoff(value: str) -> None:
    STATE_FILE.write_text(
        json.dumps({"last_check_started_at": value}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def list_children(service: Any, folder_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    token = None
    query = f"'{folder_id}' in parents and trashed = false"
    while True:
        response = service.files().list(
            q=query,
            spaces="drive",
            fields="nextPageToken, files(id,name,mimeType,modifiedTime)",
            pageToken=token,
            pageSize=1000,
        ).execute()
        result.extend(response.get("files", []))
        token = response.get("nextPageToken")
        if not token:
            return result


def list_google_docs(service: Any, folder_id: str, prefix: str = "") -> list[tuple[dict[str, Any], str]]:
    docs: list[tuple[dict[str, Any], str]] = []
    for item in list_children(service, folder_id):
        relative = f"{prefix}/{item['name']}" if prefix else item["name"]
        if item["mimeType"] == "application/vnd.google-apps.folder":
            docs.extend(list_google_docs(service, item["id"], relative))
        elif item["mimeType"] == "application/vnd.google-apps.document":
            docs.append((item, relative))
    return docs


def list_comments(service: Any, file_id: str) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    token = None
    while True:
        response = service.comments().list(
            fileId=file_id,
            includeDeleted=False,
            fields=(
                "nextPageToken,comments(id,content,author(displayName),createdTime," 
                "resolved,quotedFileContent(value),replies(content,author(displayName),createdTime))"
            ),
            pageToken=token,
            pageSize=100,
        ).execute()
        comments.extend(response.get("comments", []))
        token = response.get("nextPageToken")
        if not token:
            return comments


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def existing_comment_keys(markdown: str) -> set[tuple[str, str, str]]:
    pattern = re.compile(r"<!--\s*\n(.*?)\n-->\s*", re.DOTALL)
    keys: set[tuple[str, str, str]] = set()
    for match in pattern.finditer(markdown):
        lines = match.group(1).splitlines()
        if not lines:
            continue
        author = lines[0].strip()
        quote_lines = []
        body_lines = []
        in_body = False
        for line in lines[1:]:
            if line.startswith("> ") and not in_body:
                quote_lines.append(line[2:])
            elif not line.strip() and not in_body:
                continue
            else:
                in_body = True
                body_lines.append(line)
        keys.add((normalize(author), normalize("\n".join(quote_lines)), normalize("\n".join(body_lines))))
    return keys


def comment_parts(comment: dict[str, Any]) -> tuple[str, str, str]:
    author = comment.get("author", {}).get("displayName", "Неизвестный автор")
    quote = comment.get("quotedFileContent", {}).get("value", "")
    content = comment.get("content", "").strip()
    return author, quote, content


def insert_comment(markdown: str, author: str, quote: str, content: str) -> str:
    block = f"<!--\n{author}\n> {quote.replace(chr(10), chr(10) + '> ')}\n\n{content}\n-->"
    if quote:
        position = markdown.find(quote)
        if position >= 0:
            line_end = markdown.find("\n", position + len(quote))
            if line_end < 0:
                line_end = len(markdown)
            return markdown[:line_end] + "\n\n" + block + markdown[line_end:]
    suffix = "\n\n" if markdown and not markdown.endswith("\n") else "\n"
    return markdown + suffix + block + "\n"


def local_path(local_root: Path, relative_doc_path: str) -> Path:
    parts = Path(relative_doc_path).parts
    folder = local_root.joinpath(*parts[:-1], Path(parts[-1]).stem)
    return folder / "INDEX.md"


def pull() -> int:
    load_dotenv(ROOT / ".env")
    cutoff = read_cutoff()
    check_started_at = now_iso()
    local_root = Path(os.environ.get("LOCAL_ROOT", str(DEFAULT_LOCAL_ROOT)))
    root_folder_id = os.environ["GOOGLE_DRIVE_ROOT_FOLDER_ID"]
    service = build("drive", "v3", credentials=oauth_credentials())

    changed = 0
    resolved = 0
    for file_info, relative in list_google_docs(service, root_folder_id):
        new_comments = [
            item for item in list_comments(service, file_info["id"])
            if not item.get("resolved") and parse_time(item["createdTime"]) > cutoff
        ]
        if not new_comments:
            continue
        destination = local_path(local_root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        markdown = destination.read_text(encoding="utf-8") if destination.exists() else ""
        keys = existing_comment_keys(markdown)
        for comment in new_comments:
            author, quote, content = comment_parts(comment)
            key = (normalize(author), normalize(quote), normalize(content))
            if key not in keys:
                markdown = insert_comment(markdown, author, quote, content)
                keys.add(key)
                changed += 1
        destination.write_text(markdown, encoding="utf-8")
        for comment in new_comments:
            service.comments().update(
                fileId=file_info["id"], commentId=comment["id"], body={"resolved": True}
            ).execute()
            resolved += 1

    write_cutoff(check_started_at)
    print(f"New comments inserted: {changed}; remote comments resolved: {resolved}")
    return 0


def child_by_name(service: Any, parent_id: str, name: str, mime_type: str | None = None) -> dict[str, Any] | None:
    escaped = name.replace("'", "\\'")
    query = f"'{parent_id}' in parents and name = '{escaped}' and trashed = false"
    if mime_type:
        query += f" and mimeType = '{mime_type}'"
    items = service.files().list(
        q=query, spaces="drive", pageSize=10, fields="files(id,name,mimeType)"
    ).execute().get("files", [])
    return items[0] if items else None


def ensure_folder(service: Any, parent_id: str, name: str) -> str:
    existing = child_by_name(service, parent_id, name, "application/vnd.google-apps.folder")
    if existing:
        return existing["id"]
    created = service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id",
    ).execute()
    return created["id"]


def close_media(media: MediaFileUpload) -> None:
    # googleapiclient keeps the source file open on the private _fd attribute.
    handle = getattr(media, "_fd", None)
    if handle is not None and not handle.closed:
        handle.close()


def upload_file(service: Any, parent_id: str, source: Path, name: str, mime_type: str | None = None) -> None:
    media_type = mime_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    media = MediaFileUpload(str(source), mimetype=media_type, resumable=False)
    try:
        existing = child_by_name(service, parent_id, name)
        if existing:
            service.files().update(fileId=existing["id"], media_body=media).execute()
        else:
            service.files().create(body={"name": name, "parents": [parent_id]}, media_body=media, fields="id").execute()
    finally:
        close_media(media)


def clear_folder(service: Any, folder_id: str) -> int:
    """Delete every descendant of a Drive folder, recursively."""
    deleted = 0
    for item in list_children(service, folder_id):
        if item["mimeType"] == "application/vnd.google-apps.folder":
            deleted += clear_folder(service, item["id"])
        service.files().delete(fileId=item["id"]).execute()
        deleted += 1
    return deleted


def push(clean: bool = False) -> int:
    load_dotenv(ROOT / ".env")
    local_root = Path(os.environ.get("LOCAL_ROOT", str(DEFAULT_LOCAL_ROOT)))
    root_folder_id = os.environ["GOOGLE_DRIVE_ROOT_FOLDER_ID"]
    if not local_root.is_dir():
        raise SystemExit(f"Local root does not exist: {local_root}")
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        raise SystemExit("Pandoc is required for push but was not found on PATH.")
    service = build("drive", "v3", credentials=oauth_credentials())
    if clean:
        deleted = clear_folder(service, root_folder_id)
        print(f"Deleted existing Drive items: {deleted}")
    docs = 0
    with tempfile.TemporaryDirectory(prefix="child-ministry-push-") as temp_dir:
        temp_root = Path(temp_dir)
        for directory in sorted((p for p in local_root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts)):
            relative = directory.relative_to(local_root)
            parent_id = root_folder_id
            index = directory / "INDEX.md"
            # A directory with INDEX.md is represented directly by one Google
            # Doc in its parent; only directories without INDEX.md become Drive folders.
            parent_parts = relative.parts if not index.exists() else relative.parts[:-1]
            for component in parent_parts:
                parent_id = ensure_folder(service, parent_id, component)
            if index.exists():
                docx = temp_root / ("_" + str(docs) + ".docx")
                subprocess.run(
                    [pandoc, index.name, "--from=gfm", "--to=docx", "--resource-path=.", "--output", str(docx)],
                    check=True,
                    cwd=str(index.parent),
                )
                existing = child_by_name(service, parent_id, directory.name, "application/vnd.google-apps.document")
                media = MediaFileUpload(str(docx), mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                try:
                    if existing:
                        service.files().update(fileId=existing["id"], media_body=media).execute()
                    else:
                        service.files().create(
                            body={
                                "name": directory.name,
                                "parents": [parent_id],
                                "mimeType": "application/vnd.google-apps.document",
                            },
                            media_body=media,
                            fields="id",
                        ).execute()
                finally:
                    close_media(media)
                docs += 1
    print(f"Pushed Google Docs with embedded assets: {docs}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["pull", "push"])
    parser.add_argument("--clean", action="store_true", help="Delete all existing items in the Drive root before push")
    args = parser.parse_args()
    return pull() if args.command == "pull" else push(clean=args.clean)


if __name__ == "__main__":
    sys.exit(main())
