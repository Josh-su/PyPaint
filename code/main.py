import sys
import os
from collections import deque

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QColorDialog, QFileDialog, QMessageBox, QSizePolicy,
    QButtonGroup, QLabel, QSpinBox, QCheckBox, QScrollArea, QToolButton,
    QDialog, QDialogButtonBox
)
from PySide6.QtGui import (
    QPainter, QPixmap, QImage, QColor, QPen, QAction, QIcon, Qt,
    QKeySequence, QPalette, QCursor, QIntValidator
)
from PySide6.QtCore import QPoint, QPointF, QSize, QSizeF, Signal, Slot, QRect, QRectF, Qt, QTimer

# ============================================
#               CONSTANTS
# ============================================
DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 600
DEFAULT_PEN_SIZE = 5
MAX_PEN_SIZE = 100
MIN_PEN_SIZE = 1
ZOOM_STEP = 0.15
SIZE_STEP = 1
MAX_UNDO_STATES = 50
MAX_CANVAS_DIM = 10000

# Checkerboard settings for transparent areas
CHECKER_SIZE = 20
LIGHT_GRAY = QColor(220, 220, 220)
DARK_GRAY = QColor(180, 180, 180)

# ============================================
#              RESIZE DIALOG
# ============================================
class ResizeDialog(QDialog):
    """
    Dialog that allows the user to resize the canvas.
    """
    def __init__(self, current_width, current_height, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Resize Canvas")
        self.width_label = QLabel("Width:")
        self.width_spinbox = QSpinBox()
        self.width_spinbox.setRange(1, MAX_CANVAS_DIM)
        self.width_spinbox.setValue(current_width)
        self.height_label = QLabel("Height:")
        self.height_spinbox = QSpinBox()
        self.height_spinbox.setRange(1, MAX_CANVAS_DIM)
        self.height_spinbox.setValue(current_height)
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout = QGridLayout(self)
        layout.addWidget(self.width_label, 0, 0)
        layout.addWidget(self.width_spinbox, 0, 1)
        layout.addWidget(self.height_label, 1, 0)
        layout.addWidget(self.height_spinbox, 1, 1)
        layout.addWidget(self.button_box, 2, 0, 1, 2)

    def getDimensions(self):
        """
        Return the new width and height selected by the user.
        """
        return self.width_spinbox.value(), self.height_spinbox.value()


# ============================================
#               CANVAS WIDGET
# ============================================
class Canvas(QWidget):
    """
    Widget that handles drawing operations, image manipulation, zooming, and history (undo/redo).
    """
    color_changed = Signal(QColor)
    pen_size_changed = Signal(int)
    history_changed = Signal()

    def __init__(self, scroll_area, parent=None):
        super().__init__(parent)
        self.scroll_area = scroll_area
        self.setAttribute(Qt.WA_StaticContents)
        self.setMouseTracking(True)

        # Tool settings and initial states
        self.current_tool = "brush"
        self.current_color = QColor(Qt.black)
        self.brush_size = DEFAULT_PEN_SIZE
        self.eraser_size = DEFAULT_PEN_SIZE
        self.drawing = False
        self.last_point = QPointF()
        self._image_size = QSize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self.image = QImage(self._image_size, QImage.Format_ARGB32)
        self.image.fill(Qt.transparent)
        self.scale_factor = 1.0
        self.cursor_pos = QPoint()
        self.cursor_over_widget = False

        # Undo/Redo history stacks
        self.undo_stack = deque(maxlen=MAX_UNDO_STATES)
        self.redo_stack = deque(maxlen=MAX_UNDO_STATES)
        self._current_stroke_saved = False

        # Panning variables
        self.panning = False
        self.pan_start_pos = QPoint()
        
        self.checkerboard_pixmap = None
        self._create_checkerboard_pixmap()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(100)

    def _create_checkerboard_pixmap(self):
        """
        Create the checkerboard pixmap for the background.
        """
        checkerboard_size = QSize(CHECKER_SIZE * 20, CHECKER_SIZE * 20)  # Adjust size as needed
        self.checkerboard_pixmap = QPixmap(checkerboard_size)
        painter = QPainter(self.checkerboard_pixmap)
        painter.setPen(Qt.NoPen)
        for y in range(0, checkerboard_size.height(), CHECKER_SIZE):
            for x in range(0, checkerboard_size.width(), CHECKER_SIZE):
                color = LIGHT_GRAY if (x // CHECKER_SIZE + y // CHECKER_SIZE) % 2 == 0 else DARK_GRAY
                painter.setBrush(color)
                painter.drawRect(x, y, CHECKER_SIZE, CHECKER_SIZE)
        painter.end()

    def _update_cursor(self):
        """
        Update the mouse cursor based on the current tool and panning status.
        """
        if self.panning:
            return
        if self.current_tool == "brush":
            self.setCursor(Qt.CrossCursor)
        elif self.current_tool == "eraser":
            self.setCursor(Qt.CrossCursor)
        elif self.current_tool == "bucket":
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    @property
    def current_pen_size(self):
        """
        Returns the active pen size depending on the selected tool.
        """
        if self.current_tool == "brush":
            return self.brush_size
        elif self.current_tool == "eraser":
            return self.eraser_size
        else:
            return 1

    def _update_widget_size(self):
        """
        Adjust widget size based on the image dimensions and current zoom scale.
        """
        scaled_size = QSize(int(self._image_size.width() * self.scale_factor),
                            int(self._image_size.height() * self.scale_factor))
        self.setFixedSize(scaled_size)

    def set_tool(self, tool):
        """
        Change the drawing tool.
        """
        new_tool = tool.lower()
        if new_tool != self.current_tool:
            self.current_tool = new_tool
            self.pen_size_changed.emit(self.current_pen_size)
            self._update_cursor()
            self.update()

    def set_color(self, color):
        """
        Update the drawing color.
        """
        if color.isValid():
            self.current_color = color
            self.color_changed.emit(color)

    def set_pen_size(self, size):
        """
        Set the pen/eraser size within allowed limits.
        """
        new_size = max(MIN_PEN_SIZE, min(size, MAX_PEN_SIZE))
        changed = False
        if self.current_tool == "brush" and self.brush_size != new_size:
            self.brush_size = new_size
            changed = True
        elif self.current_tool == "eraser" and self.eraser_size != new_size:
            self.eraser_size = new_size
            changed = True
        if changed:
            self.update()

    def adjust_size(self, delta):
        """
        Increase or decrease the pen size by a given delta.
        """
        target_size = self.current_pen_size + delta
        new_size = max(MIN_PEN_SIZE, min(target_size, MAX_PEN_SIZE))
        changed = False
        if self.current_tool == "brush" and self.brush_size != new_size:
            self.brush_size = new_size
            changed = True
        elif self.current_tool == "eraser" and self.eraser_size != new_size:
            self.eraser_size = new_size
            changed = True
        if changed:
            self.pen_size_changed.emit(new_size)
            self.update()

    def clear_canvas(self):
        """
        Clear the canvas, save the current state for undo, and refresh the widget.
        """
        self._current_stroke_saved = True
        self._save_state()
        self.image.fill(Qt.transparent)
        self.update()
        self._update_widget_size()

    def get_image_for_saving(self, force_white_bg=False):
        """
        Return a copy of the image for saving. Optionally fill the background white (for formats like JPEG).
        """
        if force_white_bg:
            bg_image = QImage(self._image_size, QImage.Format_RGB32)
            bg_image.fill(Qt.white)
            painter = QPainter(bg_image)
            painter.drawImage(0, 0, self.image)
            painter.end()
            return bg_image
        else:
            return self.image.copy()

    def _to_image_coords(self, widget_pos: QPoint) -> QPointF:
        """
        Convert widget (screen) coordinates to image coordinates based on the current zoom.
        """
        return QPointF(widget_pos) / self.scale_factor

    def _to_widget_coords(self, image_pos: QPointF) -> QPoint:
        """
        Convert image coordinates to widget coordinates.
        """
        return (image_pos * self.scale_factor).toPoint()

    # -------------------------
    #       UNDO/REDO
    # -------------------------
    def _save_state(self, initial=False):
        """
        Save the current state of the image for undo/redo functionality.
        """
        state_tuple = (self.image.copy(), self._image_size)
        self.undo_stack.append(state_tuple)
        if not initial:
            self.redo_stack.clear()
        self.history_changed.emit()
        self._current_stroke_saved = False

    def can_undo(self):
        """
        Return True if an undo operation is possible.
        """
        return len(self.undo_stack) > 1

    def can_redo(self):
        """
        Return True if a redo operation is possible.
        """
        return len(self.redo_stack) > 0

    def undo(self):
        """
        Revert to the previous image state.
        """
        if not self.can_undo():
            return
        current_state = (self.image.copy(), self._image_size)
        self.redo_stack.append(current_state)
        prev_image, prev_size = self.undo_stack.pop()
        self.image = prev_image
        self._image_size = prev_size
        self._update_widget_size()
        self.update()
        self.history_changed.emit()

    def redo(self):
        """
        Reapply an image state that was undone.
        """
        if not self.can_redo():
            return
        current_state = (self.image.copy(), self._image_size)
        self.undo_stack.append(current_state)
        next_image, next_size = self.redo_stack.pop()
        self.image = next_image
        self._image_size = next_size
        self._update_widget_size()
        self.update()
        self.history_changed.emit()

    def _check_image_size(self):
        """
        Ensure that the image size matches the internal canvas size.
        """
        if self.image.size() != self._image_size:
            print(f"Warning: Image size {self.image.size()} differs from expected {self._image_size}. Recreating image.")
            new_image = QImage(self._image_size, QImage.Format_ARGB32)
            new_image.fill(Qt.transparent)
            painter = QPainter(new_image)
            painter.drawImage(0, 0, self.image)
            painter.end()
            self.image = new_image

    # -------------------------
    #         ZOOM
    # -------------------------
    def _zoom_at_point(self, widget_point: QPoint, factor: float):
        """
        Zoom in or out based on a widget point and a zoom factor.
        """
        old_scale = self.scale_factor
        new_scale = old_scale * factor
        new_scale = max(0.1, min(new_scale, 16.0))
        if abs(new_scale - old_scale) < 0.001:
            return
        image_point = QPointF(widget_point) / old_scale
        self.scale_factor = new_scale
        self._update_widget_size()
        target_widget_pos_f = image_point * new_scale
        delta_f = target_widget_pos_f - QPointF(widget_point)
        h_bar = self.scroll_area.horizontalScrollBar()
        v_bar = self.scroll_area.verticalScrollBar()
        new_h_val = h_bar.value() + int(round(delta_f.x()))
        new_v_val = v_bar.value() + int(round(delta_f.y()))
        h_bar.setValue(new_h_val)
        v_bar.setValue(new_v_val)
        self.update()

    def zoom_in(self):
        """
        Increase the zoom level, centering on the viewport.
        """
        view_rect = self.scroll_area.viewport().rect()
        h_bar = self.scroll_area.horizontalScrollBar()
        v_bar = self.scroll_area.verticalScrollBar()
        center_widget = QPoint(h_bar.value(), v_bar.value()) + view_rect.center()
        self._zoom_at_point(center_widget, 1.0 + ZOOM_STEP)

    def zoom_out(self):
        """
        Decrease the zoom level, centering on the viewport.
        """
        view_rect = self.scroll_area.viewport().rect()
        h_bar = self.scroll_area.horizontalScrollBar()
        v_bar = self.scroll_area.verticalScrollBar()
        center_widget = QPoint(h_bar.value(), v_bar.value()) + view_rect.center()
        self._zoom_at_point(center_widget, 1.0 / (1.0 + ZOOM_STEP))

    def reset_zoom(self):
        """
        Reset the zoom level back to 100%.
        """
        if self.scale_factor != 1.0:
            view_rect = self.scroll_area.viewport().rect()
            h_bar = self.scroll_area.horizontalScrollBar()
            v_bar = self.scroll_area.verticalScrollBar()
            center_widget_before = QPoint(h_bar.value(), v_bar.value()) + view_rect.center()
            image_point = QPointF(center_widget_before) / self.scale_factor
            self.scale_factor = 1.0
            self._update_widget_size()
            target_widget_pos_f = image_point * self.scale_factor
            target_center_h = int(round(target_widget_pos_f.x() - view_rect.width() / 2.0))
            target_center_v = int(round(target_widget_pos_f.y() - view_rect.height() / 2.0))
            h_bar.setValue(target_center_h)
            v_bar.setValue(target_center_v)
            self.update()

    # -------------------------
    #       CANVAS RESIZING
    # -------------------------
    def resize_canvas(self, new_width, new_height):
        """
        Change the canvas dimensions, preserving current drawing content.
        """
        new_size = QSize(new_width, new_height)
        if new_size == self._image_size:
            return
        print(f"Resizing canvas from {self._image_size} to {new_size}")
        self._save_state()

        self._create_checkerboard_pixmap()

        new_image = QImage(new_size, QImage.Format_ARGB32)
        new_image.fill(Qt.transparent)
        painter = QPainter(new_image)
        target_rect = QRect(QPoint(0, 0), self._image_size.boundedTo(new_size))
        source_rect = QRect(QPoint(0, 0), target_rect.size())
        painter.drawImage(target_rect, self.image, source_rect)
        painter.end()
        self.image = new_image
        self._image_size = new_size
        self._update_widget_size()
        self.update()

    # -------------------------
    #       IMAGE LOADING
    # -------------------------
    def load_image(self, new_image: QImage):
        """
        Replace the current canvas content with an external image.
        """
        if new_image.isNull():
            print("Error: Attempted to load a null image.")
            return
        print(f"Loading new image of size: {new_image.size()}")
        # Convert the image to a format that supports transparency
        self.image = new_image.convertToFormat(QImage.Format_ARGB32)
        self._image_size = self.image.size()
        # Clear undo/redo history and save the new state
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._save_state(initial=True)
        # Reset zoom and update the canvas
        self.scale_factor = 1.0
        self._update_widget_size()
        self.update()

    # -------------------------
    #       MOUSE EVENTS
    # -------------------------
    def mousePressEvent(self, event):
        """
        Handle mouse press events to start drawing or panning.
        """
        if event.button() == Qt.RightButton:
            self.panning = True
            self.pan_start_pos = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            image_pos_f = self._to_image_coords(event.position())
            image_pos_i = image_pos_f.toPoint()
            if self.current_tool == "bucket":
                self.flood_fill(image_pos_i)
                self._current_stroke_saved = True
                self._save_state()
                return  # Exit after fill operation
            self.drawing = True
            self.last_point = image_pos_f
            self.draw_point(image_pos_f)
            self._current_stroke_saved = True
            self._save_state()

    def mouseMoveEvent(self, event):
        """
        Process mouse move events for drawing lines or panning the view.
        """
        current_pos = event.position().toPoint()
        self.cursor_pos = current_pos
        if self.panning:
            delta = current_pos - self.pan_start_pos
            h_bar = self.scroll_area.horizontalScrollBar()
            v_bar = self.scroll_area.verticalScrollBar()
            h_bar.setValue(h_bar.value() - delta.x())
            v_bar.setValue(v_bar.value() - delta.y())
            self.pan_start_pos = current_pos
            event.accept()
            return
        if event.buttons() & Qt.LeftButton and self.drawing:
            if self.current_tool in ["brush", "eraser"]:
                self.draw_line_to(self._to_image_coords(current_pos))
            event.accept()
        self.update()

    def mouseReleaseEvent(self, event):
        """
        End drawing or panning when the mouse button is released.
        """
        if event.button() == Qt.RightButton and self.panning:
            self.panning = False
            self._update_cursor()
            event.accept()
            return
        self.cursor_pos = event.position().toPoint()
        if event.button() == Qt.LeftButton and self.drawing:
            self.drawing = False
            event.accept()
        self.update()

    def wheelEvent(self, event):
        """
        Zoom in/out when the mouse wheel is scrolled while holding the Ctrl key.
        """
        if event.modifiers() & Qt.ControlModifier:
            cursor_pos_widget = event.position().toPoint()
            delta = event.angleDelta().y()
            factor = 1.0 + ZOOM_STEP if delta > 0 else 1.0 / (1.0 + ZOOM_STEP) if delta < 0 else 0
            self._zoom_at_point(cursor_pos_widget, factor)
            event.accept()
        else:
            event.ignore()

    def enterEvent(self, event):
        """
        Set cursor state when the mouse enters the canvas.
        """
        self.cursor_over_widget = True
        self.cursor_pos = event.position().toPoint()
        self._update_cursor()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """
        Reset cursor when the mouse leaves the canvas.
        """
        self.cursor_over_widget = False
        self.unsetCursor()
        super().leaveEvent(event)

    # -------------------------
    #       PAINTING
    # -------------------------
    def paintEvent(self, event):
        """
        Render the checkerboard background, the image content, and tool overlays.
        """
        painter = QPainter(self)
        widget_rect = event.rect()

        # Draw checkerboard background to indicate transparency
        painter.save()
        painter.setPen(Qt.NoPen)
        image_rect = QRect(QPoint(0, 0), self._image_size * self.scale_factor)
        
        # Calculate the visible area of the checkerboard
        visible_checkerboard_rect = widget_rect.intersected(image_rect)
        
        # Calculate the offset for the checkerboard pattern
        offset_x = visible_checkerboard_rect.x() % self.checkerboard_pixmap.width()
        offset_y = visible_checkerboard_rect.y() % self.checkerboard_pixmap.height()
        
        # Draw the checkerboard pixmap with the calculated offset
        for y in range(visible_checkerboard_rect.top() - offset_y, visible_checkerboard_rect.bottom(), self.checkerboard_pixmap.height()):
            for x in range(visible_checkerboard_rect.left() - offset_x, visible_checkerboard_rect.right(), self.checkerboard_pixmap.width()):
                painter.drawPixmap(x, y, self.checkerboard_pixmap)
        painter.restore()

        # Draw the current image
        painter.save()
        painter.scale(self.scale_factor, self.scale_factor)
        source_rect_f = QRectF(self._to_image_coords(widget_rect.topLeft()),
                               self._to_image_coords(widget_rect.bottomRight())).normalized()
        painter.drawImage(source_rect_f.topLeft(), self.image, source_rect_f)
        painter.restore()

        # Draw preview circle for brush/eraser tools
        if self.cursor_over_widget and self.current_tool in ["brush", "eraser"]:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setBrush(Qt.NoBrush)
            black_pen = QPen(Qt.black, 1)
            white_pen = QPen(Qt.white, 1)
            radius = (self.current_pen_size / 2.0) * self.scale_factor
            center_point = QPointF(self.cursor_pos)
            painter.setPen(white_pen)
            painter.drawEllipse(center_point, radius, radius)
            inner_radius = max(0.0, radius - 1.0)
            if inner_radius > 0.1:
                painter.setPen(black_pen)
                painter.drawEllipse(center_point, inner_radius, inner_radius)
            painter.restore()

    # -------------------------
    #       DRAWING OPERATIONS
    # -------------------------
    def _create_base_pen(self):
        """
        Create a QPen with the current pen size and round styling.
        """
        pen = QPen()
        pen.setWidth(self.current_pen_size)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        return pen

    def draw_point(self, image_point: QPointF):
        """
        Draw a single point at the specified image coordinate.
        """
        painter = QPainter(self.image)
        pen = self._create_base_pen()
        if self.current_tool == "brush":
            pen.setColor(self.current_color)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        elif self.current_tool == "eraser":
            pen.setColor(Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
        else:
            painter.end()
            return
        painter.setPen(pen)
        painter.drawPoint(image_point)
        painter.end()
        rad = (self.current_pen_size / 2.0) + 2
        point_rect_f = QRectF(image_point - QPointF(rad, rad), QSizeF(2 * rad, 2 * rad))
        widget_update_rect = QRect(self._to_widget_coords(point_rect_f.topLeft()),
                                   self._to_widget_coords(point_rect_f.bottomRight())).normalized()
        self.update(widget_update_rect)

    def draw_line_to(self, end_image_point: QPointF):
        """
        Draw a line from the last recorded point to a new image coordinate.
        """
        painter = QPainter(self.image)
        pen = self._create_base_pen()
        if self.current_tool == "brush":
            pen.setColor(self.current_color)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        elif self.current_tool == "eraser":
            pen.setColor(Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
        else:
            painter.end()
            return
        painter.setPen(pen)
        painter.drawLine(self.last_point, end_image_point)
        painter.end()
        rad = (self.current_pen_size / 2.0) + 2
        update_rect_f = QRectF(self.last_point, end_image_point).normalized().adjusted(-rad, -rad, +rad, +rad)
        widget_update_rect = QRect(self._to_widget_coords(update_rect_f.topLeft()),
                                   self._to_widget_coords(update_rect_f.bottomRight())).normalized()
        self.update(widget_update_rect)
        self.last_point = end_image_point

    def flood_fill(self, start_image_point: QPoint, erase_mode=False):
        """
        Perform a flood fill starting from a specified image coordinate.
        """
        self.setCursor(Qt.BusyCursor)
        try:
            if not self.image.rect().contains(start_image_point):
                return
            target_color = self.image.pixelColor(start_image_point)
            fill_color = QColor(0, 0, 0, 0) if erase_mode else self.current_color
            if target_color == fill_color:
                return
            queue = deque([(start_image_point.x(), start_image_point.y())])
            processed = set([(start_image_point.x(), start_image_point.y())])
            img_width = self.image.width()
            img_height = self.image.height()
            while queue:
                x, y = queue.popleft()
                if not (0 <= x < img_width and 0 <= y < img_height):
                    continue
                current_pixel_color = self.image.pixelColor(x, y)
                if current_pixel_color == target_color:
                    self.image.setPixelColor(x, y, fill_color)
                    # Enqueue neighboring pixels
                    neighbors = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
                    for nx, ny in neighbors:
                        if (nx, ny) not in processed:
                            queue.append((nx, ny))
                            processed.add((nx, ny))
            self.update()
        finally:
            self._update_cursor()

# ============================================
#              MAIN APPLICATION WINDOW
# ============================================
class MainWindow(QMainWindow):
    """
    Main window that integrates the canvas, toolbar, and menus.
    """
    def __init__(self):
        super().__init__()

        # Create and configure the scroll area and canvas widget
        self.scroll_area = QScrollArea()
        self.canvas = Canvas(self.scroll_area)
        self.scroll_area.setBackgroundRole(QPalette.Dark)
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setWidget(self.canvas)

        # Set up main layout and toolbar
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)
        toolbar_widget = QWidget()
        toolbar_layout = QHBoxLayout(toolbar_widget)
        toolbar_layout.setContentsMargins(5, 5, 5, 5)

        # Create Undo/Redo buttons with icons and tooltips
        self.undo_button = QToolButton(text="Undo")
        self.redo_button = QToolButton(text="Redo")
        undo_icon = QIcon.fromTheme("edit-undo", QIcon(":/qt-project.org/styles/commonstyle/images/undo-16.png"))
        redo_icon = QIcon.fromTheme("edit-redo", QIcon(":/qt-project.org/styles/commonstyle/images/redo-16.png"))
        if not undo_icon.isNull():
            self.undo_button.setIcon(undo_icon)
        if not redo_icon.isNull():
            self.redo_button.setIcon(redo_icon)
        self.undo_button.setToolTip("Undo (Ctrl+Z)")
        self.redo_button.setToolTip("Redo (Ctrl+Y)")
        self.undo_button.clicked.connect(self.canvas.undo)
        self.redo_button.clicked.connect(self.canvas.redo)
        toolbar_layout.addWidget(self.undo_button)
        toolbar_layout.addWidget(self.redo_button)
        toolbar_layout.addSpacing(20)

        # Create tool selection buttons (Brush, Eraser, Bucket)
        tool_group = QButtonGroup(self)
        tool_group.setExclusive(True)
        self.brush_button = QPushButton("Brush")
        self.brush_button.setCheckable(True)
        self.brush_button.setChecked(True)
        self.eraser_button = QPushButton("Eraser")
        self.eraser_button.setCheckable(True)
        self.bucket_button = QPushButton("Bucket")
        self.bucket_button.setCheckable(True)
        self.brush_button.clicked.connect(lambda: self.set_active_tool("brush"))
        self.eraser_button.clicked.connect(lambda: self.set_active_tool("eraser"))
        self.bucket_button.clicked.connect(lambda: self.set_active_tool("bucket"))
        tool_group.addButton(self.brush_button)
        tool_group.addButton(self.eraser_button)
        tool_group.addButton(self.bucket_button)
        toolbar_layout.addWidget(self.brush_button)
        toolbar_layout.addWidget(self.eraser_button)
        toolbar_layout.addWidget(self.bucket_button)
        toolbar_layout.addSpacing(20)

        # Color selection controls
        self.color_button = QPushButton("Choose Color")
        self.color_preview = QLabel()
        self.color_preview.setFixedSize(25, 25)
        self.color_button.clicked.connect(self.choose_color)
        self.canvas.color_changed.connect(self.update_color_preview)
        toolbar_layout.addWidget(self.color_button)
        toolbar_layout.addWidget(self.color_preview)
        toolbar_layout.addSpacing(20)

        # Pen size adjustment control
        toolbar_layout.addWidget(QLabel("Size:"))
        self.pen_size_spinbox = QSpinBox()
        self.pen_size_spinbox.setRange(MIN_PEN_SIZE, MAX_PEN_SIZE)
        self.pen_size_spinbox.valueChanged.connect(self.canvas.set_pen_size)
        self.canvas.pen_size_changed.connect(self.update_spinbox_value)
        toolbar_layout.addWidget(self.pen_size_spinbox)
        toolbar_layout.addStretch()

        main_layout.addWidget(toolbar_widget)
        main_layout.addWidget(self.scroll_area)
        self.setCentralWidget(central_widget)

        # Configure menu actions and menus
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        edit_menu = menu.addMenu("&Edit")
        view_menu = menu.addMenu("&View")

        # File menu actions: New, Open, Save, Quit
        new_action = QAction("&New", self, triggered=self.confirm_new_drawing)
        open_action = QAction("&Open...", self, shortcut=QKeySequence.Open, triggered=self.open_image_file)
        save_action = QAction("&Save As...", self, shortcut=QKeySequence.SaveAs, triggered=self.save_drawing)
        quit_action = QAction("&Quit", self, shortcut=QKeySequence.Quit, triggered=self.close)
        file_menu.addActions([new_action, open_action, save_action, quit_action])

        # Edit menu actions: Undo, Redo, Resize Canvas
        self.undo_action = QAction("&Undo", self, shortcut=QKeySequence.Undo, triggered=self.canvas.undo)
        self.redo_action = QAction("&Redo", self, shortcut=QKeySequence.Redo, triggered=self.canvas.redo)
        resize_action = QAction("&Resize Canvas...", self, triggered=self.show_resize_dialog)
        edit_menu.addActions([self.undo_action, self.redo_action, resize_action])

        # View menu actions: Zoom In, Zoom Out, Actual Size
        zoom_in_action = QAction("Zoom &In", self, shortcut=QKeySequence.ZoomIn, triggered=self.canvas.zoom_in)
        zoom_out_action = QAction("Zoom &Out", self, shortcut=QKeySequence.ZoomOut, triggered=self.canvas.zoom_out)
        reset_zoom_action = QAction("&Actual Size", self, shortcut=QKeySequence(Qt.CTRL | Qt.Key_0), triggered=self.canvas.reset_zoom)
        view_menu.addActions([zoom_in_action, zoom_out_action, reset_zoom_action])

        self.canvas.history_changed.connect(self._update_undo_redo_enabled)
        self._update_undo_redo_enabled()
        self.setWindowTitle("Simple PyPaint")
        self.setGeometry(100, 100, DEFAULT_WIDTH + 80, DEFAULT_HEIGHT + 120)
        self.update_color_preview(self.canvas.current_color)
        self.update_spinbox_value(self.canvas.current_pen_size)

    def set_up_canvas(self):
        """
        Prepare the canvas by clearing any existing drawing and refreshing it.
        """
        self.canvas.clear_canvas()
        self.canvas.update()

    # -------------------------
    #       FILE OPERATIONS
    # -------------------------
    def open_image_file(self):
        """
        Open an image file from disk. Prompt the user to discard current work if needed.
        """
        if self.canvas.can_undo():
            reply = QMessageBox.question(self, "Open Image", "Discard current drawing and open image?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.No:
                return
        image_filters = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tiff);;All Files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Open Image", "", image_filters)
        if path:
            new_image = QImage(path)
            if new_image.isNull():
                QMessageBox.critical(self, "Error Opening Image", f"Could not load image file:\n{path}")
                return
            self.canvas.load_image(new_image)

    def show_resize_dialog(self):
        """
        Display the dialog to resize the canvas.
        """
        current_w = self.canvas._image_size.width()
        current_h = self.canvas._image_size.height()
        dialog = ResizeDialog(current_w, current_h, self)
        if dialog.exec():
            new_w, new_h = dialog.getDimensions()
            if new_w > 0 and new_h > 0:
                self.canvas.resize_canvas(new_w, new_h)
            else:
                QMessageBox.warning(self, "Invalid Size", "Width and height must be positive.")

    def keyPressEvent(self, event):
        """
        Handle key presses to adjust pen size with '+' or '-' keys.
        """
        key = event.key()
        if self.canvas.current_tool in ["brush", "eraser"]:
            if key == Qt.Key_Plus or key == Qt.Key_Equal:
                self.canvas.adjust_size(+SIZE_STEP)
                event.accept()
            elif key == Qt.Key_Minus:
                self.canvas.adjust_size(-SIZE_STEP)
                event.accept()
            else:
                super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    @Slot()
    def _update_undo_redo_enabled(self):
        """
        Enable or disable Undo/Redo controls based on the current history state.
        """
        can_undo = self.canvas.can_undo()
        can_redo = self.canvas.can_redo()
        self.undo_button.setEnabled(can_undo)
        self.undo_action.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)
        self.redo_action.setEnabled(can_redo)

    def set_active_tool(self, tool_name):
        """
        Change the active drawing tool.
        """
        self.canvas.set_tool(tool_name)
        self.canvas._is_eraser_bucket = False

    @Slot(int)
    def update_spinbox_value(self, value):
        """
        Update the pen size spinbox without triggering additional signals.
        """
        self.pen_size_spinbox.blockSignals(True)
        clamped_value = max(self.pen_size_spinbox.minimum(), min(value, self.pen_size_spinbox.maximum()))
        self.pen_size_spinbox.setValue(clamped_value)
        self.pen_size_spinbox.blockSignals(False)

    @Slot(QColor)
    def update_color_preview(self, color):
        """
        Update the color preview square.
        """
        pixmap = QPixmap(self.color_preview.size())
        pixmap.fill(color if color.isValid() else Qt.lightGray)
        self.color_preview.setPixmap(pixmap)

    def choose_color(self):
        """
        Launch a color picker to select a new drawing color.
        """
        initial = self.canvas.current_color if self.canvas.current_color.alpha() == 255 else QColor(Qt.black)
        color = QColorDialog.getColor(initial, self, "Choose Drawing Color")
        if color.isValid():
            self.canvas.set_color(color)

    def confirm_new_drawing(self):
        """
        Confirm with the user before clearing the current drawing.
        """
        reply = QMessageBox.question(self, "New", "Discard current drawing?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.canvas.clear_canvas()
            self.canvas.reset_zoom()
        return reply

    def save_drawing(self):
        """
        Save the current drawing to disk, handling format-specific details.
        """
        default_path = "untitled.png"
        filter_str = "PNG (*.png);;JPEG (*.jpg *.jpeg);;BMP (*.bmp)"
        path, selected_filter = QFileDialog.getSaveFileName(self, "Save As", default_path, filter_str)
        if path:
            fmt = None
            parts = path.lower().split('.')
            ext = parts[-1] if len(parts) > 1 else ''
            save_white = False
            if ext == 'png':
                fmt = "PNG"
            elif ext in ('jpg', 'jpeg'):
                fmt = "JPEG"
                save_white = True  # JPEG doesn't support transparency
            elif ext == 'bmp':
                fmt = "BMP"
            else:
                QMessageBox.warning(self, "Invalid File Type", "Unsupported file type. Please choose PNG, JPEG, or BMP.")
                return
            if fmt:
                try:
                    image_to_save = self.canvas.get_image_for_saving(force_white_bg=save_white)
                    if not image_to_save.save(path, fmt):
                        raise Exception("Failed to save image.")
                    print(f"Image saved to: {path}")
                except Exception as e:
                    QMessageBox.critical(self, "Error Saving Image", f"An error occurred while saving the image:\n{e}")
        else:
            print("Save cancelled.")

    def closeEvent(self, event):
        """
        Confirm with the user before closing the application.
        """
        reply = QMessageBox.question(self, 'Quit', 'Are you sure you want to quit?',
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            event.accept()
            print("Exiting application.")
        else:
            event.ignore()
            print("Close cancelled.")


# ============================================
#                APPLICATION ENTRY POINT
# ============================================
if __name__ == '__main__':
    app = QApplication(sys.argv)
    main_window = MainWindow()
    main_window.show()
    main_window.set_up_canvas()
    sys.exit(app.exec())