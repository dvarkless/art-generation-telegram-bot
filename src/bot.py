import asyncio
import html
import json
import logging
import logging.handlers
import traceback
from io import BytesIO
from pathlib import Path
from typing import List

import telegram
import translators as ts
from PIL import Image
from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
                      InputMediaPhoto, Update, User)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (AIORateLimiter, Application, ApplicationBuilder,
                          CallbackContext, CallbackQueryHandler,
                          CommandHandler, MessageHandler, filters)

from api_access import StableDiffusionAccess
from config import LoadConfig, SecretsAccess
from database_access import Database
from setup_handler import get_handler

# ts.preaccelerate()

logger = logging.getLogger(__name__)
logger.addHandler(get_handler())
logger.setLevel(logging.DEBUG)

user_semaphores = {}
user_tasks = {}


modes_config = LoadConfig('./configs/usage_modes.yml')
models_config = LoadConfig('./configs/models.yml')
dialogs_config = LoadConfig('./configs/dialogs.yml')
secrets_config = SecretsAccess('./info')

database = Database('./info/db.db')
stable_api = StableDiffusionAccess(model_config_obj=models_config)


def split_text_into_chunks(text, chunk_size):
    for i in range(0, len(text), chunk_size):
        yield text[i:i + chunk_size]


def check_for_banned_words(text: str, banned_words: List):
    for word in text.split():
        if word in banned_words:
            return True

    return False


async def translate_prompt(prompt) -> str:
    logger.debug('Call: translate_prompt')

    tr_out = ts.translate_text(prompt,
                               if_use_preacceleration=False,
                               )
    assert isinstance(tr_out, str)
    return tr_out


async def register_user_if_not_exists(user_id):
    logger.debug('Call: register_user_if_not_exists')
    with database as db:
        if not db.check_user_exists(user_id):
            db.insert('start', user_id,
                      model=0, orientation=0)
            logger.info('User registered')

    if user_id not in user_semaphores:
        user_semaphores[user_id] = asyncio.Semaphore(1)


async def start_handle(update: Update, context: CallbackContext):
    logger.debug('Call: start_handle')
    await register_user_if_not_exists(update.message.from_user.id)
    reply_text = dialogs_config['info']['welcome']
    reply_text += '\n'
    for msg in dialogs_config['help'].values():
        reply_text += msg
        reply_text += '\n'

    await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)


async def help_handle(update: Update, context: CallbackContext):
    logger.debug('Call: help_handle')
    await register_user_if_not_exists(update.message.from_user.id)
    reply_text = '\n\n'.join(dialogs_config['help'].values())
    await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)


async def retry_handle(update: Update, context: CallbackContext):
    await register_user_if_not_exists(update.message.from_user.id)
    if await is_previous_message_not_answered_yet(update, context):
        return

    user = update.message.from_user
    with database as db:
        db.update_for_user(user)
        last_action = db.last_action

    if last_action is None:
        pass
    elif last_action == 'txt2img':
        await text_message_handle(update, context,
                                  message=database.last_prompt,
                                  use_new_dialog_timeout=False)
    else:
        await photo_message_handle(update, context,
                                   message=database.last_prompt,
                                   use_new_dialog_timeout=False)


