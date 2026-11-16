"""Где поставить плашку на панели задач: поиск свободного промежутка (чистая функция)."""

from __future__ import annotations

from typing import Iterable, Optional

Interval = tuple[int, int]


def merge_intervals(intervals: Iterable[Interval], gap: int = 0) -> list[Interval]:
    """Склеивает пересекающиеся и почти соприкасающиеся (ближе gap) отрезки."""
    ordered = sorted((min(a, b), max(a, b)) for a, b in intervals if a != b)
    merged: list[Interval] = []
    for left, right in ordered:
        if merged and left <= merged[-1][1] + gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def free_gaps(left: int, right: int, occupied: Iterable[Interval]) -> list[Interval]:
    """Свободные промежутки внутри [left, right] между занятыми отрезками."""
    gaps: list[Interval] = []
    cursor = left
    for occ_left, occ_right in merge_intervals(
            (max(a, left), min(b, right)) for a, b in occupied if b > left and a < right):
        if occ_left > cursor:
            gaps.append((cursor, occ_left))
        cursor = max(cursor, occ_right)
    if cursor < right:
        gaps.append((cursor, right))
    return gaps


def find_slot(left: int, right: int, occupied: Iterable[Interval], width: int, margin: int,
              prefer: str = "left") -> Optional[int]:
    """X левого края плашки шириной width или None, если нигде не помещается.

    prefer="left"  — левый край первого подходящего промежутка (у левого края панели);
    prefer="right" — правый край последнего подходящего (вплотную к значкам или
                     к трею, если справа от значков есть место).
    Любое другое значение (например "auto") ведёт себя как "left".
    """
    need = width + 2 * margin
    fitting = [g for g in free_gaps(left, right, occupied) if g[1] - g[0] >= need]
    if not fitting:
        return None
    if prefer == "right":
        return fitting[-1][1] - margin - width
    return fitting[0][0] + margin
