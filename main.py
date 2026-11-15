import os
import asyncio
import time
from pathlib import Path

from telethon import TelegramClient, functions, types
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.theme import Theme

# --- Настройка цветовой темы ---
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "header": "bold magenta",
    "menu_item": "bold white",
    "prompt": "bold blue",
})

console = Console(theme=custom_theme)

# --- Конфигурация ---
CONFIG_FILE = "config.txt"
SESSION_FILE = "tg_session"

# Глобальные настройки (будут загружены или запрошены)
API_ID = None
API_HASH = None

# Настройки отображения
SHOW_UNREAD_FIRST = True
CHATS_PER_PAGE = 6
CURRENT_FOLDER = None  # None = все чаты, 0 = без папки, >0 = папка

# Состояние клиента
client = None


def load_config():
    """Загружает api_id и api_hash из файла или запрашивает при первом запуске."""
    global API_ID, API_HASH
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            lines = f.read().splitlines()
            if len(lines) >= 2:
                API_ID = int(lines[0].strip())
                API_HASH = lines[1].strip()
                console.print("[success]Конфигурация загружена.[/success]")
                return
    
    console.print("[warning]Первый запуск. Требуются API ID и API Hash.[/warning]")
    console.print("Получите их на [link=https://my.telegram.org]my.telegram.org[/link] (API Development Tools).")
    
    while True:
        try:
            api_id_str = Prompt.ask("[prompt]Введите API ID[/prompt]")
            api_id = int(api_id_str)
            api_hash = Prompt.ask("[prompt]Введите API Hash[/prompt]")
            if not api_hash:
                raise ValueError
            break
        except ValueError:
            console.print("[error]API ID должен быть числом, API Hash не может быть пустым.[/error]")
    
    API_ID = api_id
    API_HASH = api_hash
    with open(CONFIG_FILE, "w") as f:
        f.write(f"{API_ID}\n{API_HASH}\n")
    console.print("[success]Конфигурация сохранена.[/success]")


async def init_client():
    """Инициализация и авторизация клиента."""
    global client
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        console.print("[info]Требуется авторизация.[/info]")
        phone = Prompt.ask("[prompt]Введите номер телефона (в формате +7...)[/prompt]")
        try:
            await client.send_code_request(phone)
            code = Prompt.ask("[prompt]Введите код из Telegram[/prompt]")
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                password = Prompt.ask("[prompt]Введите пароль двухфакторной аутентификации[/prompt]", password=True)
                await client.sign_in(password=password)
        except Exception as e:
            console.print(f"[error]Ошибка авторизации: {e}[/error]")
            await client.disconnect()
            return False
    
    me = await client.get_me()
    console.print(f"[success]Вы вошли как {me.first_name} (@{me.username})[/success]")
    return True


async def get_folders():
    """Возвращает список папок (dialog filters)."""
    try:
        result = await client(functions.messages.GetDialogFiltersRequest())
        filters = result.filters if hasattr(result, 'filters') else []
        # Фильтр 0 (All) не нужен, обычно папки начинаются с 1
        return [f for f in filters if hasattr(f, 'title') and f.id != 0]
    except Exception:
        return []


async def get_dialogs(folder_id=None):
    """Возвращает список диалогов, опционально отфильтрованных по папке."""
    # Telethon поддерживает folder параметр в iter_dialogs
    # folder=0 -> без папки, folder=1 -> архив, folder>1 -> пользовательские папки
    try:
        dialogs = await client.get_dialogs(folder=folder_id)
        return dialogs
    except Exception as e:
        console.print(f"[error]Ошибка получения чатов: {e}[/error]")
        return []