async def text_message_handle(update: Update, context: CallbackContext,
                              message=None, use_new_dialog_timeout=True):
    logger.debug('Call: text_message_handle')

    # check if message is edited
    if update.edited_message is not None:
        await edited_message_handle(update, context)
        return

    await register_user_if_not_exists(update.message.from_user.id)
    if await is_previous_message_not_answered_yet(update, context):
        return

    user = update.message.from_user

    async def message_handle_fn():
        with database as db:
            db.update_for_user(user)
            model = db.last_model
            orientation = db.last_orientation

        try:
            placeholder_message = await update.message.reply_text(dialogs_config['info']['in_progress_text'])

            await update.message.chat.send_action(action='upload_photo')

            _message = message or update.message.text
            translated_msg = await translate_prompt(_message)

            if check_for_banned_words(translated_msg,
                                      secrets_config.get_banwords()):
                with database as db:
                    db.insert(generation_mode,
                              user,
                              gen_mode=generation_index,
                              model=model,
                              orientation=orientation,
                              prompt=_message,
                              blocked=True)

                text = dialogs_config["error"]['bad_message']
                await update.message.reply_text(text, reply_to_message_id=update.message.id,
                                                parse_mode=ParseMode.HTML)
                return False

            model_name = models_config["available_models"][model]

            orient_name = modes_config["available_orientations"][orientation]
            orient_name = modes_config["orientation"][orient_name]['config_name']

            image_size = models_config[model_name][orient_name]

            img_paths = await stable_api.txt2img(translated_msg,
                                                 model_name,
                                                 image_size,
                                                 f'gen_txt2img_{user.username}'
                                                 )

            with database as db:
                db.insert('txt2img',
                          user,
                          model=model,
                          orientation=orientation,
                          prompt=translated_msg)

            media = [InputMediaPhoto(open(image_path, 'rb'))
                     for image_path in img_paths]

            # Send the message with the images
            await update.message.reply_media_group(media)

            for path in img_paths:
                Path(path).unlink()

        except asyncio.CancelledError:
            pass

        except Exception as e:
            error_text = dialogs_config["error"]['generation_error']
            trb = traceback.format_exc()
            logger.error('error in text message handler:\n' + trb)
            await update.message.reply_text(error_text)
            return

    async with user_semaphores[user.id]:
        task = asyncio.create_task(message_handle_fn())
        user_tasks[user.id] = task

        try:
            await task
        except asyncio.CancelledError:
            await update.message.reply_text(dialogs_config["info"]["canceled"], parse_mode=ParseMode.HTML)
        else:
            pass
        finally:
            if user.id in user_tasks:
                del user_tasks[user.id]


async def photo_message_handle(update: Update, context: CallbackContext,
                               message=None, use_new_dialog_timeout=True):
    logger.debug('Call: photo_message_handle')

    if update.edited_message is not None:
        await edited_message_handle(update, context)
        return

    await register_user_if_not_exists(update.message.from_user.id)
    if await is_previous_message_not_answered_yet(update, context):
        return

    user = update.message.from_user

    async def message_handle_fn():
        with database as db:
            db.update_for_user(user)
            model = db.last_model
            orientation = db.last_orientation

        try:
            _message = message or update.message.text or update.message.caption
            answer_msg = dialogs_config['info']['in_progress_img'] if _message else dialogs_config['info']['in_progress_rescale']
            placeholder_message = await update.message.reply_text(answer_msg)

            await update.message.chat.send_action(action='upload_photo')
            if _message:
                translated_msg = await translate_prompt(_message)
            else:
                translated_msg = ''

            if check_for_banned_words(translated_msg,
                                      secrets_config.get_banwords()):
                with database as db:
                    db.insert('img2img',
                              user,
                              model=model,
                              orientation=orientation,
                              prompt=_message,
                              blocked=True)

                text = dialogs_config["error"]['bad_message']
                await update.message.reply_text(text, reply_to_message_id=update.message.id,
                                                parse_mode=ParseMode.HTML)
                return False

            model_name = models_config["available_models"][model]

            orient_name = modes_config["available_orientations"][orientation]
            orient_name = modes_config["orientation"][orient_name]['config_name']

            image_size = models_config[model_name][orient_name]
            # Get the picture message from the user
            # if len(update.message.photo) > 1:
            #     print(update.message.photo)
            #     print(len(update.message.photo))
            #     text = dialogs_config["warning"]['too_many_pictures']
            #     await update.message.reply_text(text, reply_to_message_id=update.message.id,
            #                                     parse_mode=ParseMode.HTML)

            img_name = f'{user.id}_' + \
                '_'.join(translated_msg.split()) + '.png'
            img_path = Path('./temp') / img_name
            photo = await update.message.photo[-1].get_file()
            await photo.download_to_drive(img_path)

            if translated_msg:
                action = 'img2img'
                img_paths = await stable_api.img2img(translated_msg,
                                                     model_name,
                                                     image_size,
                                                     img_path,
                                                     f'gen_txt2img_{user.username}',
                                                     )

            else:
                action = 'rescale'
                text = dialogs_config['error']['bad_action']
                await update.message.reply_text(text, reply_to_message_id=update.message.id,
                                                parse_mode=ParseMode.HTML)
                raise asyncio.CancelledError()
                img_paths = await stable_api.upscale_img()

            with database as db:
                prompt = translated_msg if translated_msg else ''
                db.insert(action,
                          user,
                          model=model,
                          orientation=orientation,
                          prompt=prompt
                          )

            media = [InputMediaPhoto(open(image_path, 'rb'))
                     for image_path in img_paths]

            # Send the message with the images
            await update.message.reply_media_group(media)

            for path in Path('./temp/').iterdir():
                path.unlink(missing_ok=True)

        except asyncio.CancelledError:
            pass

        except Exception as e:
            error_text = dialogs_config["error"]['generation_error']
            trb = traceback.format_exc()
            logger.error('error in photo message handler:\n' + trb)
            await update.message.reply_text(error_text)
            return

    async with user_semaphores[user.id]:
        task = asyncio.create_task(message_handle_fn())
        user_tasks[user.id] = task

        try:
            await task
        except asyncio.CancelledError:
            await update.message.reply_text(dialogs_config["info"]["canceled"], parse_mode=ParseMode.HTML)
        else:
            pass
        finally:
            if user.id in user_tasks:
                del user_tasks[user.id]


