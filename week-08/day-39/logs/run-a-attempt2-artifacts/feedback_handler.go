package api

import (
	"encoding/json"
	"os"
	"time"
)

// feedbackRecord represents a feedback record
type feedbackRecord struct {
	Rating   int    "json:rating"
	Comment  string "json:comment"
	RecipeID string "json:recipe_id"
	Timestamp string "json:timestamp"
}

// handleFeedback handles POST /api/v1/feedback requests
func (s *Server) handleFeedback(w http.ResponseWriter, r *http.Request) {
	var req feedbackRecord

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}

	if req.Rating < 1 || req.Rating > 5 {
		http.Error(w, "rating must be between 1 and 5", http.StatusBadRequest)
		return
	}

	feedbackPath := os.Getenv("FEEDBACK_PATH")
	if feedbackPath == "" {
		feedbackPath = "./feedback.jsonl"
	}

	file, err := os.OpenFile(feedbackPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		http.Error(w, "failed to open feedback file: " + err.Error(), http.StatusInternalServerError)
		return
	}
	delay := time.Now().Format(time.RFC3339)
	record := feedbackRecord{
		Rating:   req.Rating,
		Comment:  req.Comment,
		RecipeID: req.RecipeID,
		Timestamp: delay,
	}

	if err := json.NewEncoder(file).Encode(record); err != nil {
		http.Error(w, "failed to write feedback record: " + err.Error(), http.StatusInternalServerError)
		return
	}
	file.Close()

	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}