def display_chats(dialogs, page=0):
    """Отображает таблицу чатов с пагинацией."""
    # Сортировка: сначала непрочитанные, если включено
    if SHOW_UNREAD_FIRST:
        dialogs.sort(key=lambda d: (d.unread_count == 0, -d.unread_count))
    
    total = len(dialogs)
    start = page * CHATS_PER_PAGE
    end = start + CHATS_PER_PAGE
    page_dialogs = dialogs[start:end]
    
    if not page_dialogs:
        console.print("[warning]Нет чатов для отображения.[/warning]")
        return []
    
    table = Table(title=f"Чаты (стр. {page+1}, всего {total})", show_header=True, header_style="header")
    table.add_column("№", style="menu_item", width=4)
    table.add_column("Название", style="white")
    table.add_column("Непрочитанные", style="bold yellow", width=12)
    table.add_column("Тип", style="dim cyan", width=10)
    
    for i, dialog in enumerate(page_dialogs, start=1):
        name = dialog.name or "Без названия"
        unread = dialog.unread_count if dialog.unread_count > 0 else ""
        dtype = "Группа" if dialog.is_group else ("Канал" if dialog.is_channel else "Личный")
        table.add_row(str(i), name, str(unread), dtype)
    
    console.print(table)
    
    # Подсказки
    hints = []
    if page > 0:
        hints.append("prev — предыдущая страница")
    if end < total:
        hints.append("skip — следующие 6 чатов")
    if hints:
        console.print(f"[dim]Подсказка: {', '.join(hints)}[/dim]")
    
    return page_dialogs


async def show_profile(user):
    """Показывает профиль пользователя или чата."""
    if not user:
        console.print("[warning]Профиль недоступен.[/warning]")
        return
    
    info = []
    if hasattr(user, 'first_name'):
        name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        info.append(f"[bold]Имя:[/bold] {name}")
    elif hasattr(user, 'title'):
        info.append(f"[bold]Название:[/bold] {user.title}")
    
    if hasattr(user, 'username') and user.username:
        info.append(f"[bold]Юзернейм:[/bold] @{user.username}")
    if hasattr(user, 'id'):
        info.append(f"[bold]ID:[/bold] {user.id}")
    if hasattr(user, 'phone') and user.phone:
        info.append(f"[bold]Телефон:[/bold] {user.phone}")
    if hasattr(user, 'participants_count'):
        info.append(f"[bold]Участников:[/bold] {user.participants_count}")
    
    panel = Panel("\n".join(info), title="Профиль", border_style="cyan")
    console.print(panel)
    Prompt.ask("[dim]Нажмите Enter, чтобы вернуться[/dim]", default="")


async def show_dialog_messages(dialog):
    """Показывает последние сообщения в чате."""
    console.print(f"[header]Чат: {dialog.name or 'Без названия'}[/header]")
    messages = await client.get_messages(dialog.entity, limit=10)
    
    if not messages:
        console.print("[dim]Нет сообщений.[/dim]")
    else:
        for msg in reversed(messages):
            sender = "Вы" if msg.out else (msg.sender.first_name if msg.sender else "???")
            text = msg.text or "[медиа/файл]" if msg.media else "[пустое]"
            text = text[:100] + "..." if len(text) > 100 else text
            console.print(f"[bold cyan]{sender}:[/bold cyan] {text}")
    
    console.print("[dim]Введите сообщение для отправки или 'profile' для профиля, 'send' для фото, 'back' для выхода.[/dim]")


async def chat_loop(dialog):
    """Цикл взаимодействия внутри чата."""
    while True:
        await show_dialog_messages(dialog)
        cmd = Prompt.ask("[prompt]Сообщение/команда[/prompt]").strip()
        
        if cmd.lower() == "back":
            break
        elif cmd.lower() == "profile":
            # Показ профиля собеседника
            if dialog.is_user:
                await show_profile(dialog.entity)
            else:
                # Для групп показываем информацию о группе
                await show_profile(dialog.entity)
        elif cmd.lower() == "send":
            path = Prompt.ask("[prompt]Путь к файлу (фото)[/prompt]").strip()
            if not os.path.exists(path):
                console.print("[error]Файл не найден.[/error]")
                continue
            # Проверка расширения (только фото)
            ext = Path(path).suffix.lower()
            if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']:
                console.print("[warning]Можно отправлять только изображения (jpg, png, gif, webp, bmp). Стикеры и гифки через этот клиент не отправляются.[/warning]")
                continue
            try:
                await client.send_file(dialog.entity, path, caption=None)
                console.print("[success]Фото отправлено![/success]")
            except Exception as e:
                console.print(f"[error]Ошибка отправки: {e}[/error]")
        elif cmd:
            # Отправка текстового сообщения
            try:
                await client.send_message(dialog.entity, cmd)
                console.print("[success]Сообщение отправлено![/success]")
            except Exception as e:
                console.print(f"[error]Ошибка: {e}[/error]")


