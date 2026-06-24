import sqlite3
from typing import Optional


class CompressionMixin:
    """Mixin-класс для сжатия истории диалога."""

    def _save_compression_mode(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET compression_enabled = ? WHERE id = ?",
                (int(self.compression_enabled), self.current_session["id"])
            )
            conn.commit()
        self.current_session["compression_enabled"] = self.compression_enabled

    def enable_compression(self):
        self.compression_enabled = True
        self._save_compression_mode()
        print("✅ Сжатие истории включено")

    def disable_compression(self):
        self.compression_enabled = False
        self._save_compression_mode()
        print("✅ Сжатие истории выключено")

    def toggle_compression(self):
        if self.compression_enabled:
            self.disable_compression()
        else:
            self.enable_compression()

    def compress_now(self) -> Optional[dict]:
        messages = [m for m in self.conversation_history if m["role"] != "system"]
        if len(messages) < self.compression_interval:
            print(f"ℹ️ В истории {len(messages)} сообщений, нужно минимум {self.compression_interval} для сжатия.")
            return None
        return self.compress_history()

    def get_raw_messages(self) -> list:
        result = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        result.extend([m for m in self.conversation_history if m["role"] not in ("system", "command")])
        return result

    def get_compressed_messages(self) -> list:
        result = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        for item in self.compression_history:
            if item["type"] == "summary":
                result.append({"role": "system", "content": f"[АРХИВ: {item['content']}]"})
        result.extend([m for m in self.conversation_history if m["role"] not in ("system", "command")])
        return result

    def _count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def compress_history(self) -> Optional[dict]:
        messages = [m for m in self.conversation_history if m["role"] != "system"]
        if len(messages) < self.compression_interval:
            return None

        to_compress = messages[:self.compression_interval]
        remaining = messages[self.compression_interval:]

        history_text = "\n\n".join([f"{m['role'].upper()}: {m['content']}" for m in to_compress])
        token_count_before = sum(self._count_tokens(m['content']) for m in to_compress)

        compress_prompt = (
            "Суммаризируй следующий фрагмент диалога в виде краткого описания основных тем и ключевых фактов. "
            "Сохрани важную информацию, но сократи объём минимум в 5 раз.\n\n"
            f"Диалог:\n{history_text}"
        )

        messages_for_compress = [
            {"role": "system", "content": "Ты — эксперт по суммаризации диалогов."},
            {"role": "user", "content": compress_prompt}
        ]

        response = self._call_api(messages_for_compress)

        if response["success"]:
            summary = response["content"]
            token_count_after = self._count_tokens(summary)

            item = {
                "type": "summary",
                "content": summary,
                "source_messages": self.compression_interval,
                "tokens_before": token_count_before,
                "tokens_after": token_count_after
            }
            self.compression_history.append(item)
            self._save_compressed_summary(summary, self.compression_interval, token_count_before, token_count_after)

            self.conversation_history = []
            if self.system_prompt:
                self.conversation_history.append({"role": "system", "content": self.system_prompt})
            self.conversation_history.extend(remaining)

            comp_rate = round((1 - token_count_after / token_count_before) * 100, 1) if token_count_before > 0 else 0

            print(f"📦 История сжата: {len(to_compress)} сообщений → summary (экономия {comp_rate}%, "
                  f"{token_count_before} → {token_count_after} токенов)")

            return {
                "summary": summary,
                "tokens_before": token_count_before,
                "tokens_after": token_count_after,
                "compression_rate": comp_rate
            }

        return None

    def _load_compressed_summaries(self) -> list:
        summaries = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT content, source_count, tokens_before, tokens_after "
                "FROM compressed_summaries WHERE session_id = ? ORDER BY created_at ASC",
                (self.current_session["id"],)
            )
            for row in cursor.fetchall():
                summaries.append({
                    "type": "summary",
                    "content": row[0],
                    "source_messages": row[1],
                    "tokens_before": row[2],
                    "tokens_after": row[3]
                })
        return summaries

    def _save_compressed_summary(self, content: str, source_count: int, tokens_before: int, tokens_after: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO compressed_summaries (session_id, content, source_count, tokens_before, tokens_after) "
                "VALUES (?, ?, ?, ?, ?)",
                (self.current_session["id"], content, source_count, tokens_before, tokens_after)
            )
            conn.commit()

    def _clear_compressed_summaries(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM compressed_summaries WHERE session_id = ?",
                (self.current_session["id"],)
            )
            conn.commit()
