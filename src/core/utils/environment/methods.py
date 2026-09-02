import logging
import os
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from collections import OrderedDict

from src.config.paths import CACHE_DIR, MODULES_DIR

logger = logging.getLogger(__name__)

_ENV_CACHE_FILE = CACHE_DIR / 'modules_env.bin'


def _get_modules_env_mtime() -> float:
    try:
        return MODULES_DIR.stat().st_mtime if MODULES_DIR.exists() else 0
    except OSError:
        return 0


def _get_modules_env_fingerprint() -> Dict[str, float]:
    """Fingerprint всех .env файлов модулей (без .env.example)."""
    fingerprint: Dict[str, float] = {}
    modules_dir = os.path.abspath(MODULES_DIR)
    if not os.path.isdir(modules_dir):
        return fingerprint

    try:
        fingerprint['modules'] = Path(modules_dir).stat().st_mtime
    except OSError:
        fingerprint['modules'] = 0

    for file_path, _relative_path in _find_env_files_in_directory(modules_dir, 'modules'):
        try:
            fingerprint[file_path] = Path(file_path).stat().st_mtime
        except OSError:
            continue

    return fingerprint


def _env_fingerprint_equal(stored: Optional[Dict[str, float]], current: Dict[str, float]) -> bool:
    from src.core.utils.cache_fingerprint import fingerprint_equal

    if not stored:
        return False
    return fingerprint_equal(stored, current)


def _read_env_cache() -> Optional[Dict[str, str]]:
    from src.core.utils.cache_io import read_bin_cache

    data = read_bin_cache(_ENV_CACHE_FILE)
    if data is None:
        return None

    current_fingerprint = _get_modules_env_fingerprint()
    stored_fingerprint = data.get('fingerprint')
    if stored_fingerprint is None:
        if data.get('modules_mtime') != _get_modules_env_mtime():
            return None
    elif not _env_fingerprint_equal(stored_fingerprint, current_fingerprint):
        return None

    return data.get('env_vars', {})


def _write_env_cache(env_vars: Dict[str, str]) -> None:
    from src.core.utils.cache_io import write_bin_cache

    data = {
        'fingerprint': _get_modules_env_fingerprint(),
        'env_vars': env_vars,
    }
    if write_bin_cache(_ENV_CACHE_FILE, data):
        logger.debug('Modules env: сохранено в кэш (%d переменных)', len(env_vars))

def _find_env_files(configs_dir: str) -> List[Tuple[str, str]]:
    """
    Находит все .env файлы в указанной директории и её подпапках.
    
    Аргументы:
        configs_dir (str): Путь к папке configs
        
    Возвращает:
        List[Tuple[str, str]]: Список кортежей (путь_к_файлу, относительный_путь)
    """
    env_files = []
    configs_path = Path(configs_dir)
    
    if not configs_path.exists():
        logger.warning(f"Папка configs не найдена: {configs_dir}")
        return env_files
    
    # Рекурсивный поиск .env файлов
    for env_file in configs_path.rglob('.env*'):
        if env_file.is_file():
            if env_file.name.endswith('.env.example'):
                continue
            relative_path = env_file.relative_to(configs_path)
            env_files.append((str(env_file), str(relative_path)))
    
    return env_files


def _parse_env_file(file_path: str) -> Dict[str, str]:
    """
    Парсит .env файл и возвращает словарь переменных окружения.
    
    Аргументы:
        file_path (str): Путь к .env файлу
        
    Возвращает:
        Dict[str, str]: Словарь переменных окружения
    """
    env_vars = {}
    
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            for line_num, line in enumerate(file, 1):
                line = line.strip()
                
                # Пропускаем пустые строки и комментарии
                if not line or line.startswith('#'):
                    continue
                
                # Проверяем формат KEY=VALUE
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Убираем кавычки если есть
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    
                    env_vars[key] = value
                else:
                    logger.warning(f"Некорректный формат в {file_path}:{line_num}: {line}")
    
    except Exception as e:
        logger.error(f"Ошибка при чтении файла {file_path}: {e}")
    
    return env_vars


