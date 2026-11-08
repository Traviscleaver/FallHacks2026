# mascot_widget.py — Responsive Autonomous Mascot with Dynamic Window Resizing Anchors

from pathlib import Path
import random
from PySide6.QtCore import Qt, QTimer, Slot, QRect, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtGui import QPixmap, QPainter
from PySide6.QtWidgets import QWidget


class HeaderMascot(QWidget):
    """An autonomous mascot that dynamically anchors to the header or chat input bar across window resizes."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(68)
        self.setFixedWidth(74)
        
        sheet_path = Path(__file__).parent / "assets" / "mascot_spritesheet.png"
        self.sprite_sheet = QPixmap(str(sheet_path))
        self.frame_width = 48
        self.frame_height = 48
        self.num_frames = 6
        
        self.walk_sequence = [0, 1, 0, 2]
        self.walk_index = 0
        
        self.expression_frame = 0
        self.direction = 1
        self.pos_x = 220
        self.is_on_chat = False
        self.tracked_widget = None  # Tracks the current anchored widget for resizes
        
        self.is_moving = True
        self.is_jumping = False
        self.is_blinking = False
        self.is_scurrying = False
        
        self.walk_timer = QTimer(self)
        self.walk_timer.timeout.connect(self.update_walk_cycle)
        self.walk_timer.start(180)
        
        self.brain_timer = QTimer(self)
        self.brain_timer.timeout.connect(self.make_random_decision)
        self.brain_timer.start(4500)
        
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.trigger_blink)
        self.blink_timer.start(3200)
        
        self.gesture_timer = QTimer(self)
        self.gesture_timer.timeout.connect(self.trigger_gesture)
        self.gesture_timer.start(4000)
        
        self.show()
        self.raise_()

    def update_position_from_anchor(self):
        """Dynamically repositions the mascot based on the current anchored widget when screen dimensions change."""
        if not self.window():
            return
            
        if self.is_on_chat and self.tracked_widget and self.tracked_widget.isVisible():
            global_pos = self.tracked_widget.mapToGlobal(self.tracked_widget.rect().topLeft())
            window_pos = self.window().mapFromGlobal(global_pos)
            target_x = window_pos.x() + (self.tracked_widget.width() // 2) - 37
            target_y = window_pos.y() - 67
            self.move(target_x, target_y)
        elif not self.is_on_chat:
            # Anchored to header patrol area
            header_w = self.window().width()
            max_limit = header_w - 200
            if self.pos_x > max_limit:
                self.pos_x = max(200, max_limit - 50)
            self.move(self.pos_x, -3)

    def jump_to_chat(self, chat_input_widget):
        """Falls down smoothly and locks anchor to the chat input box."""
        self.is_on_chat = True
        self.tracked_widget = chat_input_widget
        self.is_jumping = False
        
        if chat_input_widget and chat_input_widget.isVisible():
            global_pos = chat_input_widget.mapToGlobal(chat_input_widget.rect().topLeft())
            window_pos = self.window().mapFromGlobal(global_pos)
            
            target_x = window_pos.x() + (chat_input_widget.width() // 2) - 37
            target_y = window_pos.y() - 67
            self.pos_x = target_x
            
            self.hop_anim = QPropertyAnimation(self, b"pos")
            self.hop_anim.setDuration(1000)
            self.hop_anim.setStartValue(self.pos())
            self.hop_anim.setEndValue(QPoint(target_x, target_y))
            self.hop_anim.setEasingCurve(QEasingCurve.InOutQuad)
            self.hop_anim.start()
            
        self.show()
        self.raise_()

    def return_to_header(self):
        """Leaps back up to header and locks anchor to top header bar."""
        self.is_on_chat = False
        self.tracked_widget = None
        self.is_jumping = False
        self.pos_x = 220
        
        self.hop_anim = QPropertyAnimation(self, b"pos")
        self.hop_anim.setDuration(1000)
        self.hop_anim.setStartValue(self.pos())
        self.hop_anim.setEndValue(QPoint(self.pos_x, -3))
        self.hop_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self.hop_anim.start()
        
        self.show()
        self.raise_()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_scurrying = True
            self.is_moving = True
            self.direction *= -1
            self.walk_timer.start(40)
            QTimer.singleShot(3000, self.calm_down)
            event.accept()

    def calm_down(self):
        self.is_scurrying = False
        self.walk_timer.start(180)

    def make_random_decision(self):
        if self.is_jumping or self.is_scurrying:
            return
            
        action = random.choice(["walk", "stop", "turn", "jump", "jump"])
        if action == "walk":
            self.is_moving = True
        elif action == "stop":
            self.is_moving = False
            QTimer.singleShot(1500, lambda: setattr(self, 'is_moving', True))
        elif action == "turn":
            self.direction *= -1
            self.is_moving = True
        elif action == "jump":
            self.perform_small_jump()

    def trigger_blink(self):
        if not self.is_blinking:
            self.is_blinking = True
            self.blink_timer.start(random.randint(2500, 6000))
            QTimer.singleShot(160, self.end_blink)

    def end_blink(self):
        self.is_blinking = False
        self.update()

    def trigger_gesture(self):
        if not self.is_jumping and not self.is_scurrying:
            self.expression_frame = 4
            QTimer.singleShot(300, lambda: setattr(self, 'expression_frame', 5))
            QTimer.singleShot(600, lambda: setattr(self, 'expression_frame', 0))

    def perform_small_jump(self):
        if self.window() and not self.is_jumping and not self.is_scurrying:
            self.is_jumping = True
            curr = self.pos()
            
            jump_up = QPropertyAnimation(self, b"pos")
            jump_up.setDuration(220)
            jump_up.setStartValue(curr)
            jump_up.setEndValue(QPoint(curr.x(), curr.y() - 18))
            jump_up.setEasingCurve(QEasingCurve.OutQuad)
            
            jump_down = QPropertyAnimation(self, b"pos")
            jump_down.setDuration(220)
            jump_down.setStartValue(QPoint(curr.x(), curr.y() - 18))
            jump_down.setEndValue(curr)
            jump_down.setEasingCurve(QEasingCurve.InQuad)
            
            def finish_jump():
                self.is_jumping = False
                
            jump_up.finished.connect(jump_down.start)
            jump_down.finished.connect(finish_jump)
            jump_up.start()
            self._active_hop = (jump_up, jump_down)

    def update_walk_cycle(self):
        if self.is_moving and not self.is_jumping:
            self.walk_index = (self.walk_index + 1) % len(self.walk_sequence)
            
        if self.is_moving and not self.is_jumping and self.window():
            speed = 6 if self.is_scurrying else 1
            
            if self.is_on_chat and self.tracked_widget:
                global_pos = self.tracked_widget.mapToGlobal(self.tracked_widget.rect().topLeft())
                window_pos = self.window().mapFromGlobal(global_pos)
                chat_left = window_pos.x() + 5
                chat_right = window_pos.x() + self.tracked_widget.width() - 75
                
                self.pos_x += self.direction * speed
                if self.pos_x >= chat_right:
                    self.direction = -1
                    self.pos_x = chat_right
                elif self.pos_x <= chat_left:
                    self.direction = 1
                    self.pos_x = chat_left
                self.move(self.pos_x, window_pos.y() - 67)
            else:
                header_w = self.window().width()
                left_limit = 175
                right_limit = min(750, header_w - 250)
                
                self.pos_x += self.direction * speed
                if self.pos_x >= right_limit:
                    self.direction = -1
                    self.pos_x = right_limit
                elif self.pos_x <= left_limit:
                    self.direction = 1
                    self.pos_x = left_limit
                self.move(self.pos_x, -3)
                
        self.update()
        self.raise_()

    def paintEvent(self, event):
        if self.sprite_sheet.isNull():
            return
            
        if self.is_blinking:
            frame_to_draw = 3
        elif self.expression_frame in [4, 5]:
            frame_to_draw = self.expression_frame
        else:
            frame_to_draw = self.walk_sequence[self.walk_index] if self.is_moving else 0
            
        painter = QPainter(self)
        src_x = frame_to_draw * self.frame_width
        src_rect = QRect(src_x, 0, self.frame_width, self.frame_height)
        dest_rect = QRect(5, 2, 64, 64)
        painter.drawPixmap(dest_rect, self.sprite_sheet, src_rect)
