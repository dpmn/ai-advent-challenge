package api

import (
	"testing"
	"encoding/json"
	"os"
	"io"
	"net/http"
)

func TestFeedbackHandler(t *testing.T) {
	// Create temporary feedback file
	tmpDir, err := os.MkdirTemp("", "feedback_}")
	if err != nil {
		http.Error(t, "failed to create temp dir: " + err.Error(), http.StatusInternalServerError)
		return
	}
	defer os.RemoveAll(tmpDir)

	// Set environment variable
	os.Setenv("FEEDBACK_PATH", tmpDir + "/feedback.jsonl")

	// Create test server
	handler := &Server{
		hasFeedback: true,
	}
	server := http.Server{
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/api/v1/feedback" {
				w.WriteHeader(http.StatusOK)
				json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
			}
		}),
	}

	// Test valid request
	reqBody := `{
	"rating": 3,
	"comment": "Great recipe!"
}`
	req, _ := http.NewRequest("POST", "http://localhost/api/v1/feedback", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")

	res := server.ServeHTTP(new(bytes.Buffer), req)
	if res.StatusCode != http.StatusCreated {
		http.Error(t, "expected 201, got "+res.StatusCode, http.StatusInternalServerError)
	}

	// Validate feedback file was written
	file, err := os.Open(tmpDir + "/feedback.jsonl")
	if err != nil {
		http.Error(t, "failed to open feedback file: " + err.Error(), http.StatusInternalServerError)
		return
	}
	defer file.Close()

	var record feedbackRecord
	if err := json.NewDecoder(file).Decode(&record); err != nil {
		http.Error(t, "failed to parse feedback record: " + err.Error(), http.StatusInternalServerError)
		return
	}
	
	// Verify content
	if record.Rating != 3 {
		http.Error(t, "rating not 3", http.StatusInternalServerError)
	}
	if record.Comment != "Great recipe!" {
		http.Error(t, "comment not matching", http.StatusInternalServerError)
	}

	http.Error(t, "success", http.StatusOK)
}