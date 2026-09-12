"""Synchronize local Markdown lessons with Google Docs.

By default, the script pushes local lessons to Google Drive. During a push,
an optional ``SLIDES.md`` beside ``INDEX.md`` is converted to a native Google
Slides presentation, and links to ``./SLIDES.md`` in the Google Doc point to
that presentation. The ``pull`` command reads new comments, adds them to the
matching local ``INDEX.md`` as an HTML comment block, and resolves each remote
comment after the local file has been written successfully.

Install dependencies::

    pip install -r requirements.txt

Run::

    python sync_google_comments.py          # push (default)
    python sync_google_comments.py pull
    python sync_google_comments.py push

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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload


ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / ".google_comments_sync_state.json"
DEFAULT_LOCAL_ROOT = ROOT / "done_output_materials"
SCOPES = ["https://www.googleapis.com/auth/drive"]
GOOGLE_PRESENTATION_MIME = "application/vnd.google-apps.presentation"

# Keep this deliberately narrow: local lesson links are written as
# ``[SLIDES.md](./SLIDES.md)`` and should become links to the native Google
# Slides presentation after a push. Other Markdown links must remain intact.
SLIDES_LINK_RE = re.compile(
    r"(\]\(\s*(?:<\s*)?)\./SLIDES\.md(?P<anchor>#[^\s)>]+)?(?P<title>\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\))\s*)?(?P<closing_angle>>)?\s*(?P<closing_paren>\))",
    flags=re.IGNORECASE,
)
SLIDES_REFERENCE_RE = re.compile(
    r"(?P<prefix>^\s*\[[^\]]+\]:\s*(?:<\s*)?)\./SLIDES\.md(?P<anchor>#[^\s>]+)?(?P<title>\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\))\s*)?(?:>\s*)?$",
    flags=re.IGNORECASE | re.MULTILINE,
)


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
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            # A revoked/expired refresh token or deleted OAuth client cannot
            # be repaired by retrying; use the interactive OAuth flow instead.
            error_text = str(exc)
            if "invalid_grant" not in error_text and "deleted_client" not in error_text:
                raise
            print(
                "Сохранённый Google OAuth-токен или клиент недействителен; требуется повторная авторизация.",
                file=sys.stderr,
            )
            creds = None
    if creds is None:
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
        # Do not match quoted text inside a previously inserted HTML comment;
        # otherwise a quote such as ``.`` nests new blocks and corrupts the
        # Markdown structure. Keep offsets unchanged while masking comments.
        searchable = re.sub(
            r"<!--.*?-->", lambda match: " " * len(match.group(0)), markdown, flags=re.DOTALL
        )
        position = searchable.find(quote)
        if position >= 0:
            line_end = markdown.find("\n", position + len(quote))
            if line_end < 0:
                line_end = len(markdown)
            return markdown[:line_end] + "\n\n" + block + markdown[line_end:]
    suffix = "\n\n" if markdown and not markdown.endswith("\n") else "\n"
    return markdown + suffix + block + "\n"


def local_path(local_root: Path, relative_doc_path: str) -> Path:
    parts = Path(relative_doc_path).parts
    # Google Docs are named after the lesson directory (for example,
    # ``01. Сотворение``), not after a filename with an extension. Using
    # ``Path.stem`` would incorrectly turn that name into just ``01``.
    folder = local_root.joinpath(*parts[:-1], parts[-1])
    return folder / "INDEX.md"


def questions_json_path(lesson_dir: Path) -> Path:
    return lesson_dir / "QUESTIONS.json"


def parse_questions_markdown(markdown: str) -> dict[str, Any]:
    slides=[]
    parts=re.split(r"^##\s+Слайд\s+(\d+)\.\s+(.+)$", markdown, flags=re.MULTILINE)
    for pos in range(1,len(parts)-2,3):
        number,title,body=parts[pos],parts[pos+1],parts[pos+2]
        questions=[]
        for block in re.split(r"(?=^\d+\.\s+)", body, flags=re.MULTILINE):
            if not re.match(r"^\d+\.\s+", block.strip()): continue
            lines=block.strip().splitlines(); prompt=re.sub(r"^\d+\.\s+", "", lines[0]).strip()
            options=[]
            for line in lines[1:]:
                m=re.match(r"^[-*]\s+\[([ xX])\]\s+(.+)$", line)
                if m: options.append({"text":m.group(2).strip(),"correct":m.group(1).lower()=="x"})
            questions.append({"prompt":prompt,"options":options})
        slides.append({"number":int(number),"title":title.strip(),"questions":questions,"presentation_url":None,"video_url":None})
    return {"version":1,"title":"Вопросы по уроку","slides":slides}


def find_drive_videos(service: Any, lesson_folder_id: str) -> dict[int,str]:
    videos={}
    for item in list_children(service, lesson_folder_id):
        if item.get('mimeType') == 'application/vnd.google-apps.folder': continue
        match=re.search(r'(?<!\d)(\d+)(?!\d)', item.get('name',''))
        if not match: continue
        mime=item.get('mimeType','')
        if mime.startswith('video/') or re.search(r'\\.(mp4|webm|mov|m4v|avi)$',item.get('name',''),re.I):
            videos[int(match.group(1))]=f"https://drive.google.com/file/d/{item['id']}/preview"
    return videos


def find_drive_lesson_folder(service: Any, root_id: str, course: str, lesson: str) -> str | None:
    course_item=child_by_name(service,root_id,course,'application/vnd.google-apps.folder')
    if not course_item: return None
    item=child_by_name(service,course_item['id'],lesson,'application/vnd.google-apps.folder')
    return item['id'] if item else None


def update_questions_json(lesson_dir: Path, presentation_url: str | None = None, video_urls: dict[int,str] | None = None) -> None:
    video_urls=video_urls or {}


    data=parse_questions_markdown(source.read_text(encoding="utf-8"))
    old=questions_json_path(lesson_dir)
    if old.exists():
        try:
            previous=json.loads(old.read_text(encoding="utf-8"))
            for slide in data["slides"]:
                prior=next((x for x in previous.get("slides",[]) if x.get("number")==slide["number"]),{})
                slide["presentation_url"]=presentation_url or prior.get("presentation_url")
                slide["video_url"]=video_urls.get(slide["number"], prior.get("video_url"))
        except json.JSONDecodeError: pass
    for slide in data["slides"]:
        if presentation_url: slide["presentation_url"]=presentation_url
        if slide["number"] in video_urls: slide["video_url"]=video_urls[slide["number"]]
    data["title"]=source.read_text(encoding="utf-8").splitlines()[0].lstrip("# ")
    old.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def sync_lesson_media(service: Any, slides_service: Any, root_id: str, local_root: Path, directory: Path) -> None:
    """Read media metadata; never upload files or change sharing permissions."""
    relative = directory.relative_to(local_root)
    if len(relative.parts) != 2:
        return
    course = child_by_name(service, root_id, relative.parts[0], 'application/vnd.google-apps.folder')
    if not course:
        update_questions_json(directory)
        return
    folder = child_by_name(service, course['id'], directory.name, 'application/vnd.google-apps.folder')
    videos = find_drive_videos(service, folder['id']) if folder else {}
    deck = child_by_name(service, course['id'], directory.name, GOOGLE_PRESENTATION_MIME)
    if not deck and folder:
        candidates = [x for x in list_children(service, folder['id']) if x['mimeType'] == GOOGLE_PRESENTATION_MIME]
        if len(candidates) == 1:
            deck = candidates[0]
    pages = []
    if deck:
        pages = slides_service.presentations().get(presentationId=deck['id'], fields='slides(objectId)').execute().get('slides', [])
    update_questions_json(directory, video_urls=videos)
    target = questions_json_path(directory)
    if not target.exists():
        return
    data = json.loads(target.read_text(encoding='utf-8'))
    for slide in data['slides']:
        number = slide['number']
        slide['video_url'] = videos.get(number)
        slide['presentation_url'] = None
        slide['presentation_embed_url'] = None
        if deck and 0 < number <= len(pages):
            page_id = pages[number - 1]['objectId']
            base = f"https://docs.google.com/presentation/d/{deck['id']}"
            slide['presentation_url'] = f'{base}/edit#slide=id.{page_id}'
            slide['presentation_embed_url'] = f'{base}/embed?start=false&loop=false&slide=id.{page_id}'
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Media: {directory.name}: {len(videos)} videos, {len(pages)} presentation slides")


def google_presentation_url(file_id: str) -> str:
    """Return the stable browser URL for a native Google Slides file."""
    return f"https://docs.google.com/presentation/d/{file_id}/edit"


def replace_slides_links(markdown: str, presentation_url: str) -> str:
    """Replace local ``./SLIDES.md`` Markdown links with a Slides URL.

    Anchors and optional Markdown link titles are preserved. The helper is
    intentionally limited to relative links so that an unrelated external
    link or a code sample mentioning ``SLIDES.md`` is not rewritten.
    """

    def replacement(match: re.Match[str]) -> str:
        anchor = match.group("anchor") or ""
        title = match.group("title") or ""
        closing_angle = match.group("closing_angle") or ""
        return f"{match.group(1)}{presentation_url}{anchor}{title}{closing_angle}{match.group('closing_paren')}"

    rewritten = SLIDES_LINK_RE.sub(replacement, markdown)

    def reference_replacement(match: re.Match[str]) -> str:
        anchor = match.group("anchor") or ""
        title = match.group("title") or ""
        return f"{match.group('prefix')}{presentation_url}{anchor}{title}"

    return SLIDES_REFERENCE_RE.sub(reference_replacement, rewritten)


def pull() -> int:
    load_dotenv(ROOT / ".env")
    cutoff = read_cutoff()
    check_started_at = now_iso()
    local_root = Path(os.environ.get("LOCAL_ROOT", str(DEFAULT_LOCAL_ROOT)))
    root_folder_id = os.environ["GOOGLE_DRIVE_ROOT_FOLDER_ID"]
    credentials = oauth_credentials()
    service = build("drive", "v3", credentials=credentials)

    slides_service = build('slides', 'v1', credentials=credentials)
    for directory in local_root.rglob('QUESTIONS.json'):
        sync_lesson_media(service, slides_service, root_folder_id, local_root, directory.parent)
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
            service.replies().create(
                fileId=file_info["id"],
                commentId=comment["id"],
                body={"action": "resolve"},
                fields="id,action",
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


def upload_slide_image(service: Any, parent_id: str, source: Path) -> dict[str, Any]:
    """Upload/update one slide image and return a public URL plus dimensions.

    Google Slides fetches images server-side, so a local Markdown path cannot
    be used directly.  The image is kept in a small assets folder in Drive,
    made readable by anyone with the link, and then referenced by its stable
    Drive download URL.  Updating a file with the same name keeps repeated
    pushes idempotent instead of creating a new asset on every run.
    """
    media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    media = MediaFileUpload(str(source), mimetype=media_type, resumable=False)
    try:
        existing = child_by_name(service, parent_id, source.name)
        if existing:
            file_info = service.files().update(
                fileId=existing["id"], media_body=media, fields="id,mimeType,imageMediaMetadata"
            ).execute()
            file_id = existing["id"]
        else:
            file_info = service.files().create(
                body={"name": source.name, "parents": [parent_id]},
                media_body=media,
                fields="id,mimeType,imageMediaMetadata",
            ).execute()
            file_id = file_info["id"]
    finally:
        close_media(media)

    permissions = service.permissions().list(
        fileId=file_id, fields="permissions(id,type,role)"
    ).execute().get("permissions", [])
    if not any(item.get("type") == "anyone" and item.get("role") == "reader" for item in permissions):
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()

    metadata = service.files().get(
        fileId=file_id,
        fields="id,mimeType,imageMediaMetadata(width,height)",
    ).execute()
    dimensions = metadata.get("imageMediaMetadata", {}) or {}
    return {
        "file_id": file_id,
        "url": f"https://drive.google.com/uc?export=download&id={file_id}",
        "width": int(dimensions.get("width", 1) or 1),
        "height": int(dimensions.get("height", 1) or 1),
    }


SLIDE_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+[^)]*)?\)",
    flags=re.IGNORECASE,
)


def parse_slides_markdown(
    markdown: str, source_dir: Path | None = None
) -> list[tuple[str, str, Path | None]]:
    """Extract slide titles, Markdown bodies, and an optional local image path.

    ``SLIDES.md`` uses one image per slide.  The image syntax is kept in the
    source Markdown so it also renders in the Google Doc; the resolved local
    path is returned separately for the native Google Slides upload.
    """
    headings = list(re.finditer(r"^##\s+(.+?)\s*$", markdown, flags=re.MULTILINE))
    slides: list[tuple[str, str, Path | None]] = []
    for index, heading in enumerate(headings):
        title = heading.group(1).strip()
        if not re.match(r"^(?:Слайд|Slide)\s+\d+\b", title, flags=re.IGNORECASE):
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        body = markdown[heading.end() : end].strip()
        image_path: Path | None = None
        image_match = SLIDE_IMAGE_RE.search(body)
        if image_match and source_dir is not None:
            target = image_match.group("target")
            # Native Slides needs a local file that can be uploaded to Drive.
            # Ignore remote URLs here; the Markdown remains valid, and a slide
            # without a local image still gets its text content.
            if not re.match(r"^[a-z][a-z0-9+.-]*://", target, flags=re.IGNORECASE):
                candidate = (source_dir / target).resolve()
                try:
                    candidate.relative_to(source_dir.resolve())
                except ValueError as exc:
                    raise ValueError(
                        f"Изображение слайда выходит за пределы папки SLIDES.md: {target}"
                    ) from exc
                if not candidate.is_file():
                    raise FileNotFoundError(f"Изображение для слайда не найдено: {candidate}")
                image_path = candidate
        slides.append((title, body, image_path))
    return slides


def markdown_to_slide_text(markdown: str) -> str:
    """Turn the small Markdown vocabulary used by ``SLIDES.md`` into plain text."""
    result: list[str] = []
    in_code_block = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if SLIDE_IMAGE_RE.fullmatch(line):
            continue
        if not line:
            if result and result[-1] != "":
                result.append("")
            continue
        line = re.sub(r"^\*\*([^*]+):\*\*\s*", r"\1: ", line)
        line = re.sub(r"^\*\*(Кадр\s+[^*]+):\*\*\s*", r"\1: ", line)
        line = re.sub(r"^>\s?", "", line)
        line = re.sub(r"^[-*]\s+", "• ", line)
        # Images are placed as native Slides image elements; do not duplicate
        # their alt text inside the body text box.
        line = re.sub(r"!\[[^]]*\]\([^)]*\)", "", line)
        line = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", line)
        line = line.replace("**", "").replace("__", "")
        result.append(line)
    while result and result[-1] == "":
        result.pop()
    return "\n".join(result)


def text_box_requests(
    slide_id: str,
    object_id: str,
    text: str,
    x: float,
    y: float,
    width: float,
    height: float,
    font_size: float,
    bold: bool = False,
) -> list[dict[str, Any]]:
    """Build Slides API requests for one positioned, styled text box."""
    return [
        {
            "createShape": {
                "objectId": object_id,
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {
                        "width": {"magnitude": width, "unit": "PT"},
                        "height": {"magnitude": height, "unit": "PT"},
                    },
                    "transform": {
                        "scaleX": 1,
                        "scaleY": 1,
                        "translateX": x,
                        "translateY": y,
                        "unit": "PT",
                    },
                },
            }
        },
        {"insertText": {"objectId": object_id, "text": text or " "}},
        {
            "updateTextStyle": {
                "objectId": object_id,
                "textRange": {"type": "ALL"},
                "style": {
                    "fontFamily": "Arial",
                    "fontSize": {"magnitude": font_size, "unit": "PT"},
                    "bold": bold,
                    "foregroundColor": {
                        "opaqueColor": {
                            "rgbColor": {"red": 0.12, "green": 0.10, "blue": 0.18}
                        }
                    },
                },
                "fields": "fontFamily,fontSize,bold,foregroundColor",
            }
        },
    ]


def image_request(slide_id: str, object_id: str, image: dict[str, Any]) -> dict[str, Any]:
    """Build a Slides API ``createImage`` request fitted into the right column."""
    source_width = max(float(image.get("width", 1)), 1.0)
    source_height = max(float(image.get("height", 1)), 1.0)
    max_width = 274.0
    max_height = 264.0
    scale = min(max_width / source_width, max_height / source_height)
    width = source_width * scale
    height = source_height * scale
    x = 720.0 - 32.0 - width
    y = 86.0 + (max_height - height) / 2.0
    return {
        "createImage": {
            "objectId": object_id,
            "url": image["url"],
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {
                    "width": {"magnitude": width, "unit": "PT"},
                    "height": {"magnitude": height, "unit": "PT"},
                },
                "transform": {
                    "scaleX": 1,
                    "scaleY": 1,
                    "translateX": x,
                    "translateY": y,
                    "unit": "PT",
                },
            },
        }
    }


def rebuild_presentation(
    slides_service: Any,
    presentation_id: str,
    slides: list[tuple[str, str, dict[str, Any] | None]],
) -> None:
    """Replace all slides in a presentation with content parsed from Markdown."""
    current = slides_service.presentations().get(
        presentationId=presentation_id,
        fields="slides(objectId)",
    ).execute()
    old_slide_ids = [item["objectId"] for item in current.get("slides", [])]
    requests: list[dict[str, Any]] = []
    for number, (title, markdown_body, image) in enumerate(slides, start=1):
        # Random IDs avoid collisions with a previous sync while the old
        # slides still exist in this same batchUpdate request.
        prefix = f"sync_{uuid.uuid4().hex[:12]}_{number}"
        slide_id = f"{prefix}_slide"
        display_title = re.sub(
            r"^(?:Слайд|Slide)\s+\d+\s*[.:—-]?\s*",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip() or title
        requests.append(
            {
                "createSlide": {
                    "objectId": slide_id,
                    "slideLayoutReference": {"predefinedLayout": "BLANK"},
                }
            }
        )
        requests.extend(
            text_box_requests(
                slide_id,
                f"{prefix}_title",
                display_title,
                x=36,
                y=24,
                width=648,
                height=52,
                font_size=35,
                bold=True,
            )
        )
        requests.extend(
            text_box_requests(
                slide_id,
                f"{prefix}_body",
                markdown_to_slide_text(markdown_body),
                x=42,
                y=86,
                width=350 if image else 636,
                height=290,
                font_size=16,
            )
        )
        if image:
            requests.append(image_request(slide_id, f"{prefix}_image", image))
    requests.extend({"deleteObject": {"objectId": object_id}} for object_id in old_slide_ids)
    if not requests:
        raise ValueError("SLIDES.md does not contain any '## Слайд N' sections")
    slides_service.presentations().batchUpdate(
        presentationId=presentation_id,
        body={"requests": requests},
    ).execute()


def move_presentation_to_folder(drive_service: Any, presentation_id: str, parent_id: str) -> None:
    """Move a newly created Slides file from Drive root into the lesson folder."""
    current = drive_service.files().get(fileId=presentation_id, fields="parents").execute()
    old_parents = current.get("parents", []) or []
    if parent_id in old_parents:
        return
    kwargs: dict[str, Any] = {
        "fileId": presentation_id,
        "addParents": parent_id,
        "fields": "id,parents",
    }
    if old_parents:
        kwargs["removeParents"] = ",".join(old_parents)
    drive_service.files().update(**kwargs).execute()


def sync_presentation(
    slides_service: Any,
    drive_service: Any,
    parent_id: str,
    source: Path,
    name: str,
) -> str:
    """Create or update a native Google Slides deck from ``SLIDES.md``."""
    parsed_slides = parse_slides_markdown(source.read_text(encoding="utf-8"), source.parent)
    slides: list[tuple[str, str, dict[str, Any] | None]] = []
    assets_folder_id: str | None = None
    for title, markdown_body, image_path in parsed_slides:
        image: dict[str, Any] | None = None
        if image_path is not None:
            if assets_folder_id is None:
                assets_folder_id = ensure_folder(drive_service, parent_id, ".slides_assets")
            image = upload_slide_image(drive_service, assets_folder_id, image_path)
        slides.append((title, markdown_body, image))
    if not slides:
        raise ValueError(f"{source} does not contain any '## Слайд N' sections")
    existing = child_by_name(drive_service, parent_id, name, GOOGLE_PRESENTATION_MIME)
    if existing:
        presentation_id = existing["id"]
    else:
        created = slides_service.presentations().create(body={"title": name}).execute()
        presentation_id = created["presentationId"]
        move_presentation_to_folder(drive_service, presentation_id, parent_id)
    rebuild_presentation(slides_service, presentation_id, slides)
    return google_presentation_url(presentation_id)


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
    # Pandoc is still used for INDEX.md -> Google Docs conversion. SLIDES.md
    # is parsed and sent to the Slides API directly below, without Pandoc.
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        raise SystemExit("Pandoc is required for push but was not found on PATH.")
    credentials = oauth_credentials()
    service = build("drive", "v3", credentials=credentials)
    slides_service: Any | None = None
    if clean:
        deleted = clear_folder(service, root_folder_id)
        print(f"Deleted existing Drive items: {deleted}")
    docs = 0
    presentations = 0
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
                presentation_url: str | None = None
                slides = directory / "SLIDES.md"
                if slides.is_file():
                    if slides_service is None:
                        slides_service = build("slides", "v1", credentials=credentials)
                    try:
                        presentation_url = sync_presentation(
                            slides_service,
                            service,
                            parent_id,
                            slides,
                            directory.name,
                        )
                    except HttpError as exc:
                        if exc.resp.status == 403 and "SERVICE_DISABLED" in str(exc):
                            raise SystemExit(
                                "Google Slides API отключён в проекте Google Cloud. "
                                "Включите slides.googleapis.com и повторите push."
                            ) from exc
                        raise
                    presentations += 1
                    rel_parts = directory.relative_to(local_root).parts
                    drive_folder = find_drive_lesson_folder(service, root_folder_id, rel_parts[-2], rel_parts[-1]) if len(rel_parts) >= 2 else None
                    videos = find_drive_videos(service, drive_folder) if drive_folder else {}
                    update_questions_json(directory, presentation_url=presentation_url, video_urls=videos)

                markdown = index.read_text(encoding="utf-8")
                source = index
                if presentation_url:
                    rewritten = replace_slides_links(markdown, presentation_url)
                    if rewritten != markdown:
                        source = temp_root / ("_" + str(docs) + "-index.md")
                        source.write_text(rewritten, encoding="utf-8")

                docx = temp_root / ("_" + str(docs) + ".docx")
                source_arg = source.name if not source.is_absolute() else str(source)
                subprocess.run(
                    [
                        pandoc,
                        source_arg,
                        "--from=gfm",
                        "--to=docx",
                        f"--resource-path=.{os.pathsep}{index.parent.resolve()}",
                        "--output",
                        str(docx),
                    ],
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
    if slides_service is None:
        slides_service = build('slides', 'v1', credentials=credentials)
    for directory in local_root.rglob('QUESTIONS.json'):
        sync_lesson_media(service, slides_service, root_folder_id, local_root, directory.parent)
    print(f"Pushed Google Docs with embedded assets: {docs}")
    print(f"Pushed Google Slides presentations: {presentations}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["pull", "push"],
        nargs="?",
        default="push",
        help="Sync direction (default: push)",
    )
    parser.add_argument("--clean", action="store_true", help="Delete all existing items in the Drive root before push")
    args = parser.parse_args()
    return pull() if args.command == "pull" else push(clean=args.clean)


if __name__ == "__main__":
    sys.exit(main())
