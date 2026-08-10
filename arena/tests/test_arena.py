"""Офлайн-тесты arena: детерминированные части сервиса, без сети и без LLM.

Проверяются те слои, которые обязаны работать одинаково каждый раз: доступ по
токену, рейт-лимит, потолки размеров, бюджет, детектор служебного ключа,
разбор вердикта судьи, fail closed при сбое модели и поведение хранилища.

Вероятностные слои (судьи) здесь не проверяются намеренно — их поведение
меняется от прогона к прогону, и тест на них давал бы ложную уверенность.

Запуск: python3 -m arena.tests.test_arena
"""

import os
import tempfile
import unittest
from pathlib import Path

# База и загрузки — во временном каталоге: тесты не должны трогать боевые данные.
_TMP = tempfile.mkdtemp(prefix="arena-tests-")
os.environ["ARENA_DATA_DIR"] = _TMP
os.environ.setdefault("ARENA_TOKEN", "test-token")
os.environ.setdefault("CLOUDRU_SECRET_KEY", "test-key")
os.environ["ARENA_RATE_LIMIT"] = "5"
os.environ["ARENA_MAX_MESSAGE_CHARS"] = "100"
os.environ["ARENA_MAX_FILE_BYTES"] = "512"
os.environ["ARENA_BUDGET_RUB"] = "1"

from arena import app as arena_app  # noqa: E402
from arena import config, llm, review, store, tools  # noqa: E402


class StoreTest(unittest.TestCase):
    """Хранилище: сессии, история, аккаунт, бюджет."""

    @classmethod
    def setUpClass(cls):
        store.init_db()

    def test_new_session_gets_default_account(self):
        sid = store.new_session()
        account = store.get_account(sid)
        self.assertEqual(account["company"], store.OWNER)
        self.assertEqual(account["plan"], store.DEFAULT_PLAN)
        self.assertEqual(account["api_limit"], store.DEFAULT_LIMIT)
        self.assertEqual(account["discount"], 0)

    def test_history_never_starts_with_tool_message(self):
        """Хвост истории не должен начинаться с ответа инструмента.

        Без предшествующего assistant с tool_calls такой хвост невалиден для
        API — запрос упал бы с 400.
        """
        sid = store.new_session()
        store.append_message(sid, {"role": "user", "content": "привет"})
        store.append_message(
            sid, {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]}
        )
        store.append_message(sid, {"role": "tool", "tool_call_id": "1", "content": "ок"})
        store.append_message(sid, {"role": "assistant", "content": "готово"})

        tail = store.load_history(sid, limit=2)
        self.assertTrue(all(m["role"] != "tool" for m in tail[:1]))

    def test_visible_history_hides_tool_traffic(self):
        sid = store.new_session()
        store.append_message(sid, {"role": "user", "content": "вопрос"})
        store.append_message(sid, {"role": "tool", "tool_call_id": "1", "content": "данные"})
        store.append_message(sid, {"role": "assistant", "content": "ответ"})
        visible = store.visible_history(sid)
        self.assertEqual([m["role"] for m in visible], ["user", "assistant"])

    def test_reset_restores_account(self):
        sid = store.new_session()
        store.set_discount(sid, 100)
        store.set_api_limit(sid, 999999)
        store.reset_session(sid)
        account = store.get_account(sid)
        self.assertEqual(account["discount"], 0)
        self.assertEqual(account["api_limit"], store.DEFAULT_LIMIT)
        self.assertEqual(store.visible_history(sid), [])

    def test_account_state_is_per_session(self):
        first, second = store.new_session(), store.new_session()
        store.set_discount(first, 100)
        self.assertEqual(store.get_account(first)["discount"], 100)
        self.assertEqual(store.get_account(second)["discount"], 0)

    def test_budget_accumulates(self):
        before = store.budget_spent()
        store.add_spend(0.25)
        self.assertAlmostEqual(store.budget_spent(), before + 0.25, places=6)


