# Неделя 10 — День 49. Security step

## Задание

Возьмите свой execution loop из Недели 7 (docent). Добавьте security step между генерацией и коммитом:
- отправьте код на security review вторым вызовом LLM с отдельным security-промптом 
- если найдены Critical/High — цикл возвращается на генерацию с фидбеком, например: "исправь: SQL injection в строке 42" 
- если только Medium/Low — пропускаем с warning в логе 
- если чисто — коммитим

Пропустите весь loop через LLM Gateway из Дня 48:
- все вызовы к LLM (и генерация, и security review) идут через ваш прокси 
- убедитесь что ни на одном этапе в промпт не утекают секреты, токены, PII из кодовой базы 
- зафиксируйте в логах: что gateway поймал и заблокировал, что пропустил чистым

Настройте security-промпт под свой стек:
- iOS: проверка Keychain vs UserDefaults, ATS exceptions, certificate pinning 
- Android: проверка encrypted SharedPreferences, network security config, root detection 
- общее: hardcoded secrets, PII в логах, HTTP вместо HTTPS, отсутствие input validation

Протестируйте на 3 задачах:
- дайте агенту 3 задачи которые провоцируют небезопасный код: "сохрани токен авторизации", "добавь логирование всех запросов", "сделай запрос на API" 
- зафиксируйте: что поймал security step, что поймал gateway, что пропустили оба

Результат:
- Execution loop с security step + LLM Gateway на всех вызовах + security-промпт под ваш стек + 3 задачи с логами: что поймал security review, что поймал gateway, что прошло мимо обоих

Формат:
- Видео + Код
