"""Проверка живости. Пригодится и как smoke-тест выкладки.

Заодно отдаёт пределы, которые нужны клиенту, чтобы не предлагать невозможного.
Их место здесь, а не в диагностике: та считает статьи в словаре на три с
лишним миллиона строк, и звать её с экрана ввода ради одного числа значило бы
платить за него секундой ожидания.

Предел ровно один, и он тот, который иначе пришлось бы задать на клиенте
второй раз: сколько глав можно попросить пройти за раз. Разъехавшись с
сервером, такая константа даёт поле ввода, принимающее больше, чем примут в
ответ, — и отказ вместо загрузки.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings

router = APIRouter()


class Limits(BaseModel):
    max_chapters_per_run: int


class HealthOut(BaseModel):
    status: str
    limits: Limits


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(
        status="ok",
        limits=Limits(max_chapters_per_run=settings.max_chapters_per_run),
    )
