"""
Real-time speed graph.

A dependency-free line chart painted with QPainter: one sample per second
(total transfer speed), last 120 seconds.  Auto-scaling Y axis with a
human-readable max label.
"""

from __future__ import annotations

from typing import Sequence

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QWidget

from utils.file_utils import human_size

_PAD = 8
_GRAPH_H = 120
_POINTS = 120


class SpeedGraph(QWidget):
    """Draws total download speed over time (120 s window)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("graphPanel")
        self._values: list[float] = []
        self._color = QColor("#e94560")
        self.setMinimumHeight(_GRAPH_H)
        self._label = "Speed: 0 B/s"

    # ----------------------------------------------------------------- API

    def push(self, value: float) -> None:
        self._values.append(float(value))
        if len(self._values) > _POINTS:
            self._values.pop(0)
        self._label = f"Speed: {human_size(value)}/s"
        self.update()

    def clear(self) -> None:
        self._values.clear()
        self._label = "Speed: 0 B/s"
        self.update()

    def set_accent(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    # --------------------------------------------------------------- paint

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # title
        painter.setPen(QColor(160, 160, 170))
        painter.drawText(QRectF(0, 2, w, 16),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._label)

        top = 20
        area = QRectF(_PAD, top, w - 2 * _PAD, h - top - _PAD)
        if area.width() <= 4 or area.height() <= 4:
            painter.end()
            return

        values = self._values or [0.0]
        peak = max(max(values), 8 * 1024)          # at least 8 KB/s visible
        peak *= 1.15

        points: list[tuple[float, float]] = []
        n = len(values)
        step_x = area.width() / (_POINTS - 1)
        start_x = area.right() - (n - 1) * step_x
        for i, v in enumerate(values):
            x = start_x + i * step_x
            y = area.bottom() - (v / peak) * area.height()
            points.append((x, y))

        if n >= 2:
            # gradient fill
            poly = QPolygonF()
            for x, y in points:
                poly.append(QPointF(x, y))
            poly.append(QPointF(points[-1][0], area.bottom()))
            poly.append(QPointF(points[0][0], area.bottom()))

            gradient = QLinearGradient(0, area.top(), 0, area.bottom())
            light = QColor(self._color)
            light.setAlpha(120)
            dark = QColor(self._color)
            dark.setAlpha(10)
            gradient.setColorAt(0.0, light)
            gradient.setColorAt(1.0, dark)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawPolygon(poly)

            # line
            line = QPolygonF()
            for x, y in points:
                line.append(QPointF(x, y))
            pen = QPen(QColor(self._color), 2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(line)

        # baseline
        painter.setPen(QPen(QColor(160, 160, 170, 90), 1))
        painter.drawLine(int(area.left()), int(area.bottom()),
                         int(area.right()), int(area.bottom()))
        painter.end()