def _merge_env_variables(env_files: List[Tuple[str, str]]) -> Dict[str, Tuple[str, str]]:
    """
    Объединяет переменные окружения из всех .env файлов.
    
    Аргументы:
        env_files (List[Tuple[str, str]]): Список .env файлов
        
    Возвращает:
        Dict[str, Tuple[str, str]]: Словарь {переменная: (значение, источник)}
    """
    merged_vars = OrderedDict()
    conflicts = []
    
    for file_path, relative_path in env_files:
        env_vars = _parse_env_file(file_path)
        
        for key, value in env_vars.items():
            if key in merged_vars:
                old_value, old_source = merged_vars[key]
                if old_value != value:
                    conflicts.append({
                        'variable': key,
                        'old_value': old_value,
                        'old_source': old_source,
                        'new_value': value,
                        'new_source': relative_path
                    })
                    logger.warning(f"Конфликт переменной {key}: {old_source} vs {relative_path}")
            
            merged_vars[key] = (value, relative_path)
    
    if conflicts:
        logger.warning(f"Найдено {len(conflicts)} конфликтов переменных окружения!")
        logger.warning("=" * 60)
        for i, conflict in enumerate(conflicts, 1):
            logger.warning(f"Конфликт #{i}: {conflict['variable']}")
            logger.warning(f"  {conflict['old_source']}: (значение скрыто)")
            logger.warning(f"  {conflict['new_source']}: (значение скрыто)")
            logger.warning(f"  Используется значение из: {conflict['new_source']}")
            logger.warning("-" * 40)
        logger.warning("=" * 60)
    
    return merged_vars


def collect_env_files_from_all_sources(use_cache: Optional[bool] = None) -> Dict[str, str]:
    """
    Собирает .env файлы из modules. Кэш по mtime modules/.
    Отключить: MODULES_ENV_USE_CACHE=false.
    """
    if use_cache is None:
        use_cache = os.environ.get('MODULES_ENV_USE_CACHE', 'true').lower() in ('1', 'true', 'yes')

    if use_cache:
        cached = _read_env_cache()
        if cached is not None:
            logger.debug('Modules env: загружено из кэша')
            return cached

    try:
        modules_dir = os.path.abspath(MODULES_DIR)
        all_env_files = _find_env_files_in_directory(modules_dir, "modules")

        if not all_env_files:
            logger.info("Не найдено ни одного .env файла в папке modules")
            return {}

        merged_vars = _merge_env_variables(all_env_files)

        if not merged_vars:
            logger.info("Не найдено ни одной переменной окружения в папке modules")
            return {}

        env_dict = {key: value for key, (value, source) in merged_vars.items()}

        if use_cache:
            _write_env_cache(env_dict)

        logger.info(f"Загружено {len(env_dict)} переменных окружения из modules")

        return env_dict

    except Exception as e:
        logger.error(f"Ошибка при сборе .env файлов: {e}")
        return {}


def _find_env_files_in_directory(directory: str, source_name: str) -> List[Tuple[str, str]]:
    """
    Находит все .env файлы в указанной директории и её подпапках.
    Пропускает файлы из отключённых модулей (DISABLED_MODULES).
    
    Аргументы:
        directory (str): Путь к директории для поиска
        source_name (str): Название источника для логирования
        
    Возвращает:
        List[Tuple[str, str]]: Список кортежей (путь_к_файлу, относительный_путь)
    """
    disabled_raw = os.environ.get('DISABLED_MODULES', '')
    disabled = {m.strip() for m in disabled_raw.split(',') if m.strip()}

    env_files = []
    search_path = Path(directory)
    
    if not search_path.exists():
        logger.warning(f"Директория {source_name} не найдена: {directory}")
        return env_files
    
    for env_file in search_path.rglob('*.env*'):
        if env_file.is_file():
            if env_file.name.endswith('.env.example'):
                continue
            if disabled:
                try:
                    relative = env_file.relative_to(search_path)
                    if relative.parts and relative.parts[0] in disabled:
                        continue
                except ValueError:
                    pass
            try:
                relative_path = env_file.relative_to(search_path)
                env_files.append((str(env_file), f"{source_name}/{relative_path}"))
            except ValueError:
                env_files.append((str(env_file), f"{source_name}/{env_file.name}"))
    
    return env_files


def get_env_sources() -> Dict[str, List[str]]:
    """
    Возвращает информацию о том, из каких файлов взяты переменные окружения.
    
    Возвращает:
        Dict[str, List[str]]: Словарь {источник: [переменные]}
    """
    modules_dir = os.path.abspath(MODULES_DIR)
    
    all_env_files = []
    
    # Собираем .env файлы из папки modules
    modules_env_files = _find_env_files_in_directory(modules_dir, "modules")
    all_env_files.extend(modules_env_files)
    
    sources = {}
    for file_path, relative_path in all_env_files:
        env_vars = _parse_env_file(file_path)
        sources[relative_path] = list(env_vars.keys())
    
    return sources