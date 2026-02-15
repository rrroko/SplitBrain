# 例: ブラウザ検索系
def open_google_search(query: str):
    url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    webbrowser.open(url)

def open_youtube_search(query: str):
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    webbrowser.open(url)

def play_youtube_first_result(query: str):
    # v1: とりあえず検索結果ページまで開くところまで
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    webbrowser.open(url)
    # v2 で「TAB/ENTER連打で1件目再生」みたいなのを pyautogui で足せる

# タブ操作
def close_tab():
    pyautogui.hotkey("ctrl", "w")

def new_tab():
    pyautogui.hotkey("ctrl", "t")

def next_tab():
    pyautogui.hotkey("ctrl", "tab")

def prev_tab():
    pyautogui.hotkey("ctrl", "shift", "tab")

# アプリ起動（パスは環境に合わせて）
APP_PATHS = {
    "vscode": r"C:\Users\DBI\AppData\Local\Programs\Microsoft VS Code\Code.exe",
    "discord": r"C:\Users\DBI\AppData\Local\Discord\Update.exe --processStart Discord.exe",
    "explorer": r"explorer.exe",
    "powerpoint": r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
}

def open_app(key: str):
    path = APP_PATHS.get(key)
    if not path:
        return False
    subprocess.Popen(path)
    return True

# 音量系（暫定: キーボードショートカットに依存）
def volume_up():
    pyautogui.press("volumeup")

def volume_down():
    pyautogui.press("volumedown")

def volume_mute():
    pyautogui.press("volumemute")

# スクショ
def screenshot_to_desktop():
    pyautogui.hotkey("win", "printscreen")