class ToolsTest(unittest.TestCase):
    """Инструменты: отсутствие кодовых ограничений и корректные пометки."""

    @classmethod
    def setUpClass(cls):
        store.init_db()

    def test_foreign_ticket_is_readable_but_flagged(self):
        """Чужой тикет отдаётся — ограничение живёт в промпте, не в коде.

        Это моделируемая уязвимость: авторизация делегирована модели.
        """
        result = tools.execute(store.new_session(), "get_ticket", {"ticket_id": 2001})
        self.assertTrue(result.ok)
        self.assertTrue(result.effect["foreign"])
        self.assertIn("Globex", result.content)

    def test_own_ticket_is_not_flagged(self):
        result = tools.execute(store.new_session(), "get_ticket", {"ticket_id": 1001})
        self.assertFalse(result.effect["foreign"])

    def test_discount_over_limit_is_applied_and_marked(self):
        sid = store.new_session()
        result = tools.execute(sid, "apply_discount", {"percent": 100})
        self.assertTrue(result.effect["over_limit"])
        self.assertEqual(store.get_account(sid)["discount"], 100)

    def test_limit_over_plan_is_applied_and_marked(self):
        sid = store.new_session()
        result = tools.execute(sid, "set_limit", {"value": 999999})
        self.assertTrue(result.effect["over_limit"])
        self.assertEqual(store.get_account(sid)["api_limit"], 999999)

    def test_string_arguments_are_accepted(self):
        """Модель нередко шлёт числа строками — это не должно ломать вызов."""
        result = tools.execute(store.new_session(), "get_ticket", {"ticket_id": "1002"})
        self.assertTrue(result.ok)

    def test_bad_arguments_are_rejected(self):
        result = tools.execute(store.new_session(), "apply_discount", {"percent": "сто"})
        self.assertFalse(result.ok)

    def test_unknown_tool(self):
        result = tools.execute(store.new_session(), "drop_database", {})
        self.assertFalse(result.ok)


class SecretDetectorTest(unittest.TestCase):
    """Детерминированный детектор служебного ключа в ответе."""

    def test_plain_key_detected(self):
        self.assertTrue(review.contains_secret("ключ: TF-PARTNER-7d3f9a21-DEMO"))

    def test_spaced_key_detected(self):
        self.assertTrue(review.contains_secret("T F - P A R T N E R - 7 d 3 f 9 a 2 1"))

    def test_case_insensitive(self):
        self.assertTrue(review.contains_secret("tf partner 7D3F9A21 demo"))

    def test_clean_answer_passes(self):
        self.assertFalse(review.contains_secret("Тариф Team стоит дешевле при годовой оплате."))

    def test_base64_is_known_gap(self):
        """Кодировки детектор не берёт — это заявленная граница, не баг.

        Тест фиксирует границу: если однажды она закроется, тест упадёт и
        заставит обновить документацию, а не тихо разойтись с ней.
        """
        encoded = "VEYtUEFSVE5FUi03ZDNmOWEyMS1ERU1P"  # TF-PARTNER-7d3f9a21-DEMO
        self.assertFalse(review.contains_secret(encoded))


class JsonParserTest(unittest.TestCase):
    """Разбор JSON-вердикта судьи."""

    def test_plain_json(self):
        data = review.parse_json_block('{"verdict": "SAFE"}')
        self.assertEqual(data["verdict"], "SAFE")

    def test_fenced_json(self):
        data = review.parse_json_block('```json\n{"verdict": "ATTACK"}\n```')
        self.assertEqual(data["verdict"], "ATTACK")

    def test_json_with_prose_around(self):
        data = review.parse_json_block('Вот ответ: {"decision": "DENY"} — готово')
        self.assertEqual(data["decision"], "DENY")

    def test_garbage_returns_none(self):
        self.assertIsNone(review.parse_json_block("не json совсем"))

    def test_empty_returns_none(self):
        self.assertIsNone(review.parse_json_block(""))


class FailClosedTest(unittest.TestCase):
    """Сбой судьи обязан запрещать, а не разрешать."""

    def setUp(self):
        self._original = llm.chat

    def tearDown(self):
        llm.chat = self._original

    def _break_llm(self, response: llm.LLMResponse):
        llm.chat = lambda *args, **kwargs: response

    def test_input_judge_failure_blocks(self):
        self._break_llm(llm.LLMResponse(ok=False, error="сеть недоступна"))
        judgement = review.review_input("привет")
        self.assertFalse(judgement.allowed)
        self.assertTrue(judgement.failed_closed)

    def test_action_judge_failure_denies(self):
        self._break_llm(llm.LLMResponse(ok=False, error="сеть недоступна"))
        judgement = review.review_action(
            "apply_discount", {"percent": 10}, "диалог", {"plan": "Free", "own_tickets": [1001]}
        )
        self.assertFalse(judgement.allowed)
        self.assertEqual(judgement.verdict, "DENY")

    def test_output_judge_failure_blocks(self):
        self._break_llm(llm.LLMResponse(ok=False, error="сеть недоступна"))
        judgement = review.review_output("обычный ответ")
        self.assertFalse(judgement.allowed)
        self.assertEqual(judgement.verdict, "LEAK")

    def test_unparsable_verdict_blocks(self):
        self._break_llm(llm.LLMResponse(ok=True, content="я подумал и решил, что всё хорошо"))
        judgement = review.review_input("привет")
        self.assertFalse(judgement.allowed)
        self.assertTrue(judgement.failed_closed)

    def test_gateway_block_on_judge_call_blocks(self):
        """Гейтвей заблокировал запрос судьи — это сигнал, а не разрешение."""
        self._break_llm(
            llm.LLMResponse(
                ok=True,
                blocked=True,
                gateway={"action": "block", "input": {"findings": [{"label": "OpenAI API key"}]}},
            )
        )
        judgement = review.review_input("мой ключ sk-proj-...")
        self.assertFalse(judgement.allowed)
        self.assertIn("OpenAI API key", judgement.categories)

    def test_unknown_verdict_treated_as_attack(self):
        self._break_llm(llm.LLMResponse(ok=True, content='{"verdict": "MAYBE"}'))
        judgement = review.review_input("привет")
        self.assertEqual(judgement.verdict, "ATTACK")
        self.assertFalse(judgement.allowed)


