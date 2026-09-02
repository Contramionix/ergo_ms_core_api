"""Скачивание DB-IP City Lite MMDB в каталог GEOIP_PATH."""

from __future__ import annotations

import gzip
import logging
import shutil
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from src.config.settings.geoip import iter_geoip_download_url_candidates, resolve_geoip_download_url
from src.core.utils.geoip import reset_geoip_reader_cache

logger = logging.getLogger('core.utils.commands')

_REQUEST_TIMEOUT = 120


class Command(BaseCommand):
    help = 'Скачивает DB-IP City Lite MMDB (без аккаунта и ключей) в GEOIP_PATH'

    def add_arguments(self, parser):
        parser.add_argument(
            '--url',
            dest='url',
            default='',
            help='Override URL скачивания (по умолчанию GEOIP_DOWNLOAD_URL из .env)',
        )

    def handle(self, *args, **options):
        target_dir = Path(settings.GEOIP_PATH)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / settings.GEOIP_CITY_DB

        url = (options.get('url') or '').strip() or resolve_geoip_download_url()
        self.stdout.write(f'Скачивание GeoIP базы: {url}')

        try:
            archive_path = self._download(url)
            mmdb_path = self._extract_mmdb(archive_path)
            self._install_mmdb(mmdb_path, target_path)
        except CommandError:
            raise
        except Exception as exc:
            logger.exception('GeoIP database download failed')
            raise CommandError(f'Не удалось скачать GeoIP базу: {exc}') from exc
        finally:
            if 'archive_path' in locals() and archive_path.exists():
                archive_path.unlink(missing_ok=True)

        reset_geoip_reader_cache()
        size_mb = target_path.stat().st_size / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(
            f'GeoIP база установлена: {target_path} ({size_mb:.1f} MB)'
        ))

    def _download(self, primary_url: str) -> Path:
        last_error = None
        for url in iter_geoip_download_url_candidates(primary_url):
            try:
                return self._download_once(url)
            except CommandError as exc:
                last_error = exc
                self.stdout.write(self.style.WARNING(str(exc)))
        raise CommandError(
            str(last_error) if last_error else 'Не удалось скачать GeoIP базу'
        )

    def _download_once(self, url: str) -> Path:
        from src.config.paths import CACHE_DIR

        suffix = Path(urlparse(url).path).suffix or '.gz'
        tmp_root = CACHE_DIR / 'tmp'
        tmp_root.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=str(tmp_root))
        tmp_path = Path(tmp.name)
        try:
            with requests.get(url, stream=True, timeout=_REQUEST_TIMEOUT) as response:
                if response.status_code != 200:
                    raise CommandError(
                        f'HTTP {response.status_code} для {url}'
                    )
                content_type = (response.headers.get('Content-Type') or '').lower()
                if 'text/html' in content_type:
                    raise CommandError(f'Ожидался бинарный архив, получен HTML: {url}')
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        tmp.write(chunk)
            tmp.close()
            if tmp_path.stat().st_size < 1024:
                raise CommandError(f'Слишком маленький файл после загрузки: {url}')
            return tmp_path
        except Exception:
            tmp.close()
            tmp_path.unlink(missing_ok=True)
            raise

    def _extract_mmdb(self, archive_path: Path) -> Path:
        from src.config.paths import CACHE_DIR

        suffixes = ''.join(archive_path.suffixes).lower()
        tmp_root = CACHE_DIR / 'tmp'
        tmp_root.mkdir(parents=True, exist_ok=True)
        extract_dir = Path(tempfile.mkdtemp(prefix='geoip_extract_', dir=str(tmp_root)))
        try:
            if suffixes.endswith('.tar.gz') or suffixes.endswith('.tgz'):
                with tarfile.open(archive_path, 'r:*') as tar:
                    tar.extractall(extract_dir)
            elif suffixes.endswith('.gz'):
                out_path = extract_dir / 'dbip-city-lite.mmdb'
                with gzip.open(archive_path, 'rb') as src, out_path.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
            else:
                raise CommandError(f'Неподдерживаемый формат архива: {archive_path.name}')

            matches = sorted(extract_dir.rglob('*.mmdb'))
            if not matches:
                raise CommandError('В архиве не найден файл .mmdb')
            return matches[0]
        except Exception:
            shutil.rmtree(extract_dir, ignore_errors=True)
            raise

    def _install_mmdb(self, source_path: Path, target_path: Path) -> None:
        temp_target = target_path.with_suffix(target_path.suffix + '.tmp')
        try:
            shutil.copy2(source_path, temp_target)
            temp_target.replace(target_path)
        finally:
            temp_target.unlink(missing_ok=True)
            extract_root = source_path.parent
            if extract_root.name.startswith('geoip_extract_'):
                shutil.rmtree(extract_root, ignore_errors=True)
