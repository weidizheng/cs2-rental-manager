"""Central visual tokens for the desktop UI."""

APP_QSS = """
QMainWindow, QDialog, QWidget#appRoot { background-color: #181825; color: #cdd6f4; }
QDialog { background-color: #181825; }
QLabel { color: #cdd6f4; font-size: 13px; }
QLabel#titleLabel { font-size: 18px; font-weight: bold; color: #89b4fa; }
QLabel#cardTitle { color: #a6adc8; font-size: 11px; }
QLabel#cardValue { font-size: 21px; font-weight: bold; }
QLabel#sectionTitle { font-size: 15px; font-weight: bold; color: #89b4fa; padding: 4px 0; }
QLabel#validationError { color: #f38ba8; font-weight: 600; padding: 4px 0; }
QFrame#customTitleBar { background: #11111b; border-bottom: 1px solid #313244; }
QLabel#windowTitle { color: #f5e0dc; font-size: 14px; font-weight: 700; }
QLabel#syncStatus { color: #a6e3a1; font-size: 11px; padding-left: 4px; }
QToolButton#windowControl, QToolButton#closeControl { background: transparent; border: none; border-radius: 0; padding: 0; }
QToolButton#windowControl:hover { background: #313244; }
QToolButton#closeControl:hover { background: #f38ba8; }
QFrame#navPanel { background: #11111b; border-right: 1px solid #313244; }
QFrame#orderToolsPanel { background: #1e1e2e; border: 1px solid #313244; border-radius: 7px; }
QLabel#navCaption { color: #6c7086; font-size: 10px; font-weight: 700; padding: 6px 12px 2px; }
QLabel#navFooter { color: #6c7086; font-size: 10px; padding: 8px 12px 12px; }
QPushButton#navButton { background: transparent; color: #a6adc8; border: none; border-radius: 7px; padding: 10px 12px; text-align: left; font-weight: 600; }
QPushButton#navButton:hover { background: #1e1e2e; color: #cdd6f4; }
QPushButton#navButton:checked { background: #313b5c; color: #b4d0ff; }
QPushButton { background-color: #313244; color: #cdd6f4; border-radius: 6px; padding: 7px 14px; font-weight: bold; border: none; }
QPushButton:hover { background-color: #45475a; }
QPushButton:pressed { background-color: #585b70; }
QPushButton#primaryBtn { background-color: #89b4fa; color: #11111b; }
QPushButton#primaryBtn:hover { background-color: #74c7ec; }
QPushButton#dangerBtn { background-color: #f38ba8; color: #11111b; }
QPushButton#dangerBtn:hover { background-color: #eba0ac; }
QPushButton#successBtn { background-color: #a6e3a1; color: #11111b; }
QPushButton:disabled { background-color: #585b70; color: #6c7086; }
QTableWidget, QTableView { background-color: #181825; color: #cdd6f4; gridline-color: #313244; border-radius: 8px; border: 1px solid #313244; selection-background-color: #313b5c; }
QHeaderView::section { background-color: #313244; color: #cdd6f4; padding: 8px; border: none; font-weight: bold; }
QComboBox, QLineEdit, QPlainTextEdit { background-color: #313244; color: #cdd6f4; padding: 6px; border: 1px solid #45475a; border-radius: 4px; }
QComboBox:hover, QLineEdit:hover, QPlainTextEdit:hover { border-color: #89b4fa; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border: 1px solid #89b4fa; }
QComboBox::drop-down { border: none; }
QComboBox::down-arrow { image: none; }
QGroupBox { color: #89b4fa; font-weight: bold; border: 1px solid #45475a; border-radius: 8px; margin-top: 12px; padding-top: 16px; font-size: 13px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
QScrollArea { border: none; background: transparent; }
QCheckBox { color: #cdd6f4; spacing: 6px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 4px; border: 2px solid #585b70; }
QCheckBox::indicator:checked { background-color: #89b4fa; border-color: #89b4fa; }
QFrame#cardFrame { background-color: #252638; border: 1px solid #36384d; border-radius: 10px; }
QFrame#emphasisCard { background-color: #2c2744; border: 1px solid #6d5c9d; border-radius: 10px; }
QFrame#detailFrame { background-color: #181825; border-radius: 8px; border: 1px solid #313244; padding: 12px; }
QToolTip { color: #cdd6f4; background-color: #11111b; border: 1px solid #45475a; padding: 5px; }
"""
