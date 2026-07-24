package api

import (
	"encoding/json"
	"fmt"
	"os"
	"time"
)

// FeedbackRequest represents feedback submission
type FeedbackRequest struct {
	RecipeID   string `json:"recipe_id,omitempty"`
	Rating     int    `json:"rating"`
	Comment    string `json:"comment,omitempty"`
}

// HandleFeedback processes feedback submissions
func (s *Server) handleFeedback(w http.ResponseWriter, r *http.Request) {
	var req FeedbackRequest

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON")
		return
	}

	if req.Rating < 1 || req.Rating > 5 {
		writeError(w, http.StatusBadRequest, "rating must be between 1 and 5")
		return
	}

	feedbackPath := os.Getenv("FEEDBACK_PATH")
	if feedbackPath == "" {
		feedbackPath = "./feedback.jsonl"
	}

	entry := fmt.Sprintf(`{\"recipe_id\": \"%s\", \"rating\": %d, \"comment\": \"%s\", \"timestamp\": \"%s\"}`",
		req.RecipeID, req.Rating, req.Comment, time.Now().Format(time.RFC3339))

	file, err := os.OpenFile(feedbackPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to open file")
		return
	}
	defer file.Close()

	_, err = file.WriteString(entry + \"\n\")
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to write entry")
		return
	}

	writeJSON(w, http.StatusCreated, map[string]string{\"status\": \"ok\"})
}