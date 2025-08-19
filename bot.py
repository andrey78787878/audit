import os, json, datetime, requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

# === Конфигурация ===
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")  # храните токен как секрет в Render
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")        # URL вашего Apps Script webhook

# Загружаем вопросы
with open("questions.json", "r", encoding="utf-8") as f:
    questions = json.load(f)

# === Функции ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    categories = sorted(set(q["category"] for q in questions))
    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat|{cat}")] for cat in categories]
    await update.message.reply_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard))

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

async def show_question(query, q):
    text = f"Вопрос: {q['task']}\nКод: {q['code']}"
    options = [
        [
            InlineKeyboardButton("Да", callback_data=f"ans|yes|{q['id']}"),
            InlineKeyboardButton("Нет", callback_data=f"ans|no|{q['id']}"),
            InlineKeyboardButton("Частично", callback_data=f"ans|part|{q['id']}")
        ]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(options))

async def on_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("|")
    answer = data[1]
    qid = int(data[2])
    user_id = update.effective_user.id

    # ищем вопрос
    q = next((item for item in questions if item["id"] == qid), None)
    if not q:
        await query.edit_message_text("Ошибка: вопрос не найден.")
        return

    if answer in ["no", "part"]:
        # ждём комментарий
        context.user_data["pending"] = {
            "question": q,
            "answer": "Нет" if answer == "no" else "Частично",
            "user_id": user_id
        }
        await query.edit_message_text(
            f"Вы выбрали '{'Нет' if answer == 'no' else 'Частично'}'. "
            "Пожалуйста, введите комментарий:"
        )
        return

    # --- вариант 'Да' сохраняем сразу ---
    payload = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "user_id": str(user_id),
        "category": q["category"],
        "task": q["task"],
        "answer": "Да",
        "code": q["code"],
        "comment": ""
    }
    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print("Ошибка при отправке:", e)

    await go_next_question(query, context)

async def on_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстового комментария после 'Нет' или 'Частично'"""
    if "pending" not in context.user_data:
        return

    pending = context.user_data.pop("pending")
    q = pending["question"]
    user_id = pending["user_id"]

    payload = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "user_id": str(user_id),
        "category": q["category"],
        "task": q["task"],
        "answer": pending["answer"],   # "Нет" или "Частично"
        "code": q["code"],
        "comment": update.message.text
    }
    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print("Ошибка при отправке:", e)

    await update.message.reply_text("Комментарий сохранён ✅")
    await go_next_question(update.message, context)

async def go_next_question(message_or_query, context):
    items = context.user_data.get("current", {}).get("items", [])
    idx = context.user_data.get("current", {}).get("index", 0) + 1
    context.user_data["current"]["index"] = idx

    if idx < len(items):
        next_q = items[idx]
        keyboard = [
            [
                InlineKeyboardButton("Да", callback_data=f"ans|yes|{next_q['id']}"),
                InlineKeyboardButton("Нет", callback_data=f"ans|no|{next_q['id']}"),
                InlineKeyboardButton("Частично", callback_data=f"ans|part|{next_q['id']}")
            ]
        ]
        if hasattr(message_or_query, "edit_message_text"):
            await show_question(message_or_query, next_q)
        else:
            await message_or_query.reply_text(
                f"Вопрос: {next_q['task']}\nКод: {next_q['code']}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    else:
        if hasattr(message_or_query, "reply_text"):
            await message_or_query.reply_text("Чек-лист завершён ✅ Спасибо!")
        else:
            await message_or_query.edit_message_text("Чек-лист завершён ✅ Спасибо!")
        context.user_data.pop("current", None)

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_category, pattern=r"^cat\|"))
    app.add_handler(CallbackQueryHandler(on_answer, pattern=r"^ans\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_comment))
    app.run_polling()

if __name__ == "__main__":
    main()
