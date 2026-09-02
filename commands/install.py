"""
Команда установки всех зависимостей: ядра и всех модулей.

Использование:
    api install                    — установить ядро + модули, удалить лишние пакеты
    api install --force            — принудительно переустановить зависимости модулей
    api install --with loadtest    — ядро + optional Poetry-группа (например locust)
"""

import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from packaging.markers import InvalidMarker, Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from commands.base import PoetryCommand

_CORE_LOCK_GROUPS = frozenset({"main"})
_PIP_BATCH_SIZE = 40
_PYTHON_DEPS_STAMP_REL = Path("virtual_env/cache/.ergo-python-deps-ok")
# Инструменты окружения: не в poetry.lock ядра, но нужны для ergoms/poetry/pip.
_ENV_TOOL_PACKAGES = frozenset({"pip", "setuptools", "wheel", "poetry"})
# moviepy 2.2.1 объявляет pillow<12.0, хотя с Pillow 12.x обычно работает (см. Zulko/moviepy#2553).
# Ставим moviepy через --no-deps; runtime-зависимости — вручную, без pillow (конфликт с ядром).
_NO_DEPS_PACKAGES = frozenset({"moviepy"})
_NO_DEPS_RUNTIME_DEPS: Dict[str, List[str]] = {
    "moviepy": [
        "decorator>=4.0.2,<6.0",
        "imageio>=2.5,<3.0",
        "imageio_ffmpeg>=0.2.0",
        "numpy>=1.25.0",
        "proglog<=1.0.0",
        "python-dotenv>=0.10",
    ],
}


def _jupyter_enabled_from_env(project_root: Path) -> bool:
    env_path = project_root / ".env"
    if not env_path.is_file():
        return False
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() != "ERGO_JUPYTER":
                continue
            token = value.strip().strip("'").strip('"').lower()
            return token not in ("", "none", "0", "false")
    except OSError:
        return False
    return False


def _auto_extra_groups(project_root: Path) -> frozenset[str]:
    groups: set[str] = set()
    docker_service = os.environ.get("ERGO_DOCKER_SERVICE_NAME", "").strip()
    if not docker_service:
        groups.add("mcp")
    if docker_service == "jupyter" or _jupyter_enabled_from_env(project_root):
        groups.add("jupyter")
    return frozenset(groups)


def _parse_with_groups(args: Tuple[Any, ...]) -> frozenset[str]:
    """Poetry-стиль: --with loadtest | --with=loadtest | --with a,b."""
    groups: list[str] = []
    tokens = [str(a) for a in args]
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == '--with' and i + 1 < len(tokens):
            groups.extend(g.strip() for g in tokens[i + 1].split(',') if g.strip())
            i += 2
            continue
        if token.startswith('--with='):
            groups.extend(
                g.strip() for g in token.split('=', 1)[1].split(',') if g.strip()
            )
        i += 1
    return frozenset(groups)