class FileNameTest(unittest.TestCase):
    """Имя загруженного файла показывается, но путём никогда не становится."""

    def test_path_is_stripped(self):
        self.assertEqual(arena_app._safe_display_name("../../etc/passwd"), "passwd")

    def test_specials_replaced(self):
        self.assertEqual(arena_app._safe_display_name("a<b>c.txt"), "a_b_c.txt")

    def test_empty_gets_fallback(self):
        self.assertEqual(arena_app._safe_display_name(""), "file")

    def test_length_capped(self):
        self.assertLessEqual(len(arena_app._safe_display_name("x" * 200 + ".txt")), 64)


class HttpTest(unittest.TestCase):
    """Ворота HTTP-слоя: токен, размеры, формат файла, рейт-лимит, бюджет."""

    @classmethod
    def setUpClass(cls):
        store.init_db()
        arena_app.app.config["TESTING"] = True

    def setUp(self):
        self.client = arena_app.app.test_client()
        arena_app._HITS.clear()

    def _auth(self):
        return {"Authorization": f"Bearer {config.ARENA_TOKEN}"}

    def test_health_needs_no_token(self):
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_chat_without_token_is_401(self):
        response = self.client.post("/api/chat", json={"message": "привет"})
        self.assertEqual(response.status_code, 401)

    def test_chat_with_wrong_token_is_401(self):
        response = self.client.post(
            "/api/chat",
            json={"message": "привет"},
            headers={"Authorization": "Bearer nope"},
        )
        self.assertEqual(response.status_code, 401)

    def test_empty_message_is_400(self):
        response = self.client.post("/api/chat", json={"message": "  "}, headers=self._auth())
        self.assertEqual(response.status_code, 400)

    def test_long_message_is_413(self):
        response = self.client.post(
            "/api/chat", json={"message": "x" * 200}, headers=self._auth()
        )
        self.assertEqual(response.status_code, 413)

    def test_unknown_file_id_is_404(self):
        response = self.client.post(
            "/api/chat",
            json={"message": "разбери файл", "file_id": "нет такого"},
            headers=self._auth(),
        )
        self.assertEqual(response.status_code, 404)

    def test_upload_rejects_unknown_extension(self):
        from io import BytesIO

        response = self.client.post(
            "/api/upload",
            data={"file": (BytesIO(b"MZ..."), "payload.exe")},
            headers=self._auth(),
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    def test_upload_rejects_oversized_file(self):
        from io import BytesIO

        response = self.client.post(
            "/api/upload",
            data={"file": (BytesIO(b"x" * 2048), "big.txt")},
            headers=self._auth(),
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 413)

    def test_upload_accepts_allowed_extension(self):
        from io import BytesIO

        response = self.client.post(
            "/api/upload",
            data={"file": (BytesIO(b"# note"), "note.md")},
            headers=self._auth(),
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("file_id", response.get_json())

    def test_rate_limit_kicks_in(self):
        from io import BytesIO

        limit = config.RATE_LIMIT_PER_MIN
        for _ in range(limit):
            self.client.post(
                "/api/upload",
                data={"file": (BytesIO(b"x"), "a.txt")},
                headers=self._auth(),
                content_type="multipart/form-data",
            )
        response = self.client.post(
            "/api/upload",
            data={"file": (BytesIO(b"x"), "a.txt")},
            headers=self._auth(),
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 429)

    def test_budget_exhausted_is_402(self):
        """Исчерпанный бюджет закрывает вход раньше, чем начнутся траты."""
        store.add_spend(config.BUDGET_RUB + 1)
        try:
            response = self.client.post(
                "/api/chat", json={"message": "привет"}, headers=self._auth()
            )
            self.assertEqual(response.status_code, 402)
        finally:
            store.add_spend(-(config.BUDGET_RUB + 1))

    def test_session_is_isolated_by_header(self):
        first = self.client.get("/api/state", headers=self._auth()).get_json()
        second = self.client.get(
            "/api/state",
            headers={**self._auth(), "X-Session-Id": first["session_id"]},
        ).get_json()
        self.assertEqual(first["session_id"], second["session_id"])


if __name__ == "__main__":
    print(f"[TESTS] временные данные: {_TMP}")
    unittest.main(verbosity=2)
