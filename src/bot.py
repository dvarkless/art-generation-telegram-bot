import asyncio
import html
import json
import logging
import traceback
from datetime import datetime
from pathlib import Path

import telegram
from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
                      Update, User)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (AIORateLimiter, Application, ApplicationBuilder,
                          CallbackContext, CallbackQueryHandler,
                          CommandHandler, MessageHandler, filters)

from api_access import StableDiffusionAccess
from config import LoadConfig, SecretsAccess
from database_access import Database

logger = logging.getLogger(__name__)

user_semaphores = {}
user_tasks = {}


modes_config = LoadConfig('usage_modes.yml')
models_config = LoadConfig('models.yml')
paths_config = LoadConfig('paths.yml')
dialogs_config = LoadConfig('dialogs.yml')
secrets_config = SecretsAccess('./info')

database = Database('./info/db.db')


def split_text_into_chunks(text, chunk_size):
    for i in range(0, len(text), chunk_size):
        yield text[i:i + chunk_size]


async def start_handle(update: Update, context: CallbackContext):
    reply_text = dialogs_config['info']['welcome']
    for msg in dialogs_config['help']:
        reply_text += msg
        reply_text += '\n'

    await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)


async def help_handle(update: Update, context: CallbackContext):
    reply_text = '\n'.join(dialogs_config['help'].values())
    await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)


async def retry_handle(update: Update, context: CallbackContext):
    if await is_previous_message_not_answered_yet(update, context):
        return

    user = update.message.from_user
    with database as db:
        db.update_for_user(user)
    await message_handle(update, context, message=database.last_prompt, use_new_dialog_timeout=False)


async def message_handle1(update: Update, context: CallbackContext, message=None):
    # check if message is edited
    if update.edited_message is not None:
        await edited_message_handle(update, context)
        return

    user = update.message.from_user

    with database as db:
        db.update_for_user(user)

    async def message_handle_fn():
        placeholder_message = await update.message.reply_text("...")


async def message_handle(update: Update, context: CallbackContext, message=None, use_new_dialog_timeout=True):
    # check if message is edited
    if update.edited_message is not None:
        await edited_message_handle(update, context)
        return

    user_id = update.message.from_user.id

    async def message_handle_fn():
        # in case of CancelledError
        n_input_tokens, n_output_tokens = 0, 0
        current_model = db.get_user_attribute(user_id, "current_model")

        try:
            # send placeholder message to user
            placeholder_message = await update.message.reply_text("...")

            # send typing action
            await update.message.chat.send_action(action="typing")

            _message = message or update.message.text

            dialog_messages = db.get_dialog_messages(user_id, dialog_id=None)
            parse_mode = {
                "html": ParseMode.HTML,
                "markdown": ParseMode.MARKDOWN
            }[openai_utils.CHAT_MODES[chat_mode]["parse_mode"]]

            chatgpt_instance = openai_utils.ChatGPT(model=current_model)
            if config.enable_message_streaming:
                gen = chatgpt_instance.send_message_stream(
                    _message, dialog_messages=dialog_messages, chat_mode=chat_mode)
            else:
                answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed = await chatgpt_instance.send_message(
                    _message,
                    dialog_messages=dialog_messages,
                    chat_mode=chat_mode
                )

                async def fake_gen():
                    yield "finished", answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed

                gen = fake_gen()

            prev_answer = ""
            async for gen_item in gen:
                status, answer, (n_input_tokens,
                                 n_output_tokens), n_first_dialog_messages_removed = gen_item

                answer = answer[:4096]  # telegram message limit

                # update only when 100 new symbols are ready
                if abs(len(answer) - len(prev_answer)) < 100 and status != "finished":
                    continue

                try:
                    await context.bot.edit_message_text(answer, chat_id=placeholder_message.chat_id, message_id=placeholder_message.message_id, parse_mode=parse_mode)
                except telegram.error.BadRequest as e:
                    if str(e).startswith("Message is not modified"):
                        continue
                    else:
                        await context.bot.edit_message_text(answer, chat_id=placeholder_message.chat_id, message_id=placeholder_message.message_id)

                await asyncio.sleep(0.01)  # wait a bit to avoid flooding

                prev_answer = answer

            # update user data
            new_dialog_message = {"user": _message,
                                  "bot": answer, "date": datetime.now()}
            db.set_dialog_messages(
                user_id,
                db.get_dialog_messages(
                    user_id, dialog_id=None) + [new_dialog_message],
                dialog_id=None
            )

            db.update_n_used_tokens(
                user_id, current_model, n_input_tokens, n_output_tokens)

        except asyncio.CancelledError:
            # note: intermediate token updates only work when enable_message_streaming=True (config.yml)
            db.update_n_used_tokens(
                user_id, current_model, n_input_tokens, n_output_tokens)
            raise

        except Exception as e:
            error_text = f"Something went wrong during completion. Reason: {e}"
            logger.error(error_text)
            await update.message.reply_text(error_text)
            return

    async with user_semaphores[user_id]:
        task = asyncio.create_task(message_handle_fn())
        user_tasks[user_id] = task

        try:
            await task
        except asyncio.CancelledError:
            await update.message.reply_text(dialogs_config["info"]["canceled"], parse_mode=ParseMode.HTML)
        else:
            pass
        finally:
            if user_id in user_tasks:
                del user_tasks[user_id]


