import os
import json
import datetime
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# =========================
# Конфигурация
# =========================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")  # URL Google Apps Script
PORT = int(os.environ.get("PORT", 8443))

if not TELEGRAM_TOKEN or not WEBHOOK_URL.startswith("https://"):
    raise RuntimeError("Проверьте TELEGRAM_TOKEN и WEBHOOK_URL в переменных окружения.")

# =========================
# Загружаем вопросы
# =========================
with open("questions.json", "r", encoding="utf-8") as f:
    questions = json.load(f)

# =========================
# /start
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    categories = sorted({q["category"] for q in questions})
    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat|{cat}")] for cat in categories]
    await update.message.reply_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard))

# =========================
# Выбор категории
# =========================
async def on_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data.split("|")[1]

    items = [q for q in questions if q["category"] == category]
    if not items:
        await query.edit_message_text("Нет вопросов в этой категории.")
        return

    context.user_data["current"] = {"category": category, "index": 0, "items": items}
    await show_question(query, items[0])

# =========================
# Показ вопроса
# =========================
async def show_question(query, q):
    task = q.get("task", "")
    code = q.get("code", "")
    text = f"Вопрос: {task}" + (f"\nКод: {code}" if code else "")
    keyboard = [[
        InlineKeyboardButton("Да", callback_data=f"ans|yes|{q['id']}"),
        InlineKeyboardButton("Нет", callback_data=f"ans|no|{q['id']}"),
        InlineKeyboardButton("Частично", callback_data=f"ans|part|{q['id']}"),
    ]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# =========================
# Ответ на вопрос
# =========================
async def on_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        _, answer, qid_str = query.data.split("|")
        qid = int(qid_str)
    except Exception:
        await query.edit_message_text("Некорректные данные ответа.")
        return

    q = next((x for x in questions if x["id"] == qid), None)
    if not q:
        await query.edit_message_text("Вопрос не найден.")
        return

    user_id = update.effective_user.id

    if answer in ("no", "part"):
        context.user_data["pending"] = {
            "question": q,
            "answer": "Нет" if answer == "no" else "Частично",
            "user_id": user_id
        }
        await query.edit_message_text(
            f"Вы выбрали '{'Нет' if answer=='no' else 'Частично'}'. Введите комментарий:"
        )
        return

    # Если "Да" — сразу отправляем
    await send_to_webhook(user_id, q["category"], q["task"], "Да", q.get("code", ""), "")
    await go_next_question(query, context)

# =========================
# Обработка текстового комментария
# =========================
async def on_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "pending" not in context.user_data:
        return
    pending = context.user_data.pop("pending")
    q = pending["question"]
    comment = update.message.text.strip()
    await send_to_webhook(pending["user_id"], q["category"], q["task"], pending["answer"], q.get("code", ""), comment)
    await update.message.reply_text("Комментарий сохранён ✅")
    await go_next_question(update.message, context)

# =========================
# Переход к следующему вопросу
# =========================
async def go_next_question(msg_or_query, context):
    current = context.user_data.get("current", {})
    items = current.get("items", [])
    idx = current.get("index", 0) + 1
    context.user_data["current"]["index"] = idx

    if idx < len(items):
        next_q = items[idx]
        if hasattr(msg_or_query, "edit_message_text"):
            await show_question(msg_or_query, next_q)
        else:
            task = next_q.get("task", "")
            code = next_q.get("code", "")
            text = f"Вопрос: {task}" + (f"\nКод: {code}" if code else "")
            keyboard = [[
                InlineKeyboardButton("Да", callback_data=f"ans|yes|{next_q['id']}"),
                InlineKeyboardButton("Нет", callback_data=f"ans|no|{next_q['id']}"),
                InlineKeyboardButton("Частично", callback_data=f"ans|part|{next_q['id']}"),
            ]]
            await msg_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        if hasattr(msg_or_query, "reply_text"):
            await msg_or_query.reply_text("Чек-лист завершён ✅ Спасибо!")
        else:
            await msg_or_query.edit_message_text("Чек-лист завершён ✅ Спасибо!")
        context.user_data.pop("current", None)

# =========================
# Отправка данных в Google Apps Script
# =========================
async def send_to_webhook(user_id, category, task, answer, code, comment):
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
        requests.post(WEBHOOK_URL, json=payload, timeout=7)
    except Exception as e:
        print("Ошибка при отправке:", e)

# =========================
# Основная точка входа
# =========================
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_category, pattern=r"^cat\|"))
    app.add_handler(CallbackQueryHandler(on_answer, pattern=r"^ans\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_comment))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
