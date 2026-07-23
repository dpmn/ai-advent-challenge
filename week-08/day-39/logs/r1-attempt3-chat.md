
# aider chat started at 2026-07-23 16:49:03

> /root/.local/aider-venv/bin/aider --message-file /tmp/claude-0/-home-development-personal-ai-advent-challenge/70aac302-91af-4d2d-a430-767a7810a5b8/scratchpad/r1-prompt.txt --yes-always --no-stream internal/api/api.go internal/api/api_test.go  
> Aider v0.86.2  
> Model: ollama_chat/qwen2.5-coder:7b-instruct-q3_K_M with whole edit format  
> Git repo: .git with 34 files  
> Repo-map: using 1024 tokens, auto refresh  
> Added internal/api/api.go to the chat.  
> Added internal/api/api_test.go to the chat.  
> Added CONVENTIONS.md to the chat (read-only).  

#### Добавь новый HTTP-эндпоинт POST /api/v1/feedback — обратная связь пользователя по готовому рецепту.  
####   
#### Контракт:  
#### - Тело запроса — JSON: recipe_id (string, опционально), rating (int, обязателен, допустимо 1..5),  
####   comment (string, опционально, не длиннее 1000 символов).  
#### - Битый JSON или rating вне 1..5 → ответ 400 с JSON-ошибкой.  
#### - При успехе: добавить одну запись в append-only JSONL-файл. Путь берётся из переменной окружения  
####   FEEDBACK_PATH (по умолчанию ./feedback.jsonl). Запись — строка JSON с полями запроса плюс временная  
####   метка получения (UTC, RFC3339). Ответ — JSON {"status":"ok"}, код 201.  
#### - Эндпоинт покрой тестом.  
####   
#### Больше ничего не меняй.  

Конечно, добавлю новый HTTP-эндпоинт `/api/v1/feedback`.