async def is_previous_message_not_answered_yet(update: Update, context: CallbackContext):
    logger.debug('Call: is_previous_message_not_answered_yet')
    user_id = update.message.from_user.id
    if user_semaphores[user_id].locked():
        text = dialogs_config["warning"]['wait_or_cancel']
        await update.message.reply_text(text, reply_to_message_id=update.message.id, parse_mode=ParseMode.HTML)
        return True
    else:
        return False


async def new_dialog_handle(update: Update, context: CallbackContext):
    logger.debug('Call: new_dialog_handle')
    await register_user_if_not_exists(update.message.from_user.id)
    if await is_previous_message_not_answered_yet(update, context):
        return

    user = update.message.from_user

    with database as db:
        db.insert('start', user, 0, 0, '')
        db.update_for_user(user)
        gen_mode = db.last_gen_mode
    await update.message.reply_text(dialogs_config["info"]["new_dialog"])

    await update.message.reply_text(
        f"{modes_config['generation'][gen_mode]['welcome_message']}",
        parse_mode=ParseMode.HTML)


async def cancel_handle(update: Update, context: CallbackContext):
    logger.debug('Call: cancel_handle')
    await register_user_if_not_exists(update.message.from_user.id)
    user_id = update.message.from_user.id

    if user_id in user_tasks:
        task = user_tasks[user_id]
        task.cancel()
    else:
        await update.message.reply_text(
            dialogs_config["warning"]['nothing_to_cancel'],
            parse_mode=ParseMode.HTML)


