"""
AST-линтер изоляции модулей и ядра.

Правила для modules/<X>/...:
- Файл не может импортировать `from modules.<Y> ...` или `import modules.<Y>` для Y != X.
- Разрешены импорты внутри своего модуля, ядра (src.core.*), Django/DRF и т.п.

Правила для core/api/src/:
- Запрещены любые импорты `modules.*` (доменная логика — только через ModuleBridge).
- Запрещены литеральные bridge.call/has('module.operation') где префикс не в
  CORE_BRIDGE_PREFIXES; f-string / переменные не проверяются.

Для каждого нарушения выводится файл, строка и рекомендация.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Iterable, Iterator

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from src.core.integrations.isolation import (
    ALLOWED_MODULE_PREFIXES as ALLOWED_PREFIXES,
    find_modules_dir,
)
from src.core.integrations.module_contracts import CORE_BRIDGE_PREFIXES

CORE_BRIDGE_PREFIX_WHITELIST = CORE_BRIDGE_PREFIXES


class _Violation:
    __slots__ = ('file', 'line', 'statement', 'hint')

    def __init__(self, file: str, line: int, statement: str, hint: str):
        self.file = file
        self.line = line
        self.statement = statement
        self.hint = hint

    def format(self) -> str:
        return f'{self.file}:{self.line}: {self.statement}  ({self.hint})'


class Command(BaseCommand):
    help = (
        'Проверяет изоляцию: модули не импортируют чужие modules.*; '
        'ядро не импортирует modules.* и не вызывает bridge.call/has модулей по имени.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--fail-on-warning',
            action='store_true',
            help='Выходить с ненулевым кодом при наличии нарушений.',
        )
        parser.add_argument(
            '--path',
            default=None,
            help='Проверить только указанную папку/файл.',
        )
        parser.add_argument(
            '--scope',
            choices=('modules', 'core', 'all'),
            default='modules',
            help='modules — только modules/; core — только ядро; all — оба.',
        )

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR).resolve()
        scope = options.get('scope') or 'modules'
        scan_path = options.get('path')
        modules_dir = find_modules_dir(root)
        if modules_dir is None:
            raise CommandError('Не найдена корневая папка modules/.')
        project_root = modules_dir.parent

        violations: list[_Violation] = []

        if scope in ('modules', 'all'):
            targets, _ = self._resolve_module_targets(root, scan_path, modules_dir)
            for py_file, owner in targets:
                violations.extend(self._check_module_file(py_file, owner, project_root))

        if scope in ('core', 'all'):
            core_targets = self._resolve_core_targets(root, scan_path, project_root)
            for py_file in core_targets:
                violations.extend(self._check_core_file(py_file, project_root))

        if not violations:
            self.stdout.write(self.style.SUCCESS('Нарушений изоляции не найдено.'))
            return

        self.stdout.write(self.style.WARNING(f'Обнаружено нарушений: {len(violations)}'))
        for v in violations:
            self.stdout.write(self.style.ERROR(v.format()))

        if options.get('fail_on_warning'):
            raise CommandError('Обнаружены нарушения изоляции.')

    def _resolve_module_targets(
        self,
        root: Path,
        scan_path: str | None,
        modules_dir: Path,
    ) -> tuple[list[tuple[Path, str]], Path]:
        pairs: list[tuple[Path, str]] = []
        if scan_path:
            scan_abs = self._resolve_scan_path(scan_path, root, modules_dir)
            for py in self._iter_python_files(scan_abs):
                owner = self._detect_owner_module(py, modules_dir)
                if owner is not None:
                    pairs.append((py, owner))
            return pairs, modules_dir

        for module_dir in modules_dir.iterdir():
            if not module_dir.is_dir():
                continue
            owner = module_dir.name
            for py in self._iter_python_files(module_dir):
                pairs.append((py, owner))
        return pairs, modules_dir

    def _resolve_core_targets(
        self,
        root: Path,
        scan_path: str | None,
        project_root: Path,
    ) -> list[Path]:
        default_core = project_root / 'core' / 'api' / 'src'
        if scan_path:
            scan_abs = self._resolve_scan_path(scan_path, root, project_root / 'modules')
            if scan_abs.is_file():
                return [scan_abs] if scan_abs.suffix == '.py' else []
            return list(self._iter_python_files(scan_abs))
        if not default_core.is_dir():
            raise CommandError(f'Не найден каталог ядра: {default_core}')
        return list(self._iter_python_files(default_core))

    @staticmethod
    def _resolve_scan_path(scan_path: str, root: Path, modules_dir: Path) -> Path:
        if os.path.isabs(scan_path):
            return Path(scan_path)
        project_root = modules_dir.parent
        for base in (project_root, root):
            candidate = (base / scan_path).resolve()
            if candidate.exists():
                return candidate
        return (project_root / scan_path).resolve()

    @staticmethod
    def _iter_python_files(start: Path) -> Iterator[Path]:
        if start.is_file() and start.suffix == '.py':
            yield start
            return
        if not start.exists():
            return
        for path in start.rglob('*.py'):
            parts = set(path.parts)
            if '__pycache__' in parts or 'migrations' in parts:
                continue
            yield path

    @staticmethod
    def _detect_owner_module(py: Path, modules_dir: Path) -> str | None:
        try:
            relative = py.resolve().relative_to(modules_dir.resolve())
        except ValueError:
            return None
        parts = relative.parts
        return parts[0] if parts else None

    @staticmethod
    def _relative_path(py: Path, root: Path) -> str:
        try:
            return str(py.resolve().relative_to(root.resolve()))
        except ValueError:
            return str(py)

    def _check_module_file(
        self,
        py: Path,
        owner: str,
        root: Path,
    ) -> Iterable[_Violation]:
        tree = self._parse_file(py)
        if tree is None:
            return []

        rel = self._relative_path(py, root)
        violations: list[_Violation] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ''
                foreign = self._extract_foreign_module(module, owner)
                if foreign:
                    violations.append(_Violation(
                        file=rel,
                        line=node.lineno,
                        statement=f'from {module} import ...',
                        hint=f'use bridge.call("{foreign}.<operation>", ...) instead',
                    ))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    foreign = self._extract_foreign_module(alias.name, owner)
                    if foreign:
                        violations.append(_Violation(
                            file=rel,
                            line=node.lineno,
                            statement=f'import {alias.name}',
                            hint=f'use bridge.call("{foreign}.<operation>", ...) instead',
                        ))
        return violations

    def _check_core_file(self, py: Path, root: Path) -> Iterable[_Violation]:
        tree = self._parse_file(py)
        if tree is None:
            return []

        rel = self._relative_path(py, root)
        violations: list[_Violation] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ''
                if self._is_modules_import(module):
                    violations.append(_Violation(
                        file=rel,
                        line=node.lineno,
                        statement=f'from {module} import ...',
                        hint='core must not import modules.*; use ModuleBridge',
                    ))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if self._is_modules_import(alias.name):
                        violations.append(_Violation(
                            file=rel,
                            line=node.lineno,
                            statement=f'import {alias.name}',
                            hint='core must not import modules.*; use ModuleBridge',
                        ))
            elif isinstance(node, ast.Call):
                bridge_violation = self._check_core_bridge_call(node)
                if bridge_violation:
                    op_name, module_name = bridge_violation
                    violations.append(_Violation(
                        file=rel,
                        line=node.lineno,
                        statement=f"bridge call/has '{op_name}'",
                        hint=(
                            f'core must not reference module "{module_name}"; '
                            'use bridge.emit("core.<event>", ...) or generic hooks'
                        ),
                    ))
        return violations

    @staticmethod
    def _parse_file(py: Path) -> ast.AST | None:
        try:
            source = py.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            return None
        try:
            return ast.parse(source, filename=str(py))
        except SyntaxError:
            return None

    @staticmethod
    def _extract_foreign_module(import_path: str, owner: str) -> str | None:
        if not import_path:
            return None
        if any(import_path.startswith(p) for p in ALLOWED_PREFIXES):
            return None

        parts = import_path.split('.')
        if not parts or parts[0] != 'modules':
            return None
        if len(parts) < 2:
            return None
        target = parts[1]
        if target == owner:
            return None
        return target

    @staticmethod
    def _is_modules_import(import_path: str) -> bool:
        if not import_path:
            return False
        return import_path == 'modules' or import_path.startswith('modules.')

    @staticmethod
    def _check_core_bridge_call(node: ast.Call) -> tuple[str, str] | None:
        if not isinstance(node.func, ast.Attribute):
            return None
        if node.func.attr not in ('call', 'has'):
            return None
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != 'bridge':
            return None
        if not node.args:
            return None
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            return None
        op_name = first.value
        if '.' not in op_name:
            return None
        module_name, _ = op_name.split('.', 1)
        if any(op_name.startswith(prefix) for prefix in CORE_BRIDGE_PREFIX_WHITELIST):
            return None
        return op_name, module_name