internal/api/api.go
```
// Package api — HTTP-слой сервиса: маршруты, разбор запросов, JSON-ответы.
package api

import (
	"context"
	"encoding/json"
	"errors"
	"io/fs"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"fridge-chef/internal/corpus"
	"fridge-chef/internal/generate"
	"fridge-chef/internal/match"
)

// Server держит зависимости хендлеров: корпус и LLM.
type Server struct {
	corpus  *corpus.Corpus
	chatter generate.Chatter
}

// New регистрирует маршруты API и статику UI на mux.
func New(mux *http.ServeMux, c *corpus.Corpus, ch generate.Chatter, static fs.FS) *Server {
	s := &Server{corpus: c, chatter: ch}
	mux.HandleFunc("GET /healthz", s.handleHealthz)
	mux.HandleFunc("GET /api/v1/corpus", s.handleCorpus)
	mux.HandleFunc("POST /api/v1/suggest", s.handleSuggest)
	mux.HandleFunc("POST /api/v1/recipe", s.handleRecipe)
	mux.HandleFunc("POST /api/v1/feedback", s.handleFeedback) // Новый эндпоинт
	mux.Handle("GET /", http.FileServerFS(static))
	return s
}

// handleHealthz — проверка живости сервиса.
func (s *Server) handleHealthz(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// handleCorpus отдаёт манифест активной версии корпуса.
func (s *Server) handleCorpus(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.corpus.Manifest())
}

type suggestRequest struct {
	Products []string `json:"products"`
	Limit    int      `json:"limit"`
}

type candidateDTO struct {
	ID       string   `json:"id"`
	Title    string   `json:"title"`
	URL      string   `json:"url"`
	Coverage float64  `json:"coverage"`
	Matched  int      `json:"matched"`
	Required int      `json:"required"`
	Missing  []string `json:"missing"`
}

type suggestResponse struct {
	Products   []string       `json:"products"`
	Candidates []candidateDTO `json:"candidates"`
	Message    string         `json:"message,omitempty"`
}

// handleSuggest подбирает рецепты-кандидаты по списку продуктов.
func (s *Server) handleSuggest(w http.ResponseWriter, r *http.Request) {
	var req suggestRequest
	if !decode(w, r, &req) {
		return
	}
	products := normalize(s.corpus, req.Products)
	if len(products) == 0 {
		writeError(w, http.StatusBadRequest, "укажи хотя бы один продукт")
		return
	}
	if req.Limit > 50 {
		req.Limit = 50
	}

	cands := match.Suggest(s.corpus.Recipes(), products, req.Limit)
	resp := suggestResponse{Products: products, Candidates: make([]candidateDTO, 0, len(cands))}
	for _, c := range cands {
		if c.Missing == nil {
			c.Missing = []string{}
		}
		resp.Candidates = append(resp.Candidates, candidateDTO{
			ID:       c.Recipe.ID,
			Title:    c.Recipe.Title,
			URL:      c.Recipe.Source.URL,
			Coverage: c.Coverage,
			Matched:  c.Matched,
			Required: c.Required,
			Missing:  c.Missing,
		})
	}
	if len(cands) == 0 {
		resp.Message = "Подходящего рецепта не нашёл. Могу придумать блюдо из твоих продуктов (режим creative)."
	}
	writeJSON(w, http.StatusOK, resp)
}

type recipeRequest struct {
	RecipeID string   `json:"recipe_id"`
	Products []string `json:"products"`
	Mode     string   `json:"mode"`
}

type recipeResponse struct {
	Mode     string         `json:"mode"`
	Invented bool           `json:"invented"`
	Title    string         `json:"title,omitempty"`
	Source   *corpus.Source `json:"source,omitempty"`
	Text     string         `json:"text"`
}

// handleRecipe отдаёт полный рецепт: strict — из корпуса без LLM,
// combine — LLM-адаптация под продукты, creative — LLM-импровизация без рецепта-основы.
func (s *Server) handleRecipe(w http.ResponseWriter, r *http.Request) {
	var req recipeRequest
	if !decode(w, r, &req) {
		return
	}
	mode := req.Mode
	if mode == "" {
		mode = "combine"
	}
	products := normalize(s.corpus, req.Products)

	switch mode {
	case "creative":
		if len(products) == 0 {
			writeError(w, http.StatusBadRequest, "для creative нужны продукты")
			return
		}
		text, err := generate.Creative(r.Context(), s.chatter, products)
		if err != nil {
			log.Printf("recipe creative: products=%v: %v", products, err)
			writeError(w, http.StatusBadGateway, "генерация не удалась, попробуй ещё раз")
			return
		}
		writeJSON(w, http.StatusOK, recipeResponse{Mode: mode, Invented: true, Text: text})

	case "strict", "combine":
		rec, err := s.corpus.Get(req.RecipeID)
		if errors.Is(err, corpus.ErrNotFound) {
			writeError(w, http.StatusNotFound, "рецепт не найден: "+req.RecipeID)
			return
		}
		if err != nil {
			log.Printf("recipe: corpus.Get(%s): %v", req.RecipeID, err)
			writeError(w, http.StatusInternalServerError, "внутренняя ошибка")
			return
		}
		resp := recipeResponse{Mode: mode, Title: rec.Title, Source: &rec.Source}
		if mode == "strict" {
			resp.Text = generate.Strict(rec)
			writeJSON(w, http.StatusOK, resp)
			return
		}
		if len(products) == 0 {
			writeError(w, http.StatusBadRequest, "для combine нужны продукты")
			return
		}
		text, err := generate.Combine(r.Context(), s.chatter, rec, products)
		if err != nil {
			log.Printf("recipe combine: id=%s products=%v: %v", rec.ID, products, err)
			writeError(w, http.StatusBadGateway, "генерация не удалась, попробуй ещё раз")
			return
		}
		resp.Text = text
		writeJSON(w, http.StatusOK, resp)

	default:
		writeError(w, http.StatusBadRequest, "mode должен быть strict, combine или creative")
	}
}

// normalize канонизирует имена продуктов и выкидывает пустые и дубли.
func normalize(c *corpus.Corpus, products []string) []string {
	seen := make(map[string]bool)
	out := make([]string, 0, len(products))
	for _, p := range products {
		if strings.TrimSpace(p) == "" {
			continue
		}
		name := c.Canon(p)
		if !seen[name] {
			seen[name] = true
			out = append(out, name)
		}
	}
	return out
}

// decode разбирает JSON-тело; false — ответ с ошибкой уже отправлен.
func decode(w http.ResponseWriter, r *http.Request, dst any) bool {
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(dst); err != nil {
		writeError(w, http.StatusBadRequest, "некорректный JSON в теле запроса")
		return false
	}
	return true
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		log.Printf("api: запись ответа: %v", err)
	}
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

// handleFeedback обрабатывает обратную связь пользователя.
func (s *Server) handleFeedback(w http.ResponseWriter, r *http.Request) {
	var req struct {
		RecipeID string `json:"recipe_id,omitempty"`
		Rating   int    `json:"rating"`
		Comment  string `json:"comment,omitempty"`
	}
	if !decode(w, r, &req) {
		return
	}

	if req.Rating < 1 || req.Rating > 5 {
		writeError(w, http.StatusBadRequest, "рейтинг должен быть в диапазоне 1..5")
		return
	}

	if len(req.Comment) > 1000 {
		writeError(w, http.StatusBadRequest, "комментарий не может превышать 1000 символов")
		return
	}

	path := os.Getenv("FEEDBACK_PATH")
	if path == "" {
		path = "./feedback.jsonl"
	}

	timestamp := time.Now().UTC().Format(time.RFC3339)
	entry, err := json.Marshal(map[string]interface{}{
		"recipe_id": req.RecipeID,
		"rating":    req.Rating,
		"comment":   req.Comment,
		"timestamp": timestamp,
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, "ошибка при формировании записи")
		return
	}

	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "ошибка открытия файла для записи")
		return
	}
	defer file.Close()

	if _, err := file.Write(entry); err != nil {
		writeError(w, http.StatusInternalServerError, "ошибка записи в файл")
		return
	}

	writeJSON(w, http.StatusCreated, map[string]string{"status": "ok"})
}
```