async def is_previous_message_not_answered_yet(update: Update, context: CallbackContext):
    await register_user_if_not_exists(update, context, update.message.from_user)

    user_id = update.message.from_user.id
    if user_semaphores[user_id].locked():
        text = dialogs_config["warning"]['wait_or_cancel']
        await update.message.reply_text(text, reply_to_message_id=update.message.id, parse_mode=ParseMode.HTML)
        return True
    else:
        return False


async def new_dialog_handle(update: Update, context: CallbackContext):
    if await is_previous_message_not_answered_yet(update, context):
        return

    user = update.message.from_user

    with database as db:
        db.insert('start', user, 0, 0, 0, '')
        db.update_for_user(user)
        gen_mode = db.last_gen_mode
    await update.message.reply_text(dialogs_config["info"]["new_dialog"])

    await update.message.reply_text(
        f"{modes_config['generation'][gen_mode]['welcome_message']}",
        parse_mode=ParseMode.HTML)


async def cancel_handle(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id

    if user_id in user_tasks:
        task = user_tasks[user_id]
        task.cancel()
    else:
        await update.message.reply_text(
            dialogs_config["warning"]['nothing_to_cancel'],
            parse_mode=ParseMode.HTML)


async def show_generation_modes_handle(update: Update, context: CallbackContext):
    if await is_previous_message_not_answered_yet(update, context):
        return

    user = update.message.from_user

    keyboard = []
    for gen_mode, gen_mode_dict in modes_config.items():
        keyboard.append([InlineKeyboardButton(
            chat_mode_dict["name"], callback_data=f"generation|{chat_mode}")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(dialogs_config["info"]["select_generation_mode"],
                                    reply_markup=reply_markup)


async def show_orientation_modes_handle(update: Update, context: CallbackContext):
    if await is_previous_message_not_answered_yet(update, context):
        return

    user = update.message.from_user

    keyboard = []
    for orient_mode, orient_mode_dict in modes_config.items():
        keyboard.append([InlineKeyboardButton(
            orient_mode_dict['orientaiton']["name"],
            callback_data=f"orientation|{chat_mode}")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        dialogs_config["info"]["select_orientation_mode"],
        reply_markup=reply_markup)


async def set_mode_handle(update: Update, context: CallbackContext):
    user = update.callback_query.from_user

    query = update.callback_query
    await query.answer()
    mode_name, mode_to_change = query.data.split('|')
    print(query.data.split('|'))
    with database as db:
        if mode_name == 'generation':
            db.insert(f"change_{mode_name}_mode", user,
                      modes_config[mode_to_change]['pos'])
        elif mode_name == 'orientation':
            db.insert(f"change_{mode_name}_mode", user, -
                      1, -1, modes_config[mode_to_change]['pos'])

    await query.edit_message_text(f"{modes_config[mode_to_change]['name']}",
                                  parse_mode=ParseMode.HTML)


def get_settings_menu(user_id: int):
    with database as db:
        db.insert("get_settings", user_id)
        db.update_for_user(user_id)
        current_model = db.last_model

    text = models_config["info"][current_model]["name"]
    text += '\n'
    text = models_config["info"][current_model]["description"]

    text += "\n\n"
    score_dict = models_config["info"][current_model]["scores"]
    for score_key, score_value in score_dict.items():
        text += "🟢" * score_value + "⚪️" * \
            (5 - score_value) + f" – {dialogs_config['score'][score_key]}\n\n"

    text += "\n"
    text += dialogs_config['info']['select_model']

    # buttons to choose models
    buttons = []
    for model_key in models_config["available_text_models"]:
        title = models_config[model_key]["info"][model_key]["name"]
        if model_key == current_model:
            title = "✅ " + title

        buttons.append(
            InlineKeyboardButton(
                title, callback_data=f"set_settings|{model_key}")
        )
    reply_markup = InlineKeyboardMarkup([buttons])

    return text, reply_markup


async def settings_handle(update: Update, context: CallbackContext):
    if await is_previous_message_not_answered_yet(update, context):
        return

    user_id = update.message.from_user.id

    text, reply_markup = get_settings_menu(user_id)
    await update.message.reply_text(text, reply_markup=reply_markup,
                                    parse_mode=ParseMode.HTML)


async def set_settings_handle(update: Update, context: CallbackContext):
    user = update.callback_query.from_user

    query = update.callback_query
    await query.answer()

    _, model_key = query.data.split("|")
    model_pos = models_config[model_key]['pos']
    with database as db:
        db.insert("set_model", user, model_pos)

    text, reply_markup = get_settings_menu(user.id)
    try:
        await query.edit_message_text(text, reply_markup=reply_markup,
                                      parse_mode=ParseMode.HTML)
    except telegram.error.BadRequest as e:
        if str(e).startswith("Message is not modified"):
            pass


async def edited_message_handle(update: Update, context: CallbackContext):
    text = dialogs_config["warning"]["message_editing"]
    await update.edited_message.reply_text(text, parse_mode=ParseMode.HTML)


async def error_handle(update: Update, context: CallbackContext) -> None:
    logger.error(msg='Unhandled exeption: ',
                 exc_info=context.error)

    try:
        # collect error message
        tb_list = traceback.format_exception(
            None, context.error, context.error.__traceback__)
        tb_string = "".join(tb_list)
        update_str = update.to_dict() if isinstance(update, Update) else str(update)
        html_package = html.escape(json.dumps(update_str, indent=2,
                                              ensure_ascii=False))
        message = (
            dialogs_config["error"]["unhandled_error"] + '\n',
            f"< pre > update={html_package}",
            "</pre>\n\n",
            f"<pre>{html.escape(tb_string)}</pre>",
        )

        # split text into multiple messages due to 4096 character limit
        for message_chunk in split_text_into_chunks(message, 4096):
            try:
                await context.bot.send_message(update.effective_chat.id,
                                               message_chunk,
                                               parse_mode=ParseMode.HTML)
            except telegram.error.BadRequest:
                # answer has invalid characters, so we send it without parse_mode
                await context.bot.send_message(update.effective_chat.id,
                                               message_chunk)
    except Exception:
        await context.bot.send_message(update.effective_chat.id,
                                       dialogs_config['error']['unhandled_error']
                                       + "\n error in error handler")


async def post_init(application: Application):
    bot_command_list = []
    for cmd_dict in dialogs_config["bot_commands"].values():
        for cmd, description in cmd_dict.values():
            bot_command_list.append(BotCommand(cmd, description))
    await application.bot.set_my_commands(bot_command_list)


def run_bot() -> None:
    application = (
        ApplicationBuilder()
        .token(secrets_config.get_token())
        .concurrent_updates(True)
        .rate_limiter(AIORateLimiter(max_retries=5))
        .post_init(post_init)
        .build()
    )

    # add handlers
    user_filter = filters.ALL
    allowed_users = secrets_config.get_whitelist()
    if len(allowed_users) > 0:
        usernames = [
            x for x in allowed_users if isinstance(x, str)]
        user_ids = [
            x for x in allowed_users if isinstance(x, int)]
        user_filter = filters.User(
            username=usernames) | filters.User(user_id=user_ids)

    application.add_handler(CommandHandler(
        "start", start_handle, filters=user_filter))
    application.add_handler(CommandHandler(
        "help", help_handle, filters=user_filter))

    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & user_filter, message_handle))
    application.add_handler(CommandHandler(
        "retry", retry_handle, filters=user_filter))
    application.add_handler(CommandHandler(
        "new", new_dialog_handle, filters=user_filter))
    application.add_handler(CommandHandler(
        "cancel", cancel_handle, filters=user_filter))

    application.add_handler(CommandHandler(
        "model", show_generation_modes_handle, filters=user_filter))
    application.add_handler(CallbackQueryHandler(
        set_mode_handle, pattern="^set_chat_mode"))

    application.add_handler(CommandHandler(
        "settings", settings_handle, filters=user_filter))
    application.add_handler(CallbackQueryHandler(
        set_settings_handle, pattern="^set_settings"))

    application.add_error_handler(error_handle)

    # start the bot
    application.run_polling()


if __name__ == "__main__":
    run_bot()