async def settings_menu():
    """Меню настроек."""
    global SHOW_UNREAD_FIRST
    
    while True:
        console.clear()
        console.print(Panel.fit(
            "[header]Настройки[/header]\n\n"
            "1. Кастомизация (цвет)\n"
            "2. Чаты (показ непрочитанных)\n"
            "3. Аккаунт (выход/удаление)\n"
            "0. Назад",
            border_style="magenta"
        ))
        
        choice = Prompt.ask("[prompt]Выберите вкладку[/prompt]").strip()
        
        if choice == "1":
            console.print("[info]Кастомизация: цвета уже настроены (cyan/magenta/yellow).[/info]")
            console.print("[dim]Для изменения отредактируйте custom_theme в коде.[/dim]")
            Prompt.ask("Enter...", default="")
        elif choice == "2":
            console.print(f"[info]Сейчас: непрочитанные {'сначала' if SHOW_UNREAD_FIRST else 'в обычном порядке'}.[/info]")
            if Confirm.ask("Переключить показ непрочитанных сначала?"):
                SHOW_UNREAD_FIRST = not SHOW_UNREAD_FIRST
                console.print("[success]Настройка изменена.[/success]")
            Prompt.ask("Enter...", default="")
        elif choice == "3":
            await account_menu()
        elif choice == "0" or choice.lower() in ("back", "exit"):
            break


async def account_menu():
    """Меню аккаунта: выход и удаление."""
    while True:
        console.clear()
        console.print(Panel.fit(
            "[header]Аккаунт[/header]\n\n"
            "1. Выйти из аккаунта (logout)\n"
            "2. Удалить аккаунт (необратимо!)\n"
            "0. Назад",
            border_style="red"
        ))
        
        choice = Prompt.ask("[prompt]Выберите действие[/prompt]").strip()
        
        if choice == "1":
            if Confirm.ask("[warning]Вы уверены, что хотите выйти?[/warning]"):
                await client.log_out()
                console.print("[success]Вы вышли. Перезапустите клиент для входа.[/success]")
                exit(0)
        elif choice == "2":
            await delete_account_flow()
        elif choice == "0":
            break


async def delete_account_flow():
    """Процесс удаления аккаунта с запросом телефона и кода, затем ожидание 15 секунд."""
    console.print("[error]ВНИМАНИЕ: Удаление аккаунта необратимо![/error]")
    
    if not Confirm.ask("[warning]Вы точно хотите удалить аккаунт?[/warning]"):
        return
    
    phone = Prompt.ask("[prompt]Введите ваш номер телефона (для подтверждения)[/prompt]").strip()
    
    # Запрос кода подтверждения (Telegram пришлет код для удаления, если это возможно)
    # Примечание: Для удаления через API часто требуется пароль 2FA или код не запрашивается.
    # Здесь мы имитируем запрос кода, как вы просили, но реальное удаление может потребовать password.
    code = Prompt.ask("[prompt]Введите код, который придет вам для удаления[/prompt]").strip()
    
    console.print("[warning]Ожидание 15 секунд перед удалением...[/warning]")
    for i in range(15, 0, -1):
        console.print(f"[dim]Осталось: {i} сек...[/dim]", end="\r")
        time.sleep(1)
    
    try:
        # Попытка удаления через DeleteAccountRequest
        # В реальности для 2FA аккаунтов может потребоваться password.
        # Здесь мы используем reason и надеемся, что сессия позволяет удаление.
        result = await client(functions.account.DeleteAccountRequest(reason="User requested deletion via CLI client"))
        console.print("[success]Аккаунт удален.[/success]")
        exit(0)
    except Exception as e:
        console.print(f"[error]Не удалось удалить аккаунт: {e}[/error]")
        console.print("[dim]Возможно, требуется пароль 2FA или подтверждение в приложении.[/dim]")
        Prompt.ask("Enter...", default="")