async def show_orientation_modes_handle(update: Update, context: CallbackContext):
    logger.debug('Call: show_orientation_modes_handle')
    await register_user_if_not_exists(update.message.from_user.id)
    if await is_previous_message_not_answered_yet(update, context):
        return

    keyboard = []
    for orient_mode, orient_mode_dict in modes_config['orientation'].items():
        keyboard.append([InlineKeyboardButton(
            orient_mode_dict["name"],
            callback_data=f"orientation|{orient_mode}")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        dialogs_config["info"]["select_orientation_mode"],
        reply_markup=reply_markup)


async def set_mode_handle(update: Update, context: CallbackContext):
    logger.debug('Call: set_mode_handle')
    user = update.callback_query.from_user

    query = update.callback_query
    await query.answer()
    mode_name, mode_to_change = query.data.split('|')
    with database as db:
        if mode_name == 'orientation':
            db.insert(f"change_{mode_name}_mode", user,
                      orientation=modes_config[mode_name][mode_to_change]['pos'])

    await query.edit_message_text(
        f"{modes_config[mode_name][mode_to_change]['name']}",
        parse_mode=ParseMode.HTML)


def get_models_menu(user_id: int):
    logger.debug('Call: get_models_menu')
    with database as db:
        db.update_for_user(user_id)
        current_model = db.last_model
        print(current_model)
    curr_model_name = models_config["available_models"][current_model]
    text = models_config[curr_model_name]["name"]
    text += '\n'
    text += models_config[curr_model_name]["description"]

    text += "\n\n"
    score_dict = models_config[curr_model_name]["scores"]
    for score_key, score_value in score_dict.items():
        text += "🟢" * score_value + "⚪️" * \
            (5 - score_value) + \
            f" – {dialogs_config['info']['scores'][score_key]}\n\n"

    text += "\n"
    text += dialogs_config['info']['select_model']

    # buttons to choose models
    buttons = []
    for model_key in models_config["available_models"]:
        title = models_config[model_key]["name"]
        if model_key == curr_model_name:
            title = "✅ " + title

        buttons.append(
            InlineKeyboardButton(
                title, callback_data=f"set_model|{model_key}")
        )
    reply_markup = InlineKeyboardMarkup([buttons])

    return text, reply_markup


async def models_handle(update: Update, context: CallbackContext):
    logger.debug('Call: models_handle')
    await register_user_if_not_exists(update.message.from_user.id)
    if await is_previous_message_not_answered_yet(update, context):
        return

    user_id = update.message.from_user.id

    text, reply_markup = get_models_menu(user_id)
    await update.message.reply_text(text, reply_markup=reply_markup,
                                    parse_mode=ParseMode.HTML)


async def set_models_handle(update: Update, context: CallbackContext):
    logger.debug('Call: set_model_handle')
    user = update.callback_query.from_user

    query = update.callback_query
    await query.answer()

    _, model_key = query.data.split("|")
    model_pos = models_config[model_key]['pos']
    with database as db:
        db.insert("set_model", user, model=model_pos)

    text, reply_markup = get_models_menu(user.id)
    try:
        await query.edit_message_text(text, reply_markup=reply_markup,
                                      parse_mode=ParseMode.HTML)
    except telegram.error.BadRequest as e:
        if str(e).startswith("Message is not modified"):
            pass


async def edited_message_handle(update: Update, context: CallbackContext):
    logger.debug('Call: edited_message_handle')
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
    for cmd_key in modes_config["bot_commands"]:
        cmd = modes_config["bot_commands"][cmd_key]["command"]
        description = modes_config["bot_commands"][cmd_key]["description"]
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
        filters.TEXT & ~filters.ATTACHMENT & ~filters.COMMAND & user_filter,
        text_message_handle))
    application.add_handler(MessageHandler(
        (filters.PHOTO | filters.CAPTION) & ~filters.COMMAND & user_filter,
        photo_message_handle))

    application.add_handler(CommandHandler(
        "retry", retry_handle, filters=user_filter))
    application.add_handler(CommandHandler(
        "cancel", cancel_handle, filters=user_filter))

    application.add_handler(CommandHandler(
        "artist", models_handle, filters=user_filter))
    application.add_handler(CallbackQueryHandler(
        set_models_handle, pattern="^set_model"))

    application.add_handler(CommandHandler(
        "picture_orientation", show_orientation_modes_handle, filters=user_filter))
    application.add_handler(CallbackQueryHandler(
        set_mode_handle, pattern="^orientation"))

    application.add_error_handler(error_handle)

    # start the bot
    print('Bot started')
    application.run_polling()


if __name__ == "__main__":
    run_bot()
