"""Сущности каталога экрана: поля, кнопки, маршрут."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Requirement = Literal['required', 'optional', 'unspecified']


@dataclass
class UiField:
    label: str
    required: Requirement = 'unspecified'
    hint: str = ''
    placeholder: str = ''


@dataclass
class UiButton:
    label: str


@dataclass
class UiScreen:
    screen_id: str
    title: str
    path: str
    section: str = ''
    audience: str = 'user'
    component_path: Path | None = None
    fields: list[UiField] = field(default_factory=list)
    buttons: list[UiButton] = field(default_factory=list)

    def has_content(self) -> bool:
        return bool(self.title or self.path or self.fields or self.buttons)