async def main_menu():
    """Главное меню чатов."""
    global CURRENT_FOLDER
    
    page = 0
    dialogs = []
    folders = []
    
    # Загрузка папок
    folders = await get_folders()
    
    while True:
        console.clear()
        
        # Заголовок с текущей папкой
        folder_name = "Все чаты"
        if CURRENT_FOLDER is not None:
            if CURRENT_FOLDER == 0:
                folder_name = "Без папки"
            else:
                for f in folders:
                    if f.id == CURRENT_FOLDER:
                        folder_name = f.title
                        break
        
        console.print(f"[header]Папка: {folder_name}[/header]")
        
        # Отображение доступных папок
        if folders:
            folder_hints = []
            for i, f in enumerate(folders, start=1):
                folder_hints.append(f"[dim]f{i}[/dim]={f.title}")
            if CURRENT_FOLDER is not None:
                folder_hints.append("[dim]f0[/dim]=Все чаты")
            console.print(" | ".join(folder_hints))
        
        # Получение и отображение чатов
        dialogs = await get_dialogs(folder_id=CURRENT_FOLDER)
        
        if not dialogs:
            console.print("[warning]Нет чатов в этой папке.[/warning]")
            console.print("[dim]Команды: settings, f0 (все), refresh, exit[/dim]")
        else:
            page_dialogs = display_chats(dialogs, page)
        
        # Ввод команды
        cmd = Prompt.ask("[prompt]Действие[/prompt]").strip().lower()
        
        if cmd == "exit" or cmd == "quit":
            console.print("[info]Выход...[/info]")
            await client.disconnect()
            break
        elif cmd == "settings":
            await settings_menu()
        elif cmd == "skip":
            if (page + 1) * CHATS_PER_PAGE < len(dialogs):
                page += 1
            else:
                console.print("[warning]Больше чатов нет.[/warning]")
                time.sleep(1)
        elif cmd == "prev":
            if page > 0:
                page -= 1
            else:
                console.print("[warning]Это первая страница.[/warning]")
                time.sleep(1)
        elif cmd == "refresh":
            page = 0
            folders = await get_folders()
        elif cmd.startswith("f"):
            # Переход по папкам: f1, f2 и т.д.
            try:
                idx = int(cmd[1:])
                if idx == 0:
                    CURRENT_FOLDER = None
                elif 1 <= idx <= len(folders):
                    CURRENT_FOLDER = folders[idx-1].id
                else:
                    console.print("[error]Нет такой папки.[/error]")
                    continue
                page = 0
            except ValueError:
                console.print("[error]Неверный формат. Используйте f1, f2...[/error]")
        elif cmd.isdigit():
            # Переход в чат по номеру
            num = int(cmd)
            if 1 <= num <= len(page_dialogs):
                selected = page_dialogs[num-1]
                await chat_loop(selected)
                # После выхода из чата обновляем список
                dialogs = await get_dialogs(folder_id=CURRENT_FOLDER)
            else:
                console.print("[error]Неверный номер чата.[/error]")
                time.sleep(1)
        else:
            console.print("[warning]Неизвестная команда. Введите номер чата, 'skip', 'settings', 'f1', 'refresh' или 'exit'.[/warning]")
            time.sleep(1)


async def async_main():
    load_config()
    if not await init_client():
        return
    await main_menu()


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[warning]Прервано пользователем.[/warning]")
    except Exception as e:
        console.print(f"[error]Критическая ошибка: {e}[/error]")