class InstallCommand(PoetryCommand):
    """
    Устанавливает зависимости ядра через poetry install, затем сканирует
    pyproject.toml всех модулей и доустанавливает недостающие пакеты через pip
    (requirements-modules.txt + constraints из poetry.lock ядра).
    В конце удаляет пакеты, которых нет в poetry.lock ядра и в зависимостях модулей.
    Корневой pyproject.toml при этом не изменяется.
    """

    poetry_command_name = "install"
    script_command = "install"

    def run(self, *args) -> int:
        force = "--force" in args

        project_root = self._find_project_root()
        if project_root is None:
            print("Ошибка: не удалось найти корневой pyproject.toml.")
            return 1

        extra_groups = _parse_with_groups(args) | _auto_extra_groups(project_root)

        fingerprint = self._python_deps_fingerprint(project_root, extra_groups=extra_groups)
        root_data = self._read_toml(project_root / "pyproject.toml")
        if root_data is None:
            return 1

        module_configs = self._scan_module_configs(project_root)
        module_only: Dict[str, Any] = {}
        if module_configs:
            merged_deps, _conflicts = self._merge_dependencies(
                root_data, module_configs
            )
            root_deps = self._get_poetry_deps(root_data)
            module_only = {
                pkg: constraint
                for pkg, constraint in merged_deps.items()
                if pkg != "python" and pkg not in root_deps
            }

        if not force:
            unsatisfied = (
                self._filter_unsatisfied_module_deps(module_only, project_root)
                if module_only
                else {}
            )
            stamp_ok = self._python_deps_stamp_matches(project_root, fingerprint)
            # Stamp совпал — достаточно проверки модульных пакетов (быстрый путь).
            # Stamp отсутствует/устарел — сверить poetry.lock с venv без poetry install.
            if not unsatisfied and (
                stamp_ok
                or self._core_lock_satisfied(project_root, extra_groups=extra_groups)
            ):
                self._write_python_deps_stamp(project_root, fingerprint)
                if stamp_ok:
                    print(
                        "─── Зависимости Python уже актуальны "
                        "(fingerprint совпал) — установка пропущена."
                    )
                else:
                    print(
                        "─── Зависимости Python уже в окружении "
                        "(stamp обновлён) — poetry install пропущен."
                    )
                return 0

        if extra_groups:
            print(
                "─── Установка зависимостей ядра (main + "
                f"{', '.join(sorted(extra_groups))})..."
            )
        else:
            print("─── Установка зависимостей ядра (main)...")
        rc = self._install_core(project_root, extra_groups=extra_groups)
        if rc != 0:
            return rc

        if not module_configs:
            print("Модульных pyproject.toml не найдено.")
        else:
            merged_deps, conflicts = self._merge_dependencies(root_data, module_configs)

            if conflicts:
                print("\nКонфликты версий (применена более строгая):")
                for pkg, sources in conflicts.items():
                    for src, ver in sources.items():
                        print(f"  {pkg} [{src}]: {ver}")

            root_deps = self._get_poetry_deps(root_data)
            module_only = {
                pkg: constraint
                for pkg, constraint in merged_deps.items()
                if pkg != "python" and pkg not in root_deps
            }

            if not module_only:
                print("─── Дополнительных зависимостей модулей (вне ядра) нет.")
            else:
                to_install = module_only
                if not force:
                    to_install = self._filter_unsatisfied_module_deps(
                        module_only, project_root
                    )

                if not to_install:
                    print("─── Все модульные пакеты уже установлены.")
                else:
                    print(
                        f"\n─── Установка {len(to_install)} пакетов из модулей: "
                        f"{', '.join(sorted(to_install))}"
                    )

                    if force:
                        self._uninstall_packages(list(to_install.keys()))

                    rc = self._install_module_packages(
                        project_root, root_data, to_install
                    )
                    if rc != 0:
                        return rc

        rc = self._sync_main_lock_versions(project_root)
        if rc != 0:
            return rc

        rc = self._remove_orphaned_packages(
            project_root, module_only, extra_groups=extra_groups
        )
        if rc != 0:
            return rc

        rc = self._prune_unused_dep_caches(
            project_root, module_only, extra_groups=extra_groups
        )
        if rc != 0:
            return rc

        self._write_python_deps_stamp(project_root, fingerprint)
        return 0

    def _python_deps_fingerprint(
        self,
        project_root: Path,
        *,
        extra_groups: frozenset[str] = frozenset(),
    ) -> str:
        digest = hashlib.sha256()
        digest.update(
            f"groups:{','.join(sorted(extra_groups))}\n".encode("utf-8")
        )
        digest.update(
            f"disabled:{os.environ.get('DISABLED_MODULES', '')}\n".encode("utf-8")
        )
        for rel in ("poetry.lock", "pyproject.toml"):
            path = project_root / rel
            if not path.is_file():
                continue
            digest.update(rel.encode("utf-8"))
            digest.update(path.read_bytes())
        modules_dir = project_root / "modules"
        if modules_dir.is_dir():
            for config_path in sorted(modules_dir.glob("*/pyproject.toml")):
                rel = config_path.relative_to(project_root).as_posix()
                digest.update(rel.encode("utf-8"))
                digest.update(config_path.read_bytes())
        return digest.hexdigest()

    def _python_deps_stamp_path(self, project_root: Path) -> Path:
        return project_root / _PYTHON_DEPS_STAMP_REL

    def _python_deps_stamp_matches(self, project_root: Path, fingerprint: str) -> bool:
        stamp = self._python_deps_stamp_path(project_root)
        if not stamp.is_file():
            return False
        try:
            return stamp.read_text(encoding="utf-8").strip() == fingerprint
        except OSError:
            return False

    def _write_python_deps_stamp(self, project_root: Path, fingerprint: str) -> None:
        stamp = self._python_deps_stamp_path(project_root)
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(fingerprint + "\n", encoding="utf-8")

    def _install_core(
        self,
        project_root: Path,
        *,
        extra_groups: frozenset[str] = frozenset(),
    ) -> int:
        env = os.environ.copy()
        env["POETRY_VIRTUALENVS_CREATE"] = "false"
        pip_cache = project_root / "virtual_env" / "cache" / "pip"
        poetry_cache = project_root / "virtual_env" / "cache" / "poetry"
        pip_cache.mkdir(parents=True, exist_ok=True)
        poetry_cache.mkdir(parents=True, exist_ok=True)
        env["PIP_CACHE_DIR"] = str(pip_cache)
        env["POETRY_CACHE_DIR"] = str(poetry_cache)
        cmd = [
            "poetry",
            "install",
            "--no-root",
            "--directory",
            str(project_root),
        ]
        if extra_groups:
            # main + optional groups (не --only main — иначе группа не ставится)
            cmd.extend(["--with", ",".join(sorted(extra_groups))])
        else:
            cmd.extend(["--only", "main"])
        result = subprocess.run(cmd, cwd=str(project_root), env=env)
        return result.returncode

    def _install_module_packages(
        self, project_root: Path, root_data: dict, module_deps: dict
    ) -> int:
        lock_path = project_root / "poetry.lock"
        main_versions = self._parse_poetry_lock(lock_path, groups=_CORE_LOCK_GROUPS)
        if not main_versions:
            print("Предупреждение: poetry.lock ядра не найден или пуст — constraints не применены.")

        regular_deps = {
            pkg: constraint
            for pkg, constraint in module_deps.items()
            if pkg not in _NO_DEPS_PACKAGES
        }
        no_deps_packages = {
            pkg: constraint
            for pkg, constraint in module_deps.items()
            if pkg in _NO_DEPS_PACKAGES
        }

        all_sources = self._collect_all_sources(project_root, root_data)
        extra_index_urls = self._collect_extra_index_urls(module_deps, all_sources)

        cache_tmp = project_root / 'virtual_env' / 'cache' / 'tmp'
        cache_tmp.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix='ergo_install_', dir=str(cache_tmp)))
        try:
            if regular_deps:
                constraints_path = tmp_dir / "core_constraints.txt"
                requirements_path = tmp_dir / "requirements-modules.txt"

                if main_versions:
                    self._write_core_constraints(constraints_path, main_versions)

                self._write_module_requirements(
                    requirements_path,
                    regular_deps,
                    project_root,
                    extra_index_urls,
                )

                cmd: List[str] = [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    str(requirements_path),
                ]
                if main_versions:
                    cmd.extend(["-c", str(constraints_path)])

                print(
                    "Установка модульных пакетов: pip install -r requirements-modules.txt "
                    "(constraints: poetry.lock ядра)..."
                )
                pip_env = os.environ.copy()
                pip_cache = project_root / 'virtual_env' / 'cache' / 'pip'
                poetry_cache = project_root / 'virtual_env' / 'cache' / 'poetry'
                pip_cache.mkdir(parents=True, exist_ok=True)
                poetry_cache.mkdir(parents=True, exist_ok=True)
                pip_env['PIP_CACHE_DIR'] = str(pip_cache)
                pip_env['POETRY_CACHE_DIR'] = str(poetry_cache)
                result = subprocess.run(cmd, cwd=str(project_root), env=pip_env)
                if result.returncode != 0:
                    return result.returncode

            return self._install_no_deps_packages(
                project_root,
                no_deps_packages,
                extra_index_urls,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _install_no_deps_packages(
        self,
        project_root: Path,
        no_deps_packages: Dict[str, Any],
        extra_index_urls: List[str],
    ) -> int:
        if not no_deps_packages:
            return 0

        for pkg, constraint in sorted(no_deps_packages.items()):
            req_line = self._to_pip_requirement(pkg, constraint, project_root)
            runtime_deps = _NO_DEPS_RUNTIME_DEPS.get(pkg, [])
            if runtime_deps:
                print(
                    f"Установка зависимостей {pkg} (без pillow-конфликта metadata): "
                    f"{', '.join(runtime_deps)}"
                )
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", *runtime_deps],
                    cwd=str(project_root),
                )
                if result.returncode != 0:
                    return result.returncode

            cmd: List[str] = [sys.executable, "-m", "pip", "install", "--no-deps", req_line]
            for url in extra_index_urls:
                cmd.extend(["--extra-index-url", url])

            print(
                f"Установка {pkg} без проверки metadata зависимостей "
                f"(конфликт с версией pillow ядра)..."
            )
            result = subprocess.run(cmd, cwd=str(project_root))
            if result.returncode != 0:
                return result.returncode

        return 0

    def _write_core_constraints(self, path: Path, main_versions: Dict[str, str]) -> None:
        path.write_text(
            "\n".join(f"{name}=={version}" for name, version in sorted(main_versions.items()))
            + "\n",
            encoding="utf-8",
        )

    def _write_module_requirements(
        self,
        path: Path,
        module_deps: dict,
        project_root: Path,
        extra_index_urls: List[str],
    ) -> None:
        lines: List[str] = []
        for url in extra_index_urls:
            lines.append(f"--extra-index-url {url}")
        for pkg, constraint in sorted(module_deps.items()):
            lines.append(self._to_pip_requirement(pkg, constraint, project_root))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _core_lock_satisfied(
        self,
        project_root: Path,
        *,
        extra_groups: frozenset[str] = frozenset(),
    ) -> bool:
        """True, если пакеты из poetry.lock (нужные группы) уже стоят нужных версий."""
        main_versions = self._parse_poetry_lock(
            project_root / "poetry.lock",
            groups=self._lock_groups_for_install(extra_groups),
        )
        if not main_versions:
            return False
        installed = self._get_installed_versions()
        return all(
            self._lock_version_matches_installed(name, version, installed)
            for name, version in main_versions.items()
        )

    def _sync_main_lock_versions(self, project_root: Path) -> int:
        main_versions = self._parse_poetry_lock(
            project_root / "poetry.lock",
            groups=_CORE_LOCK_GROUPS,
        )
        if not main_versions:
            return 0

        installed = self._get_installed_versions()
        drifted = [
            f"{name}=={version}"
            for name, version in sorted(main_versions.items())
            if not self._lock_version_matches_installed(name, version, installed)
        ]

        if not drifted:
            print("─── Версии ядра совпадают с poetry.lock — синхронизация не требуется.")
            return 0

        print(f"─── Синхронизация {len(drifted)} пакетов ядра с poetry.lock...")
        for i in range(0, len(drifted), _PIP_BATCH_SIZE):
            batch = drifted[i : i + _PIP_BATCH_SIZE]
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", *batch],
                cwd=str(project_root),
            )
            if result.returncode != 0:
                return result.returncode
        return 0

    def _get_installed_versions(self) -> Dict[str, str]:
        from importlib.metadata import distributions

        versions: Dict[str, str] = {}
        for dist in distributions():
            name = dist.metadata.get("Name") or dist.name
            if name:
                versions[canonicalize_name(name)] = dist.version
        return versions

    def _lock_version_matches_installed(
        self,
        name: str,
        lock_ver: str,
        installed: Dict[str, str],
    ) -> bool:
        inst_ver = installed.get(canonicalize_name(name))
        if inst_ver is None:
            return False
        try:
            return Version(inst_ver) == Version(lock_ver)
        except InvalidVersion:
            return inst_ver == lock_ver

    def _filter_unsatisfied_module_deps(
        self, module_deps: dict, project_root: Path
    ) -> dict:
        installed = self._get_installed_versions()
        unsatisfied: Dict[str, Any] = {}
        for pkg, constraint in module_deps.items():
            req_line = self._to_pip_requirement(pkg, constraint, project_root)
            if not self._requirement_satisfied(req_line, installed):
                unsatisfied[pkg] = constraint
                continue
            for dep_line in _NO_DEPS_RUNTIME_DEPS.get(canonicalize_name(pkg), []):
                if not self._requirement_satisfied(dep_line, installed):
                    unsatisfied[pkg] = constraint
                    break
        return unsatisfied

    def _requirement_satisfied(self, req_line: str, installed: Dict[str, str]) -> bool:
        try:
            req = Requirement(req_line)
        except Exception:
            return False
        inst_ver = installed.get(canonicalize_name(req.name))
        if inst_ver is None:
            return False
        try:
            return Version(inst_ver) in req.specifier
        except InvalidVersion:
            return False

    @staticmethod
    def _lock_marker_applies(marker: Optional[str]) -> bool:
        if not marker:
            return True
        try:
            return Marker(marker).evaluate()
        except InvalidMarker:
            return False

    @staticmethod
    def _parse_lock_marker_value(raw: str) -> str:
        value = raw.strip()
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, str):
                return parsed
        except (SyntaxError, ValueError):
            pass
        return value.strip('"')

    def _parse_poetry_lock(
        self, lock_path: Path, groups: Optional[frozenset] = None
    ) -> Dict[str, str]:
        if not lock_path.exists():
            return {}

        versions: Dict[str, str] = {}
        current_name: Optional[str] = None
        current_version: Optional[str] = None
        current_groups: List[str] = []
        current_marker: Optional[str] = None

        def _flush() -> None:
            nonlocal current_name, current_version, current_groups, current_marker
            if not current_name or not current_version:
                return
            if not self._lock_marker_applies(current_marker):
                current_name = None
                current_version = None
                current_groups = []
                current_marker = None
                return
            if groups is None or any(group in groups for group in current_groups):
                versions[current_name] = current_version
            current_name = None
            current_version = None
            current_groups = []
            current_marker = None

        for line in lock_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped == "[[package]]":
                _flush()
                continue
            if stripped.startswith("name = "):
                current_name = stripped.split("=", 1)[1].strip().strip('"')
            elif stripped.startswith("version = "):
                current_version = stripped.split("=", 1)[1].strip().strip('"')
            elif stripped.startswith("groups = "):
                try:
                    current_groups = ast.literal_eval(stripped.split("=", 1)[1].strip())
                except (SyntaxError, ValueError):
                    current_groups = []
            elif stripped.startswith("markers = "):
                current_marker = self._parse_lock_marker_value(stripped.split("=", 1)[1])

        _flush()
        return versions

    def _to_pip_requirement(self, pkg: str, constraint: Any, project_root: Path) -> str:
        del project_root
        if isinstance(constraint, str):
            return f"{pkg}{self._pep508_version(constraint)}"
        if isinstance(constraint, dict):
            version = constraint.get("version", "")
            if version:
                return f"{pkg}{self._pep508_version(version)}"
        return pkg

    def _pep508_version(self, constraint: str) -> str:
        value = constraint.strip()
        if not value:
            return ""

        if value.startswith("^"):
            base = value[1:]
            major = int(base.split(".", 1)[0])
            if major == 0:
                parts = base.split(".")
                minor = int(parts[1]) if len(parts) > 1 else 0
                return f">={base},<0.{minor + 1}.0"
            return f">={base},<{major + 1}.0.0"

        if value.startswith("~"):
            base = value[1:]
            parts = base.split(".")
            if len(parts) >= 2:
                return f">={base},<{parts[0]}.{int(parts[1]) + 1}.0"
            return f">={base}"

        if re.match(r"^[<>=!~]", value):
            return value

        return f"=={value}"

    def _collect_extra_index_urls(
        self, module_deps: dict, all_sources: List[dict]
    ) -> List[str]:
        source_urls = {source["name"]: source["url"] for source in all_sources}
        urls: List[str] = []
        seen: Set[str] = set()
        for constraint in module_deps.values():
            if not isinstance(constraint, dict):
                continue
            source_name = constraint.get("source")
            if not source_name:
                continue
            url = source_urls.get(source_name)
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def _uninstall_packages(self, packages: List[str]) -> None:
        print(f"Удаление {len(packages)} пакетов перед переустановкой...")
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-y", *packages],
        )

    def _lock_groups_for_install(
        self, extra_groups: frozenset[str] = frozenset()
    ) -> frozenset[str]:
        return frozenset(_CORE_LOCK_GROUPS | set(extra_groups))

    def _desired_installed_packages(
        self,
        project_root: Path,
        module_only: Dict[str, Any],
        *,
        extra_groups: frozenset[str] = frozenset(),
    ) -> Set[str]:
        """Имена пакетов, которые должны остаться в venv (lock + модули + инструменты)."""
        lock_packages = {
            canonicalize_name(name)
            for name in self._parse_poetry_lock(
                project_root / "poetry.lock",
                groups=self._lock_groups_for_install(extra_groups),
            )
        }
        if not lock_packages:
            return set()

        roots: Set[str] = set(lock_packages)
        roots.update(_ENV_TOOL_PACKAGES)
        for pkg in module_only:
            roots.add(canonicalize_name(pkg))
            for dep_line in _NO_DEPS_RUNTIME_DEPS.get(pkg, []):
                try:
                    roots.add(canonicalize_name(Requirement(dep_line).name))
                except Exception:
                    continue

        return self._dependency_closure(roots)

    def _remove_orphaned_packages(
        self,
        project_root: Path,
        module_only: Dict[str, Any],
        *,
        extra_groups: frozenset[str] = frozenset(),
    ) -> int:
        """Удаляет пакеты, которых нет в poetry.lock ядра и зависимостях модулей."""
        desired = self._desired_installed_packages(
            project_root, module_only, extra_groups=extra_groups
        )
        if not desired:
            print(
                "─── poetry.lock ядра пуст или не найден — "
                "удаление лишних пакетов пропущено."
            )
            return 0

        installed = set(self._get_installed_versions())
        orphans = sorted(installed - desired)

        if not orphans:
            print("─── Лишних пакетов в окружении нет.")
            return 0

        print(
            f"─── Удаление {len(orphans)} пакетов, которых нет в файлах зависимостей: "
            f"{', '.join(orphans)}"
        )
        for i in range(0, len(orphans), _PIP_BATCH_SIZE):
            batch = orphans[i : i + _PIP_BATCH_SIZE]
            result = subprocess.run(
                [sys.executable, "-m", "pip", "uninstall", "-y", *batch],
                cwd=str(project_root),
            )
            if result.returncode != 0:
                return result.returncode
        return 0

    def _prune_unused_dep_caches(
        self,
        project_root: Path,
        module_only: Dict[str, Any],
        *,
        extra_groups: frozenset[str] = frozenset(),
    ) -> int:
        """Чистит virtual_env/cache от артефактов пакетов вне текущих зависимостей."""
        from commands.dep_caches import prune_python_dep_caches

        desired = self._desired_installed_packages(
            project_root, module_only, extra_groups=extra_groups
        )
        if not desired:
            desired = set(self._get_installed_versions())
            desired.update(_ENV_TOOL_PACKAGES)
        try:
            prune_python_dep_caches(project_root, desired)
        except Exception as exc:
            print(f"─── Предупреждение: не удалось очистить кэш зависимостей: {exc}")
        return 0

    def _dependency_closure(self, root_names: Set[str]) -> Set[str]:
        """Имена установленных пакетов, достижимых из root_names по Requires-Dist."""
        from importlib.metadata import PackageNotFoundError, distribution

        result: Set[str] = set()
        stack = [canonicalize_name(name) for name in root_names]
        while stack:
            name = stack.pop()
            if name in result:
                continue
            result.add(name)
            try:
                dist = distribution(name)
            except PackageNotFoundError:
                continue
            for req_str in dist.requires or []:
                try:
                    req = Requirement(req_str)
                except Exception:
                    continue
                if req.marker is not None:
                    try:
                        if not req.marker.evaluate():
                            continue
                    except InvalidMarker:
                        continue
                stack.append(canonicalize_name(req.name))
        return result

    def _find_project_root(self) -> Optional[Path]:
        candidates = [
            Path(os.getcwd()),
            Path(__file__).resolve().parent.parent.parent.parent,
        ]
        for candidate in candidates:
            if (candidate / "pyproject.toml").exists():
                return candidate
        return None

    def _read_toml(self, path: Path) -> Optional[dict]:
        try:
            with open(path, "rb") as f:
                return tomllib.load(f)
        except Exception as e:
            print(f"Ошибка чтения {path}: {e}")
            return None

    def _get_poetry_deps(self, data: dict) -> dict:
        return data.get("tool", {}).get("poetry", {}).get("dependencies", {})

    def _scan_module_configs(self, project_root: Path) -> List[Tuple[str, dict]]:
        from src.core.utils.module_registry import get_installed_module_names

        modules_dir = project_root / "modules"
        results: List[Tuple[str, dict]] = []
        if not modules_dir.exists():
            return results
        enabled = set(get_installed_module_names())
        for module_dir in sorted(modules_dir.iterdir()):
            if not module_dir.is_dir() or module_dir.name not in enabled:
                continue
            config_path = module_dir / "pyproject.toml"
            if not config_path.exists():
                continue
            data = self._read_toml(config_path)
            if data:
                deps = self._get_poetry_deps(data)
                if deps:
                    results.append((module_dir.name, deps))
        return results

    def _collect_all_sources(self, project_root: Path, root_data: dict) -> List[dict]:
        from src.core.utils.module_registry import get_installed_module_names

        seen: Dict[str, dict] = {}
        for source in root_data.get("tool", {}).get("poetry", {}).get("source", []):
            seen[source["name"]] = source
        modules_dir = project_root / "modules"
        enabled = set(get_installed_module_names())
        if modules_dir.exists():
            for module_dir in sorted(modules_dir.iterdir()):
                if not module_dir.is_dir() or module_dir.name not in enabled:
                    continue
                config_path = module_dir / "pyproject.toml"
                if not config_path.exists():
                    continue
                data = self._read_toml(config_path)
                if not data:
                    continue
                for source in data.get("tool", {}).get("poetry", {}).get("source", []):
                    seen.setdefault(source["name"], source)
        return list(seen.values())

    def _merge_dependencies(
        self,
        root_data: dict,
        module_configs: List[Tuple[str, dict]],
    ) -> Tuple[dict, dict]:
        root_deps = self._get_poetry_deps(root_data)
        merged: Dict[str, Any] = dict(root_deps)
        conflicts: Dict[str, Dict[str, Any]] = {}

        for module_name, module_deps in module_configs:
            for pkg, constraint in module_deps.items():
                if pkg == "python":
                    continue
                if pkg not in merged:
                    merged[pkg] = constraint
                elif merged[pkg] != constraint:
                    if pkg not in conflicts:
                        conflicts[pkg] = {"(текущее)": merged[pkg]}
                    conflicts[pkg][module_name] = constraint
                    merged[pkg] = self._resolve_conflict(merged[pkg], constraint)

        return merged, conflicts

    def _resolve_conflict(self, a: Any, b: Any) -> Any:
        if isinstance(a, dict) and not isinstance(b, dict):
            return a
        if isinstance(b, dict) and not isinstance(a, dict):
            return b
        try:
            return a if self._lower_ver(str(a)) >= self._lower_ver(str(b)) else b
        except Exception:
            return a

    def _lower_ver(self, constraint: str) -> tuple:
        match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", constraint)
        if match:
            return tuple(int(part or 0) for part in match.groups())
        return (0, 0, 0)
