"""Публикация пакетов справки в media_api (knowledge/<owner>/<revision>/).

Владелец пишет на свой диск. Потребитель читает локально или через sign_read.
В пакет попадают user_guides, user_description, выбранные страницы .docs/
и автоматически собранный каталог экранов клиента (маршруты, поля, кнопки).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError

from src.config.paths import MODULES_DIR, SYSTEM_DIR
from src.core.integrations import bridge
from src.core.integrations.module_contracts import (
    CORE_KNOWLEDGE_PACK,
    CORE_KNOWLEDGE_SIGN_READ,
    KNOWLEDGE_PACKS_GROUP,
    KNOWLEDGE_SIGN_READ_PREFIX,
)
from src.core.utils.media_client import get_media_client
from src.core.utils.media_client.path_utils import normalize_media_path

logger = logging.getLogger('utils.knowledge_pack')

KNOWLEDGE_PREFIX = 'knowledge'
CORE_OWNER = 'core'
CURRENT_FILENAME = 'current.json'
MANIFEST_FILENAME = 'manifest.json'
DOC_TEXT_MAX_CHARS = 80_000
SIGN_READ_EXPIRES = 300
OWNER_RE = re.compile(r'^[a-z][a-z0-9_]{0,62}$')

CORE_DOCS_ALLOWLIST = (
    'architecture.md',
    'cli.md',
    'modules.md',
)


def advertise_media_base_url() -> str:
    """Как соседние процессы видят media_api этого хоста во внутренней сети."""
    explicit = (getattr(settings, 'MEDIA_API_ADVERTISE_URL', '') or '').strip()
    if explicit:
        return explicit.rstrip('/')
    internal = (getattr(settings, 'MEDIA_API_INTERNAL_URL', '') or '').strip()
    if internal:
        return internal.rstrip('/')
    from src.config.nginx_runtime import media_api_internal_base_url

    return media_api_internal_base_url().rstrip('/')


def normalize_owner(owner: str) -> str:
    name = (owner or '').strip().lower()
    if not OWNER_RE.match(name):
        raise ValidationError('Недопустимое имя владельца пакета справки')
    return name


def knowledge_owner_root(owner: str) -> str:
    return f'{KNOWLEDGE_PREFIX}/{normalize_owner(owner)}'


def current_pointer_path(owner: str) -> str:
    return f'{knowledge_owner_root(owner)}/{CURRENT_FILENAME}'


def manifest_path(owner: str, revision: str) -> str:
    return f'{knowledge_owner_root(owner)}/{revision}/{MANIFEST_FILENAME}'


def assert_knowledge_path(path: str, *, owner: str | None = None) -> str:
    """Нормализует путь и проверяет, что он внутри knowledge/ (опционально владельца)."""
    normalized = normalize_media_path(path)
    parts = [p for p in normalized.split('/') if p]
    if not parts or parts[0] != KNOWLEDGE_PREFIX:
        raise ValidationError('Путь должен быть внутри knowledge/')
    if owner:
        expected = normalize_owner(owner)
        if len(parts) < 2 or parts[1] != expected:
            raise ValidationError(f'Путь должен быть внутри knowledge/{expected}/')
    return normalized


_HTML_TAG_RE = re.compile(r'<\s*/?\s*[a-zA-Z][^>]*>')


def html_to_plain(text: str) -> str:
    """Убирает HTML из справки: списки становятся markdown, теги не попадают в чат."""
    raw = text or ''
    if not _HTML_TAG_RE.search(raw):
        return raw
    from html import unescape

    from django.utils.html import strip_tags

    value = re.sub(r'<\s*br\s*/?\s*>', '\n', raw, flags=re.I)
    value = re.sub(r'<\s*/\s*p\s*>', '\n', value, flags=re.I)
    value = re.sub(r'<\s*p\b[^>]*>', '', value, flags=re.I)
    value = re.sub(r'<\s*/\s*li\s*>\s*<\s*li\b[^>]*>', '\n- ', value, flags=re.I)
    value = re.sub(r'<\s*li\b[^>]*>', '\n- ', value, flags=re.I)
    value = re.sub(r'<\s*/?\s*(ul|ol)\b[^>]*>', '\n', value, flags=re.I)
    value = unescape(strip_tags(value))
    value = re.sub(r'[ \t]+\n', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def _title_from_markdown(text: str, fallback: str) -> str:
    for line in (text or '').splitlines():
        stripped = line.strip()
        if stripped.startswith('#'):
            title = stripped.lstrip('#').strip()
            if title:
                return title
    return fallback


def _read_text_file(path: Path) -> str:
    text = html_to_plain(path.read_text(encoding='utf-8'))
    if len(text) > DOC_TEXT_MAX_CHARS:
        return text[:DOC_TEXT_MAX_CHARS]
    return text


def _document(
    *,
    doc_id: str,
    title: str,
    text: str,
    language: str = 'ru',
    permission_key: str = '',
) -> dict[str, Any]:
    return {
        'id': doc_id,
        'title': html_to_plain(title).strip() or title,
        'text': html_to_plain(text),
        'audience': 'user',
        'permission_key': permission_key,
        'language': language,
    }


def collect_core_documents() -> list[dict[str, Any]]:
    """user_guides ядра, выбранные страницы .docs/ и автокаталог экранов кабинета."""
    documents: list[dict[str, Any]] = []
    guides_dir = SYSTEM_DIR / 'core' / 'api' / 'user_guides'
    if guides_dir.is_dir():
        for path in sorted(guides_dir.glob('*.md')):
            if not path.is_file():
                continue
            body = _read_text_file(path)
            documents.append(_document(
                doc_id=f'user_guide:{path.stem}',
                title=_title_from_markdown(body, path.stem.replace('_', ' ')),
                text=body,
            ))
    docs_dir = SYSTEM_DIR / '.docs'
    for name in CORE_DOCS_ALLOWLIST:
        path = docs_dir / name
        if not path.is_file():
            continue
        documents.append(_document(
            doc_id=f'docs:{path.stem}',
            title=path.stem.replace('_', ' '),
            text=_read_text_file(path),
        ))
    from src.core.utils.knowledge_capabilities import site_overview_documents
    from src.core.utils.ui_catalog import collect_core_ui_documents

    for item in site_overview_documents():
        documents.append(_document(
            doc_id=str(item.get('id') or 'site'),
            title=str(item.get('title') or item.get('id') or 'site'),
            text=str(item.get('text') or ''),
            language=str(item.get('language') or 'ru'),
            permission_key=str(item.get('permission_key') or ''),
        ))
    for item in collect_core_ui_documents():
        documents.append(_document(
            doc_id=str(item.get('id') or 'ui_catalog'),
            title=str(item.get('title') or item.get('id') or 'ui_catalog'),
            text=str(item.get('text') or ''),
            language=str(item.get('language') or 'ru'),
            permission_key=str(item.get('permission_key') or ''),
        ))
        if item.get('audience'):
            documents[-1]['audience'] = str(item['audience'])
    return documents


def collect_module_documents(module_name: str) -> list[dict[str, Any]]:
    """user_guides модуля, user_description и автокаталог экранов клиента.

    На процессе ядра вынесенный модуль сам публикует пакет: локальный диск
    ``modules/<name>/api/user_guides`` не обходим.
    """
    owner = normalize_owner(module_name)
    if _is_core_process():
        from src.core.utils.module_registry import get_microservice_modules

        if owner in get_microservice_modules():
            return []
    documents: list[dict[str, Any]] = []
    description = _module_user_description(owner)
    if description:
        documents.append(_document(
            doc_id='user_description',
            title=_module_display_label(owner),
            text=description,
        ))
    guides_dir = MODULES_DIR / owner / 'api' / 'user_guides'
    if guides_dir.is_dir():
        for path in sorted(guides_dir.glob('*.md')):
            if not path.is_file():
                continue
            body = _read_text_file(path)
            documents.append(_document(
                doc_id=f'user_guide:{path.stem}',
                title=_title_from_markdown(body, path.stem.replace('_', ' ')),
                text=body,
            ))
    from src.core.utils.ui_catalog import collect_module_ui_documents

    for item in collect_module_ui_documents(owner):
        documents.append(_document(
            doc_id=str(item.get('id') or 'ui_catalog'),
            title=str(item.get('title') or item.get('id') or 'ui_catalog'),
            text=str(item.get('text') or ''),
            language=str(item.get('language') or 'ru'),
            permission_key=str(item.get('permission_key') or ''),
        ))
        if item.get('audience'):
            documents[-1]['audience'] = str(item['audience'])
    return documents


def _module_catalog_row(module_name: str) -> dict[str, str]:
    from src.core.cms.adp.services.permission_catalog import (
        _resolve_module_label,
        get_modules_catalog,
    )

    for item in get_modules_catalog():
        if item.get('module_name') == module_name:
            raw = item.get('user_description') or ''
            return {
                'label': str(item.get('module_label') or '').strip() or _resolve_module_label(module_name),
                'user_description': raw.strip() if isinstance(raw, str) else '',
            }
    return {
        'label': _resolve_module_label(module_name),
        'user_description': '',
    }


def _module_display_label(module_name: str) -> str:
    from src.core.utils.user_facing import sanitize_user_facing_label

    return sanitize_user_facing_label(_module_catalog_row(module_name)['label'] or module_name)


def _module_user_description(module_name: str) -> str:
    from src.core.utils.user_facing import sanitize_user_facing_text

    return sanitize_user_facing_text(_module_catalog_row(module_name)['user_description'])


def compute_revision(documents: list[dict[str, Any]]) -> str:
    payload = json.dumps(documents, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]


def _descriptor(
    *,
    owner: str,
    revision: str,
    signer: str,
) -> dict[str, str]:
    return {
        'owner': owner,
        'revision': revision,
        'media_path': manifest_path(owner, revision),
        'signer': signer,
    }


def register_pack_descriptor(descriptor: dict[str, str]) -> None:
    owner = descriptor.get('owner') or ''
    if not owner:
        return
    bridge.provide_many(KNOWLEDGE_PACKS_GROUP, owner, dict(descriptor))


def _write_json(storage_path: str, payload: dict[str, Any]) -> None:
    client = get_media_client()
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    client.save(storage_path, data)


def _read_json(storage_path: str) -> dict[str, Any] | None:
    client = get_media_client()
    if not client.exists(storage_path):
        return None
    raw = client.read_bytes(storage_path)
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def publish_pack(
    owner: str,
    documents: list[dict[str, Any]],
    *,
    signer: str,
) -> dict[str, str] | None:
    """Пишет пакет, если revision изменился. Пустой список документов — пропуск."""
    owner = normalize_owner(owner)
    signer = normalize_owner(signer)
    if not documents:
        return None
    revision = compute_revision(documents)
    pointer = _read_json(current_pointer_path(owner))
    if pointer and pointer.get('revision') == revision:
        descriptor = _descriptor(owner=owner, revision=revision, signer=signer)
        if pointer.get('signer') != signer:
            _write_json(current_pointer_path(owner), descriptor)
        register_pack_descriptor(descriptor)
        return descriptor

    client = get_media_client()
    stored_docs: list[dict[str, Any]] = []
    for index, document in enumerate(documents):
        doc_id = str(document.get('id') or f'doc-{index}')
        safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', doc_id) or f'doc-{index}'
        rel_file = f'docs/{safe_name}.md'
        storage = f'{knowledge_owner_root(owner)}/{revision}/{rel_file}'
        body = f"# {document.get('title') or doc_id}\n\n{document.get('text') or ''}"
        client.save(storage, body.encode('utf-8'))
        stored_docs.append({
            'id': doc_id,
            'title': document.get('title') or doc_id,
            'file': rel_file,
            'audience': document.get('audience') or 'user',
            'permission_key': document.get('permission_key') or '',
            'language': document.get('language') or 'ru',
        })

    manifest = {
        'owner': owner,
        'revision': revision,
        'signer': signer,
        'documents': stored_docs,
    }
    _write_json(manifest_path(owner, revision), manifest)
    descriptor = _descriptor(owner=owner, revision=revision, signer=signer)
    _write_json(current_pointer_path(owner), descriptor)
    register_pack_descriptor(descriptor)
    logger.info('Опубликован пакет справки %s revision=%s', owner, revision)
    return descriptor


def _is_core_process() -> bool:
    import os

    from src.core.utils.module_registry import get_process_role

    role = (get_process_role() or '').strip().lower()
    if role.startswith('module:'):
        return False
    # CLI на хосте модулей часто идёт с ролью api — пакет ядра писать нельзя.
    if (os.environ.get('HOST_PROFILE') or '').strip().lower() == 'modules':
        return False
    return True


def _current_module_name() -> str | None:
    from src.core.utils.module_registry import get_process_role

    role = (get_process_role() or '').strip().lower()
    if not role.startswith('module:'):
        return None
    name = role.split(':', 1)[1].strip()
    return name or None


def publish_local_knowledge_packs() -> list[dict[str, str]]:
    """Публикует пакеты, которые принадлежат этому процессу."""
    published: list[dict[str, str]] = []
    module_name = _current_module_name()
    if module_name:
        documents = collect_module_documents(module_name)
        descriptor = publish_pack(module_name, documents, signer=module_name)
        if descriptor:
            published.append(descriptor)
        return published

    if _is_core_process():
        core_docs = collect_core_documents()
        descriptor = publish_pack(CORE_OWNER, core_docs, signer=CORE_OWNER)
        if descriptor:
            published.append(descriptor)
        from src.core.utils.module_registry import (
            get_installed_module_names,
            get_microservice_modules,
            is_module_loadable_in_process,
        )

        split = get_microservice_modules()
        for name in get_installed_module_names():
            if name in split:
                continue
            if not is_module_loadable_in_process(name):
                continue
            documents = collect_module_documents(name)
            item = publish_pack(name, documents, signer=CORE_OWNER)
            if item:
                published.append(item)
    return published


def restore_pack_descriptors_from_media() -> None:
    """Поднимает дескрипторы из current.json, чтобы мост видел пакеты после рестарта."""
    owners: list[str] = []
    module_name = _current_module_name()
    if module_name:
        owners.append(module_name)
    elif _is_core_process():
        owners.append(CORE_OWNER)
        from src.core.utils.module_registry import (
            get_installed_module_names,
            get_microservice_modules,
            is_module_loadable_in_process,
        )

        split = get_microservice_modules()
        owners.extend(
            name
            for name in get_installed_module_names()
            if name not in split and is_module_loadable_in_process(name)
        )
    for owner in owners:
        try:
            pointer = _read_json(current_pointer_path(owner))
        except Exception:
            logger.debug('Не удалось прочитать current.json для %s', owner, exc_info=True)
            continue
        if pointer and pointer.get('revision') and pointer.get('media_path'):
            register_pack_descriptor({
                'owner': str(pointer.get('owner') or owner),
                'revision': str(pointer['revision']),
                'media_path': str(pointer['media_path']),
                'signer': str(pointer.get('signer') or owner),
            })


def current_core_pack() -> dict[str, str] | None:
    pointer = _read_json(current_pointer_path(CORE_OWNER))
    if not pointer:
        return None
    owner = str(pointer.get('owner') or CORE_OWNER)
    revision = str(pointer.get('revision') or '')
    media = str(pointer.get('media_path') or '')
    signer = str(pointer.get('signer') or CORE_OWNER)
    if not revision or not media:
        return None
    return {
        'owner': owner,
        'revision': revision,
        'media_path': media,
        'signer': signer,
    }


def sign_knowledge_read(path: str, *, owner: str | None = None) -> dict[str, Any]:
    """Подписывает чтение файла пакета на media_api этого хоста."""
    normalized = assert_knowledge_path(path, owner=owner)
    client = get_media_client()
    if not client.exists(normalized):
        raise ValidationError('Файл пакета справки не найден')
    from core.shared.media_hmac import sign_url

    secret = getattr(settings, 'SECRET_KEY', '') or ''
    signature, expires = sign_url(normalized, secret, SIGN_READ_EXPIRES)
    base = advertise_media_base_url()
    url = f'{base}/serve/{normalized}?signature={signature}&expires={expires}'
    return {'url': url, 'expires': expires, 'path': normalized}


def register_module_knowledge_sign_read(module_name: str) -> None:
    """Регистрирует knowledge.sign_read.<name> для вынесенного процесса."""
    owner = normalize_owner(module_name)

    def _sign_read(*, path: str = '', **_):
        return sign_knowledge_read(path, owner=owner)

    bridge.provide(f'{KNOWLEDGE_SIGN_READ_PREFIX}{owner}', _sign_read, override=True)


def knowledge_sign_read_op(signer: str) -> str:
    name = normalize_owner(signer)
    if name == CORE_OWNER:
        return CORE_KNOWLEDGE_SIGN_READ
    return f'{KNOWLEDGE_SIGN_READ_PREFIX}{name}'


def process_signer() -> str:
    return _current_module_name() or CORE_OWNER


def visible_knowledge_owners(user) -> frozenset[str] | None:
    """None — все пакеты (админ). Иначе ядро и модули из снимка прав."""
    if user is None:
        return frozenset({CORE_OWNER})
    from src.core.cms.adp.services.permissions import PermissionService

    if PermissionService.is_admin(user):
        return None
    from src.core.integrations.session_context import get_request_session_claim_values

    payload = PermissionService.get_user_permissions(
        user,
        session_claims=get_request_session_claim_values(),
    )
    names = {CORE_OWNER}
    for perm in payload.get('module_permissions') or []:
        name = getattr(perm, 'module_name', None)
        if name is None and isinstance(perm, dict):
            name = perm.get('module_name')
        if name:
            names.add(str(name))
    return frozenset(names)


def publish_owner_pack(owner: str) -> dict[str, str] | None:
    """Публикует одного владельца. Процесс модуля не пишет пакет ядра."""
    owner = normalize_owner(owner)
    if owner == CORE_OWNER:
        if not _is_core_process():
            return None
        return publish_pack(CORE_OWNER, collect_core_documents(), signer=CORE_OWNER)
    import os

    from src.core.utils.module_registry import get_microservice_modules

    split = owner in get_microservice_modules()
    modules_host = (os.environ.get('HOST_PROFILE') or '').strip().lower() == 'modules'
    if split and _is_core_process():
        return None
    if modules_host or split:
        signer = owner
    else:
        signer = process_signer()
    return publish_pack(owner, collect_module_documents(owner), signer=signer)


def collect_pack_descriptors() -> dict[str, dict[str, str]]:
    """Дескрипторы с моста: группа + пакет ядра."""
    merged: dict[str, dict[str, str]] = {}
    for key, raw in (bridge.all(KNOWLEDGE_PACKS_GROUP) or {}).items():
        if not isinstance(raw, dict):
            continue
        owner = str(raw.get('owner') or key).strip()
        media_path = str(raw.get('media_path') or '').strip()
        revision = str(raw.get('revision') or '').strip()
        if not owner or not media_path or not revision:
            continue
        merged[owner] = {
            'owner': owner,
            'revision': revision,
            'media_path': media_path,
            'signer': str(raw.get('signer') or owner),
        }
    core = bridge.call(CORE_KNOWLEDGE_PACK, default=None)
    if isinstance(core, dict) and core.get('media_path') and core.get('revision'):
        merged[CORE_OWNER] = {
            'owner': str(core.get('owner') or CORE_OWNER),
            'revision': str(core['revision']),
            'media_path': str(core['media_path']),
            'signer': str(core.get('signer') or CORE_OWNER),
        }
    return merged


def _http_get_knowledge_bytes(url: str) -> bytes:
    from urllib.parse import urlparse

    from src.core.utils.http_proxy import urllib_opener

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError('Недопустимый URL пакета справки')
    if '/serve/knowledge/' not in (parsed.path or ''):
        raise ValueError('URL не указывает на пакет справки')
    opener = urllib_opener()
    with opener.open(url, timeout=30) as response:  # noqa: S310 — схема уже ограничена
        return response.read()


def _pack_download_error_text(url: str, exc: BaseException) -> str:
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlparse

    host = urlparse(url).netloc or '?'
    if isinstance(exc, HTTPError):
        return f'HTTP {exc.code} с {host}'
    if isinstance(exc, URLError):
        return f'{exc.reason} ({host})'
    return f'{exc} ({host})'


def _read_pack_file(storage_path: str, *, signer: str) -> bytes | None:
    client = get_media_client()
    try:
        if client.exists(storage_path):
            return client.read_bytes(storage_path)
    except Exception:
        logger.debug('Локальное чтение %s не удалось', storage_path, exc_info=True)
    signed = bridge.call(
        knowledge_sign_read_op(signer),
        path=storage_path,
        default=None,
    )
    if not isinstance(signed, dict) or not signed.get('url'):
        logger.warning('Нет подписи для пакета %s (signer=%s)', storage_path, signer)
        return None
    url = str(signed['url'])
    try:
        return _http_get_knowledge_bytes(url)
    except Exception as exc:
        logger.warning('Не удалось скачать %s: %s', storage_path, _pack_download_error_text(url, exc))
        return None


def load_published_pack_documents() -> dict[str, Any]:
    """Читает пакеты: documents, failed_owners (None — мост недоступен), descriptors."""
    documents: list[dict[str, Any]] = []
    failed_owners: list[str] = []
    try:
        descriptors = collect_pack_descriptors()
    except Exception:
        logger.warning('Не удалось собрать дескрипторы пакетов справки', exc_info=True)
        return {
            'documents': [],
            'failed_owners': None,
            'descriptors': {},
        }

    for owner, descriptor in descriptors.items():
        revision = descriptor['revision']
        signer = descriptor['signer']
        owner_failed = False
        raw = _read_pack_file(descriptor['media_path'], signer=signer)
        if not raw:
            failed_owners.append(owner)
            continue
        try:
            manifest = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning('Битый manifest пакета %s', owner)
            failed_owners.append(owner)
            continue
        if not isinstance(manifest, dict):
            failed_owners.append(owner)
            continue
        for item in manifest.get('documents') or []:
            if not isinstance(item, dict):
                continue
            rel = str(item.get('file') or '').strip()
            if not rel or '..' in rel:
                continue
            file_path = f'{knowledge_owner_root(owner)}/{revision}/{rel}'
            try:
                assert_knowledge_path(file_path, owner=owner)
            except (ValidationError, ValueError):
                continue
            body = _read_pack_file(file_path, signer=signer)
            if not body:
                owner_failed = True
                continue
            try:
                text = body.decode('utf-8').strip()
            except UnicodeDecodeError:
                owner_failed = True
                continue
            if not text:
                continue
            doc_id = str(item.get('id') or rel)
            documents.append({
                'owner': owner,
                'id': doc_id,
                'title': str(item.get('title') or doc_id),
                'text': text,
                'revision': revision,
                'source': f'knowledge/{owner}/{doc_id}',
                'permission_key': str(item.get('permission_key') or ''),
                'audience': str(item.get('audience') or 'user'),
            })
        if owner_failed:
            failed_owners.append(owner)
    return {
        'documents': documents,
        'failed_owners': failed_owners,
        'descriptors': descriptors,
    }


def iter_published_pack_documents() -> list[dict[str, Any]]:
    """Документы всех доступных пакетов: owner, id, title, text, source, revision, audience."""
    return load_published_pack_documents()['documents']
