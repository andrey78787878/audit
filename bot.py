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

# ----------------------------
# Конфигурация
# ----------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", 8443))

if not TELEGRAM_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_TOKEN")
if not WEBHOOK_URL.startswith("https://"):
    raise RuntimeError("WEBHOOK_URL должен быть публичным HTTPS URL")

# ----------------------------
# Загружаем вопросы
# ----------------------------
with open("questions.json", "r", encoding="utf-8") as f:
    questions = json.load(f)
    if not isinstance(questions, list):
        raise RuntimeError("questions.json должен содержать список объектов")

# ----------------------------
# /start
# ----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    categories = sorted({q["category"] for q in questions})
    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat|{cat}")] for cat in categories]
    await update.message.reply_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard))

# ----------------------------
# Выбор категории
# ----------------------------
async def on_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data.split("|", 1)[1]
    items = [q for q in questions if q["category"] == category]
    if not items:
        await query.edit_message_text("Нет вопросов в этой категории.")
        return
    context.user_data["current"] = {"category": category, "index": 0, "items": items}
    await show_question(query, items[0])

# ----------------------------
# Показ вопроса
# ----------------------------
async def show_question(query, q):
    text = f"Вопрос: {q.get('task','')}"
    if q.get("code"):
        text += f"\nКод: {q['code']}"
    keyboard = [[
        InlineKeyboardButton("Да", callback_data=f"ans|yes|{q['id']}"),
        InlineKeyboardButton("Нет", callback_data=f"ans|no|{q['id']}"),
        InlineKeyboardButton("Частично", callback_data=f"ans|part|{q['id']}"),
    ]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ----------------------------
# Ответ на вопрос
# ----------------------------
async def on_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, answer, id_str = query.data.split("|")
    qid = int(id_str)
    q = next((item for item in questions if item["id"] == qid), None)
    if not q:
        await query.edit_message_text("Вопрос не найден.")
        return
    user_id = update.effective_user.id

    if answer in ("no", "part"):
        context.user_data["pending"] = {
            "question": q,
            "answer": "Нет" if answer=="no" else "Частично",
            "user_id": user_id
        }
        await query.edit_message_text(f"Вы выбрали '{context.user_data['pending']['answer']}'. Введите комментарий:")
        return

    # Если Да — сразу отправляем
    await send_to_webhook(user_id, q["category"], q["task"], "Да", q.get("code",""), "")
    await go_next_question(query, context)

# ----------------------------
# Комментарий после Нет/Частично
# ----------------------------
async def on_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "pending" not in context.user_data:
        return
    pending = context.user_data.pop("pending")
    q = pending["question"]
    comment = update.message.text
    await send_to_webhook(pending["user_id"], q["category"], q["task"], pending["answer"], q.get("code",""), comment)
    await update.message.reply_text("Комментарий сохранён ✅")
    await go_next_question(update.message, context)

# ----------------------------
# Переход к следующему вопросу
# ----------------------------
async def go_next_question(msg, context: ContextTypes.DEFAULT_TYPE):
    current = context.user_data.get("current")
    if not current:
        return
    items = current["items"]
    idx = current["index"] + 1
    context.user_data["current"]["index"] = idx

    if idx < len(items):
        next_q = items[idx]
        if hasattr(msg, "edit_message_text"):
            await show_question(msg, next_q)
        else:
            text = f"Вопрос: {next_q.get('task','')}"
            if next_q.get("code"):
                text += f"\nКод: {next_q['code']}"
            keyboard = [[
                InlineKeyboardButton("Да", callback_data=f"ans|yes|{next_q['id']}"),
                InlineKeyboardButton("Нет", callback_data=f"ans|no|{next_q['id']}"),
                InlineKeyboardButton("Частично", callback_data=f"ans|part|{next_q['id']}"),
            ]]
            await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        # конец
        if hasattr(msg, "reply_text"):
            await msg.reply_text("Чек-лист завершён ✅ Спасибо!")
        else:
            await msg.edit_message_text("Чек-лист завершён ✅ Спасибо!")
        context.user_data.pop("current", None)

# ----------------------------
# Отправка в Google Apps Script
# ----------------------------
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
        print("Ошибка отправки в Webhook:", e)

# ----------------------------
# Точка входа
# ----------------------------
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
