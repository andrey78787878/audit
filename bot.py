import os
import json
import datetime
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# Конфигурация (через Render → Environment)
# =========================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()  # токен бота от @BotFather
WEBHOOK_BASE = os.environ.get("WEBHOOK_URL", "").rstrip("/")   # публичный URL сервиса Render (например, https://audit-91nm.onrender.com)
PORT = int(os.environ.get("PORT", 8443))

if not TELEGRAM_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_TOKEN в переменных окружения Render.")
if not WEBHOOK_BASE.startswith("https://"):
    raise RuntimeError("WEBHOOK_URL должен быть публичным HTTPS URL вашего сервиса на Render.")

# =========================
# Данные вопросов
# формат items: [{"id":1,"category":"...","task":"...","code":"...?"}, ...]
# =========================
with open("questions.json", "r", encoding="utf-8") as f:
    questions = json.load(f)
    if not isinstance(questions, list):
        raise RuntimeError("questions.json должен содержать список объектов.")

# =========================
# /start
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    categories = sorted({str(q.get("category", "")).strip() for q in questions if q.get("category")})
    if not categories:
        await update.message.reply_text("Список категорий пуст. Проверьте questions.json.")
        return
    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat|{cat}")] for cat in categories]
    await update.message.reply_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard))

# =========================
# Выбор категории
# =========================
async def on_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data.split("|", 1)[1]

    items = [q for q in questions if q.get("category") == category]
    if not items:
        await query.edit_message_text("Нет вопросов в этой категории.")
        return

    # сбрасываем состояние текущего прогресса
    context.user_data["current"] = {"category": category, "index": 0, "items": items}
    await show_question(query, items[0])

# =========================
# Показ вопроса
# =========================
async def show_question(query, q):
    task = q.get("task", "").strip()
    code = q.get("code", "")
    text = f"Вопрос: {task}" + (f"\nКод: {code}" if code else "")
    options = [[
        InlineKeyboardButton("Да", callback_data=f"ans|yes|{q['id']}"),
        InlineKeyboardButton("Нет", callback_data=f"ans|no|{q['id']}"),
        InlineKeyboardButton("Частично", callback_data=f"ans|part|{q['id']}"),
    ]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(options))

# =========================
# Ответ на вопрос (кнопки)
# =========================
async def on_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        _, answer, id_str = query.data.split("|", 2)
        qid = int(id_str)
    except Exception:
        await query.edit_message_text("Некорректные данные ответа.")
        return

    user_id = update.effective_user.id
    q = next((item for item in questions if int(item.get("id", -1)) == qid), None)
    if not q:
        await query.edit_message_text("Ошибка: вопрос не найден.")
        return

    # Если "Нет" или "Частично" — ждём текстовый комментарий
    if answer in ("no", "part"):
        context.user_data["pending"] = {
            "question": q,
            "answer": "Нет" if answer == "no" else "Частично",
            "user_id": user_id
        }
        await query.edit_message_text(
            f"Вы выбрали «{'Нет' if answer == 'no' else 'Частично'}». Введите, пожалуйста, комментарий одним сообщением:"
        )
        return

    # Если "Да" — отправляем сразу без комментария
    await send_to_webhook(
        user_id=user_id,
        category=q.get("category", ""),
        task=q.get("task", ""),
        answer="Да",
        code=q.get("code", ""),
        comment=""
    )
    await go_next_question(query, context)

# =========================
# Приём комментария (текст) после "Нет/Частично"
# =========================
async def on_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "pending" not in context.user_data:
        # это обычный текст не по теме — игнорируем в рамках опроса
        return

    pending = context.user_data.pop("pending")
    q = pending["question"]
    user_id = pending["user_id"]
    comment_text = (update.message.text or "").strip()

    await send_to_webhook(
        user_id=user_id,
        category=q.get("category", ""),
        task=q.get("task", ""),
        answer=pending["answer"],  # "Нет" или "Частично"
        code=q.get("code", ""),
        comment=comment_text
    )

    await update.message.reply_text("Комментарий сохранён ✅")
    await go_next_question(update.message, context)

# =========================
# Переход к следующему вопросу
# =========================
async def go_next_question(message_or_query, context: ContextTypes.DEFAULT_TYPE):
    current = context.user_data.get("current") or {}
    items = current.get("items", [])
    idx = int(current.get("index", 0)) + 1
    if "current" not in context.user_data:
        context.user_data["current"] = {}
    context.user_data["current"]["index"] = idx

    if idx < len(items):
        next_q = items[idx]
        if hasattr(message_or_query, "edit_message_text"):
            # пришли из callbackQuery
            await show_question(message_or_query, next_q)
        else:
            # пришли из обычного сообщения (после комментария)
            task = next_q.get("task", "")
            code = next_q.get("code", "")
            text = f"Вопрос: {task}" + (f"\nКод: {code}" if code else "")
            keyboard = [[
                InlineKeyboardButton("Да", callback_data=f"ans|yes|{next_q['id']}"),
                InlineKeyboardButton("Нет", callback_data=f"ans|no|{next_q['id']}"),
                InlineKeyboardButton("Частично", callback_data=f"ans|part|{next_q['id']}"),
            ]]
            await message_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        # завершение чек-листа
        if hasattr(message_or_query, "reply_text"):
            await message_or_query.reply_text("Чек-лист завершён ✅ Спасибо!")
        else:
            await message_or_query.edit_message_text("Чек-лист завершён ✅ Спасибо!")
        context.user_data.pop("current", None)

# =========================
# Отправка результатов в Google Apps Script (ваша таблица)
# =========================
async def send_to_webhook(user_id: int, category: str, task: str, answer: str, code: str, comment: str):
    payload = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "user_id": str(user_id),
        "category": category,
        "task": task,
        "answer": answer,
        "code": code or "",
        "comment": comment or ""
    }
    try:
        # POST JSON на ваш Apps Script Web App URL
        requests.post(WEBHOOK_BASE, json=payload, timeout=7)
    except Exception as e:
        # логируем, но не падаем
        print("Ошибка при отправке в Webhook:", e)

# =========================
# Точка входа
# =========================
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Хэндлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_category, pattern=r"^cat\|"))
    app.add_handler(CallbackQueryHandler(on_answer, pattern=r"^ans\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_comment))

    # Запуск вебхука (PTB сам поднимет aiohttp-сервер)
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,  # секретный путь
        webhook_url=f"{WEBHOOK_BASE}/{TELEGRAM_TOKEN}",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
