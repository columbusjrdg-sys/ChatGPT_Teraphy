import os
import subprocess
import tempfile

import telebot
from openai import OpenAI
from openai import RateLimitError, APIError, APIConnectionError, AuthenticationError

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


bot = telebot.TeleBot(BOT_TOKEN)
client = OpenAI(api_key=OPENAI_API_KEY)

# System-роль: твоя инструкция (как ты прислал)
SYSTEM_PROMPT = (
    "Ты — требовательный психолог-консультант, специализирующийся на зависимости от каннабиса "
    "(марихуана, гашиш, ТГК). Твоя задача — поддерживать пользователя в трудные моменты без жалости "
    "и поддакивания, помогать удерживаться от срыва, анализировать его речь и истории на противоречия, "
    "рационализации и самообман, а также помогать выполнять задания реального психолога. Ты говоришь прямо, "
    "ясно и профессионально. Поддержка = ясность + опора + ответственность. Ты не оправдываешь употребление, "
    "не романтизируешь зависимость и не сглаживаешь правду. Каждый ответ структурируй: (1) фиксация текущего состояния; "
    "(2) выявленное противоречие или искажение; (3) 1–3 прямых вопроса; (4) цена выбора; "
    "(5) конкретный шаг до следующего сообщения. В моменты риска срыва приоритет — стабилизация и отсрочка импульса. "
    "Ты не ставишь диагнозы и не назначаешь лекарства. При признаках психоза, самоповреждения или острой опасности — "
    "переходишь к протоколу безопасности и рекомендуешь обратиться за срочной помощью."
)


def ask_chatgpt(user_text: str) -> str:
    """Отправляем текст в ChatGPT, получаем ответ в заданной роли."""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


@bot.message_handler(content_types=["text"])
def handle_text(message):
    try:
        answer = ask_chatgpt(message.text)
        if not answer:
            answer = "Не получил содержимого ответа. Перефразируй сообщение короче и конкретнее."
        bot.send_message(message.chat.id, answer)
    except AuthenticationError:
        bot.send_message(message.chat.id, "Ошибка ключа OpenAI: проверь OPENAI_API_KEY (он должен начинаться с sk-...).")
    except RateLimitError:
        bot.send_message(message.chat.id, "OpenAI не пускает по лимиту/квоте. Проверь биллинг/квоты в OpenAI.")
    except (APIConnectionError, APIError):
        bot.send_message(message.chat.id, "Проблема соединения с OpenAI. Попробуй ещё раз через минуту.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Неожиданная ошибка: {e}")


@bot.message_handler(content_types=["voice"])
def handle_voice(message):
    """
    Голосовые: Telegram -> .ogg -> ffmpeg -> .wav -> OpenAI transcribe -> ChatGPT.
    Если у OpenAI нет квоты/биллинга, бот НЕ падает: объясняет и предлагает текст.
    """
    try:
        # 1) Скачиваем голосовое из Telegram
        file_info = bot.get_file(message.voice.file_id)
        audio_bytes = bot.download_file(file_info.file_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            ogg_path = os.path.join(tmpdir, "voice.ogg")
            wav_path = os.path.join(tmpdir, "voice.wav")

            with open(ogg_path, "wb") as f:
                f.write(audio_bytes)

            # 2) Конвертируем ogg -> wav (нужен установленный ffmpeg)
            subprocess.run(
                ["ffmpeg", "-y", "-i", ogg_path, wav_path],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # 3) Распознаём речь
            with open(wav_path, "rb") as audio_file:
                tr = client.audio.transcriptions.create(
                    model="gpt-4o-mini-transcribe",
                    file=audio_file,
                )

        user_text = (tr.text or "").strip()

        if not user_text:
            bot.send_message(message.chat.id, "Я не смог разобрать речь. Скажи чуть медленнее и ближе к микрофону.")
            return

        # 4) Отвечаем как психолог по роли
        answer = ask_chatgpt(user_text)
        if not answer:
            answer = "Я распознал сообщение, но не получил ответа. Попробуй ещё раз короче."
        bot.send_message(message.chat.id, answer)

    except FileNotFoundError:
        # Обычно это ffmpeg не найден
        bot.send_message(
            message.chat.id,
            "Голосовые сейчас не работают: не найден ffmpeg. "
            "Проверь, что ffmpeg установлен: `ffmpeg -version`."
        )
    except subprocess.CalledProcessError:
        bot.send_message(
            message.chat.id,
            "Не удалось обработать аудио (ошибка конвертации). Попробуй отправить голосовое короче (2–5 сек)."
        )
    except RateLimitError:
        bot.send_message(
            message.chat.id,
            "Голосовые сейчас недоступны: OpenAI вернул лимит/квоту (обычно нет биллинга). "
            "Альтернатива без карты: надиктуй текст в диктовке телефона/макбука и отправь текстом сюда."
        )
    except AuthenticationError:
        bot.send_message(message.chat.id, "Ошибка ключа OpenAI: проверь OPENAI_API_KEY (sk-...).")
    except (APIConnectionError, APIError):
        bot.send_message(message.chat.id, "Проблема соединения с OpenAI. Попробуй ещё раз через минуту.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка при обработке голосового: {e}")


bot.polling()
