"""Central visual tokens for the calm, dense desktop workspace."""

# The palette is deliberately small: blue commits work, green confirms a completed
# action, red removes data, and amber asks for attention.  Everything else stays
# neutral so live rental data remains the visual focus.
APP_QSS = """
QMainWindow, QDialog, QWidget#appRoot {
    background: #10131a;
    color: #e8edf5;
    font-family: "Segoe UI Variable", "Microsoft YaHei UI", "Microsoft YaHei";
    font-size: 13px;
}
QDialog, QWidget#pageStack { background: #171b24; }
QLabel { color: #d8dee9; font-size: 13px; }
QLabel#titleLabel, QLabel#pageTitle {
    color: #f7f9fc;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.35px;
}
QLabel#pageSubtitle { color: #98a4b7; font-size: 12px; padding-top: 2px; }
QLabel#cardTitle { color: #9faabd; font-size: 11px; font-weight: 600; }
QLabel#cardValue { color: #f3f6fb; font-size: 21px; font-weight: 700; letter-spacing: -0.2px; }
QLabel#sectionTitle { color: #d7e5ff; font-size: 15px; font-weight: 700; padding: 4px 0; }
QLabel#validationError { color: #fb7185; font-weight: 600; padding: 4px 0; }
QLabel#lastUpdatedLabel {
    color: #b8c4d8;
    font-size: 11px;
    background: #202735;
    border: 1px solid #384357;
    border-radius: 8px;
    padding: 5px 9px;
}

QFrame#customTitleBar {
    background: #0e1117;
    border-bottom: 1px solid #293142;
}
QLabel#windowTitle { color: #f7f9fc; font-size: 14px; font-weight: 700; }
QLabel#syncStatus { color: #65d69b; font-size: 11px; font-weight: 600; padding-left: 4px; }
QToolButton#windowControl, QToolButton#closeControl {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
}
QToolButton#windowControl:hover { background: #252d3c; }
QToolButton#windowControl:pressed { background: #354057; }
QToolButton#closeControl:hover { background: #c94359; }
QToolButton#closeControl:pressed { background: #ad3549; }

QFrame#navPanel {
    background: #12161e;
    border-right: 1px solid #293142;
}
QLabel#navCaption { color: #748196; font-size: 10px; font-weight: 700; padding: 8px 12px 3px; }
QLabel#navFooter { color: #748196; font-size: 10px; padding: 8px 12px 12px; }
QPushButton#navButton {
    background: transparent;
    color: #aeb9c9;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 10px 12px;
    text-align: left;
    font-weight: 600;
}
QPushButton#navButton:hover { background: #202735; color: #f3f6fb; }
QPushButton#navButton:checked {
    background: #1d3455;
    border-color: #315b92;
    color: #e5efff;
}
QToolButton#settingsNavButton {
    color: #aeb9c9;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 8px;
    font-weight: 600;
}
QToolButton#settingsNavButton:hover { color: #f3f6fb; background: #202735; }
QToolButton#settingsNavButton:pressed { background: #2b3548; }

QPushButton {
    background: #293243;
    color: #e9eef7;
    border: 1px solid #3b485f;
    border-radius: 8px;
    min-height: 18px;
    padding: 7px 13px;
    font-weight: 600;
}
QPushButton:hover { background: #354157; border-color: #53647f; }
QPushButton:pressed { background: #222a39; border-color: #6f85a8; }
QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 2px solid #70a5f7;
}
QPushButton#primaryBtn {
    background: #2f78d4;
    color: #f8fbff;
    border-color: #3d89e9;
}
QPushButton#primaryBtn:hover { background: #4089e4; border-color: #76b0ff; }
QPushButton#primaryBtn:pressed { background: #2465bb; border-color: #9bc4ff; }
QPushButton#successBtn {
    background: #167a4b;
    color: #f7fffa;
    border-color: #25985f;
}
QPushButton#successBtn:hover { background: #1e925a; border-color: #65d69b; }
QPushButton#successBtn:pressed { background: #10643d; border-color: #85e1ae; }
QPushButton#dangerBtn {
    background: #c94359;
    color: #fff8f8;
    border-color: #e26377;
}
QPushButton#dangerBtn:hover { background: #dc566b; border-color: #ff9aaa; }
QPushButton#dangerBtn:pressed { background: #ad3549; border-color: #ffb3be; }
QPushButton:disabled {
    background: #222936;
    color: #768197;
    border-color: #2e394b;
}
QToolButton {
    color: #d5ddea;
    background: #293243;
    border: 1px solid #3b485f;
    border-radius: 8px;
    padding: 6px 9px;
    font-weight: 600;
}
QToolButton:hover { background: #354157; border-color: #53647f; }
QToolButton:pressed { background: #222a39; }

QTableWidget, QTableView {
    background: #151a23;
    alternate-background-color: #191f2a;
    color: #e1e7f0;
    gridline-color: transparent;
    border-radius: 12px;
    border: 1px solid #303a4c;
    selection-background-color: #244b7e;
    selection-color: #ffffff;
}
QTableWidget::item, QTableView::item { border-bottom: 1px solid #283143; padding: 4px 6px; }
QTableWidget::item:hover, QTableView::item:hover { background: #222b3a; }
QTableWidget::item:selected, QTableView::item:selected { background: #294f83; }
QHeaderView::section {
    background: #202735;
    color: #bdc8d9;
    padding: 9px 8px;
    border: none;
    border-right: 1px solid #303a4c;
    font-weight: 700;
}
QLineEdit, QComboBox, QPlainTextEdit {
    background: #1c2431;
    color: #f1f5fb;
    padding: 7px 9px;
    border: 1px solid #3c4860;
    border-radius: 8px;
    selection-background-color: #3566a6;
}
QComboBox:hover, QLineEdit:hover, QPlainTextEdit:hover { border-color: #6680a8; }
QLineEdit::placeholder, QPlainTextEdit::placeholder { color: #8390a5; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: none; }
QComboBox QAbstractItemView {
    background: #1c2431;
    color: #eef3fb;
    border: 1px solid #4a5a75;
    selection-background-color: #294f83;
}
QGroupBox {
    color: #d9e6ff;
    background: #1b222f;
    font-weight: 700;
    border: 1px solid #354057;
    border-radius: 12px;
    margin-top: 14px;
    padding: 18px 14px 14px;
    font-size: 13px;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 7px; }
QWidget#settingsContent { background: #171b24; }
QWidget#settingsContent QLabel { color: #e4e9f2; font-size: 13px; }
QWidget#settingsContent QGroupBox { background: #1b222f; color: #d9e6ff; border-color: #39465d; }
QWidget#settingsContent QLineEdit, QWidget#settingsContent QComboBox {
    background: #121720;
    color: #f5f8fd;
    border-color: #485670;
    min-height: 20px;
}
QWidget#settingsContent QLineEdit::placeholder { color: #8d99ae; }
QWidget#settingsContent QCheckBox { color: #e3e8f1; font-weight: 600; }
QScrollArea { border: none; background: transparent; }
QCheckBox { color: #d8dee9; spacing: 7px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 2px solid #6d7a90; background: #151a23; }
QCheckBox::indicator:checked { background: #2f78d4; border-color: #70a5f7; }
QFrame#cardFrame {
    background: #1d2532;
    border: 1px solid #344056;
    border-radius: 14px;
}
QFrame#emphasisCard {
    background: #1b2940;
    border: 1px solid #375c8f;
    border-radius: 14px;
}
QFrame#cardFrame:hover { border-color: #4d607d; background: #222c3b; }
QFrame#emphasisCard:hover { border-color: #6091d3; background: #1e304c; }
QFrame#orderToolsPanel {
    background: #1b2330;
    border: 1px solid #39465d;
    border-radius: 11px;
}
QFrame#detailFrame { background: #171e29; border-radius: 10px; border: 1px solid #344056; padding: 12px; }
QMenu {
    background: #1c2431;
    color: #eaf0f8;
    border: 1px solid #465672;
    border-radius: 8px;
    padding: 5px;
}
QMenu::item { padding: 7px 22px 7px 10px; border-radius: 5px; }
QMenu::item:selected { background: #294f83; }
QStatusBar { background: #0e1117; color: #c8d1df; border-top: 1px solid #293142; }
QToolTip { color: #eff4fb; background: #222b3a; border: 1px solid #53647f; border-radius: 6px; padding: 6px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: #4b5870; min-height: 30px; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #6a7b98; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px 4px; }
QScrollBar::handle:horizontal { background: #4b5870; min-width: 30px; border-radius: 5px; }
QScrollBar::handle:horizontal:hover { background: #6a7b98; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""