internal/api/api_test.go
```
package api

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"testing/fstest"

	"fridge-chef/internal/corpus"
)

// fakeChatter возвращает фиксированный ответ вместо LLM.
type fakeChatter struct {
	reply string
	err   error
}

func (f *fakeChatter) Chat(context.Context, string, string) (string, error) {
	return f.reply, f.err
}

const testRecipes = `{"id":"r1","title":"Сырники","source":{"name":"povarenok.ru","url":"https://example.com/1"},"ingredients":[{"name_raw":"Творог","name":"творог","qty":400,"unit":"г"},{"name_raw":"Яйцо куриное","name":"яйцо","qty":2,"unit":"шт"}],"steps":[],"tags":[],"text":""}
{"id":"r2","title":"Плов","source":{"name":"povarenok.ru","url":"https://example.com/2"},"ingredients":[{"name_raw":"Рис","name":"рис","qty":null,"unit":""},{"name_raw":"Свинина","name":"свинина","qty":null,"unit":""},{"name_raw":"Морковь","name":"морковь","qty":null,"unit":""}],"steps":[],"tags":[],"text":""}
`

const testManifest = `{"version":"v-test","created_at":"","sources":[],"embed_model":null,"embed_dim":null,"recipes_count":2,"checksum":""}`

// newTestServer собирает сервер API поверх мини-корпуса и фейковой LLM.
func newTestServer(t *testing.T, ch *fakeChatter) *httptest.Server {
	t.Helper()
	dir := t.TempDir()
	files := map[string]string{
		"recipes.jsonl": testRecipes,
		"manifest.json": testManifest,
		"canon.json":    `{"яйца":"яйцо"}`,
	}
	for name, content := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	c, err := corpus.Load(dir)
	if err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	static := fstest.MapFS{"index.html": &fstest.MapFile{Data: []byte("<html>ui</html>")}}
	New(mux, c, ch, static)
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

// post выполняет POST с JSON и декодирует ответ в out.
func post(t *testing.T, srv *httptest.Server, path, body string, out any) int {
	t.Helper()
	resp, err := http.Post(srv.URL+path, "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode
}

func TestHealthzAndStatic(t *testing.T) {
	srv := newTestServer(t, &fakeChatter{})
	for path, want := range map[string]int{"/healthz": 200, "/": 200, "/api/v1/corpus": 200} {
		resp, err := http.Get(srv.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != want {
			t.Errorf("GET %s = %d, ожидалось %d", path, resp.StatusCode, want)
		}
	}
}

func TestSuggest(t *testing.T) {
	srv := newTestServer(t, &fakeChatter{})
	var resp suggestResponse
	code := post(t, srv, "/api/v1/suggest", `{"products":["Творог","яйца",""]}`, &resp)
	if code != 200 {
		t.Fatalf("статус %d", code)
	}
	// «яйца» нормализовалось словарём, пустой продукт выброшен.
	if len(resp.Products) != 2 || resp.Products[1] != "яйцо" {
		t.Fatalf("products = %v", resp.Products)
	}
	if len(resp.Candidates) != 1 || resp.Candidates[0].ID != "r1" {
		t.Fatalf("candidates = %+v", resp.Candidates)
	}
	if resp.Candidates[0].Coverage != 1 {
		t.Fatalf("coverage = %v", resp.Candidates[0].Coverage)
	}
}

func TestSuggestNothingFound(t *testing.T) {
	srv := newTestServer(t, &fakeChatter{})
	var resp suggestResponse
	if code := post(t, srv, "/api/v1/suggest", `{"products":["ананас"]}`, &resp); code != 200 {
		t.Fatalf("статус %d", code)
	}
	if len(resp.Candidates) != 0 || resp.Message == "" {
		t.Fatalf("ожидалось пусто с message, получено %+v", resp)
	}
}

func TestSuggestBadRequest(t *testing.T) {
	srv := newTestServer(t, &fakeChatter{})
	var e map[string]string
	if code := post(t, srv, "/api/v1/suggest", `{"products":[]}`, &e); code != 400 {
		t.Fatalf("пустые продукты: статус %d", code)
	}
	if code := post(t, srv, "/api/v1/suggest", `не json`, &e); code != 400 {
		t.Fatalf("битый json: статус %d", code)
	}
}

func TestRecipeStrict(t *testing.T) {
	srv := newTestServer(t, &fakeChatter{err: errors.New("LLM не должна вызываться в strict")})
	var resp recipeResponse
	code := post(t, srv, "/api/v1/recipe", `{"recipe_id":"r1","mode":"strict"}`, &resp)
	if code != 200 {
		t.Fatalf("статус %d", code)
	}
	if !strings.Contains(resp.Text, "Сырники") || !strings.Contains(resp.Text, "Творог — 400 г") {
		t.Fatalf("strict-текст: %s", resp.Text)
	}
	if resp.Invented {
		t.Fatal("strict не может быть invented")
	}
}

func TestRecipeCombine(t *testing.T) {
	srv := newTestServer(t, &fakeChatter{reply: "адаптированный рецепт"})
	var resp recipeResponse
	code := post(t, srv, "/api/v1/recipe",
		`{"recipe_id":"r1","products":["творог","яйца"],"mode":"combine"}`, &resp)
	if code != 200 || resp.Text != "адаптированный рецепт" {
		t.Fatalf("combine: код %d, %+v", code, resp)
	}
	if resp.Source == nil || resp.Source.URL != "https://example.com/1" {
		t.Fatalf("combine без источника: %+v", resp)
	}
}

func TestRecipeCreative(t *testing.T) {
	srv := newTestServer(t, &fakeChatter{reply: "> Импровизация…"})
	var resp recipeResponse
	code := post(t, srv, "/api/v1/recipe", `{"products":["кабачок"],"mode":"creative"}`, &resp)
	if code != 200 || !resp.Invented {
		t.Fatalf("creative: код %d, %+v", code, resp)
	}
}

func TestRecipeErrors(t *testing.T) {
	tests := []struct {
		name, body string
		chatter    *fakeChatter
		wantCode   int
	}{
		{"неизвестный id", `{"recipe_id":"нет","mode":"strict"}`, &fakeChatter{}, 404},
		{"неизвестный mode", `{"recipe_id":"r1","mode":"жарь"}`, &fakeChatter{}, 400},
		{"combine без продуктов", `{"recipe_id":"r1","mode":"combine"}`, &fakeChatter{}, 400},
		{"creative без продуктов", `{"mode":"creative"}`, &fakeChatter{}, 400},
		{"ошибка LLM", `{"recipe_id":"r1","products":["творог"],"mode":"combine"}`,
			&fakeChatter{err: errors.New("boom")}, 502},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := newTestServer(t, tc.chatter)
			var e map[string]string
			if code := post(t, srv, "/api/v1/recipe", tc.body, &e); code != tc.wantCode {
				t.Fatalf("статус %d, ожидался %d (%v)", code, tc.wantCode, e)
			}
		})
	}
}

func TestFeedback(t *testing.T) {
	srv := newTestServer(t, &fakeChatter{})
	var resp map[string]string
	code := post(t, srv, "/api/v1/feedback", `{"rating":3}`, &resp)
	if code != 201 || resp["status"] != "ok" {
		t.Fatalf("статус %d, ожидался 201 и status:ok (%v)", code, resp)
	}

	code = post(t, srv, "/api/v1/feedback", `{"rating":5,"comment":"Отлично!"}`, &resp)
	if code != 201 || resp["status"] != "ok" {
		t.Fatalf("статус %d, ожидался 201 и status:ok (%v)", code, resp)
	}

	code = post(t, srv, "/api/v1/feedback", `{"rating":5,"comment":"Очень длинный комментарий, который должен быть не более 1000 символов."}`, &resp)
	if code != 201 || resp["status"] != "ok" {
		t.Fatalf("статус %d, ожидался 201 и status:ok (%v)", code, resp)
	}

	code = post(t, srv, "/api/v1/feedback", `{"rating":5,"comment":"Очень длинный комментарий, который должен быть не более 1000 символов. Ого, это очень длинный текст!"}`, &resp)
	if code != 400 {
		t.Fatalf("статус %d, ожидался 400", code)
	}

	code = post(t, srv, "/api/v1/feedback", `{"rating":6}`, &resp)
	if code != 400 {
		t.Fatalf("статус %d, ожидался 400", code)
	}
}
```

> Tokens: 6.4k sent, 4.7k received.  
> Applied edit to internal/api/api_test.go  
> Applied edit to internal/api/api.go  
> summarizer unexpectedly failed for all models  
